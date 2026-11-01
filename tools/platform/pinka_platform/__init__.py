"""Contract v2: the platform manifest validator and renderer.

The package validates a platform release's ``platform-manifest.yaml`` against
the contract published in ``contract/`` (schema, profiles, reserved names),
derives every name the manifest would create, checks the invariants of
contract v2 and renders the principals and endpoints files. It runs
with the pinned CPython of bootstrap's Ansible environment and needs only
PyYAML besides the standard library.
"""

from __future__ import annotations

CONTRACT_VERSION = 2
