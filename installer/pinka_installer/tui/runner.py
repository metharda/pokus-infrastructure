"""Running the installation, or uninstall, from the TUI (BUILD_PROMPT §5.4, §18.1, §18.4).

The TUI is a front-end only: it runs ``bootstrap.sh`` again, non-interactively
and without the TUI, exactly as an ``--answers`` run would after the files are
written. The vault password reaches it through a 0600 file in the run-time
directory (tmpfs) that is removed when the run ends; the one-time MOK password
(``--mok-password-stdin``) through its standard input only. Progress comes from the
event stream (``PINKA_EVENTS_FILE``); the human-readable output feeds the log
pane, and the full log stays in ``/var/log/pinka-bootstrap/``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pinka_installer.paths import Paths
from pinka_installer.tui.events import read_new

LOG_PATH_RE = re.compile(r"log: (/var/log/pinka-bootstrap/\S+\.log)")
POLL_SECONDS = 0.25


@dataclass
class RunRequest:
    """One bootstrap run.

    Attributes:
        machine: Machine name.
        options: Further bootstrap options (overrides, ``--restore``, tags).
        vault_password: The vault password (never written anywhere but the 0600 handover file).
        mok_password: The one-time MOK password, written to bootstrap's standard
            input (with ``--mok-password-stdin`` among the options).
    """

    machine: str
    options: list[str] = field(default_factory=list)
    vault_password: str = field(default="", repr=False)
    mok_password: str = field(default="", repr=False)

    def argv(self, bootstrap: Path, password_file: Path) -> list[str]:
        """The bootstrap command line.

        Args:
            bootstrap: ``bootstrap/bootstrap.sh``.
            password_file: The handover file.

        Returns:
            The command.
        """
        return [
            str(bootstrap),
            "--machine",
            self.machine,
            "--non-interactive",
            "--no-tui",
            "--vault-password-file",
            str(password_file),
            *self.options,
        ]


@dataclass
class UninstallRun:
    """One ``bootstrap.sh --uninstall --json`` run (BUILD_PROMPT §5.4).

    Attributes:
        code: Bootstrap's exit code (0, 80, 81, ...).
        result: The JSON it printed: the plan (a dry run, or a run that stopped
            before removing anything) or the report; empty when none.
    """

    code: int
    result: dict[str, Any]


class Runner(Protocol):
    """Runs bootstrap and reports its events and output."""

    def run(
        self,
        request: RunRequest,
        on_events: Callable[[list[dict[str, Any]]], None],
        on_output: Callable[[str], None],
    ) -> int:
        """Run until bootstrap ends.

        Args:
            request: The run.
            on_events: Called with new events.
            on_output: Called with each output line.
        """
        ...

    def cancel(self) -> None:
        """Stop a running bootstrap (it is idempotent; running it again continues)."""
        ...

    def uninstall(self, options: Sequence[str], on_output: Callable[[str], None]) -> UninstallRun:
        """Run ``bootstrap.sh --uninstall`` non-interactively with ``--json``.

        Args:
            options: Uninstall's options (``--dry-run``, ``--confirm-machine``, ...).
            on_output: Called with each line of bootstrap's log.
        """
        ...


class BootstrapRunner:
    """Runs ``bootstrap.sh`` on this host."""

    def __init__(self, paths: Paths) -> None:
        """Bind to the checkout.

        Args:
            paths: Installer paths.
        """
        self.paths = paths
        self.log_file = ""
        self._process: subprocess.Popen[str] | None = None

    def run(  # noqa: D102
        self,
        request: RunRequest,
        on_events: Callable[[list[dict[str, Any]]], None],
        on_output: Callable[[str], None],
    ) -> int:
        run_dir = self.paths.run_dir
        run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        events = self.paths.events_file
        events.unlink(missing_ok=True)
        password = run_dir / "vault-pass"
        fd = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(request.vault_password + "\n")
        try:
            return self._run(
                request.argv(self.paths.bootstrap, password),
                events,
                on_events,
                on_output,
                stdin=request.mok_password + "\n" if request.mok_password else None,
            )
        finally:
            password.unlink(missing_ok=True)

    def _run(
        self,
        argv: Sequence[str],
        events: Path,
        on_events: Callable[[list[dict[str, Any]]], None],
        on_output: Callable[[str], None],
        stdin: str | None = None,
    ) -> int:
        env = {**os.environ, "PINKA_EVENTS_FILE": str(events)}
        process = subprocess.Popen(  # noqa: S603 - bootstrap with options the TUI built
            list(argv),
            stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=self.paths.infra,
            start_new_session=True,  # cancel() stops bootstrap and Ansible together
        )
        self._process = process
        if stdin is not None and process.stdin is not None:
            # bootstrap reads its first line before anything else (a pipe buffer holds it)
            with contextlib.suppress(BrokenPipeError):
                process.stdin.write(stdin)
            with contextlib.suppress(BrokenPipeError):
                process.stdin.close()
        reader = threading.Thread(target=self._read, args=(process, on_output), daemon=True)
        reader.start()
        offset = 0
        while True:
            found, offset = read_new(events, offset)
            if found:
                on_events(found)
            if process.poll() is not None:
                found, offset = read_new(events, offset)
                if found:
                    on_events(found)
                break
            time.sleep(POLL_SECONDS)
        reader.join(timeout=5)
        self._process = None
        return int(process.returncode)

    def uninstall(  # noqa: D102
        self, options: Sequence[str], on_output: Callable[[str], None]
    ) -> UninstallRun:
        argv = [
            str(self.paths.bootstrap),
            "--uninstall",
            "--non-interactive",
            "--no-tui",
            "--json",
            *options,
        ]
        # The log comes on standard error, the JSON alone on standard output.
        process = subprocess.Popen(  # noqa: S603 - bootstrap with options the TUI built
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.paths.infra,
            start_new_session=True,
        )
        self._process = process
        reader = threading.Thread(
            target=self._read, args=(process, on_output, "stderr"), daemon=True
        )
        reader.start()
        assert process.stdout is not None  # noqa: S101 - set by Popen(stdout=PIPE)
        text = process.stdout.read()
        process.wait()
        reader.join(timeout=5)
        self._process = None
        try:
            result = json.loads(text) if text.strip() else {}
        except ValueError:
            result = {}
        return UninstallRun(int(process.returncode), result if isinstance(result, dict) else {})

    def cancel(self) -> None:  # noqa: D102
        process = self._process
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)

    def _read(
        self,
        process: subprocess.Popen[str],
        on_output: Callable[[str], None],
        stream: str = "stdout",
    ) -> None:
        source = process.stdout if stream == "stdout" else process.stderr
        assert source is not None  # noqa: S101 - set by Popen(stdout=PIPE, stderr=PIPE)
        for line in source:
            text = line.rstrip("\n")
            match = LOG_PATH_RE.search(text)
            if match and not self.log_file:
                self.log_file = match.group(1)
            on_output(text)
