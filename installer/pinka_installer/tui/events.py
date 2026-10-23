"""The install progress from the event stream (BUILD_PROMPT §18.4 screen 16, §18.5).

Bootstrap writes its stage, signal and result events and the ``pinka_events``
callback its play, role and task events to one JSON-lines file. A
:class:`Progress` folds them into the phase tree the Install screen shows. It
is pure, so a recorded ``events.jsonl`` replays it in the tests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

# Bootstrap stages before and after the play (bootstrap/lib/stages.sh).
STAGES_BEFORE = ("git", "prepreflight", "tooling", "frontend", "preflight", "ansible-env")
STAGES_AFTER = ("restore", "doctor")
LOGIN_URL_RE = re.compile(r"https://login\.tailscale\.com/\S+")
# The tailscale role's task that ends the interactive login (roles/tailscale/tasks/join.yml).
LOGIN_DONE_TASK = "Wait for the login to complete"
DEPLOY_KEY_RE = re.compile(r"(ssh-ed25519 [A-Za-z0-9+/=]+(?: \S+)?)")
ICONS = {
    "pending": "·",
    "running": "▸",
    "ok": "✔",
    "changed": "✔",
    "skipped": "-",
    "failed": "✖",
    "signaled": "⏸",
}


@dataclass
class Phase:
    """One line of the phase tree: a bootstrap stage or an Ansible role.

    Attributes:
        name: Stage or role name.
        kind: ``stage`` or ``role``.
        status: ``pending``, ``running``, ``ok``, ``failed``, ``skipped`` or ``signaled``.
        ok: Tasks that succeeded.
        changed: Tasks that changed something.
        failed: Tasks that failed.
        skipped: Tasks that were skipped.
    """

    name: str
    kind: str
    status: str = "pending"
    ok: int = 0
    changed: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def icon(self) -> str:
        """The status icon."""
        return ICONS.get(self.status, "?")


@dataclass
class Failure:
    """The task that failed, for the failure panel.

    Attributes:
        phase: Role or stage.
        task: Task name.
        message: Its message.
        stderr: The end of its error output.
    """

    phase: str
    task: str
    message: str
    stderr: str = ""


@dataclass
class Progress:
    """The state of one installation run.

    Attributes:
        phases: The phase tree, in order.
        current_task: The running task.
        messages: Messages of debug tasks (shown in the log pane).
        warnings: Ansible warnings.
        failure: The first failed task, if any.
        signal: Bootstrap signal (code, message), if the run stopped for one.
        result: Bootstrap's result name and exit code, once it ended.
        login_url: The Tailscale login URL, while the login waits.
        deploy_key: A new read-only deploy key to add to the Git host.
        log: Every line for the log pane.
    """

    phases: dict[str, Phase] = field(default_factory=dict)
    current_task: str = ""
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failure: Failure | None = None
    signal: tuple[int, str] | None = None
    result: tuple[str, int] | None = None
    login_url: str = ""
    deploy_key: str = ""
    log: list[str] = field(default_factory=list)
    _role: str = ""

    @classmethod
    def planned(cls, roles: Iterable[str], restore: bool = False, provider: str = "") -> Progress:
        """The tree before the run: every stage and role pending.

        Args:
            roles: Roles of site.yml, in order.
            restore: Whether the restore stage runs.
            provider: The host's provider (``wsl2``, ...), which names the role that
                site.yml includes as ``provider_{{ pinka_provider }}``.

        Returns:
            The progress.
        """
        progress = cls()
        for stage in STAGES_BEFORE:
            progress.phases[stage] = Phase(stage, "stage")
        for role in roles:
            if provider and role.startswith("provider_{{"):
                role = f"provider_{provider}"  # noqa: PLW2901 - the resolved name
            if role not in progress.phases:
                progress.phases[role] = Phase(role, "role")
        for stage in STAGES_AFTER:
            if stage != "restore" or restore:
                progress.phases[stage] = Phase(stage, "stage")
        return progress

    @property
    def totals(self) -> dict[str, int]:
        """ok, changed, failed and skipped tasks over all roles."""
        roles = [p for p in self.phases.values() if p.kind == "role"]
        return {
            k: sum(getattr(p, k) for p in roles) for k in ("ok", "changed", "failed", "skipped")
        }

    @property
    def finished(self) -> bool:
        """Whether bootstrap ended."""
        return self.result is not None

    def _phase(self, name: str, kind: str) -> Phase:
        if name not in self.phases:
            self.phases[name] = Phase(name, kind)
        return self.phases[name]

    def apply(self, event: dict[str, Any]) -> None:
        """Fold one event in.

        Args:
            event: A parsed event line.
        """
        kind = str(event.get("type", ""))
        handler = {
            "bootstrap.stage": self._stage,
            "bootstrap.signal": self._signal,
            "bootstrap.result": self._result,
            "ansible.role_start": self._role_start,
            "ansible.task_start": self._task_start,
            "ansible.task_end": self._task_end,
            "ansible.message": self._message,
            "ansible.warning": self._warning,
        }.get(kind)
        if handler is not None:
            handler(event)

    def _stage(self, event: dict[str, Any]) -> None:
        name, status = str(event.get("stage", "")), str(event.get("status", ""))
        if name == "ansible":
            if status != "started" and self._role:
                self._close_role(failed=status == "failed")
            return
        phase = self._phase(name, "stage")
        phase.status = {"started": "running", "ok": "ok"}.get(status, "failed")
        self.log.append(f"stage {name}: {status}")

    def _signal(self, event: dict[str, Any]) -> None:
        self.signal = (int(event.get("exit_code", 0)), str(event.get("message", "")))
        if self._role:
            self.phases[self._role].status = "signaled"
        self.log.append(f"signal {self.signal[0]}: {self.signal[1]}")

    def _result(self, event: dict[str, Any]) -> None:
        self.result = (str(event.get("status", "")), int(event.get("exit_code", 0)))
        self.current_task = ""
        self.login_url = ""

    def _close_role(self, failed: bool = False) -> None:
        phase = self.phases[self._role]
        if phase.status == "running":
            phase.status = "failed" if failed or phase.failed else "ok"
        self._role = ""

    def _role_start(self, event: dict[str, Any]) -> None:
        role = str(event.get("role", ""))
        if role.startswith("provider_") and role not in self.phases:
            # site.yml names the provider role through a variable
            planned = next((p for p in self.phases if p.startswith("provider_{{")), None)
            if planned:
                self.phases = {(role if k == planned else k): v for k, v in self.phases.items()}
                self.phases[role].name = role
        if role not in self.phases and self._role:
            # a helper role (pkg, compose, bootstrap_signal, ...) inside the current one
            return
        if self._role and self._role != role:
            self._close_role()
        self._role = role
        self._phase(role, "role").status = "running"

    def _task_start(self, event: dict[str, Any]) -> None:
        role = str(event.get("role", ""))
        if role and role != self._role:
            self._role_start({"role": role})
        self.current_task = str(event.get("task", ""))

    def _task_end(self, event: dict[str, Any]) -> None:
        status = str(event.get("status", ""))
        phase = self.phases.get(self._role)
        if phase is not None:
            if status in ("ok", "ignored"):
                phase.ok += 1
            elif status == "changed":
                phase.ok += 1
                phase.changed += 1
            elif status == "skipped":
                phase.skipped += 1
            elif status in ("failed", "unreachable"):
                phase.failed += 1
        task = str(event.get("task", ""))
        if self.login_url and task == LOGIN_DONE_TASK and status in ("ok", "changed"):
            self.login_url = ""
        if status in ("failed", "unreachable") and self.failure is None:
            self.failure = Failure(
                self._role or "ansible",
                str(event.get("task", "")),
                str(event.get("message", "")),
                str(event.get("stderr", "")),
            )
            self.log.append(f"FAILED {event.get('task')}: {event.get('message', '')}")

    def _message(self, event: dict[str, Any]) -> None:
        text = str(event.get("message", ""))
        self.messages.append(text)
        self.log.append(text)
        url = LOGIN_URL_RE.search(text)
        if url:
            self.login_url = url.group(0)
        if "Deploy key of this host" in text:
            key = DEPLOY_KEY_RE.search(text)
            self.deploy_key = key.group(1) if key else ""

    def _warning(self, event: dict[str, Any]) -> None:
        text = str(event.get("message", ""))
        self.warnings.append(text)
        self.log.append(f"warning: {text}")


def site_roles(site: Path) -> list[str]:
    """The roles site.yml applies, in order (the planned phase tree).

    Args:
        site: ``ansible/site.yml``.

    Returns:
        Role names; the provider role keeps its templated name until it starts.
    """
    roles: list[str] = []
    for play in YAML(typ="safe").load(site.read_text(encoding="utf-8")) or []:
        for section in ("pre_tasks", "tasks", "post_tasks"):
            for task in play.get(section) or []:
                for module in ("ansible.builtin.import_role", "ansible.builtin.include_role"):
                    if module in task:
                        name = str(task[module]["name"])
                        if name not in roles:
                            roles.append(name)
    return roles


def read_new(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read the complete event lines appended since ``offset``.

    Args:
        path: The events file.
        offset: Bytes already read.

    Returns:
        The new events and the new offset (a partial last line is left for later).
    """
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except FileNotFoundError:
        return [], offset
    end = data.rfind(b"\n") + 1
    found = []
    for line in data[:end].splitlines():
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found, offset + end
