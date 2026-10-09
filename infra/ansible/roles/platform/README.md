# platform

The platform services, deployed from the root-only mirror of the pokus-platform
repository (BUILD_PROMPT §7.14, contracts.md); tag `platform`. Off by default
(`platform_enabled: false`), and only on `full` and `lab` nodes.

With `platform_enabled: true` and `platform_ref` set, the role:

1. fetches `pinka_source_repo` and extracts `platform/` at that ref into
   `/srv/pinka/deploy/platform/<ref>/` with `DEPLOYED_FROM.json` (see the
   `deploy_source` role);
2. creates `<ref>/.venv` with `uv sync --frozen --all-packages --no-dev
   --no-editable` and the pinned CPython, and checks that every console script
   of the contract exists;
3. writes `/etc/pinka/platform/endpoints.toml` (contracts.md,
   "Configuration") and one `<svc>.toml` per service from `platform_settings`,
   each checked against the release's `schemas/config/<svc>.schema.json` with
   the release's own interpreter before it is installed (C9b);
4. creates `/etc/pinka/secrets/<svc>/` (0750 root:`<account>`), the
   run-manifest signing key (ed25519 PKCS#8, 0400 potka-orch) and publishes
   its public key in `/srv/pinka/shared/signing-keys/<machine>-<date>.pub`
   (C9d), and the QA directories with `qa_trusted_keys`;
5. installs the pinned Quarto CLI (`/usr/local/bin/quarto`);
6. renders the eight units with the hardening baseline (`ProtectSystem=strict`,
   no capabilities, `UMask=0077`, `InaccessiblePaths`; the agent runner also
   loses the catalog, artifacts, secrets, Incus, signing and other services'
   sockets) and `ExecStartPost=+/usr/local/libexec/pinka/socket-perms`, which
   gives each socket the contract's owner, group, mode and ACL (C8) once the
   service has created it; without ACL support on `/run` the contract's
   fallback groups apply. The expectations doctor checks are written to
   `/etc/pinka/platform/sockets.json`;
7. runs `potka-migrate upgrade` from the new release when its commit differs
   from the last migrated one (C9f);
8. points `current` at the release, links `/usr/local/bin/potka` (C9g) and
   restarts the running platform units at the end of the play.

All units are `WantedBy` and `PartOf` `pinka-lab.target` and start only after
the health gate. The evaluator runs without `MemoryDenyWriteExecute`, because
Quarto's Deno and Typst generate code at run time.

`tasks_from: rotate_signing` (used by `rotate.yml --tags signing`) replaces the
signing key and publishes the new public key; old ones stay.

## Variables

See `meta/argument_specs.yml` and the source settings in
`group_vars/all/main.yml` (`pinka_source_repo`, `require_signed_tags`,
`pinka_allowed_signers`, `inference_remote_node`, `qa_trusted_keys`).

## Feature flags

`gpu_inference` on a `full` node selects the local inference backend; a `lab`
node uses its paired inference node.

## Idempotency

A deployed ref is not extracted again, the environment is created once per
release, migrations run once per commit, configuration files are only
rewritten when their content changes, and units restart only when their
file, their configuration or the release changed.
