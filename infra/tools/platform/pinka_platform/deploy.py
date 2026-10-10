"""Deploy a platform release and its content packages (BUILD_PROMPT §7.14).

The steps, each a function the ``platform`` role calls through
``pinka-platform deploy <step>``:

* :func:`prepare` (steps 1-4): read the source settings, fetch every
  repository into its root-only mirror, resolve and check the refs (signed
  tags when required), extract each source into a fresh run directory below
  ``/var/lib/pinka/staging`` and validate the sources, the manifest, the
  release and the one-platform rule. Any problem deletes the run directory
  and refuses the deployment (exit 65) with every reason; nothing else on
  the host has changed. ``inspect`` stops here.
* :func:`build` (step 5): ``uv sync --frozen`` as ``pinka-build`` in a
  sandbox (``python-uv``), then the checks that need the build.
* :func:`pull` (step 5): the training job images, by digest.
* :func:`publish` (steps 6 and 9): the release to
  ``/srv/pinka/platforms/<platform>/releases/<name>/``, restricted content
  packages to ``/srv/pinka/restricted/<platform>/content/<package>/<name>/``
  only, other content to ``/srv/pinka/platforms/<platform>/content/...``,
  each with ``DEPLOYED_FROM.json``.
* :func:`retire_plan`: what replacing the active platform takes away.
* :func:`activate` (step 8's switch): the ``current`` links, the active
  platform record ``/etc/pinka/platform/active.json`` and the deployment
  record.
* :func:`purge`: remove a replaced platform's releases, state, restricted
  area and database.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import tempfile
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pinka_platform import (
    config,
    database,
    mirror,
    names,
    release,
    render,
    rendering,
    sources,
    validate,
)
from pinka_platform.contract import Contract
from pinka_platform.loader import MANIFEST_NAME, load_file
from pinka_platform.problems import ManifestError, Problem

PROVENANCE = "DEPLOYED_FROM.json"
PLAN = "plan.json"
ACTIVE = "active.json"
RENDER = "render.json"
RENDERED = "rendered.json"
PRINCIPALS = "principals.json"
ENDPOINTS = "endpoints.toml"
RETIRED = "retired.json"
DEPLOYMENTS = "deployments.jsonl"
CURRENT = "current"
RECORD_VERSION = 1
PLATFORM_NAME_RE = re.compile(r"^[a-z][a-z0-9]{1,11}$")
SQL_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
# Paths the build sandbox never sees (the "-" makes a missing path harmless).
BUILD_HIDDEN = (
    "/var/lib/pinka/git",
    "/etc/pinka/secrets",
    "/etc/pinka/signing",
    "/etc/pinka/incus",
    "/etc/pinka/inference",
    "/etc/pinka/qa",
    "/srv/pinka/restricted",
    "/srv/pinka/backups",
    "/srv/pinka/training",
    "/var/lib/incus",
    "/var/lib/docker",
    "/run/docker.sock",
    "/run/pinka",
)
UV = "/opt/pinka/bin/uv"
PYTHON = "/opt/pinka/python/bin/python3"
UV_SYNC = ("sync", "--frozen", "--all-packages", "--no-dev", "--no-editable")
# Phase 1 of the build (network): only the locked third-party wheels, checked
# against the hashes in uv.lock; nothing is built and no interpreter starts on
# the new environment (no bytecode compilation: a wheel's .pth file would run
# then), so no package code runs.
UV_FETCH = ("--no-install-project", "--no-install-workspace", "--no-build")
# Phase 2 (no network): build and install the release itself with the build
# backend that phase 1 installed from the "build" dependency group, then
# compile the whole environment's bytecode.
UV_BUILD = ("--offline", "--no-build-isolation", "--compile-bytecode")
POSTGRES_CONTAINER = "pinka-postgres"
PSQL_SCRIPT = (
    'PGPASSWORD="$(cat /run/secrets/postgres-superuser)" exec psql --no-psqlrc '
    "--set=ON_ERROR_STOP=1 --host=/var/run/postgresql --username=postgres --dbname=postgres "
    '"$@"'
)

Runner = Callable[[Sequence[str]], mirror.Done]


class DeployError(Exception):
    """A deployment step failed for a reason other than a refused release."""


class Refused(Exception):  # noqa: N818 - the outcome's name, like bootstrap's
    """The release is refused (exit 65); nothing on the host changed.

    Attributes:
        problems: Every reason.
    """

    def __init__(self, problems: Sequence[Problem]) -> None:
        """Create the refusal.

        Args:
            problems: Every reason.
        """
        super().__init__(f"{len(problems)} problem(s)")
        self.problems = list(problems)


@dataclass(frozen=True)
class Paths:
    """The host paths of a deployment (BUILD_PROMPT §6.2).

    Attributes:
        git: Root-only mirrors.
        staging: Root-only run directories.
        records: Deployment and retirement records per platform.
        srv: The research space (``pinka_root``).
        etc_platform: The active platform's files.
        uv_cache: The build's uv cache.
        bin: Where the researchers' CLI links live.
        secrets: Rendered secrets, one directory per platform account.
        signing: Private signing keys, one directory per platform.
    """

    git: Path = Path("/var/lib/pinka/git")
    staging: Path = Path("/var/lib/pinka/staging")
    records: Path = Path("/var/lib/pinka/platforms")
    srv: Path = Path("/srv/pinka")
    etc_platform: Path = Path("/etc/pinka/platform")
    uv_cache: Path = Path("/var/cache/pinka/uv")
    bin: Path = Path("/usr/local/bin")
    secrets: Path = Path("/etc/pinka/secrets")
    signing: Path = Path("/etc/pinka/signing")

    @classmethod
    def under(cls, root: Path) -> Paths:
        """The same layout below another root (tests, ``--root``).

        Args:
            root: Directory standing in for ``/``.

        Returns:
            The paths.
        """
        default = cls()
        return cls(
            **{
                name: root / str(getattr(default, name)).lstrip("/")
                for name in cls.__dataclass_fields__
            }
        )

    def release_dir(self, platform: str, name: str = "") -> Path:
        """``<srv>/platforms/<platform>/releases[/<name>]``."""
        base = self.srv / "platforms" / platform / "releases"
        return base / name if name else base

    def content_dir(self, platform: str, package: str, *, restricted: bool, name: str = "") -> Path:
        """Where a content package lands: the restricted area or the platform tree."""
        area = self.srv / "restricted" if restricted else self.srv / "platforms"
        base = area / platform / "content" / package
        return base / name if name else base

    @property
    def active(self) -> Path:
        """``/etc/pinka/platform/active.json``."""
        return self.etc_platform / ACTIVE


@dataclass(frozen=True)
class Owners:
    """Who owns what a deployment writes.

    Attributes:
        user: Owner of releases and content.
        group: Group of releases and non-restricted content.
        restricted_group: Group of the restricted area.
        build_user: The account that runs builds.
        build_group: Its group.
    """

    user: str = "root"
    group: str = "root"
    restricted_group: str = "pinka-restricted"
    build_user: str = "pinka-build"
    build_group: str = "pinka-build"

    def uid(self, user: str) -> int:
        """User ID of an account name."""
        return pwd.getpwnam(user).pw_uid

    def gid(self, group: str) -> int:
        """Group ID of a group name."""
        return grp.getgrnam(group).gr_gid


@dataclass(frozen=True)
class Settings:
    """The host settings of a deployment, as the platform role passes them.

    Attributes:
        platform: The ``platform`` setting.
        content: The ``content`` setting.
        require_signed_tags: Deploy only validly signed release tags.
        allowed_signers: ``ssh-keygen`` allowed signers file.
        ssh_key: The host's deploy key for SSH sources.
        known_hosts: The pinned host keys for SSH sources.
        replace: The active platform the operator allows to be replaced
            (``--replace-platform``), or "".
        owners: Who owns what a deployment writes.
        platform_settings: Non-secret configuration per service.
        lab: The lab facts of the endpoints file (:class:`render.LabFacts`).
        vault_keys: The keys the vault holds, never their values, as dotted
            names (``vault_postgres_passwords.<role>``,
            ``vault_platform_secrets.<service>.<name>``).
    """

    platform: Mapping[str, Any] = field(default_factory=dict)
    content: Mapping[str, Any] = field(default_factory=dict)
    require_signed_tags: bool = False
    allowed_signers: str = ""
    ssh_key: str = ""
    known_hosts: str = ""
    replace: str = ""
    owners: Owners = field(default_factory=Owners)
    platform_settings: Mapping[str, Any] = field(default_factory=dict)
    lab: Mapping[str, Any] = field(default_factory=dict)
    vault_keys: Sequence[str] = ()

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Settings:
        """Read the settings file the platform role writes.

        Args:
            data: Parsed JSON.

        Returns:
            The settings.
        """
        return cls(
            platform=data.get("platform") or {},
            content=data.get("content") or {},
            require_signed_tags=bool(data.get("require_signed_tags", False)),
            allowed_signers=str(data.get("allowed_signers") or ""),
            ssh_key=str(data.get("ssh_key") or ""),
            known_hosts=str(data.get("known_hosts") or ""),
            replace=str(data.get("replace") or ""),
            owners=Owners(**{k: str(v) for k, v in (data.get("owners") or {}).items()}),
            platform_settings=data.get("platform_settings") or {},
            lab=data.get("lab") or {},
            vault_keys=tuple(str(k) for k in data.get("vault_keys") or ()),
        )


def utc(now: dt.datetime) -> str:
    """ISO 8601 UTC with ``Z``."""
    return now.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON object, or None if the file is missing or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path, data: Mapping[str, Any], mode: int = 0o644) -> None:
    """Write a JSON file atomically (a temporary file renamed into place)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        Path(tmp).chmod(mode)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_active(paths: Paths) -> dict[str, Any] | None:
    """The active platform record, or None if no platform is active."""
    return read_json(paths.active)


def sha256_file(path: Path) -> str:
    """Lowercase hex SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- prepare (steps 1-4) -------------------------------------------------------


@dataclass
class _Staged:
    source: sources.Source
    resolved: mirror.Resolved
    signed: dict[str, Any]
    directory: Path


def _signature(
    git: mirror.Git,
    mirror_dir: Path,
    source: sources.Source,
    resolved: mirror.Resolved,
    settings: Settings,
) -> dict[str, Any]:
    signed: dict[str, Any] = {
        "required": settings.require_signed_tags,
        "verified": False,
        "signer": None,
    }
    signers = Path(settings.allowed_signers) if settings.allowed_signers else None
    if settings.require_signed_tags:
        if resolved.tag != source.ref:
            raise mirror.SourceError(
                "tag-signature",
                f"require_signed_tags: {source.ref!r} must be a release tag"
                + (f" starting with {source.tag_prefix!r}" if source.tag_prefix else "")
                + ", not a branch or a commit",
            )
        signer = mirror.verify_tag(git, mirror_dir, source.ref, signers or Path())
        signed.update(verified=True, signer=signer)
    elif resolved.tag and signers is not None and signers.is_file():
        with contextlib.suppress(mirror.SourceError):  # recorded as unverified; not required
            signed.update(
                verified=True, signer=mirror.verify_tag(git, mirror_dir, resolved.tag, signers)
            )
    return signed


def _fetch_all(
    git: mirror.Git, paths: Paths, all_sources: Sequence[sources.Source], settings: Settings
) -> dict[str, Path]:
    mirrors: dict[str, Path] = {}
    for source in all_sources:
        key = sources.repo_key(source.repo)
        if key in mirrors:
            continue
        mirror_dir = paths.git / sources.mirror_name(source.repo)
        env = (
            mirror.ssh_env(settings.ssh_key, settings.known_hosts)
            if source.transport == "ssh"
            else {}
        )
        mirror.fetch(git, mirror_dir, source.repo, env)
        mirrors[key] = mirror_dir
    return mirrors


def _published_problem(target: Path, resolved: mirror.Resolved, where: str) -> Problem | None:
    if not target.exists():
        return None
    existing = read_json(target / PROVENANCE) or {}
    if existing.get("commit") != resolved.commit or existing.get("tree") != resolved.tree:
        return Problem(
            "moved-tag",
            where,
            f"{target} holds commit {existing.get('commit')}, not {resolved.commit}; "
            "a moved tag is never redeployed in place",
        )
    return None


def _content_problems(
    manifest: Mapping[str, Any], packages: Mapping[str, sources.Source]
) -> list[Problem]:
    declared = {c["name"]: c for c in manifest.get("content", [])}
    problems = [
        Problem("content-unknown", f"content.{name}", "the manifest declares no such package")
        for name in sorted(set(packages) - set(declared))
    ]
    problems += [
        Problem("content-missing", f"content.{name}", "a required package has no source")
        for name, package in sorted(declared.items())
        if package["required"] and name not in packages
    ]
    return problems


def one_platform_problems(
    active: Mapping[str, Any] | None, platform: str, replace_name: str
) -> list[Problem]:
    """The one-platform rule: another active platform needs ``--replace-platform`` (§7.14).

    Args:
        active: The active platform record, or None.
        platform: Name of the release being deployed.
        replace_name: The ``--replace-platform`` argument, or "".

    Returns:
        Problems.
    """
    current = (active or {}).get("name")
    if replace_name:
        if current is None:
            return [
                Problem(
                    "platform-replace",
                    "--replace-platform",
                    f"no platform is active, so there is no {replace_name!r} to replace",
                )
            ]
        if replace_name != current:
            return [
                Problem(
                    "platform-replace",
                    "--replace-platform",
                    f"{replace_name!r} is not the active platform {current!r}",
                )
            ]
        if current == platform:
            return [
                Problem(
                    "platform-replace",
                    "--replace-platform",
                    f"the release is {platform!r} itself; deploy it without --replace-platform",
                )
            ]
        return []
    if current is not None and current != platform:
        return [
            Problem(
                "platform-active",
                "name",
                f"platform {current!r} is active; a release of {platform!r} replaces it only "
                f"with --replace-platform {current}",
            )
        ]
    return []


def grouped_names(manifest: Mapping[str, Any], contract: Contract) -> dict[str, list[str]]:
    """Every name the manifest creates, by namespace (recorded in ``active.json``).

    Args:
        manifest: A valid manifest.
        contract: The contract.

    Returns:
        Sorted names per namespace.
    """
    grouped: dict[str, set[str]] = {}
    for derived in names.derive(manifest, contract):
        grouped.setdefault(derived.namespace, set()).add(derived.name)
    return {namespace: sorted(found) for namespace, found in sorted(grouped.items())}


def vault_keys(
    manifest: Mapping[str, Any], contract: Contract, available: Sequence[str]
) -> dict[str, list[str]]:
    """The vault keys a release needs, and those the vault lacks.

    Args:
        manifest: A valid manifest.
        contract: The contract.
        available: The dotted keys the vault holds (``Settings.vault_keys``).

    Returns:
        ``{"needed": [...], "missing": [...]}`` as dotted vault keys.
    """
    needed: list[str] = []
    if "postgres" in manifest.get("requires", []):
        needed += [
            f"vault_postgres_passwords.{role}"
            for role in database.plan(manifest, contract)["vault_roles"]
        ]
    needed += [
        f"vault_platform_secrets.{s['name']}.{secret}"
        for s in manifest["services"]
        for secret in s.get("secrets", [])
    ]
    have = set(available)
    return {"needed": needed, "missing": [k for k in needed if k not in have]}


def prepare(  # noqa: PLR0912, PLR0915 - the whole of steps 1-4
    settings: Settings,
    paths: Paths,
    contract: Contract,
    *,
    git: mirror.Git = mirror.run_git,
    host: Callable[[str], names.HostFacts | None] = names.host_facts,
    owner_uid: int | None = None,
    inspect: bool = False,
    now: Callable[[], dt.datetime] = _now,
) -> dict[str, Any]:
    """Fetch, stage and validate (§7.14 steps 1-4).

    Args:
        settings: Host settings.
        paths: Host paths.
        contract: The contract.
        git: Command runner.
        host: Host facts for a platform name (None skips the host checks).
        owner_uid: Accepted owner of ``file://`` repositories besides root.
        inspect: Only inspect: delete the run directory again.
        now: Clock.

    Returns:
        The plan (also written to ``<run>/plan.json``), with ``summary``.

    Raises:
        Refused: Every reason to refuse; the run directory is gone.
        mirror.FetchError: A repository could not be fetched.
    """
    release_source, packages, problems = sources.parse(settings.platform, settings.content)
    if release_source is None and not problems:
        problems.append(Problem("source-setting", sources.RELEASE, "no platform source is set"))
    all_sources = ([release_source] if release_source else []) + list(packages.values())
    uid = sources.owner_uid() if owner_uid is None else owner_uid
    for source in all_sources:
        if source.transport == "file" and (why := sources.file_repo_problem(source.repo, uid)):
            problems.append(Problem("source-transport", f"{source.label}.repo", why))
    if problems or release_source is None:
        raise Refused(problems)
    mirrors = _fetch_all(git, paths, all_sources, settings)

    paths.staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=paths.staging))
    try:
        staged: dict[str | None, _Staged] = {}
        for source in all_sources:
            mirror_dir = mirrors[sources.repo_key(source.repo)]
            target = run / "release" if source.package is None else run / "content" / source.package
            try:
                resolved = mirror.resolve(
                    git, mirror_dir, source.ref, source.path, source.tag_prefix
                )
                signed = _signature(git, mirror_dir, source, resolved, settings)
                target.mkdir(parents=True)
                mirror.extract(mirror_dir, resolved.commit, source.path, target)
            except mirror.SourceError as exc:
                problems.append(Problem(exc.code, source.label, str(exc)))
                continue
            staged[source.package] = _Staged(source, resolved, signed, target)
        if problems:
            raise Refused(problems)

        release_dir = staged[None].directory
        try:
            platform = str(
                load_file(release_dir / MANIFEST_NAME, int(contract.caps["manifest_bytes"])).get(
                    "name", ""
                )
            )
        except ManifestError:
            platform = ""
        facts = host(platform) if PLATFORM_NAME_RE.match(platform) else None
        result = validate.validate_release(release_dir, contract, facts)
        problems += result.problems
        manifest = result.manifest
        if manifest is None or problems:
            raise Refused(problems)
        problems += _content_problems(manifest, packages)
        problems += config.check(manifest, release_dir, settings.platform_settings)
        for name, item in sorted((k, v) for k, v in staged.items() if k is not None):
            problems += [
                replace(p, where=f"content.{name}: {p.where}")
                for p in release.check_symlinks(item.directory)
                + release.check_special_files(item.directory)
            ]
        active = read_active(paths)
        problems += one_platform_problems(active, platform, settings.replace)
        restricted = {c["name"]: bool(c["restricted"]) for c in manifest.get("content", [])}
        targets: dict[str | None, Path] = {
            None: paths.release_dir(platform, staged[None].resolved.name),
            **{
                name: paths.content_dir(
                    platform, name, restricted=restricted.get(name, True), name=item.resolved.name
                )
                for name, item in staged.items()
                if name is not None
            },
        }
        for key, item in staged.items():
            problem = _published_problem(targets[key], item.resolved, item.source.label)
            if problem:
                problems.append(problem)
        if problems:
            raise Refused(problems)

        def entry(item: _Staged, target: Path) -> dict[str, Any]:
            return {
                "source": item.source.as_dict(),
                "commit": item.resolved.commit,
                "tree": item.resolved.tree,
                "tag": item.resolved.tag,
                "name": item.resolved.name,
                "signed_tag": item.signed,
                "target": str(target),
                "published": target.exists(),
            }

        plan: dict[str, Any] = {
            "plan_version": RECORD_VERSION,
            "run": run.name,
            "created_at": utc(now()),
            "platform": {
                "name": platform,
                "version": manifest["version"],
                "runtime": manifest["runtime"],
                "manifest_sha256": sha256_file(release_dir / MANIFEST_NAME),
            },
            "release": entry(staged[None], targets[None]),
            "content": {
                name: {**entry(item, targets[name]), "restricted": restricted[name]}
                for name, item in sorted((k, v) for k, v in staged.items() if k is not None)
            },
            "jobs": [{"name": j["name"], "image": j["image"]} for j in manifest.get("jobs", [])],
            "tools": list(manifest.get("tools", [])),
            "services": [
                {"name": s["name"], "profile": s["profile"]} for s in manifest["services"]
            ],
            "cli": manifest.get("cli", {}).get("name"),
            "names": grouped_names(manifest, contract),
            "replace": settings.replace or None,
            "owners": dataclasses.asdict(settings.owners),
            "summary": validate.summary(manifest, contract),
            "vault_keys": vault_keys(manifest, contract, settings.vault_keys),
            "platform_settings": dict(settings.platform_settings),
            "lab": dict(settings.lab),
        }
        write_json(run / PLAN, plan, 0o600)
    except BaseException:
        shutil.rmtree(run, ignore_errors=True)
        raise
    if inspect:
        shutil.rmtree(run, ignore_errors=True)
    plan["run_dir"] = str(run)
    return plan


def plan_owners(plan: Mapping[str, Any]) -> Owners:
    """The owners a plan was prepared with.

    Args:
        plan: The deployment plan.

    Returns:
        The owners.
    """
    return Owners(**plan.get("owners", {}))


def load_plan(run: Path) -> dict[str, Any]:
    """Read a run directory's plan.

    Args:
        run: The run directory.

    Returns:
        The plan.

    Raises:
        DeployError: No plan there.
    """
    plan = read_json(run / PLAN)
    if plan is None:
        raise DeployError(f"{run} holds no deployment plan")
    plan["run_dir"] = str(run)
    return plan


# --- build (step 5) ------------------------------------------------------------


def _entries(top: Path, skip: str) -> Iterator[tuple[str, Path]]:
    for directory, dirs, found in os.walk(top):
        here = Path(directory)
        files = found
        if here == top:
            dirs[:] = [d for d in dirs if d != skip]
            files = [f for f in found if f != skip]
        for name in dirs + files:
            path = here / name
            yield str(path.relative_to(top)), path


def snapshot(top: Path, skip: str = ".venv") -> dict[str, str]:
    """What the release holds outside the built environment: type, mode and content.

    Args:
        top: The release.
        skip: The top-level entry the build creates.

    Returns:
        Relative path to a description that changes with the entry.
    """
    out = {}
    for relative, path in _entries(top, skip):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            out[relative] = f"link {path.readlink()}"
        elif stat.S_ISDIR(info.st_mode):
            out[relative] = "dir"
        else:
            out[relative] = f"file {info.st_mode & 0o111:o} {sha256_file(path)}"
    return out


def sandbox_commands(
    plan: Mapping[str, Any], paths: Paths, owners: Owners, *, build_group: bool
) -> list[list[str]]:
    """The two ``systemd-run`` commands of a ``python-uv`` build (§7.14 step 5).

    Phase 1 (``fetch``) may use the network and installs only the locked
    third-party wheels, checked against ``uv.lock``; it builds and compiles
    nothing, so no package code runs. Phase 2 (``build``) has no network
    (``PrivateNetwork=yes``, ``--offline``) and builds the release itself with
    the build backend phase 1 installed from the ``build`` group. The staged
    release is bound at its final path, so the environment's absolute paths
    (script shebangs, ``pyvenv.cfg``) are right after publishing. Both phases
    may write only the release and the uv cache.

    Args:
        plan: The deployment plan.
        paths: Host paths.
        owners: Owners (the build account).
        build_group: The release has a ``build`` dependency group.

    Returns:
        The commands, in order.
    """
    staged = Path(plan["run_dir"]) / "release"
    final = plan["release"]["target"]
    properties = [
        f"User={owners.build_user}",
        f"Group={owners.build_group}",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateTmp=yes",
        "PrivateDevices=yes",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectKernelLogs=yes",
        "ProtectControlGroups=yes",
        "ProtectClock=yes",
        "ProtectHostname=yes",
        "RestrictSUIDSGID=yes",
        "RestrictNamespaces=yes",
        "RestrictRealtime=yes",
        "LockPersonality=yes",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        "UMask=0022",
        "RuntimeMaxSec=3600",
        f"BindPaths={staged}:{final}",
        f"ReadWritePaths={paths.uv_cache}",
        *(f"InaccessiblePaths=-{hidden}" for hidden in BUILD_HIDDEN),
        f"WorkingDirectory={final}",
        f"Environment=UV_CACHE_DIR={paths.uv_cache}",
        "Environment=UV_PYTHON_DOWNLOADS=never",
        "Environment=UV_NO_CONFIG=1",
        "Environment=HOME=/tmp",
    ]
    group = ["--group", release.BUILD_GROUP] if build_group else []
    unit = f"pinka-platform-build-{plan['run'].removeprefix('run-').lower()}"
    commands = []
    for phase, extra_properties, extra_args in (
        ("fetch", [], UV_FETCH),
        ("build", ["PrivateNetwork=yes", "Environment=UV_OFFLINE=1"], UV_BUILD),
    ):
        argv = [
            "systemd-run",
            "--quiet",
            "--wait",
            "--pipe",
            "--collect",
            "--service-type=exec",
            f"--unit={unit}-{phase}",
        ]
        for prop in [*properties, *extra_properties]:
            argv += ["--property", prop]
        commands.append([*argv, "--", UV, *UV_SYNC, *extra_args, *group, "--python", PYTHON])
    return commands


def _has_build_group(staged: Path) -> bool:
    try:
        data = tomllib.loads((staged / release.PYPROJECT).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    groups = data.get("dependency-groups", {})
    return isinstance(groups, dict) and bool(groups.get(release.BUILD_GROUP))


def _chown_tree(top: Path, uid: int, gid: int) -> None:
    os.lchown(top, uid, gid)
    for _, path in _entries(top, ""):
        os.lchown(path, uid, gid)


def build(
    plan: Mapping[str, Any],
    paths: Paths,
    contract: Contract,
    runner: Runner,
) -> dict[str, Any]:
    """Build a ``python-uv`` release in its sandbox and check it again (§7.14 step 5).

    Args:
        plan: The deployment plan.
        paths: Host paths.
        contract: The contract.
        runner: Command runner.

    Returns:
        ``{"changed": bool, "built": bool}``.

    Raises:
        Refused: The build changed files of the release, or the built release
            fails the checks that need the build.
        DeployError: The build failed.
    """
    if plan["platform"]["runtime"] != "python-uv" or plan["release"]["published"]:
        return {"changed": False, "built": False}
    owners = plan_owners(plan)
    staged = Path(plan["run_dir"]) / "release"
    final = Path(plan["release"]["target"])
    before = snapshot(staged)
    paths.uv_cache.mkdir(parents=True, exist_ok=True)
    build_uid, build_gid = owners.uid(owners.build_user), owners.gid(owners.build_group)
    _chown_tree(staged, build_uid, build_gid)
    created_mount_point = not final.exists()
    final.mkdir(parents=True, exist_ok=True)  # the bind mount's target
    try:
        for phase, command in zip(
            ("fetch", "build"),
            sandbox_commands(plan, paths, owners, build_group=_has_build_group(staged)),
            strict=True,
        ):
            done = runner(command)
            if done.returncode != 0:
                detail = (done.stderr or done.stdout).strip()[-2000:]
                raise DeployError(f"the build's {phase} phase failed: {detail}")
        # Before the tree goes back to root: chown clears setuid and setgid bits.
        special = release.check_special_files(staged)
    finally:
        _chown_tree(staged, owners.uid(owners.user), owners.gid(owners.group))
        if created_mount_point:
            final.rmdir()
    after = snapshot(staged)
    problems = [
        Problem("build-modified-release", relative, "the build changed or removed this entry")
        for relative in sorted(before)
        if after.get(relative) != before[relative]
    ]
    problems += special
    result = validate.validate_release(staged, contract, None, built=True)
    problems += [p for p in result.problems if p not in special]
    if problems:
        raise Refused(problems)
    return {"changed": True, "built": True}


# --- pull (step 5) -------------------------------------------------------------


def _has_image(runner: Runner, image: str) -> bool:
    done = runner(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image])
    if done.returncode != 0:
        return False
    try:
        digests = json.loads(done.stdout.strip() or "[]")
    except ValueError:
        return False
    digest = image.rsplit("@", 1)[1]
    return any(str(d).endswith(f"@{digest}") for d in digests or [])


def pull(plan: Mapping[str, Any], runner: Runner) -> dict[str, Any]:
    """Pull every training job image by its digest (§7.14 step 5, §7.18).

    Args:
        plan: The deployment plan.
        runner: Command runner.

    Returns:
        ``{"changed": bool, "pulled": [...]}``.

    Raises:
        DeployError: A pull failed or the local image does not carry the digest.
    """
    pulled = []
    for job in plan["jobs"]:
        image = job["image"]
        if _has_image(runner, image):
            continue
        done = runner(["docker", "pull", "--quiet", image])
        if done.returncode != 0:
            raise DeployError(
                f"pulling {image} for job {job['name']} failed: {done.stderr.strip()}"
            )
        if not _has_image(runner, image):
            raise DeployError(f"{image} for job {job['name']} does not carry its digest")
        pulled.append(image)
    return {"changed": bool(pulled), "pulled": pulled}


# --- publish (steps 6 and 9) -----------------------------------------------------


def provenance(
    plan: Mapping[str, Any], item: Mapping[str, Any], *, package: str | None, now: dt.datetime
) -> dict[str, Any]:
    """The ``DEPLOYED_FROM.json`` record of the release or a content package (§12.10).

    Args:
        plan: The deployment plan.
        item: The plan's entry of the release or the package.
        package: Package name; None for the release.
        now: Deployment time.

    Returns:
        The record (``contract/records/deployed-from.schema.json``).
    """
    return {
        "record_version": RECORD_VERSION,
        "kind": "release" if package is None else "content",
        "package": package,
        "restricted": bool(item.get("restricted", False)),
        "source": dict(item["source"]),
        "commit": item["commit"],
        "tree": item["tree"],
        "tag": item["tag"],
        "signed_tag": dict(item["signed_tag"]),
        "platform": dict(plan["platform"]),
        "deployed_at": utc(now),
    }


def _ensure_dir(path: Path, uid: int, gid: int, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.lchown(path, uid, gid)
    path.chmod(mode)


def _normalize(top: Path, uid: int, gid: int, dir_mode: int, file_mode: int) -> None:
    exec_bits = dir_mode & 0o111
    _chown_tree(top, uid, gid)
    top.chmod(dir_mode)
    for _, path in _entries(top, ""):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            continue
        if stat.S_ISDIR(info.st_mode):
            path.chmod(dir_mode)
        else:
            path.chmod(file_mode | (exec_bits if info.st_mode & 0o100 else 0))


def _publish_one(
    staged: Path,
    target: Path,
    record: Mapping[str, Any],
    *,
    uid: int,
    gid: int,
    dir_mode: int,
    file_mode: int,
) -> bool:
    if target.exists():
        existing = read_json(target / PROVENANCE) or {}
        if existing.get("commit") == record["commit"] and existing.get("tree") == record["tree"]:
            return False
        raise DeployError(f"{target} holds another commit; a moved tag is never redeployed")
    tmp = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        tmp.rmdir()
        shutil.copytree(staged, tmp, symlinks=True)
        _normalize(tmp, uid, gid, dir_mode, file_mode)
        write_json(tmp / PROVENANCE, record, file_mode)
        os.lchown(tmp / PROVENANCE, uid, gid)
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return True


def publish(
    plan: Mapping[str, Any],
    paths: Paths,
    now: Callable[[], dt.datetime] = _now,
) -> dict[str, Any]:
    """Publish the release and the content packages read-only (§7.14 steps 6 and 9).

    Args:
        plan: The deployment plan.
        paths: Host paths.
        now: Clock.

    Returns:
        ``{"changed": bool, "release": path, "content": {package: path}}``.
    """
    owners = plan_owners(plan)
    when = now()
    platform = plan["platform"]["name"]
    root_uid, root_gid = owners.uid(owners.user), owners.gid(owners.group)
    restricted_gid = owners.gid(owners.restricted_group)
    run = Path(plan["run_dir"])
    _ensure_dir(paths.srv / "platforms" / platform, root_uid, root_gid, 0o755)
    _ensure_dir(paths.release_dir(platform), root_uid, root_gid, 0o755)
    changed = _publish_one(
        run / "release",
        Path(plan["release"]["target"]),
        provenance(plan, plan["release"], package=None, now=when),
        uid=root_uid,
        gid=root_gid,
        dir_mode=0o755,
        file_mode=0o644,
    )
    content = {}
    for name, item in plan["content"].items():
        if item["restricted"]:
            gid, dir_mode, file_mode = restricted_gid, 0o750, 0o640
            _ensure_dir(paths.srv / "restricted" / platform, root_uid, gid, dir_mode)
            _ensure_dir(paths.srv / "restricted" / platform / "content", root_uid, gid, dir_mode)
        else:
            gid, dir_mode, file_mode = root_gid, 0o755, 0o644
            _ensure_dir(paths.srv / "platforms" / platform / "content", root_uid, gid, dir_mode)
        base = paths.content_dir(platform, name, restricted=item["restricted"])
        _ensure_dir(base, root_uid, gid, dir_mode)
        changed |= _publish_one(
            run / "content" / name,
            Path(item["target"]),
            provenance(plan, item, package=name, now=when),
            uid=root_uid,
            gid=gid,
            dir_mode=dir_mode,
            file_mode=file_mode,
        )
        content[name] = item["target"]
    return {"changed": changed, "release": plan["release"]["target"], "content": content}


# --- render (§7.14 step 7) ---------------------------------------------------------


def published_manifest(plan: Mapping[str, Any], contract: Contract) -> dict[str, Any]:
    """The manifest of the published release.

    Args:
        plan: The deployment plan (after publish).
        contract: The contract.

    Returns:
        The manifest, validated before it was published.
    """
    target = Path(plan["release"]["target"])
    return load_file(target / MANIFEST_NAME, int(contract.caps["manifest_bytes"]))


def read_rendered(paths: Paths) -> dict[str, Any] | None:
    """What infra rendered for the active platform, or None."""
    return read_json(paths.etc_platform / RENDERED)


def render_step(plan: Mapping[str, Any], paths: Paths, contract: Contract) -> dict[str, Any]:
    """Render everything the platform role applies (§7.14 step 7).

    Args:
        plan: The deployment plan (after publish).
        paths: Host paths.
        contract: The contract.

    Returns:
        ``{"changed": False, "render": document}``; the document is also
        written to ``<run>/render.json``.
    """
    manifest = published_manifest(plan, contract)
    lab = plan.get("lab") or {}
    document = rendering.render(
        manifest,
        contract,
        release_target=plan["release"]["target"],
        ports=lab.get("ports") or {},
        settings=plan.get("platform_settings") or {},
        machine_name=str(lab.get("machine_name", "")),
        previous=read_rendered(paths),
    )
    write_json(Path(plan["run_dir"]) / RENDER, document, 0o600)
    return {"changed": False, "render": document}


def lab_facts(lab: Mapping[str, Any]) -> render.LabFacts:
    """The lab facts of the endpoints file from the settings' ``lab`` section.

    Args:
        lab: ``Settings.lab``.

    Returns:
        The facts.
    """
    return render.LabFacts(
        machine_name=str(lab["machine_name"]),
        lab_mode=str(lab["lab_mode"]),
        node_role=str(lab["node_role"]),
        ports={k: int(v) for k, v in lab["ports"].items()},
        inference_mode=str(lab.get("inference_mode", "local")),
        scripted_api_url=str(lab.get("scripted_api_url", "")),
        vm_collector_address=str(lab.get("vm_collector_address", "198.18.0.1")),
        cpuset_targets=str(lab.get("cpuset_targets", "")),
    )


def write_text(path: Path, text: str, mode: int = 0o644) -> bool:
    """Write a file atomically if its content or mode differs.

    Args:
        path: The file.
        text: Its content.
        mode: Its permission bits.

    Returns:
        True if the file changed.
    """
    try:
        same = (
            path.read_text(encoding="utf-8") == text and stat.S_IMODE(path.stat().st_mode) == mode
        )
    except OSError:
        same = False
    if same:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(tmp).chmod(mode)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def principals_step(
    plan: Mapping[str, Any],
    paths: Paths,
    contract: Contract,
    uid: Callable[[str], int] = lambda name: pwd.getpwnam(name).pw_uid,
) -> dict[str, Any]:
    """Write the principals and endpoints files once the accounts exist (§12.3).

    Args:
        plan: The deployment plan (after the accounts were created).
        paths: Host paths.
        contract: The contract.
        uid: UID of an account.

    Returns:
        ``{"changed": bool}``.

    Raises:
        DeployError: An account does not exist yet.
    """
    manifest = published_manifest(plan, contract)
    p = manifest["name"]
    members = [*manifest["services"], *manifest.get("jobs", [])]
    uids = {}
    for member in members:
        account = names.account(p, member["name"])
        try:
            uids[account] = uid(account)
        except KeyError:
            raise DeployError(f"account {account} does not exist; render creates it") from None
    principals = render.principals_json(render.principals(manifest, contract, uids))
    endpoints = render.endpoints_toml(render.endpoints(manifest, lab_facts(plan["lab"])))
    changed = write_text(paths.etc_platform / PRINCIPALS, principals)
    changed |= write_text(paths.etc_platform / ENDPOINTS, endpoints)
    return {"changed": changed}


# --- replace, activate, purge ----------------------------------------------------


def retire_plan(active: Mapping[str, Any], contract: Contract, paths: Paths) -> dict[str, Any]:
    """What replacing the active platform takes away (§7.14 "One platform per lab").

    Its state, database, restricted area and releases stay until ``pinka
    platform purge``. What was rendered for it (``rendered.json``) names its
    units, sockets, configuration files and CLI; without that record the
    services in ``active.json`` stand in.

    Args:
        active: The active platform record of the platform being replaced.
        contract: The contract.
        paths: Host paths.

    Returns:
        Units to stop and remove, socket directories, configuration files and
        the CLI link and wrapper to remove, accounts to lock, database roles
        to make ``NOLOGIN`` and the Incus certificates to remove from the
        trust store.
    """
    platform = active["name"]
    recorded = active.get("names", {})
    services = active.get("services", [])
    rendered = read_rendered(paths)
    if rendered is not None and rendered.get("platform") != platform:
        rendered = None
    if rendered is not None:
        units = [
            u["name"] for u in rendered.get("units", []) if u["name"] != "pinka-platform.target"
        ]
        config_files = [f["path"] for f in rendered.get("config_files", [])]
        cli = rendered.get("cli") or {}
        certificates = [c["name"] for c in rendered.get("incus_certificates", [])]
        roles = sorted(
            {
                *recorded.get("postgresql_roles", []),
                *((rendered.get("database") or {}).get("all_roles", [])),
            }
        )
    else:
        units = [f"{names.account(platform, s['name'])}.service" for s in services]
        config_files = []
        cli = {"link": str(paths.bin / active["cli"])} if active.get("cli") else {}
        certificates = [
            names.account(platform, s["name"])
            for s in services
            if s["profile"] in contract.profiles
            and contract.profile(s["profile"]).data["incus_projects"]
        ]
        roles = list(recorded.get("postgresql_roles", []))
    return {
        "name": platform,
        "units": units,
        "socket_dirs": [f"/run/pinka/platform/{s['name']}" for s in services],
        "config_files": config_files,
        "cli_link": cli.get("link"),
        "cli_wrapper": cli.get("wrapper"),
        "accounts": [
            names.account(platform, member)
            for member in [
                *(s["name"] for s in services),
                *(j["name"] for j in active.get("jobs", [])),
                "migrate",
            ]
        ],
        "postgresql_roles": roles,
        "incus_certificates": certificates,
    }


def _link(link: Path, target: str) -> bool:
    if link.is_symlink() and str(link.readlink()) == target:
        return False
    tmp = link.parent / f".{link.name}.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target)
    tmp.replace(link)
    return True


def activate(
    plan: Mapping[str, Any],
    paths: Paths,
    now: Callable[[], dt.datetime] = _now,
) -> dict[str, Any]:
    """Switch ``current``, record the active platform and the deployment (§7.14 steps 8-9).

    Replacing a platform records it as retired, with what it had, so that
    ``pinka platform purge`` can remove it later.

    Args:
        plan: The deployment plan (after publish).
        paths: Host paths.
        now: Clock.

    Returns:
        ``{"changed": bool, "active": record}``.
    """
    when = utc(now())
    platform = plan["platform"]["name"]
    for item in [plan["release"], *plan["content"].values()]:
        if not (Path(item["target"]) / PROVENANCE).is_file():
            raise DeployError(f"{item['target']} is not published")
    changed = _link(paths.release_dir(platform) / CURRENT, plan["release"]["name"])
    for name, item in plan["content"].items():
        base = paths.content_dir(platform, name, restricted=item["restricted"])
        changed |= _link(base / CURRENT, item["name"])
    previous = read_active(paths)
    if plan.get("replace") and previous and previous.get("name") == plan["replace"]:
        old = previous["name"]
        write_json(
            paths.records / old / RETIRED,
            {
                "record_version": RECORD_VERSION,
                "retired_at": when,
                "replaced_by": platform,
                "purged_at": None,
                "active": previous,
            },
            0o600,
        )
    record = {
        "record_version": RECORD_VERSION,
        "name": platform,
        "version": plan["platform"]["version"],
        "runtime": plan["platform"]["runtime"],
        "manifest_sha256": plan["platform"]["manifest_sha256"],
        "release": {k: plan["release"][k] for k in ("name", "commit", "tree", "tag", "target")},
        "content": {
            name: {k: item[k] for k in ("name", "commit", "tree", "tag", "target", "restricted")}
            for name, item in plan["content"].items()
        },
        "services": list(plan["services"]),
        "jobs": [{"name": j["name"]} for j in plan["jobs"]],
        "cli": plan["cli"],
        "names": dict(plan["names"]),
        "activated_at": when,
    }
    document = read_json(Path(plan["run_dir"]) / RENDER)
    if document is not None:
        text = json.dumps(rendering.public(document), indent=2, sort_keys=True) + "\n"
        changed |= write_text(paths.etc_platform / RENDERED, text)
    same = previous is not None and {k: v for k, v in previous.items() if k != "activated_at"} == {
        k: v for k, v in record.items() if k != "activated_at"
    }
    if not same:
        write_json(paths.active, record, 0o644)
        changed = True
        log = paths.records / platform / DEPLOYMENTS
        log.parent.mkdir(parents=True, exist_ok=True)
        log.parent.chmod(0o700)
        with log.open("a", encoding="utf-8") as handle:
            entry = {
                "at": when,
                "release": record["release"],
                "content": record["content"],
                "version": record["version"],
                "replaced": plan.get("replace"),
            }
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return {"changed": changed, "active": record if not same else previous}


def clean(run: Path, paths: Paths) -> None:
    """Delete a run directory below the staging directory.

    Args:
        run: The run directory.
        paths: Host paths.

    Raises:
        DeployError: The directory is not a run directory of the staging area.
    """
    if run.parent.resolve() != paths.staging.resolve() or not run.name.startswith("run-"):
        raise DeployError(f"{run} is not a run directory of {paths.staging}")
    shutil.rmtree(run, ignore_errors=True)


def _psql(runner: Runner, sql: str) -> None:
    done = runner(
        [
            "docker",
            "exec",
            "--user",
            "postgres",
            POSTGRES_CONTAINER,
            "sh",
            "-c",
            PSQL_SCRIPT,
            "psql",
            "--quiet",
            "--command",
            sql,
        ]
    )
    if done.returncode != 0:
        raise DeployError(f"psql failed: {(done.stderr or done.stdout).strip()}")


def _retired_accounts(record: Mapping[str, Any]) -> list[str]:
    platform = record["name"]
    members = [s["name"] for s in record.get("services", [])]
    members += [j["name"] for j in record.get("jobs", [])]
    return [names.account(platform, m) for m in [*members, "migrate"]]


def _account_exists(account: str) -> bool:
    try:
        pwd.getpwnam(account)
    except KeyError:
        return False
    return True


def _secret_paths(paths: Paths, record: Mapping[str, Any]) -> list[Path]:
    roles = [r for r in record.get("names", {}).get("postgresql_roles", []) if SQL_NAME_RE.match(r)]
    candidates = [paths.secrets / a for a in _retired_accounts(record)]
    candidates += [paths.secrets / "postgres" / r for r in roles]
    candidates.append(paths.signing / record["name"])
    return [c for c in candidates if c.exists() or c.is_symlink()]


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def purge(
    platform: str,
    confirm: str,
    paths: Paths,
    runner: Runner,
    *,
    remove_secrets: bool = False,
    account_exists: Callable[[str], bool] = _account_exists,
    now: Callable[[], dt.datetime] = _now,
) -> dict[str, Any]:
    """Remove a replaced platform's releases, content, state, restricted area and database.

    Its accounts stay locked and its published signing keys stay (§12.10:
    kept forever); its secrets directories, database password files and
    private signing key stay too unless ``remove_secrets`` (owner decision on
    3d.2). The result lists everything that remains. Only a platform that
    was replaced can be purged, never the active one, and only with its name
    typed as confirmation.

    Args:
        platform: The platform.
        confirm: The typed confirmation; must equal ``platform``.
        paths: Host paths.
        runner: Command runner (psql in the PostgreSQL container).
        remove_secrets: Also remove the secrets.
        account_exists: Whether an account exists (tests).
        now: Clock.

    Returns:
        What was removed and what remains.

    Raises:
        DeployError: Wrong confirmation, the active platform, never replaced,
            or the database could not be dropped (then nothing was removed).
    """
    if not PLATFORM_NAME_RE.match(platform):
        raise DeployError(f"{platform!r} is not a platform name")
    if confirm != platform:
        raise DeployError(f"type the platform name {platform!r} to confirm")
    active = read_active(paths)
    if active and active.get("name") == platform:
        raise DeployError(f"{platform!r} is the active platform; only a replaced one is purged")
    retired = read_json(paths.records / platform / RETIRED)
    if retired is None:
        raise DeployError(f"{platform!r} was never replaced on this host (no {RETIRED})")
    record = retired.get("active", {})
    recorded = record.get("names", {})
    databases = [d for d in recorded.get("postgresql_databases", []) if SQL_NAME_RE.match(d)]
    roles = [r for r in recorded.get("postgresql_roles", []) if SQL_NAME_RE.match(r)]
    for db_name in databases:
        _psql(runner, f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    for role_name in roles:
        _psql(runner, f'DROP ROLE IF EXISTS "{role_name}"')
    removed = []
    for directory in (paths.srv / "platforms" / platform, paths.srv / "restricted" / platform):
        if directory.exists() or directory.is_symlink():
            _remove(directory)
            removed.append(str(directory))
    secrets = _secret_paths(paths, {**record, "name": platform})
    if remove_secrets:
        for path in secrets:
            _remove(path)
            removed.append(str(path))
        secrets = []
    published = sorted((paths.srv / "shared" / "signing-keys").glob(f"{platform}-*.pub"))
    retired["purged_at"] = utc(now())
    write_json(paths.records / platform / RETIRED, retired, 0o600)
    return {
        "changed": True,
        "removed": removed,
        "databases": databases,
        "roles": roles,
        "remaining": {
            "locked_accounts": [
                a for a in _retired_accounts({**record, "name": platform}) if account_exists(a)
            ],
            "secrets": [str(p) for p in secrets],
            "published_signing_keys": [str(p) for p in published],
        },
    }
