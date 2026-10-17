"""Generators for pokus-infrastructure (BUILD_PROMPT §2.3, §11; DOCS_SPEC §A.3, §A.6).

``python -m pinka_gen generate`` writes every generated file;
``check`` regenerates in memory and fails if a committed file differs (the
docs drift check); ``coverage`` verifies that every CLI option, exit code and
schema field has a reference entry.

Generated files:

* ``preflight/support-matrix.json`` and ``preflight/endpoints.json`` (the
  preflight evaluator reads only JSON; owner answer E10), and
  ``preflight/lab-defaults.json`` (the default ports and addresses preflight
  checks, from group_vars);
* ``docs/support-matrix.md``;
* ``docs/reference/cli/pinka-preflight.md``;
* ``docs/reference/exit-codes.md``;
* ``docs/reference/host-profile.md``;
* ``docs/reference/cli/pinka.md`` and ``docs/reference/doctor-checks.md``
  (from the pinka parser and check registry).
"""

__version__ = "0.1.0"
