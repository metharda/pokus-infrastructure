"""Everything infra renders for the active platform (§7.14 step 7).

:func:`render` turns a valid manifest, the contract and the host settings
into one document that the platform role applies with ordinary Ansible
modules: accounts and groups, directories, configuration and secret files,
units, the database plan with its SQL, the Incus certificates, the signing
key, the run-network helper's socket owner, the OPA bundle, the dashboards
and the CLI wrapper. The principals and endpoints files need the accounts'
UIDs and are written after the accounts exist (:mod:`pinka_platform.render`).

The document is also kept as ``/etc/pinka/platform/rendered.json`` (no
secrets in it) so that later runs and the other roles know what the active
platform has; it is infra-internal and not part of the contract.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from typing import Any

from pinka_platform import accounts, database, layout, names, units
from pinka_platform.contract import Contract

RENDER_VERSION = 1
# The key of rendered.json that the socket-ACL helper keeps up to date at
# every start of a socket: the sockets that fell back to mode 0666 because
# /run has no ACL support (owner decision with the "commit" for 3d.3). It is
# present only while the list is not empty; doctor warns about each socket.
SOCKET_ACL_FALLBACK = "socket_acl_fallback"


def _strip(path: str) -> str:
    return path.rstrip("/")


def _directories(manifest: Mapping[str, Any], contract: Contract) -> list[dict[str, str]]:
    p = manifest["name"]
    root = {"owner": "root", "group": "root", "mode": "0755"}
    out = [{"path": layout.CONFIG_DIR, **root}]
    services = manifest["services"]
    if any(s.get("state") for s in services):
        out.append({"path": f"{layout.PLATFORMS_DIR}/{p}/state", **root})
    for service in services:
        account = names.account(p, service["name"])
        if service.get("state"):
            out.append({"path": f"{layout.PLATFORMS_DIR}/{p}/state/{service['name']}", **root})
            out += [
                {
                    "path": layout.state_dir(p, service["name"], d),
                    "owner": account,
                    "group": account,
                    "mode": "0700",
                }
                for d in service["state"]
            ]
        if service.get("secrets"):
            out.append(
                {
                    "path": f"{layout.SECRETS_DIR}/{account}",
                    "owner": "root",
                    "group": account,
                    "mode": "0750",
                }
            )
    controller = names.account(p, _controller(manifest))
    out += [
        {"path": f"{layout.SIGNING_DIR}/{p}", "owner": "root", "group": controller, "mode": "0750"},
        # Attestations and the QA keys they are checked with (§12.10): the
        # trusted keys are public, the attestations readable by the controller.
        {"path": f"{layout.ETC}/qa", **root},
        {"path": layout.QA_TRUSTED_KEYS_DIR, **root},
        {"path": layout.QA_ATTESTATIONS_DIR, "owner": "root", "group": controller, "mode": "0750"},
    ]
    if "postgres" in manifest.get("requires", []):
        out.append(
            {"path": layout.POSTGRES_PASSWORD_DIR, "owner": "root", "group": "root", "mode": "0711"}
        )
    del contract  # every directory follows from the manifest alone
    return out


def _controller(manifest: Mapping[str, Any]) -> str:
    return str(next(s["name"] for s in manifest["services"] if s["profile"] == "controller"))


def _socket(platform: str, socket: Mapping[str, Any], contract: Contract) -> dict[str, Any]:
    # What doctor compares the bound socket with (§12.3). Without ACL support
    # on /run the helper makes it 0666 and records that in rendered.json
    # (SOCKET_ACL_FALLBACK); doctor then warns.
    service, name = socket["service"], socket["name"]
    return {
        "path": layout.socket_path(service, name),
        "unit": units.socket_unit(platform, service, name),
        "owner": names.account(platform, service),
        "group": names.client_group(platform, service, name),
        "mode": "0660",
        "acl": [f"group:{g}:rw-" for g in units.human_groups(socket, contract)],
        "untrusted_clients": bool(socket.get("untrusted_clients", False)),
    }


def config_text(value: Mapping[str, Any]) -> str:
    """Serialize a service's configuration.

    Args:
        value: The configuration (already checked against its schema).

    Returns:
        JSON text.
    """
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def cli_wrapper(platform: str, run: list[str], name: str) -> str:
    """The researchers' CLI: a shell wrapper around the command in the release.

    Args:
        platform: Platform name.
        run: The manifest's ``cli.run``.
        name: The command name.

    Returns:
        The wrapper's text.
    """
    words = [f"{layout.release_current(platform)}/{run[0]}", *run[1:]]
    command = " ".join(shlex.quote(w) for w in words)
    return (
        "#!/bin/sh\n"
        f"# Managed by pinka (platform {platform}, contract v2). "
        "Local changes will be overwritten.\n"
        f"# {name}: the researchers' CLI of platform {platform}.\n"
        f'exec {command} "$@"\n'
    )


def render(
    manifest: Mapping[str, Any],
    contract: Contract,
    *,
    release_target: str,
    ports: Mapping[str, int],
    settings: Mapping[str, Mapping[str, Any]],
    machine_name: str,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Render everything the platform role applies for one release.

    Args:
        manifest: A valid manifest.
        contract: The contract.
        release_target: The published release directory.
        ports: Effective ports (§12.11).
        settings: ``platform_settings``: configuration per service, checked
            against the release's config schemas before.
        machine_name: The machine's name (published signing keys).
        previous: The same platform's previous rendering, if any.

    Returns:
        The rendered document.
    """
    p = manifest["name"]
    requires = set(manifest.get("requires", []))
    acc = accounts.render(manifest, contract)
    by_name = {a["name"]: a for a in acc["accounts"]}
    # The agent's units name the release itself (resolved now), never the link.
    unit_texts = units.render(manifest, contract, by_name, ports, release=release_target)
    controller = names.account(p, _controller(manifest))
    previous = previous if previous and previous.get("platform") == p else None
    previous_units = {u["name"] for u in (previous or {}).get("units", [])}
    db = None
    if "postgres" in requires:
        recorded = (previous or {}).get("database") or {}
        db = database.plan(manifest, contract, recorded.get("all_roles", []))
        db["all_roles"] = sorted(
            {*db["vault_roles"], *db["group_roles"], *recorded.get("all_roles", [])}
        )
        db["bootstrap_sql"] = database.bootstrap_sql(db)
        db["restricted_check_sql"] = database.restricted_check_sql(db)
    config_files = [
        {
            "service": s["name"],
            "path": layout.config_file(s["name"]),
            "owner": "root",
            "group": names.account(p, s["name"]),
            "mode": "0640",
            "content": config_text(settings.get(s["name"], {})),
        }
        for s in manifest["services"]
        if "config" in s
    ]
    secrets = [
        {
            "service": s["name"],
            "name": secret,
            "path": f"{layout.SECRETS_DIR}/{names.account(p, s['name'])}/{secret}",
            "owner": "root",
            "group": names.account(p, s["name"]),
            "mode": "0640",
        }
        for s in manifest["services"]
        for secret in s.get("secrets", [])
    ]
    certificates = []
    if "incus" in requires:
        for s in manifest["services"]:
            projects = list(contract.profile(s["profile"]).data["incus_projects"])
            if projects:
                account = names.account(p, s["name"])
                certificates.append(
                    {
                        "name": account,
                        "account": account,
                        "short": account,
                        "projects": projects,
                        "dir": f"{layout.INCUS_CERT_DIR}/{account}",
                    }
                )
    opa = None
    if "policy" in manifest:
        opa = {
            "bundle": f"{release_target}/{_strip(manifest['policy']['bundle'])}",
            "decision_path": manifest["policy"]["decision_path"],
        }
    dashboards = None
    if "dashboards" in manifest:
        dashboards = {
            "folder": p,
            "dir": f"{release_target}/{_strip(manifest['dashboards'])}",
            "database": db["database"] if db else None,
            "role": db["grafana"] if db else None,
            "socket_dir": layout.POSTGRES_READER_SOCKET_DIR if db else None,
        }
    cli = None
    if "cli" in manifest:
        name = manifest["cli"]["name"]
        cli = {
            "name": name,
            "link": f"{layout.CLI_BIN_DIR}/{name}",
            "wrapper": f"{layout.CLI_WRAPPER_DIR}/{name}",
            "content": cli_wrapper(p, list(manifest["cli"]["run"]), name),
        }
    return {
        "render_version": RENDER_VERSION,
        "platform": p,
        "requires": sorted(requires),
        **acc,
        "controller": controller,
        "directories": _directories(manifest, contract),
        "config_files": config_files,
        "secrets": secrets,
        "units": [{"name": n, "content": unit_texts[n]} for n in sorted(unit_texts)],
        "stale_units": sorted(previous_units - set(unit_texts)),
        "service_units": [units.service_unit(p, s["name"]) for s in manifest["services"]],
        "socket_units": [
            units.socket_unit(p, s["service"], s["name"]) for s in manifest.get("sockets", [])
        ],
        "sockets": [_socket(p, s, contract) for s in manifest.get("sockets", [])],
        "hardening": units.hardening(manifest, contract, by_name, ports, release=release_target),
        "database": db,
        "incus_certificates": certificates,
        "signing": {
            "dir": f"{layout.SIGNING_DIR}/{p}",
            "key": f"{layout.SIGNING_DIR}/{p}/{layout.SIGNING_KEY_NAME}",
            "pub": f"{layout.SIGNING_DIR}/{p}/{layout.SIGNING_PUB_NAME}",
            "owner": controller,
            "published_dir": layout.SIGNING_KEYS_PUBLIC_DIR,
            "published_prefix": f"{p}-{machine_name}-",
        },
        "run_network": {"socket_owner": controller} if "incus" in requires else None,
        "opa": opa,
        "dashboards": dashboards,
        "telemetry_labels": list(manifest.get("telemetry", {}).get("labels", [])),
        # Every declared state directory: doctor lists those backup.paths leaves out.
        "state_dirs": [
            layout.state_dir(p, s["name"], d)
            for s in manifest["services"]
            for d in s.get("state", [])
        ],
        "backup": {
            "database": bool(manifest.get("backup", {}).get("database", False)) and db is not None,
            "paths": [
                f"{layout.PLATFORMS_DIR}/{p}/state/{path}"
                for path in manifest.get("backup", {}).get("paths", [])
            ],
        },
        "cli": cli,
        "tools": list(manifest.get("tools", [])),
    }


def public(document: Mapping[str, Any]) -> dict[str, Any]:
    """The part of the document kept as ``rendered.json``.

    Args:
        document: The output of :func:`render`.

    Returns:
        The document without the configuration contents.
    """
    out = dict(document)
    out["config_files"] = [
        {k: v for k, v in f.items() if k != "content"} for f in document["config_files"]
    ]
    return out
