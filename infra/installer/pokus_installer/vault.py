"""The lab's secrets in ``group_vars/all/vault.yml`` (BUILD_PROMPT §18.3, §18.7).

Secrets stay in memory until they are encrypted here with Ansible's own vault
code (the installer environment holds the same ansible-core as the Ansible
environment). Generated secrets use :func:`secrets.token_urlsafe`; existing
secrets are never regenerated, so modify mode only adds what is missing and
replaces what the operator provided anew. The layout is the one of
``vault.yml.example``.
"""

from __future__ import annotations

import io
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from pokus_installer.answers import Answers

TOKEN_BYTES = 32
MIN_GRAFANA_ADMIN = 8
# Order of the keys in the vault (vault.yml.example).
ORDER = (
    "vault_postgres_passwords",
    "vault_grafana_admin_password",
    "vault_grafana_user_passwords",
    "vault_restic_repository",
    "vault_restic_password",
    "vault_tailscale_authkey",
    "vault_hf_token",
)


class VaultError(RuntimeError):
    """The vault cannot be read or a required secret is missing."""


@dataclass
class Provided:
    """Secrets the operator entered or passed as POKUS_SECRET_<NAME> (never logged).

    Attributes:
        tailscale_authkey: Tailscale auth key.
        restic_password: restic repository password.
        grafana_admin_password: Grafana admin password.
        hf_token: Hugging Face token for gated models.
    """

    tailscale_authkey: str = field(default="", repr=False)
    restic_password: str = field(default="", repr=False)
    grafana_admin_password: str = field(default="", repr=False)
    hf_token: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> Provided:
        """Read the ``POKUS_SECRET_<NAME>`` variables (BUILD_PROMPT §18.6).

        Args:
            environ: The environment.

        Returns:
            The provided secrets.
        """
        return cls(
            tailscale_authkey=environ.get("POKUS_SECRET_TAILSCALE_AUTHKEY", ""),
            restic_password=environ.get("POKUS_SECRET_RESTIC_PASSWORD", ""),
            grafana_admin_password=environ.get("POKUS_SECRET_GRAFANA_ADMIN_PASSWORD", ""),
            hf_token=environ.get("POKUS_SECRET_HF_TOKEN", ""),
        )

    def values(self) -> list[str]:
        """Every non-empty provided secret (for the log redaction filter).

        Returns:
            The values.
        """
        return [v for v in vars(self).values() if v]


@dataclass
class Result:
    """What :func:`build` did.

    Attributes:
        data: The vault content.
        generated: Names of the secrets generated now.
        shown_once: Generated secrets the operator must store (name, value).
    """

    data: dict[str, Any]
    generated: list[str]
    shown_once: list[tuple[str, str]] = field(repr=False)


def postgres_roles(defaults: Path) -> list[str]:
    """The database roles that get a password, from the postgres role's defaults.

    Args:
        defaults: ``ansible/roles/postgres/defaults/main.yml``.

    Returns:
        Role names: the service roles, the researchers' reader role and Grafana's.
    """
    data = YAML(typ="safe").load(defaults.read_text(encoding="utf-8"))
    roles = [str(r["role"]) for r in data["postgres_service_roles"]]
    return [*roles, str(data["postgres_reader_role"]), str(data["postgres_grafana_role"])]


def _token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def build(
    answers: Answers,
    existing: Mapping[str, Any] | None,
    provided: Provided,
    roles: list[str],
    generate: Callable[[], str] = _token,
) -> Result:
    """The vault content for the answers.

    Args:
        answers: The answers.
        existing: The decrypted current vault (modify mode), or None.
        provided: Secrets entered or passed in the environment.
        roles: Database roles (:func:`postgres_roles`).
        generate: Secret generator (tests replace it).

    Returns:
        The content, the generated names and the shown-once secrets.

    Raises:
        VaultError: If a secret the answers call provided is missing.
    """
    data: dict[str, Any] = {k: v for k, v in (existing or {}).items()}
    generated: list[str] = []
    shown_once: list[tuple[str, str]] = []

    def ensure(mapping: dict[str, Any], key: str, label: str) -> None:
        if not mapping.get(key):
            mapping[key] = generate()
            generated.append(label)

    passwords = dict(data.get("vault_postgres_passwords") or {})
    for role in roles:
        ensure(passwords, role, f"vault_postgres_passwords.{role}")
    data["vault_postgres_passwords"] = passwords
    _grafana_admin(answers, provided, data, ensure)
    users = dict(data.get("vault_grafana_user_passwords") or {})
    for researcher in answers.researchers:
        if researcher.grafana:
            ensure(users, researcher.name, f"vault_grafana_user_passwords.{researcher.name}")
    data["vault_grafana_user_passwords"] = users
    _restic(answers, provided, data, ensure, shown_once)
    _tailscale(answers, provided, data)
    if provided.hf_token:
        data["vault_hf_token"] = provided.hf_token
    elif answers.secrets.hf_token == "provided" and not data.get("vault_hf_token"):  # noqa: S105
        raise VaultError("the answers need a Hugging Face token: set POKUS_SECRET_HF_TOKEN")
    data.setdefault("vault_hf_token", "")
    ordered = {k: data[k] for k in ORDER if k in data}
    ordered.update({k: v for k, v in data.items() if k not in ordered})
    return Result(ordered, generated, shown_once)


def _grafana_admin(
    answers: Answers,
    provided: Provided,
    data: dict[str, Any],
    ensure: Callable[[dict[str, Any], str, str], None],
) -> None:
    if provided.grafana_admin_password:
        if len(provided.grafana_admin_password) < MIN_GRAFANA_ADMIN or "$" in (
            provided.grafana_admin_password
        ):
            raise VaultError("the Grafana admin password needs 8 or more characters and no '$'")
        data["vault_grafana_admin_password"] = provided.grafana_admin_password
    elif answers.secrets.grafana_admin == "provided" and not data.get(
        "vault_grafana_admin_password"
    ):
        raise VaultError(
            "the answers provide the Grafana admin password: "
            "set POKUS_SECRET_GRAFANA_ADMIN_PASSWORD"
        )
    else:
        ensure(data, "vault_grafana_admin_password", "vault_grafana_admin_password")


def _restic(
    answers: Answers,
    provided: Provided,
    data: dict[str, Any],
    ensure: Callable[[dict[str, Any], str, str], None],
    shown_once: list[tuple[str, str]],
) -> None:
    repository = answers.backups.repository
    data["vault_restic_repository"] = repository.url_for_restic()
    if repository.type == "later":
        data.setdefault("vault_restic_password", "")
        return
    if provided.restic_password:
        data["vault_restic_password"] = provided.restic_password
    elif answers.backups.password == "provided" and not data.get(  # noqa: S105 - the choice
        "vault_restic_password"
    ):
        raise VaultError(
            "the answers provide the restic password: set POKUS_SECRET_RESTIC_PASSWORD"
        )
    elif not data.get("vault_restic_password"):
        ensure(data, "vault_restic_password", "vault_restic_password")
        shown_once.append(("restic password", str(data["vault_restic_password"])))


def _tailscale(answers: Answers, provided: Provided, data: dict[str, Any]) -> None:
    if answers.remote_access.tailscale_login == "interactive":
        data["vault_tailscale_authkey"] = ""
    elif provided.tailscale_authkey:
        data["vault_tailscale_authkey"] = provided.tailscale_authkey
    elif not data.get("vault_tailscale_authkey"):
        raise VaultError(
            "the answers log in to Tailscale with an auth key: set POKUS_SECRET_TAILSCALE_AUTHKEY"
        )


def preload() -> None:
    """Import Ansible's vault code now.

    The TUI calls this before Textual takes over the terminal: on import,
    ``ansible.utils.color`` probes standard output with curses, which fails on
    the stream Textual puts in its place.
    """
    import ansible.errors  # noqa: PLC0415
    import ansible.parsing.vault  # noqa: F401, PLC0415


def _vault(password: str) -> Any:  # noqa: ANN401 - ansible's VaultLib has no type hints
    from ansible.parsing.vault import VaultLib, VaultSecret  # noqa: PLC0415 - heavy import

    return VaultLib([("default", VaultSecret(password.encode("utf-8")))])


def encrypt(data: Mapping[str, Any], password: str) -> str:
    """Encrypt the vault content.

    Args:
        data: The content.
        password: The vault password.

    Returns:
        The vault file text (``$ANSIBLE_VAULT;1.1;AES256``).
    """
    yaml = YAML(typ="safe", pure=True)
    yaml.default_flow_style = False
    stream = io.StringIO()
    yaml.dump(dict(data), stream)
    plaintext = "# Managed by the Pokus installer (group_vars/all/vault.yml).\n" + stream.getvalue()
    ciphertext: bytes = _vault(password).encrypt(plaintext.encode("utf-8"))
    return ciphertext.decode("ascii")


def decrypt(text: str, password: str) -> dict[str, Any]:
    """Decrypt a vault file.

    Args:
        text: The vault file.
        password: The vault password.

    Returns:
        The content.

    Raises:
        VaultError: For a wrong password or a damaged file.
    """
    from ansible.errors import AnsibleError  # noqa: PLC0415 - heavy import

    try:
        plaintext: bytes = _vault(password).decrypt(text.encode("ascii"))
    except (AnsibleError, UnicodeError) as exc:
        raise VaultError("cannot decrypt the vault: wrong vault password or damaged file") from exc
    loaded = YAML(typ="safe").load(plaintext.decode("utf-8"))
    return dict(loaded or {})


def keys_of(data: Mapping[str, Any]) -> list[str]:
    """The secret names of a vault, without values (for the Review diff).

    Args:
        data: Vault content.

    Returns:
        Dotted names such as ``vault_postgres_passwords.pokus_orch``.
    """
    names = []
    for key, value in data.items():
        if isinstance(value, Mapping):
            names += [f"{key}.{sub}" for sub in value]
        else:
            names.append(key)
    return names
