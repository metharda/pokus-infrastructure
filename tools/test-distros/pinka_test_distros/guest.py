"""Inside a test VM: the harness's helpers for the reference platform.

Run by the harness with the Ansible venv's Python, as a script, never
imported by the lab: ``python -I guest.py
<command> ...``; every command prints one JSON line.

The harness enables, replaces and restores the platform through the
installer's answers (``bootstrap.sh --answers``); this helper only waits,
and tries the image store:

* ``wait-report``, ``wait-run`` and ``wait-checkpoint`` wait for the agent's
  probe report, for a training run's end and for a run's checkpoint.
* ``image-store`` trusts a client certificate restricted to some projects,
  as a platform service's, and reports what it could do with the image
  store those projects share (``incus``, root for the rest); it removes
  everything it created again.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

CLI = "/usr/local/bin/refplat"
JOBS = Path("/srv/pinka/training/jobs")
# The reference platform's final run statuses (refplat.training.FINAL).
FINAL = ("succeeded", "failed", "interrupted", "ended")
INTERVAL = 5.0


def cli(*args: str) -> dict[str, Any] | None:
    """One answer of the reference platform's CLI as root, or None if it gave none."""
    done = subprocess.run([CLI, *args], capture_output=True, text=True, check=False)  # noqa: S603
    try:
        value = json.loads(done.stdout)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def poll(
    probe: Callable[[], dict[str, Any] | None],
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any] | None:
    """Call ``probe`` until it returns something or the time is up."""
    deadline = time.monotonic() + timeout
    while True:
        found = probe()
        if found is not None or time.monotonic() >= deadline:
            return found
        sleep(INTERVAL)


def agent_report() -> dict[str, Any] | None:
    """The agent's latest probe report, once it sent one."""
    answer = cli("probe-report") or {}
    report = answer.get("report") or {}
    return report if report.get("outcomes") else None


def run_end(run: str) -> dict[str, Any] | None:
    """A run's record in the controller once it is final."""
    item = ((cli("runs") or {}).get("runs") or {}).get(run)
    return {"run": run, **item} if item and item.get("status") in FINAL else None


def checkpoint(run: str, step: int, jobs: Path = JOBS) -> dict[str, Any] | None:
    """A run's checkpoint once it reached ``step``."""
    try:
        saved = json.loads((jobs / run / "checkpoints" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and int(saved.get("step", 0)) >= step else None


# The image store check: its trusted certificate's name, its directory and the
# alias prefix of the images it creates (none of a blueprint's form).
STORE_NAME = "pinka-test-images"
STORE_DIR = Path("/root/pinka-test-images")
STORE_ALIAS = "pinka-test-store"
STORE_REMOTE = "store"
# The property the client sets through a new image request (imagestore.REWRITTEN).
STORE_REWRITTEN = ("user.pinka-test", "rewritten")
# The incus CLI of every family (ansible/roles/incus/vars/<family>.yml, incus_cli).
INCUS = "/usr/bin/incus"
# Variables that would point the CLI at another configuration, remote, project or socket.
INCUS_ENV = ("INCUS_CONF", "INCUS_PROJECT", "INCUS_REMOTE", "INCUS_SOCKET", "INCUS_DIR")
# Each incus call's limit: the check makes fewer than 40, so even if every one
# hung it ends, cleanup included, within the harness's limit for the stage
# (imagestore.STAGE_TIMEOUT).
CALL_TIMEOUT = 30
LIST_IMAGES = ("image", "list", "--project", "default", "--format", "json")
LIST_TRUST = ("config", "trust", "list", "--format", "json")

Run = Callable[[Sequence[str], "Path | None"], "subprocess.CompletedProcess[str]"]


def incus(argv: Sequence[str], conf: Path | None) -> subprocess.CompletedProcess[str]:
    """Run the incus CLI: as root over its socket, or as the client of ``conf``."""
    env = {k: v for k, v in os.environ.items() if k not in INCUS_ENV}
    if conf is not None:
        env["INCUS_CONF"] = str(conf)
    return subprocess.run(  # noqa: S603
        [INCUS, *argv], capture_output=True, text=True, check=False, env=env, timeout=CALL_TIMEOUT
    )


def tiny_image(path: Path, description: str) -> None:
    """A minimal container image: its metadata and an empty root file system."""
    meta = (
        f"architecture: {platform.machine()}\ncreation_date: {int(time.time())}\n"
        f"properties:\n  description: {description}\n"
    ).encode()
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("metadata.yaml")
        info.size = len(meta)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(meta))
        root = tarfile.TarInfo("rootfs")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tar.addfile(root)


def _json(run: Run, argv: Sequence[str]) -> Any:  # noqa: ANN401 - a JSON document
    """Root's answer to an incus command as JSON, or None when it gave none."""
    try:
        done = run(argv, None)
        return json.loads(done.stdout) if done.returncode == 0 else None
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def _root_list(run: Run, argv: Sequence[str]) -> list[dict[str, Any]] | None:
    """A list root's incus command gives, or None when it gave none (never an empty one instead)."""
    value = _json(run, argv)
    return [i for i in value if isinstance(i, dict)] if isinstance(value, list) else None


def _listing(run: Run) -> list[dict[str, Any]]:
    return _root_list(run, LIST_IMAGES) or []


def _fingerprint(images: list[dict[str, Any]], alias: str) -> str:
    for image in images:
        if any(a.get("name") == alias for a in image.get("aliases") or []):
            return str(image.get("fingerprint", ""))
    return ""


def _ours(images: list[dict[str, Any]]) -> list[str]:
    """The fingerprints of the images the check created (by their description)."""
    return sorted(
        str(i.get("fingerprint"))
        for i in images
        if str((i.get("properties") or {}).get("description", "")).startswith(f"{STORE_NAME} ")
    )


def _trusted(entries: list[dict[str, Any]]) -> list[str]:
    """The fingerprints of the trust entries the check created (by their name)."""
    return sorted(str(e.get("fingerprint")) for e in entries if e.get("name") == STORE_NAME)


def image_store(  # noqa: PLR0915 - one step per operation, in order
    projects: Sequence[str],
    project: str,
    address: str,
    server_cert: Path,
    *,
    run: Run = incus,
    work: Path = STORE_DIR,
) -> dict[str, Any]:
    """What a client restricted to ``projects`` can do with the image store of ``project``.

    Root imports a stand-in of a golden image (alias ``<STORE_ALIAS>/1.0.0``)
    into the default project; the client, working in ``project``, imports an
    image of its own with an alias, creates an alias of the stand-in, and
    tries to create the stand-in's alias again, to point it elsewhere (PUT
    and PATCH), to delete or rename it, to delete its own alias, to change a
    property of the stand-in and to delete either image. Everything created
    is removed again, also after a failure; each removal is a step of its own.

    Args:
        projects: The projects the client certificate is restricted to.
        project: The project the client works in.
        address: Incus's HTTPS address.
        server_cert: Incus's server certificate, which the client pins.
        run: Runs the incus CLI (argv, the client's configuration or None for root).
        work: The check's directory (replaced).

    Returns:
        ``steps`` (name, exit code, the end of the output), ``aliases`` (the
        default project's aliases while the client's exist), ``golden`` (the
        stand-in's aliases and properties after the client's steps), ``left``
        (what the check created and could not remove) and ``server_version``.
    """
    steps: list[dict[str, Any]] = []
    conf = work / "conf"

    def step(name: str, argv: Sequence[str], *, client: bool = True, flag: bool = True) -> None:
        if client and flag:
            argv = [*argv, "--project", project]
        try:
            done = run(argv, conf if client else None)
            rc, output = done.returncode, (done.stderr or done.stdout).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            rc, output = -1, str(exc)
        steps.append({"name": name, "rc": rc, "output": output[-300:]})

    shutil.rmtree(work, ignore_errors=True)
    (conf / "servercerts").mkdir(parents=True, mode=0o700)
    shutil.copyfile(server_cert, conf / "servercerts" / f"{STORE_REMOTE}.crt")
    (conf / "config.yml").write_text(
        f"default-remote: {STORE_REMOTE}\nremotes:\n  {STORE_REMOTE}:\n"
        f"    addr: https://{address}\n    protocol: incus\n    auth_type: tls\n"
        f"    project: {project}\n",
        encoding="utf-8",
    )
    golden_tar, other_tar = work / "golden.tar.gz", work / "other.tar.gz"
    tiny_image(golden_tar, f"{STORE_NAME} golden")
    tiny_image(other_tar, f"{STORE_NAME} {project}")
    golden_alias, other_alias = f"{STORE_ALIAS}/1.0.0", f"{STORE_ALIAS}/1.0.1"
    alias_path = f"{STORE_REMOTE}:/1.0/images/aliases/{golden_alias}?project={project}"
    server = _json(run, ["query", "/1.0"])
    environment = server.get("environment") if isinstance(server, dict) else None
    version = environment.get("server_version") if isinstance(environment, dict) else None
    aliases: list[str] = []
    golden: dict[str, Any] = {}
    trusted = ""
    try:
        step("generate a client certificate", ["remote", "generate-certificate"])
        pem = (
            (conf / "client.crt").read_text(encoding="utf-8")
            if (conf / "client.crt").exists()
            else ""
        )
        if pem:
            trusted = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
        step(
            "trust it, restricted to the projects",
            [
                "config",
                "trust",
                "add-certificate",
                str(conf / "client.crt"),
                "--restricted",
                "--projects",
                ",".join(projects),
                "--name",
                STORE_NAME,
            ],
            client=False,
        )
        step(
            "root imports a golden image",
            ["image", "import", str(golden_tar), "--alias", golden_alias, "--project", "default"],
            client=False,
        )
        golden_fp = _fingerprint(_listing(run), golden_alias) or "none"
        step(
            "the client imports an image with an alias",
            ["image", "import", str(other_tar), f"{STORE_REMOTE}:", "--alias", other_alias],
        )
        other_fp = _fingerprint(_listing(run), other_alias) or "none"
        step(
            "the client creates an alias of the golden image",
            ["image", "alias", "create", f"{STORE_REMOTE}:{STORE_ALIAS}/2.0.0", golden_fp],
        )
        aliases = sorted(
            a["name"] for i in _listing(run) for a in i.get("aliases") or [] if a.get("name")
        )
        step(
            "the client creates the golden alias again",
            ["image", "alias", "create", f"{STORE_REMOTE}:{golden_alias}", other_fp],
        )
        target = json.dumps({"target": other_fp, "description": ""})
        step(
            "the client points the golden alias at its image (PUT)",
            ["query", "-X", "PUT", "-d", target, alias_path],
            flag=False,
        )
        step(
            "the client points the golden alias at its image (PATCH)",
            ["query", "-X", "PATCH", "-d", json.dumps({"target": other_fp}), alias_path],
            flag=False,
        )
        step(
            "the client deletes the golden alias",
            ["image", "alias", "delete", f"{STORE_REMOTE}:{golden_alias}"],
        )
        step(
            "the client renames the golden alias",
            ["image", "alias", "rename", f"{STORE_REMOTE}:{golden_alias}", f"{STORE_ALIAS}/9.0.0"],
        )
        step(
            "the client deletes its own alias",
            ["image", "alias", "delete", f"{STORE_REMOTE}:{STORE_ALIAS}/2.0.0"],
        )
        rewrite = {
            "source": {"type": "image", "fingerprint": golden_fp},
            "properties": {STORE_REWRITTEN[0]: STORE_REWRITTEN[1]},
        }
        # Incus 6.0.7 rewrites the record, then fails on the image's own aliases.
        step(
            "the client rewrites the golden image's record through a new image request",
            [
                "query",
                "--wait",
                "-X",
                "POST",
                "-d",
                json.dumps(rewrite),
                f"{STORE_REMOTE}:/1.0/images?project={project}",
            ],
            flag=False,
        )
        step(
            "the client changes a property of the golden image",
            ["image", "set-property", f"{STORE_REMOTE}:{golden_fp}", "description", "changed"],
        )
        step(
            "the client deletes the golden image",
            ["image", "delete", f"{STORE_REMOTE}:{golden_fp}"],
        )
        step("the client deletes its own image", ["image", "delete", f"{STORE_REMOTE}:{other_fp}"])
        for image in _listing(run):
            if image.get("fingerprint") == golden_fp:
                golden = {
                    "aliases": sorted(a.get("name", "") for a in image.get("aliases") or []),
                    "properties": image.get("properties") or {},
                }
    finally:
        # Each removal on its own, so that one that fails or hangs skips no other;
        # a listing that fails is a failed step, never read as an empty one.
        listed = _root_list(run, LIST_IMAGES)
        if listed is None:
            steps.append({"name": "cleanup: root lists the images", "rc": -1, "output": "no list"})
        for fingerprint in _ours(listed or []):
            step(
                f"cleanup: root deletes the image {fingerprint[:12]}",
                ["image", "delete", fingerprint, "--project", "default"],
                client=False,
            )
        entries = _root_list(run, LIST_TRUST)
        if entries is None:
            steps.append(
                {"name": "cleanup: root lists the trust entries", "rc": -1, "output": "no list"}
            )
        for fingerprint in (
            _trusted(entries) if entries is not None else [trusted] if trusted else []
        ):
            step(
                f"cleanup: root removes the trust entry {fingerprint[:12]}",
                ["config", "trust", "remove", fingerprint],
                client=False,
            )
        shutil.rmtree(work, ignore_errors=True)
    images = _root_list(run, LIST_IMAGES)
    entries = _root_list(run, LIST_TRUST)
    left = sorted(
        {
            a["name"]
            for i in images or []
            for a in i.get("aliases") or []
            if str(a.get("name", "")).startswith(f"{STORE_ALIAS}/")
        }
        | {f"image {fp[:12]}" for fp in _ours(images or [])}
        | {f"trust entry {fp[:12]}" for fp in _trusted(entries or [])}
        | ({"the images (no list)"} if images is None else set())
        | ({"the trust entries (no list)"} if entries is None else set())
        | ({str(work)} if work.exists() else set())
    )
    return {
        "steps": steps,
        "aliases": aliases,
        "golden": golden,
        "left": left,
        "server_version": version,
    }


def emit(value: Mapping[str, Any] | None, what: str) -> int:
    """Print the result line; 1 when nothing came."""
    if value is None:
        sys.stdout.write(json.dumps({"ok": False, "error": f"no {what} in time"}) + "\n")
        return 1
    sys.stdout.write(json.dumps({"ok": True, **value}, sort_keys=True) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run a command; prints one JSON line."""
    parser = argparse.ArgumentParser(prog="guest.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("wait-report")
    report.add_argument("--timeout", type=float, default=600)
    run = sub.add_parser("wait-run")
    run.add_argument("run")
    run.add_argument("--timeout", type=float, default=1800)
    step = sub.add_parser("wait-checkpoint")
    step.add_argument("run")
    step.add_argument("--step", type=int, required=True)
    step.add_argument("--timeout", type=float, default=600)
    store = sub.add_parser("image-store")
    store.add_argument("--projects", required=True, help="comma-separated")
    store.add_argument("--project", required=True)
    store.add_argument("--address", required=True)
    store.add_argument("--server-cert", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "image-store":
        tried = image_store(args.projects.split(","), args.project, args.address, args.server_cert)
        return emit(tried, "image store report")
    if args.command == "wait-report":
        return emit(poll(agent_report, args.timeout), "probe report of the agent")
    if args.command == "wait-run":
        ended = poll(lambda: run_end(args.run), args.timeout)
        code = emit(ended, f"end of run {args.run}")
        return code if code or (ended or {}).get("status") == "succeeded" else 1
    found = poll(lambda: checkpoint(args.run, args.step), args.timeout)
    return emit(found, f"checkpoint of run {args.run} at step {args.step}")


if __name__ == "__main__":
    sys.exit(main())
