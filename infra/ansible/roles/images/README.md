# images

Golden image pipeline (BUILD_PROMPT §7.13, `docs/contracts.md`, golden
images), applied on `full` and `lab` nodes; tag `images`, skippable.

- Input: the blueprints `images/<blueprint>/` (`image.yaml`, schema
  `tools/lockcheck/schema/image-blueprint.schema.json`; `cloud-init.yaml`;
  `provision.yml`; `files/`; `tests/`) and the pins of `versions.lock.yml`:
  `incus_base_images` (the upstream base by fingerprint per architecture),
  `binaries.otelcol_contrib` (the in-image agent package) and
  `containers.target_images` (container images that blueprints with a
  container engine pull by digest).
- Every listed blueprint that has neither the alias `<blueprint>/<version>`
  nor an export is built in the Incus project `build` (profile `build`,
  network `pokus-build0`), as instance `build-<blueprint>`:
  1. launch from the base (the pinned upstream fingerprint, or the image of
     the base blueprint), with the blueprint's `nesting` and cloud-init user
     data, and wait for cloud-init;
  2. record the leakage baseline of the base (`files/pokus_leakage_lint.py
     scan`, run inside the instance with its own `python3`);
  3. run `provision.yml` and then `images/_base/agent.yml` (the agent
     configuration of the blueprint's `telemetry`) with a nested
     `ansible-playbook` over the `community.general.incus` connection;
  4. run the blueprint's tests and record the installed packages;
  5. run the leakage lint (`check`): the build fails on every finding the
     build added that no allow rule covers;
  6. clean (`files/image-clean.sh`: package caches, shell histories, logs,
     cloud-init state, machine-id, SSH host keys, host name and the build's
     own files), stop, and `incus publish` with the alias and the properties
     `pokus.recipe_commit` (the repository commit, `-dirty` appended when the
     infra tree had changes), `pokus.built_at` and `pokus.base_fingerprint`,
     plus Incus's `requirements.nesting` (the blueprint's `nesting`) and
     `description` (a published image starts from its base's metadata, so
     these are always set rather than inherited);
  7. delete the instance. A failed build keeps it for inspection; the next
     run replaces it.
- The image is exported to `/srv/pokus/images/<blueprint>/<version>/`: the
  image files, `SHA256SUMS`, `packages.txt` and `build.json` (written last).
  The upstream base is exported once to
  `/srv/pokus/images/_upstream/<base>/<fingerprint>/` with `SHA256SUMS` and
  `source.json`, because public image servers rotate their images.
- `/usr/local/libexec/pokus/image-files verify` recomputes an export's
  fingerprint (the sha256 of the tarball, or of the metadata followed by the
  root file system) and writes `SHA256SUMS`.
- Build instances and images are created and deleted with the `incus` CLI;
  the Incus objects of the incus role (pool, networks, projects, profiles,
  ACLs) are only read.

## Leakage lint

`images_leakage_patterns` (default: `[A-Z]+-[0-9]{3}`, and `fault`,
`inject`, `ground_truth` and `scenario` in any case) are searched in the
names and contents of the files below `images_leakage_paths`, in unit names
and the host name; any `.git` directory is a finding too. A finding is the
whole token around a match (letters, digits, `_`, `-`). Findings of the base
before provisioning (the baseline) do not count. An allow rule names a benign
word (`match`, a regular expression, optionally limited to the paths that
`path` matches); it is cut out of the token, and the token is allowed only if
no pattern matches what is left: `DefaultLimitCORE` is allowed by
`(?i)default`, `default_fault` is not.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

None; applied on `node_role` `full` and `lab` in `site.yml`.

## Idempotency

A blueprint whose alias and `build.json` name the same fingerprint is not
touched. A missing alias is restored by importing the export (or by pointing
the alias at the image when the store still has it), a missing or different
export by exporting the image again; only a blueprint with neither is built.
`images_rebuild: true` builds the listed blueprints again on every run.
