"""One real CI run per commit.

Every commit pushed to a branch of this repository runs once, on its push,
whether or not a pull request is open; a pull request runs the jobs only
when it comes from a fork. The required check of a pull request's head
commit always comes from a real run of that commit: in the pull request run
of a branch of this repository the gate job is skipped under another name,
and in a real run it always runs. The workflows' own expressions are
evaluated here for each kind of event.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
REPO = "example-org/pokus-infrastructure"
FORK = "researcher1/pokus-infrastructure"
REQUIRED = "required"
# The workflows that test commits; release.yml runs on tags only.
COMMIT_WORKFLOWS = (
    "ci.yml",
    "lock-collections.yml",
    "isolated.yml",
    "check-mode-extended.yml",
    "release-package.yml",
)
TOKEN = re.compile(
    r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<op>==|!=|&&|\|\||!|\(|\))"
    r"|(?P<call>always\(\))|(?P<name>[A-Za-z_][\w.-]*))"
)


def workflow(name: str) -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data


def lookup(context: dict[str, Any], dotted: str) -> Any:
    value: Any = context
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def evaluate(expression: str, context: dict[str, Any]) -> Any:
    """A GitHub expression of the forms the workflows use: literals, lookups,
    ``==``, ``!=``, ``!``, ``&&``, ``||`` (returning an operand, as GitHub
    does), parentheses and ``always()``."""
    parts: list[str] = []
    values: list[Any] = []
    position = 0
    text = expression.strip()
    while position < len(text):
        match = TOKEN.match(text, position)
        assert match, f"cannot read {text[position:]!r}"
        position = match.end()
        if match["str"]:
            values.append(match["str"][1:-1].replace("''", "'"))
            parts.append(f"V[{len(values) - 1}]")
        elif match["call"]:
            parts.append("True")
        elif match["name"]:
            values.append(lookup(context, match["name"]))
            parts.append(f"V[{len(values) - 1}]")
        else:
            parts.append({"&&": " and ", "||": " or ", "!": " not "}.get(match["op"], match["op"]))
    return eval("".join(parts), {"__builtins__": {}}, {"V": values})  # noqa: S307 - test input


def render(template: str, context: dict[str, Any]) -> str:
    """A value with ``${{ }}`` expressions, as GitHub renders it."""
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: str(evaluate(m[1], context)), template)


def condition(job: dict[str, Any], context: dict[str, Any]) -> bool:
    """Whether a job runs (a job without ``if`` runs; ``needs`` assumed passed)."""
    return bool(evaluate(str(job["if"]), context)) if "if" in job else True


def event(kind: str, ref: str = "refs/heads/moving/on", sha: str = "a" * 40) -> dict[str, Any]:
    """The ``github`` context of an event of this repository."""
    github: dict[str, Any] = {
        "event_name": kind,
        "ref": ref,
        "sha": sha,
        "repository": REPO,
        "workflow": "infra-ci",
        "event": {},
    }
    if kind in ("pull_request", "pull_request_fork"):
        github["event_name"] = "pull_request"
        github["ref"] = "refs/pull/7/merge"
        head = REPO if kind == "pull_request" else FORK
        github["event"] = {"pull_request": {"head": {"repo": {"full_name": head}}}}
    return {"github": github, "needs": {"changes": {"outputs": {"ansible": "true"}}}}


REAL = {
    "push to moving/on": event("push"),
    "push to main": event("push", "refs/heads/main"),
    "push to another branch": event("push", "refs/heads/feature-1"),
    "a fork's pull request": event("pull_request_fork"),
    "a manual run": event("workflow_dispatch"),
}


@pytest.mark.parametrize("name", COMMIT_WORKFLOWS)
def test_every_commit_of_a_branch_runs_once_on_its_push(name: str) -> None:
    data = workflow(name)
    on = data[True]  # YAML 1.1 reads the key "on" as True
    # Every branch, never a tag (release.yml tests a tag); a fork's pull
    # request watches the same paths as a push.
    assert on["push"]["branches"] == ["**"], name
    assert "tags" not in on["push"], name
    assert "pull_request" in on, name
    assert (on["pull_request"] or {}).get("paths") == on["push"].get("paths"), name
    same_repo_pr = event("pull_request")
    for job_name, job in data["jobs"].items():
        assert not condition(job, same_repo_pr), f"{name}: {job_name} runs twice"
        if job_name == "links":
            continue
        for kind, context in REAL.items():
            assert condition(job, context), f"{name}: {job_name} skipped for {kind}"


def test_the_required_check_comes_only_from_a_real_run() -> None:
    required = workflow("ci.yml")["jobs"]["required"]
    for kind, context in REAL.items():
        # A real run always runs the gate job under the required name, also
        # when the run is cancelled or a job failed (always()).
        assert condition(required, context), kind
        assert render(str(required["name"]), context) == REQUIRED, kind
        assert "always()" in str(required["if"])
    # Skipped (a skipped job counts as passing), it must not carry the name.
    for kind, context in {
        "a pull request of a branch of this repository": event("pull_request"),
        "the weekly run": event("schedule", "refs/heads/main"),
    }.items():
        assert not condition(required, context), kind
        assert render(str(required["name"]), context) != REQUIRED, kind
    # Its verdict fails on anything but success, cancelled and skipped jobs
    # included (check-mode may skip only for a change of docs).
    step = required["steps"][0]["run"]
    assert 'r not in allowed.get(n, {"success"})' in step


@pytest.mark.parametrize("name", COMMIT_WORKFLOWS)
def test_main_and_moving_on_never_cancel_a_commits_run(name: str) -> None:
    concurrency = workflow(name)["concurrency"]
    group = str(concurrency["group"])
    for branch in ("refs/heads/main", "refs/heads/moving/on"):
        one = render(group, event("push", branch, "a" * 40))
        two = render(group, event("push", branch, "b" * 40))
        assert one != two, (name, branch)  # each commit's run has a group of its own
    # A fork's pull request or another branch: a newer run replaces the older.
    fork = event("pull_request_fork")
    newer = {**fork, "github": {**fork["github"], "sha": "b" * 40}}
    assert render(group, fork) == render(group, newer)
    other = event("push", "refs/heads/feature-1")
    assert render(group, other) == render(group, event("push", "refs/heads/feature-1", "b" * 40))
    # Push, pull request and manual runs never share a group.
    assert render(group, event("push")) != render(group, event("workflow_dispatch"))
    assert concurrency["cancel-in-progress"] is True


def test_the_weekly_and_manual_runs_keep_working() -> None:
    for name in (*COMMIT_WORKFLOWS[1:], "ci.yml"):
        on = workflow(name)[True]
        assert "schedule" in on and "workflow_dispatch" in on, name
    weekly = event("schedule", "refs/heads/main")
    for name in COMMIT_WORKFLOWS[1:]:
        for job_name, job in workflow(name)["jobs"].items():
            assert condition(job, weekly), (name, job_name)
    links = workflow("ci.yml")["jobs"]["links"]
    assert condition(links, weekly) and condition(links, event("workflow_dispatch"))
    release = workflow("release.yml")[True]
    assert release == {"push": {"tags": ["v*"]}}


def test_the_evaluator_reads_github_expressions_as_github_does() -> None:
    context = event("push")
    assert evaluate("github.event_name == 'push' && 'x' || 'y'", context) == "x"
    assert evaluate("github.event_name != 'push' && 'x' || 'y'", context) == "y"
    assert evaluate("!(github.event_name == 'push')", context) is False
    assert evaluate("always() && github.ref == 'refs/heads/moving/on'", context) is True
    assert render("a-${{ github.sha }}", context) == "a-" + "a" * 40
