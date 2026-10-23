"""Another attempt for a transport error or a malformed answer, and only for those.

Every path that downloads Ansible collections (``make setup``, ``make
lock-collections``, ``make lock-check``'s collection artifacts, ``make
release-package``; bootstrap from a checkout applies the same rule in
``bootstrap/lib/tooling.sh``) runs each request through :class:`Policy`
(owner decision after ``fc7cc3b``):

* a transport error (:class:`pinka_lockcheck.net.FetchError` with
  ``transient``: no connection, a timeout, a connection that broke off, HTTP
  408, 429 or 5xx) or a malformed answer (:class:`MalformedAnswerError`: not JSON, or
  without a field the protocol promises) is tried again, up to 3 attempts
  with an increasing delay;
* every attempt is logged, with the error of a failed one;
* anything else stops at once. A hash or signature mismatch is never
  retried: the caller checks the content after the download and stops with
  the expected and the actual value.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from pinka_lockcheck import net

T = TypeVar("T")
TRIES = 3
DELAYS = (5.0, 15.0)  # seconds before the second and the third attempt


class MalformedAnswerError(RuntimeError):
    """An answer that is not what the server's protocol promises (retried)."""


def transient(exc: BaseException) -> bool:
    """Whether an error is worth another attempt.

    Args:
        exc: The error of one attempt.

    Returns:
        True for a transport error or a malformed answer.
    """
    if isinstance(exc, MalformedAnswerError):
        return True
    return isinstance(exc, net.FetchError) and exc.transient


def _stderr(line: str) -> None:
    sys.stderr.write(f"{line}\n")
    sys.stderr.flush()


@dataclass(frozen=True)
class Policy:
    """How often and how long to try.

    Attributes:
        tries: Attempts in all.
        delays: Seconds before each attempt after the first (the last one
            repeats if there are more attempts than delays).
        sleep: Waits (tests replace it).
        log: Writes one log line (standard error by default).
    """

    tries: int = TRIES
    delays: tuple[float, ...] = DELAYS
    sleep: Callable[[float], None] = time.sleep
    log: Callable[[str], None] = _stderr

    def run(self, action: Callable[[], T], what: str) -> T:
        """Run ``action``, again after a transport error or a malformed answer.

        Args:
            action: One attempt.
            what: What it does, for the log (``GET <url>``).

        Returns:
            The result of the first attempt that succeeds.

        Raises:
            Exception: The error of an attempt that is not :func:`transient`,
                at once, or of the last attempt.
        """
        attempt = 1
        while True:
            self.log(f"{what}: attempt {attempt} of {self.tries}")
            try:
                return action()
            except Exception as exc:
                if not transient(exc):
                    raise
                if attempt >= self.tries:
                    self.log(f"{what}: attempt {attempt} of {self.tries} failed: {exc}; none left")
                    raise
                delay = self.delays[min(attempt, len(self.delays)) - 1]
                self.log(
                    f"{what}: attempt {attempt} of {self.tries} failed: {exc};"
                    f" next attempt in {delay:g} s"
                )
                self.sleep(delay)
                attempt += 1


DEFAULT = Policy()
