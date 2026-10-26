"""Tests for the privacy scanner.

Values that the scanner must flag are assembled at runtime, so this file itself
passes the privacy scan.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from pinka_repo_checks import privacy

PLAIN = Path("docs/page.md")

# Assembled so the literals never appear in the repository.
PUBLIC_V4 = ".".join(["8", "8", "4", "4"])
PUBLIC_V6 = ":".join(["2606", "4700", "", "1111"])
FOREIGN_EMAIL = "someone" + "@" + "mail-provider" + ".dev"
LOCAL_MAC = ":".join(["02", "42", "ac", "11", "00", "02"])
RANDOM_UUID = str(uuid.UUID(int=0x1234_5678_9ABC))
SSH_KEY = "ssh-ed25519 " + "AAAA" + "C3NzaC1lZDI1NTE5" + "A" * 24
PRIVATE_KEY = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
PGP_KEY = "-----BEGIN PGP " + "PUBLIC KEY BLOCK-----"
TS_KEY = "tskey" + "-auth-" + "k" * 20
# Commit identities: a GitHub noreply address and GitHub's own address.
NOREPLY = "1" + "+researcher1@" + privacy.GITHUB_NOREPLY_DOMAIN
GITHUB_OWN = "@".join(privacy.GITHUB_OWN_ADDRESS)


def rules(text: str, path: Path = PLAIN, denylist: tuple[str, ...] = ()) -> list[str]:
    return [f.rule for f in privacy.scan_text(path, text, denylist)]


@pytest.mark.parametrize(
    "text",
    [
        "researcher1@example.com",
        "ops@lab.example.org",
        "x@host.test",
        "git clone git@github.com:example-org/pokus-platform.git",
        "PubkeyAcceptedAlgorithms sk-ssh-ed25519@openssh.com",
        "uses: actions/checkout@v4",
        "npm install cspell@10.3.6",
    ],
)
def test_allowed_email_like_text(text: str) -> None:
    assert rules(text) == []


def test_foreign_email_is_flagged() -> None:
    assert rules(f"contact {FOREIGN_EMAIL} today") == ["email"]


@pytest.mark.parametrize(
    "text",
    [
        "listen 127.0.0.1:4317",
        "pool 10.0.0.0/8 and 172.30.0.0/16 and 192.168.0.0/16",
        "tailnet 100.64.0.0/10",
        "docs 192.0.2.10 198.51.100.7 203.0.113.0/24",
        "bind 0.0.0.0",
        "kernel 6.6.87.2-microsoft-standard-WSL2",
        "version 1.2.3",
        "localhost ::1 and fe80::1 and fd00::5 and 2001:db8::42",
        "time 2026-10-05T09:12:44Z",
    ],
)
def test_allowed_addresses(text: str) -> None:
    assert rules(text) == []


def test_public_ipv4_is_flagged() -> None:
    assert rules(f"nameserver {PUBLIC_V4}") == ["public-ip"]


def test_public_ipv4_network_is_flagged() -> None:
    assert rules(f"allow {PUBLIC_V4}/32") == ["public-ip"]


def test_public_ipv6_is_flagged() -> None:
    assert rules(f"addr {PUBLIC_V6}") == ["public-ip"]


def test_documentation_mac_is_allowed() -> None:
    assert rules("hwaddr 00:00:5e:00:53:01 and 00-00-5E-00-53-FF") == []


def test_other_mac_is_flagged() -> None:
    assert rules(f"hwaddr {LOCAL_MAC}") == ["mac"]


def test_nil_uuid_is_allowed() -> None:
    assert rules("product_uuid 00000000-0000-0000-0000-000000000000") == []


def test_non_nil_uuid_is_flagged() -> None:
    assert rules(f"product_uuid {RANDOM_UUID}") == ["uuid"]


@pytest.mark.parametrize("material", [SSH_KEY, PRIVATE_KEY, PGP_KEY])
def test_key_material_is_flagged_outside_fixtures(material: str) -> None:
    assert rules(material) == ["key-material"]


@pytest.mark.parametrize(
    "path",
    [
        Path("preflight/tests/fixtures/test-keys/id_ed25519.pub"),
        Path("installer/tests/test-keys/sample.pub"),
    ],
)
def test_key_material_is_allowed_in_test_key_fixtures(path: Path) -> None:
    assert rules(SSH_KEY, path) == []


def test_test_keys_must_be_below_tests() -> None:
    assert rules(SSH_KEY, Path("test-keys/tests/x.pub")) == ["key-material"]


def test_tailscale_auth_key_is_flagged() -> None:
    assert rules(f"authkey: {TS_KEY}") == ["tailscale-auth-key"]


def test_placeholder_auth_key_is_allowed() -> None:
    assert rules("authkey: tskey-auth-<key>") == []


def test_denylist_is_case_insensitive_and_redacted() -> None:
    findings = privacy.scan_text(PLAIN, "Lab of Somebody", ["somebody"])
    assert [f.rule for f in findings] == ["denylist"]
    assert "somebody" not in findings[0].message.lower()
    assert findings[0].value == "<redacted>"


def test_denylist_matches_whole_words_only() -> None:
    assert rules("hash bCAsomebodyX0 and somebodys", denylist=("somebody",)) == []
    assert rules("by Somebody-Else", denylist=("somebody",)) == ["denylist"]
    assert rules("owner: example-org/team", denylist=("example-org",)) == ["denylist"]


def test_load_denylist_splits_on_newlines_and_commas() -> None:
    env = {privacy.DENYLIST_ENV: " Alpha ,beta\n\n gamma "}
    assert privacy.load_denylist(env) == ["alpha", "beta", "gamma"]


def test_load_denylist_empty_by_default() -> None:
    assert privacy.load_denylist({}) == []


def test_run_reports_without_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(privacy.DENYLIST_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clean.md").write_text("researcher1@example.com\n", encoding="utf-8")
    (tmp_path / "dirty.md").write_text(f"dns {PUBLIC_V4}\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\0" + PUBLIC_V4.encode())
    assert privacy.run([tmp_path]) == 1
    out = capsys.readouterr().out
    assert "dirty.md:1:5: public-ip" in out
    assert PUBLIC_V4 not in out
    assert "binary.bin" not in out


def test_run_show_matches_prints_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(privacy.DENYLIST_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dirty.md").write_text(f"dns {PUBLIC_V4}\n", encoding="utf-8")
    assert privacy.run([tmp_path], show_matches=True) == 1
    assert PUBLIC_V4 in capsys.readouterr().out


def test_run_clean_tree_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(privacy.DENYLIST_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.md").write_text("bind 127.0.0.1\n", encoding="utf-8")
    assert privacy.run([tmp_path]) == 0


# Assembled so the attribution lines never appear in this file as such.
TRAILER = "Co-" + "Authored-By: researcher1 <researcher1@example.com>"
SESSION = "Agent-" + "Session: https://example.com/session"
GENERATED = "\N{ROBOT FACE} " + "Generated" + " with [a tool](https://example.com)"


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    identity = ("-c", "user.name=researcher1", "-c", f"user.email={NOREPLY}")
    return subprocess.run(
        ["git", *identity, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    ).stdout.strip()


def commit(repo: Path, message: str, *, author: str = NOREPLY, committer: str = NOREPLY) -> str:
    (repo / "file.txt").write_text(message, encoding="utf-8")
    git(repo, "add", "file.txt")
    env = {"GIT_AUTHOR_EMAIL": author, "GIT_COMMITTER_EMAIL": committer}
    git(repo, "commit", "-q", "--allow-empty", "-m", message, env=env)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(privacy.DENYLIST_ENV, raising=False)
    monkeypatch.delenv(privacy.CI_ENV, raising=False)
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    monkeypatch.chdir(path)
    return path


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        (TRAILER, "co-authored-by"),
        (TRAILER.lower(), "co-authored-by"),
        (SESSION, "session-trailer"),
        (SESSION.upper(), "session-trailer"),
        ("  Tool-" + "Session : 1", "session-trailer"),
        ("X-" + "Session: abc", "session-trailer"),
        (GENERATED, "generated-with"),
        ("Generated by a tool", "generated-with"),
    ],
)
def test_attribution_lines_are_found(line: str, rule: str) -> None:
    message = f"feat: a change\n\nBody text.\n\n{line}\n"
    assert [(f.line, f.rule) for f in privacy.scan_message("0" * 40, message)] == [(5, rule)]


@pytest.mark.parametrize(
    "word", ["Agent", "Assistant", "Bot", "Editor", "Model", "Pair", "X", "Some-Tool", "tool2"]
)
def test_a_session_trailer_of_any_name_is_found(word: str) -> None:
    # The pattern names no tool: whatever word comes before "-Session:", the
    # line is an attribution line.
    message = f"fix: a change\n\n{word}-" + "Session: https://example.com/s/1\n"
    assert [(f.line, f.rule) for f in privacy.scan_message("0" * 40, message)] == [
        (3, "session-trailer")
    ]


@pytest.mark.parametrize(
    "message",
    [
        "docs: the reference pages are generated with make generate",
        "feat: name the co-author in the notes\n\nThe co-authored-by field is gone.",
        "fix: keep the session record",
        "fix: the lab session: it starts after the health gate",
        "feat: lab_session: a record per session",
        "docs: Session: the lab's working period",
        "fix: the agent-session socket\n\nThe agent-session socket is gone.",
        "fix: an option\n\n-" + "Session: x\n--no-" + "session: skip it",
        "fix: names\n\nx.y_z-" + "session: 1\nfix(agent-" + "session): x",
    ],
)
def test_ordinary_messages_pass(message: str) -> None:
    assert privacy.scan_message("0" * 40, message) == []


def test_history_with_an_attribution_line_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, "feat: first")
    bad = commit(repo, f"feat: second\n\n{TRAILER}")
    commit(repo, "feat: third")
    assert privacy.run([repo]) == 1
    captured = capsys.readouterr()
    assert f"commit {bad[:12]}:3: co-authored-by" in captured.out
    assert "researcher1" not in captured.out
    assert "history of 3 commits, 1 findings, 0 known exceptions" in captured.err


def test_known_exceptions_are_allowed(repo: Path) -> None:
    known = commit(repo, f"feat: first\n\n{TRAILER}\n{SESSION}")
    other = commit(repo, f"feat: second\n\n{GENERATED}")
    history = privacy.read_history()
    assert history is not None
    findings, seen = privacy.scan_history(history, frozenset({known}))
    assert seen == 1
    assert [(f.commit, f.rule) for f in findings] == [(other, "generated-with")]


def test_no_commit_is_a_known_exception_since_the_rewrite() -> None:
    # The owner's one-time rewrite removed the only two trailers.
    assert not privacy.KNOWN_ATTRIBUTION_COMMITS


def test_a_known_commit_excuses_only_its_attribution_lines(repo: Path) -> None:
    # The trailer may name an address outside the example domains (as the
    # two known commits do); any other line of the same message is checked.
    trailer = "Co-" + f"Authored-By: a tool <{FOREIGN_EMAIL}>"
    known = commit(repo, f"feat: first\n\nWrite to {FOREIGN_EMAIL}.\n\n{trailer}")
    history = privacy.read_history()
    assert history is not None
    findings, seen = privacy.scan_history(history, frozenset({known}))
    assert seen == 1
    assert [(f.commit, f.line, f.rule) for f in findings] == [(known, 3, "email")]


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        (f"Reported by {FOREIGN_EMAIL}", "email"),
        ("Reported by someone-1", "denylist"),
    ],
)
def test_a_commit_message_is_scanned_for_addresses_and_denylist_terms(line: str, rule: str) -> None:
    message = f"fix: a change\n\n{line}\n"
    findings = privacy.scan_message("0" * 40, message, ("someone-1",))
    assert [(f.line, f.rule) for f in findings] == [(3, rule)]
    if rule == "denylist":
        assert findings[0].value == "<redacted>"


def test_example_addresses_in_a_commit_message_pass() -> None:
    message = "docs: write to researcher1@example.com\n\nOr ops@lab.example.org.\n"
    assert privacy.scan_message("0" * 40, message, ("someone-1",)) == []


@pytest.mark.parametrize(
    ("address", "allowed"),
    [
        (NOREPLY, True),
        ("researcher1@" + privacy.GITHUB_NOREPLY_DOMAIN, True),
        ("41898282+github-actions[bot]@" + privacy.GITHUB_NOREPLY_DOMAIN, True),
        (GITHUB_OWN, True),
        (GITHUB_OWN.upper(), True),
        (FOREIGN_EMAIL, False),
        ("researcher1@example.com", False),
        ("-researcher1@" + privacy.GITHUB_NOREPLY_DOMAIN, False),
        ("1+researcher1@sub." + privacy.GITHUB_NOREPLY_DOMAIN, False),
        (GITHUB_OWN + ".example", False),
        ("a@b@" + privacy.GITHUB_NOREPLY_DOMAIN, False),
        ("researcher1", False),
        ("", False),
    ],
)
def test_the_identities_github_allows(address: str, allowed: bool) -> None:
    assert privacy.is_github_noreply(address) is allowed


def test_a_commit_with_another_identity_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, "feat: first")
    by_author = commit(repo, "feat: second", author=FOREIGN_EMAIL)
    by_committer = commit(repo, "feat: third", committer="researcher1@example.com")
    commit(repo, "feat: merged on github.com", committer=GITHUB_OWN)
    assert privacy.run([repo]) == 1
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        f"commit {by_committer[:12]}: committer-identity: committer e-mail is neither "
        "a GitHub noreply address nor GitHub's own",
        f"commit {by_author[:12]}: author-identity: author e-mail is neither "
        "a GitHub noreply address nor GitHub's own",
    ]
    assert "history of 4 commits, 2 findings, 0 known exceptions" in captured.err
    assert privacy.run([repo], show_matches=True) == 1
    assert f"[{FOREIGN_EMAIL}]" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("ci", "denylist", "warned"),
    [("true", "", True), ("true", "someone-1", False), (None, "", False)],
)
def test_ci_warns_when_it_scans_without_the_denylist(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    ci: str | None,
    denylist: str,
    warned: bool,
) -> None:
    commit(repo, "feat: first")
    if ci is None:
        monkeypatch.delenv(privacy.CI_ENV, raising=False)
    else:
        monkeypatch.setenv(privacy.CI_ENV, ci)
    monkeypatch.setenv(privacy.DENYLIST_ENV, denylist)
    assert privacy.run([repo]) == 0
    out = capsys.readouterr().out
    assert out.startswith("::warning title=privacy scan::") is warned
    assert "someone-1" not in out


def test_clean_history_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(repo, "feat: first")
    assert privacy.run([repo]) == 0
    assert "history of 1 commits, 0 findings" in capsys.readouterr().err


def test_shallow_clone_fails(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commit(repo, "feat: first")
    commit(repo, "feat: second")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo}", str(clone))
    monkeypatch.chdir(clone)
    history = privacy.read_history()
    assert history is not None
    assert history.shallow
    findings, _ = privacy.scan_history(history)
    assert [f.rule for f in findings] == ["shallow-history"]


def test_empty_repository_passes(repo: Path) -> None:
    history = privacy.read_history()
    assert history == privacy.History([], shallow=False)
    assert privacy.scan_history(history) == ([], 0)


def test_outside_git_the_history_is_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(privacy.DENYLIST_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    assert privacy.read_history() is None
    assert privacy.run([tmp_path]) == 0
    assert "history not checked" in capsys.readouterr().err
