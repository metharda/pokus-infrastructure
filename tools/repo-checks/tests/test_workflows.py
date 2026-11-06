"""Every action a workflow uses is pinned to a full commit SHA, its version in a comment."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
PINNED_RE = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^\s+# v\d+\.\d+\.\d+$")


def test_every_action_is_pinned_to_a_commit_with_its_version() -> None:
    found = []
    for workflow in sorted(WORKFLOWS.glob("*.y*ml")):
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = USES_RE.match(line)
            if match is None:
                continue
            where = f"{workflow.name}:{number}: {line.strip()}"
            found.append(where)
            # A local action or a container would be pinned another way; none is used.
            assert PINNED_RE.match(match.group("ref")), where
            assert VERSION_RE.match(match.group("rest")), where
    assert found  # the workflows use actions, and every one was checked


def test_lock_check_runs_the_markers_and_every_other_check_as_steps_of_their_own() -> None:
    job = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))["jobs"]["lock-check"]
    steps = [step for step in job["steps"] if "run" in step]
    assert [step["run"] for step in steps] == [
        "make setup",
        "make lock-check-markers",
        "make lock-check-without-markers",
    ]
    # The other checks run also when the marker step failed, so no error and no
    # warning hides behind the markers; neither step may fail without failing
    # the job.
    assert steps[2]["if"] == "${{ !cancelled() }}"
    assert "if" not in steps[1]
    assert all("continue-on-error" not in step for step in job["steps"])
    assert "continue-on-error" not in job
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "\t$(LOCKCHECK) markers --allowed tools/lockcheck/marker-count\n" in makefile
    assert "\t$(LOCKCHECK) check --skip-markers $(if $(GITHUB_STEP_SUMMARY)," in makefile


def test_only_the_scans_get_the_denylist_and_they_check_a_pull_requests_own_commits() -> None:
    # The secret reaches only
    # the two jobs that scan with it, and both check out a pull request's head,
    # whose commits are its author's, not GitHub's test merge commit.
    secret = "${{ secrets.PINKA_PRIVACY_DENYLIST }}"  # noqa: S105 - the secret's name, no value
    holders = []
    for workflow in sorted(WORKFLOWS.glob("*.y*ml")):
        text = workflow.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        assert "PINKA_PRIVACY_DENYLIST" not in (data.get("env") or {}), workflow.name
        for name, job in data["jobs"].items():
            if "PINKA_PRIVACY_DENYLIST" in (job.get("env") or {}):
                holders.append((workflow.name, name))
                assert job["env"]["PINKA_PRIVACY_DENYLIST"] == secret
                checkout = job["steps"][0]
                assert checkout["uses"].startswith("actions/checkout@")
                assert checkout["with"]["fetch-depth"] == 0
                assert checkout["with"]["ref"] == (
                    "${{ github.event.pull_request.head.sha || github.sha }}"
                )
            for step in job.get("steps", []):
                step_env = step.get("env") or {}
                assert "PINKA_PRIVACY_DENYLIST" not in step_env, (workflow.name, name)
        assert text.count(secret) == sum(1 for w, _ in holders if w == workflow.name), workflow.name
    assert holders == [("ci.yml", "lint"), ("ci.yml", "privacy")]
