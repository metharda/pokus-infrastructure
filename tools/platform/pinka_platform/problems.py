"""The findings of a validation run."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Problem:
    """One reason to refuse a manifest.

    Attributes:
        code: Stable identifier of the rule (tests and the docs use it).
        where: Location in the manifest or the release, for example ``services[1].profile``.
        message: What is wrong, for the operator.
    """

    code: str
    where: str
    message: str

    def __str__(self) -> str:
        """Render the problem as one line.

        Returns:
            ``<where>: <message> [<code>]``.
        """
        return f"{self.where}: {self.message} [{self.code}]"


class ManifestError(Exception):
    """The manifest could not be read at all (size, encoding, YAML)."""

    def __init__(self, problem: Problem) -> None:
        """Wrap a problem.

        Args:
            problem: The reason.
        """
        super().__init__(str(problem))
        self.problem = problem
