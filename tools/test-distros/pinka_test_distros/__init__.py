"""Distribution tests of pokus-infrastructure: ``make test-distros`` (BUILD_PROMPT §19.9).

Launches one disposable Incus VM per test target, copies the repository in,
runs bootstrap from the CI answers file, runs it a second time (which must
change nothing), backs up and runs ``pinka doctor --smoke``, collects the
logs and the host profile, and records the result in
``tests/distros/results.yml``. Explicitly invoked on a developer machine or a
lab host whose lab is stopped; never part of ``make test`` or CI.
"""

__version__ = "0.1.0"
