"""The retry policy of the collection downloads, without waiting, for the tests."""

from __future__ import annotations

from pinka_lockcheck import retry


def quick(log: list[str] | None = None, waits: list[float] | None = None) -> retry.Policy:
    """The default attempts and delays, with the waits and the log lines recorded.

    Args:
        log: Receives every log line (dropped when None).
        waits: Receives every delay instead of sleeping (dropped when None).

    Returns:
        The policy.
    """
    lines = log if log is not None else []
    slept = waits if waits is not None else []
    return retry.Policy(sleep=slept.append, log=lines.append)
