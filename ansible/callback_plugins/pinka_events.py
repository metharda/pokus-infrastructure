# Managed by pinka (ansible/callback_plugins/pinka_events.py). Local changes will be overwritten.
"""Ansible progress as JSON lines for the installer TUI.

Appends one JSON object per line to ``$PINKA_EVENTS_FILE`` (or writes to the
file descriptor ``$PINKA_EVENTS_FD``), the same stream bootstrap writes its
stage and signal events to: play start, role start, task start and end
(status, changed, duration), the messages of ``debug`` tasks (the Tailscale
login URL, a new deploy key), warnings, and failures with their message and a
truncated stderr. Without either variable it does nothing, so plain runs are
unaffected; the human-readable output still goes to the bootstrap log.

Secrets: task arguments are never emitted, and for a ``no_log`` task neither
its message nor its output, only its name and status.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import IO, Any

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
name: pinka_events
type: notification
short_description: JSON lines with the play's progress for the pinka installer TUI
description:
  - Writes play, role and task events to $PINKA_EVENTS_FILE or the file descriptor
    $PINKA_EVENTS_FD; does nothing when neither is set. Never emits task arguments,
    nor the message or output of no_log tasks.
requirements:
  - enabled in ansible.cfg (callbacks_enabled)
"""

MAX_MESSAGE = 1000
DEBUG_ACTIONS = frozenset({"debug", "ansible.builtin.debug", "ansible.legacy.debug"})
MAX_STDERR = 2000


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _task_name(task: Any) -> str:
    """The task's own name (``get_name()`` prefixes the role, which is a field of its own)."""
    return str(task.name or task.action).strip()


def _tail(text: object, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else "…" + value[-limit:]


class CallbackModule(CallbackBase):  # type: ignore[misc] # ansible ships no type hints
    """The ``pinka_events`` notification callback."""

    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "pinka_events"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, display: Any = None) -> None:
        """Open the event stream, if one is configured.

        Args:
            display: Ansible's display object.
        """
        super().__init__(display=display)
        self._stream: IO[str] | None = None
        self._role = ""
        self._started: dict[str, float] = {}
        fd = os.environ.get("PINKA_EVENTS_FD", "")
        path = os.environ.get("PINKA_EVENTS_FILE", "")
        if fd.isdigit():
            self._stream = os.fdopen(int(fd), "a", encoding="utf-8", closefd=False)
        elif path:
            self._stream = Path(path).open("a", encoding="utf-8")  # noqa: SIM115 - open for the run

    def _emit(self, kind: str, **fields: Any) -> None:
        if self._stream is None:
            return
        event = {"ts": _now(), "type": kind, **fields}
        self._stream.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        self._stream.flush()

    def v2_playbook_on_play_start(self, play: Any) -> None:
        """A play starts.

        Args:
            play: The play.
        """
        self._role = ""
        self._emit("ansible.play_start", play=str(play.get_name()).strip())

    def _task_start(self, task: Any, handler: bool) -> None:
        role = task._role.get_name() if getattr(task, "_role", None) else ""
        if role and role != self._role:
            self._emit("ansible.role_start", role=role)
        self._role = role or self._role
        self._started[str(task._uuid)] = time.monotonic()
        self._emit(
            "ansible.task_start",
            task=_task_name(task),
            role=role,
            handler=handler,
        )

    def v2_playbook_on_task_start(self, task: Any, is_conditional: bool) -> None:
        """A task starts.

        Args:
            task: The task.
            is_conditional: Unused.
        """
        self._task_start(task, handler=False)

    def v2_playbook_on_handler_task_start(self, task: Any) -> None:
        """A handler starts.

        Args:
            task: The handler.
        """
        self._task_start(task, handler=True)

    def _task_end(self, result: Any, status: str) -> None:
        task = result.task
        started = self._started.get(str(task._uuid))
        fields: dict[str, Any] = {
            "task": _task_name(task),
            "host": result.host.get_name(),
            "status": "changed" if status == "ok" and result.is_changed() else status,
            "changed": bool(result.is_changed()),
            "duration": round(time.monotonic() - started, 3) if started is not None else None,
        }
        hidden = bool(task.no_log) or bool(result.result.get("_ansible_no_log"))
        if status in ("failed", "ignored", "unreachable"):
            if hidden:
                fields["message"] = "the output is hidden (no_log)"
            else:
                fields["message"] = _tail(result.result.get("msg"), MAX_MESSAGE)
                fields["stderr"] = _tail(result.result.get("stderr"), MAX_STDERR)
        self._emit("ansible.task_end", **fields)
        if not hidden and status == "ok" and task.action in DEBUG_ACTIONS:
            message = result.result.get("msg")
            if message is not None:
                self._emit(
                    "ansible.message", task=fields["task"], message=_tail(message, MAX_MESSAGE)
                )
        if not hidden:
            for warning in result.warnings:
                self._emit("ansible.warning", task=fields["task"], message=warning.event.msg)

    def v2_runner_on_ok(self, result: Any) -> None:
        """A task succeeded on a host.

        Args:
            result: The result.
        """
        self._task_end(result, "ok")

    def v2_runner_on_failed(self, result: Any, ignore_errors: bool = False) -> None:
        """A task failed on a host.

        Args:
            result: The result.
            ignore_errors: The failure is ignored by the task.
        """
        self._task_end(result, "ignored" if ignore_errors else "failed")

    def v2_runner_on_skipped(self, result: Any) -> None:
        """A task was skipped on a host.

        Args:
            result: The result.
        """
        self._task_end(result, "skipped")

    def v2_runner_on_unreachable(self, result: Any) -> None:
        """A host is unreachable.

        Args:
            result: The result.
        """
        self._task_end(result, "unreachable")

    def v2_playbook_on_stats(self, stats: Any) -> None:
        """The play recap.

        Args:
            stats: Per-host counters.
        """
        for host in sorted(stats.processed):
            summary = stats.summarize(host)
            self._emit(
                "ansible.stats",
                host=host,
                **{
                    k: summary.get(k, 0)
                    for k in ("ok", "changed", "failures", "skipped", "unreachable")
                },
            )
