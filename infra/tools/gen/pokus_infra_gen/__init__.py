"""Generators for pokus-infra (BUILD_PROMPT §2.3, §11; DOCS_SPEC §A.3, §A.6).

``python -m pokus_infra_gen generate`` writes every generated file;
``check`` regenerates in memory and fails if a committed file differs (the
docs drift check); ``coverage`` verifies that every CLI option, exit code and
schema field has a reference entry.

Generated files:

* ``preflight/support-matrix.json`` and ``preflight/endpoints.json`` (the
  preflight evaluator reads only JSON; owner answer E10);
* ``docs/support-matrix.md``;
* ``docs/reference/cli/pokus-preflight.md``;
* ``docs/reference/exit-codes.md``;
* ``docs/reference/host-profile.md``;
* ``docs/reference/cli/pokus-lab.md`` and ``docs/reference/doctor-checks.md``
  (from the pokus-lab parser and check registry).
"""

__version__ = "0.1.0"
