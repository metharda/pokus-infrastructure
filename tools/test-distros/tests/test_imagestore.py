"""The images stage: a client restricted like a platform service and the shared image store."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from pinka_test_distros import guest, imagestore

from pinka_platform import contract as contract_mod

CONTRACT = contract_mod.load()
INFRA = Path(__file__).resolve().parents[3]
INCUS_ROLE = INFRA / "ansible" / "roles" / "incus"
PEM = "-----BEGIN CERTIFICATE-----\nAAECAwQ=\n-----END CERTIFICATE-----\n"
# What Incus 6.0.6 prints, written out here rather than taken from the module:
# a trusted certificate restricted to other projects (internal/server/auth/
# driver_tls.go, CheckPermission), and an alias that exists (cmd/incusd/
# images.go, imageAliasesPost).
DENIED = 'Error: User does not have permission for project "default"'
EXISTS = 'Error: Alias "pinka-test-store/1.0.0" already exists'
GOLDEN = "pinka-test-store/1.0.0"


def good_report() -> dict[str, Any]:
    """The report of a store that behaves as the images role relies on."""
    steps = [{"name": n, "rc": 0, "output": ""} for n in imagestore.ALLOWED]
    outputs = {imagestore.RESTRICTED: DENIED, "already exists": EXISTS}
    steps += [
        {"name": n, "rc": 1, "output": outputs.get(e, f"Error: {e}: {GOLDEN}")}
        for n, e in imagestore.REFUSED.items()
    ]
    steps += [{"name": "cleanup: root deletes the image 111111111111", "rc": 0, "output": ""}]
    return {
        "steps": steps,
        "aliases": [f"pinka-test-store/{v}" for v in ("1.0.0", "1.0.1", "2.0.0")],
        "golden": {
            "aliases": [GOLDEN, "pinka-test-store/2.0.0"],
            "properties": {
                "description": "pinka-test-images golden",
                "user.pinka-test": "rewritten",
            },
        },
        "left": [],
        "server_version": "6.0.6",
    }


# --- what the stage expects ---------------------------------------------------------------


def test_the_refusals_are_incus_6_0_6s() -> None:
    assert DENIED.removeprefix("Error: ") == imagestore.RESTRICTED
    assert imagestore.REFUSED == {
        "the client creates the golden alias again": "already exists",
        "the client rewrites the golden image's record through a new image request": (
            "Alias already exists"
        ),
        "the client points the golden alias at its image (PUT)": imagestore.RESTRICTED,
        "the client points the golden alias at its image (PATCH)": imagestore.RESTRICTED,
        "the client deletes the golden alias": imagestore.RESTRICTED,
        "the client renames the golden alias": imagestore.RESTRICTED,
        "the client deletes its own alias": imagestore.RESTRICTED,
        "the client changes a property of the golden image": imagestore.RESTRICTED,
        "the client deletes the golden image": imagestore.RESTRICTED,
        "the client deletes its own image": imagestore.RESTRICTED,
    }
    # An untrusted client's answer is not taken for the restriction's.
    report = good_report()
    for s in report["steps"]:
        if s["output"] == DENIED:
            s["output"] = "Error: not authorized"
    assert len(imagestore.compare(report, "build")) == 8


def test_the_runs_cover_every_restricted_profile() -> None:
    assert imagestore.runs(CONTRACT) == [
        (["bench"], "bench"),
        (["bench", "build"], "bench"),
        (["bench", "build"], "build"),
    ]
    assert sorted(CONTRACT.profile("controller").data["incus_projects"]) == ["bench", "build"]


def test_the_address_and_certificate_are_the_incus_roles() -> None:
    defaults = yaml.safe_load((INCUS_ROLE / "defaults" / "main.yml").read_text())
    assert defaults["incus_https_address"] == imagestore.ADDRESS
    for family in sorted((INCUS_ROLE / "vars").glob("*.yml")):
        values = yaml.safe_load(family.read_text())
        assert values["incus_server_certificate"] == imagestore.SERVER_CERT, family.name
        assert values["incus_cli"] == guest.INCUS, family.name
    assert guest.STORE_ALIAS == imagestore.ALIAS
    assert guest.STORE_REWRITTEN == imagestore.REWRITTEN
    # The stand-in's alias is of no blueprint's form.
    assert not (INFRA / "images" / imagestore.ALIAS).exists()


def test_a_store_that_behaves_passes() -> None:
    assert imagestore.compare(good_report(), "build") == []


def with_step(name: str, rc: int, output: str = "") -> dict[str, Any]:
    report = good_report()
    report["steps"] = [s for s in report["steps"] if s["name"] != name]
    report["steps"].append({"name": name, "rc": rc, "output": output})
    return report


def with_golden(**properties: Any) -> dict[str, Any]:
    report = good_report()
    report["golden"] = {"aliases": [GOLDEN, "pinka-test-store/2.0.0"], "properties": properties}
    return report


@pytest.mark.parametrize(
    ("report", "problem"),
    [
        (
            with_step("the client deletes the golden alias", 0),
            "the client deletes the golden alias succeeded",
        ),
        (
            with_step("the client deletes its own alias", 0),
            "the client deletes its own alias succeeded",
        ),
        (
            with_step("the client points the golden alias at its image (PUT)", 0),
            "the client points the golden alias at its image (PUT) succeeded",
        ),
        (
            with_step("the client creates the golden alias again", 0),
            "the client creates the golden alias again succeeded",
        ),
        (
            with_step(
                "the client changes a property of the golden image", 1, "Error: unknown command"
            ),
            f"failed without {imagestore.RESTRICTED!r}: Error: unknown command",
        ),
        (
            with_step("the client imports an image with an alias", 1, DENIED),
            f"the client imports an image with an alias failed: {DENIED}",
        ),
        (
            with_step("cleanup: root removes the trust entry 000102030405", 1, "Error: not found"),
            "root removes the trust entry 000102030405 failed: Error: not found",
        ),
        ({**good_report(), "steps": []}, "the client deletes the golden image: not tried"),
        ({**good_report(), "aliases": []}, "the client's alias pinka-test-store/1.0.1 is not in"),
        (
            {**good_report(), "golden": {"aliases": ["pinka-test-store/2.0.0"], "properties": {}}},
            "the golden image's aliases changed",
        ),
        (
            with_golden(description="changed", **{"user.pinka-test": "rewritten"}),
            "the golden image's description changed: 'changed'",
        ),
        (
            with_golden(description="pinka-test-images golden"),
            "the client's new image request did not set user.pinka-test: None",
        ),
        (
            {**good_report(), "left": ["pinka-test-store/1.0.0"]},
            "left behind: pinka-test-store/1.0.0",
        ),
    ],
)
def test_every_difference_is_named(report: dict[str, Any], problem: str) -> None:
    problems = imagestore.compare(report, "build")
    assert any(problem in p for p in problems), problems
    assert all(p.startswith("build: ") for p in problems)


# --- the guest helper -----------------------------------------------------------------------


def described(tar: str) -> str:
    with tarfile.open(tar) as archive:
        meta = yaml.safe_load(archive.extractfile("metadata.yaml").read())  # type: ignore[union-attr]
    return str(meta["properties"]["description"])


class FakeStore:
    """The incus CLI on a store with features.images false, as 6.0.6 answers.

    The client may create images and aliases and rewrite an image's record
    through a new image request; everything else on the store is refused.
    """

    def __init__(
        self,
        *,
        hang: str = "",
        alias_fails: bool = False,
        trust_rc: int = 0,
        remove_rc: int = 0,
        trust: dict[str, str] | None = None,
        list_fails: bool = False,
    ) -> None:
        self.images: dict[str, dict[str, Any]] = {}
        self.trust: dict[str, str] = dict(trust or {})
        self.remove_rc = remove_rc
        self.list_fails = list_fails
        self.listed = 0
        self.calls: list[tuple[list[str], Path | None]] = []
        self.hang = hang
        self.alias_fails = alias_fails
        self.trust_rc = trust_rc
        self.count = 0

    def done(
        self, args: list[str], rc: int = 0, out: str = "", err: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, rc, out, err)

    def __call__(  # noqa: PLR0911, PLR0912 - one answer per command
        self, argv: Sequence[str], conf: Path | None
    ) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        self.calls.append((args, conf))
        if args[:2] == ["image", "list"]:
            self.listed += 1
            # The check lists the images four times before its cleanup.
            if self.list_fails and self.listed > 4:
                # A failure that still prints a list is no list either.
                return self.done(args, 1, out="[]", err="Error: simulated")
        if self.hang and self.hang in " ".join(args):
            raise subprocess.TimeoutExpired(args, 600)
        if args[:2] == ["remote", "generate-certificate"]:
            assert conf is not None
            (conf / "client.crt").write_text(PEM)
            return self.done(args)
        if args[:2] == ["query", "/1.0"]:
            return self.done(args, out=json.dumps({"environment": {"server_version": "6.0.6"}}))
        if args[:2] == ["config", "trust"]:
            return self.trust_command(args)
        if args[:2] == ["image", "list"]:
            listing = [
                {
                    "fingerprint": f,
                    "aliases": [{"name": a} for a in i["aliases"]],
                    "properties": i["properties"],
                }
                for f, i in self.images.items()
            ]
            return self.done(args, out=json.dumps(listing))
        if args[:2] == ["image", "import"]:
            self.count += 1
            alias = args[args.index("--alias") + 1]
            no_alias = self.alias_fails and conf is not None
            self.images[str(self.count) * 64] = {
                "aliases": [] if no_alias else [alias],
                "properties": {"description": described(args[2])},
            }
            return self.done(args, 1 if no_alias else 0, err="Error: simulated" if no_alias else "")
        if args[:3] == ["image", "alias", "create"]:
            name = args[3].split(":", 1)[-1]
            if any(name in i["aliases"] for i in self.images.values()):
                return self.done(args, 1, err=f'Error: Alias "{name}" already exists')
            self.images[args[4]]["aliases"].append(name)
            return self.done(args)
        if args[:2] == ["query", "--wait"] and "POST" in args:
            # As 6.0.6: the record is rewritten, then the request fails on the
            # image's own aliases (cmd/incusd/images.go, imagesPost).
            body = json.loads(args[args.index("-d") + 1])
            image = self.images[body["source"]["fingerprint"]]
            image["properties"].update(body["properties"])
            if image["aliases"]:
                return self.done(args, 1, err=f"Error: Alias already exists: {image['aliases'][0]}")
            return self.done(args, out="{}")
        if conf is not None and args[0] in ("image", "query"):
            return self.done(args, 1, err=DENIED)
        if args[:2] == ["image", "delete"]:
            self.images.pop(args[2], None)
        return self.done(args)

    def trust_command(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[2] == "add-certificate":
            self.trust[hashlib.sha256(bytes(range(5))).hexdigest()] = args[args.index("--name") + 1]
            return self.done(
                args, self.trust_rc, err="" if self.trust_rc == 0 else "Error: simulated"
            )
        if args[2] == "list":
            listing = [{"name": n, "fingerprint": f} for f, n in self.trust.items()]
            return self.done(args, out=json.dumps(listing))
        if self.remove_rc:
            return self.done(args, self.remove_rc, err="Error: simulated")
        self.trust.pop(args[3], None)
        return self.done(args)


def check(
    tmp_path: Path, store: FakeStore, projects: Sequence[str] = ("bench", "build")
) -> dict[str, Any]:
    server = tmp_path / "server.crt"
    server.write_text("server")
    return guest.image_store(
        list(projects), "build", "127.0.0.1:8443", server, run=store, work=tmp_path / "work"
    )


def test_the_image_store_check(tmp_path: Path) -> None:
    store = FakeStore()
    report = check(tmp_path, store)
    assert imagestore.compare(report, "build") == []
    assert report["server_version"] == "6.0.6"
    client = [a for a, conf in store.calls if conf is not None]
    root = [a for a, conf in store.calls if conf is None]
    # The client works in the project: the configuration's, its flag or the
    # query's parameter; root only in the default project.
    for argv in client:
        if argv[0] == "query":
            assert argv[-1].startswith("store:/1.0/images") and argv[-1].endswith("?project=build")
            assert "--project" not in argv
        else:
            assert argv[-2:] == ["--project", "build"]
    for argv in root:
        if argv[0] == "image":
            assert argv[argv.index("--project") + 1] == "default", argv
    put = next(a for a in client if "PUT" in a)
    assert put[-1] == "store:/1.0/images/aliases/pinka-test-store/1.0.0?project=build"
    trust = next(a for a in root if a[:3] == ["config", "trust", "add-certificate"])
    assert trust[4:] == ["--restricted", "--projects", "bench,build", "--name", guest.STORE_NAME]
    # Everything created is removed: both images and the trusted certificate.
    assert store.images == {} and store.trust == {}
    assert ["config", "trust", "remove", hashlib.sha256(bytes(range(5))).hexdigest()] in root
    assert not (tmp_path / "work").exists()
    assert report["left"] == []


def test_the_client_configuration(tmp_path: Path) -> None:
    seen: dict[str, str] = {}
    store = FakeStore()

    def run(argv: Sequence[str], conf: Path | None) -> subprocess.CompletedProcess[str]:
        if conf is not None and not seen:
            seen["config"] = (conf / "config.yml").read_text()
            seen["pinned"] = (conf / "servercerts" / "store.crt").read_text()
        return store(argv, conf)

    server = tmp_path / "server.crt"
    server.write_text("server")
    guest.image_store(["bench"], "bench", "127.0.0.1:8443", server, run=run, work=tmp_path / "work")
    assert yaml.safe_load(seen["config"]) == {
        "default-remote": "store",
        "remotes": {
            "store": {
                "addr": "https://127.0.0.1:8443",
                "protocol": "incus",
                "auth_type": "tls",
                "project": "bench",
            }
        },
    }
    assert seen["pinned"] == "server"


def test_a_removal_that_hangs_skips_no_other(tmp_path: Path) -> None:
    store = FakeStore(hang="image delete " + "1" * 64)
    report = check(tmp_path, store)
    assert store.trust == {}  # the trust entry went although the first deletion hung
    assert "2" * 64 not in store.images
    assert not (tmp_path / "work").exists()
    problems = imagestore.compare(report, "build")
    assert any("root deletes the image 111111111111 failed" in p for p in problems), problems
    assert any("left behind: image 111111111111" in p for p in problems), problems


def test_an_upload_without_its_alias_is_removed(tmp_path: Path) -> None:
    store = FakeStore(alias_fails=True)
    report = check(tmp_path, store)
    assert store.images == {}  # found by its description
    problems = imagestore.compare(report, "build")
    assert any("the client imports an image with an alias failed" in p for p in problems)


def test_a_trust_entry_added_by_a_failed_step_is_removed(tmp_path: Path) -> None:
    store = FakeStore(trust_rc=1)
    report = check(tmp_path, store)
    assert store.trust == {}
    assert report["left"] == []


def test_a_trust_entry_of_an_earlier_run_is_removed(tmp_path: Path) -> None:
    store = FakeStore(trust={"ab" * 32: guest.STORE_NAME, "cd" * 32: "pinka-ctl"})
    report = check(tmp_path, store)
    assert store.trust == {"cd" * 32: "pinka-ctl"}  # found by its name; others stay
    assert report["left"] == []


def test_a_trust_entry_that_stays_is_left_behind(tmp_path: Path) -> None:
    store = FakeStore(remove_rc=1)
    report = check(tmp_path, store)
    fingerprint = hashlib.sha256(bytes(range(5))).hexdigest()[:12]
    assert report["left"] == [f"trust entry {fingerprint}"]
    problems = imagestore.compare(report, "build")
    assert any(f"root removes the trust entry {fingerprint} failed" in p for p in problems)


def test_a_listing_that_fails_is_never_an_empty_store(tmp_path: Path) -> None:
    # From the cleanup's listing on: nothing is read as removed.
    report = check(tmp_path, FakeStore(list_fails=True))
    failed = [
        s["name"] for s in report["steps"] if s["rc"] != 0 and s["name"].startswith("cleanup:")
    ]
    assert failed == ["cleanup: root lists the images"]
    assert "the images (no list)" in report["left"]
    assert any("root lists the images failed" in p for p in imagestore.compare(report, "build"))


def test_the_check_ends_within_the_stage(tmp_path: Path) -> None:
    store = FakeStore()
    check(tmp_path, store)
    # Even if every call hung until its limit, cleanup included.
    assert len(store.calls) * guest.CALL_TIMEOUT < imagestore.STAGE_TIMEOUT
    assert len(store.calls) < 40


def test_the_check_cleans_up_when_an_error_escapes(tmp_path: Path) -> None:
    class Broken(FakeStore):
        def __call__(
            self, argv: Sequence[str], conf: Path | None
        ) -> subprocess.CompletedProcess[str]:
            if list(argv)[:3] == ["image", "alias", "create"]:
                raise RuntimeError("simulated")
            return super().__call__(argv, conf)

    store = Broken()
    with pytest.raises(RuntimeError, match="simulated"):
        check(tmp_path, store)
    assert store.images == {} and store.trust == {}
    assert not (tmp_path / "work").exists()


def test_a_trust_entry_that_was_never_added_is_not_removed(tmp_path: Path) -> None:
    class Untrusted(FakeStore):
        def trust_command(self, args: list[str]) -> subprocess.CompletedProcess[str]:
            if args[2] == "add-certificate":
                return self.done(args, 1, err="Error: simulated")
            return super().trust_command(args)

    report = check(tmp_path, Untrusted())
    assert not any("trust entry" in s["name"] for s in report["steps"])
    assert report["left"] == []


def test_a_step_that_hangs_is_a_failed_step(tmp_path: Path) -> None:
    store = FakeStore(hang="alias delete")
    report = check(tmp_path, store)
    hung = [s["name"] for s in report["steps"] if s["rc"] == -1]
    assert hung == ["the client deletes the golden alias", "the client deletes its own alias"]
    assert store.images == {} and store.trust == {}


def test_the_tiny_image(tmp_path: Path) -> None:
    path = tmp_path / "image.tar.gz"
    guest.tiny_image(path, "pinka-test-images golden")
    with tarfile.open(path) as tar:
        names = tar.getnames()
        meta = yaml.safe_load(tar.extractfile("metadata.yaml").read())  # type: ignore[union-attr]
        assert tar.getmember("rootfs").isdir()
    assert names == ["metadata.yaml", "rootfs"]
    assert meta["properties"] == {"description": "pinka-test-images golden"}
    assert isinstance(meta["creation_date"], int) and meta["architecture"]


def test_the_command_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[Any] = []

    def fake(projects: list[str], project: str, address: str, server_cert: Path) -> dict[str, Any]:
        seen.append((projects, project, address, server_cert))
        return {"steps": []}

    monkeypatch.setattr(guest, "image_store", fake)
    code = guest.main(
        [
            "image-store",
            "--projects",
            "bench,build",
            "--project",
            "build",
            "--address",
            "a:1",
            "--server-cert",
            "/x",
        ]
    )
    assert code == 0
    assert seen == [(["bench", "build"], "build", "a:1", Path("/x"))]
    assert json.loads(capsys.readouterr().out) == {"ok": True, "steps": []}


def test_the_cli_runs_as_root_or_as_the_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append((argv, kwargs["env"]))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    for name in guest.INCUS_ENV:
        monkeypatch.setenv(name, "/elsewhere")
    guest.incus(["image", "list"], None)
    guest.incus(["image", "list"], tmp_path)
    (root_argv, root_env), (client_argv, client_env) = seen
    assert root_argv == client_argv == [guest.INCUS, "image", "list"]
    assert set(guest.INCUS_ENV) == {
        "INCUS_CONF",
        "INCUS_PROJECT",
        "INCUS_REMOTE",
        "INCUS_SOCKET",
        "INCUS_DIR",
    }
    assert not set(guest.INCUS_ENV) & set(root_env)
    assert client_env["INCUS_CONF"] == str(tmp_path)
    assert not (set(guest.INCUS_ENV) - {"INCUS_CONF"}) & set(client_env)
