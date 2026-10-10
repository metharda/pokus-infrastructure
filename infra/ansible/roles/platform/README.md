# platform

Generic platform deployment (BUILD_PROMPT §7.14, contract v2); tag
`platform`. At most one platform per lab, only on `full` and `lab` nodes, and
only when the host variables set a platform source (`platform`). The role
names no platform: everything comes from the release's
`platform-manifest.yaml`, the profiles in `contract/` and the host settings.

The steps run in the deployment tool `pinka-platform` (`tools/platform`,
installed below `/usr/local/lib/pinka/platform` with the published contract,
command `/usr/local/libexec/pinka/pinka-platform`):

1. **Sources**: `platform` and `content` (`{repo, ref, path, tag_prefix}`).
   SSH sources use a read-only deploy key generated on the host
   (`/var/lib/pinka/git/deploy_key`, shown once to add to the Git host) and
   the pinned host keys (GitHub's from `versions.lock.yml`, others from
   `pinka_source_known_hosts`); anonymous HTTPS and root-owned `file:///`
   repositories need neither. Signed tags are checked when
   `require_signed_tags` is set.
2. **Fetch** into one root-only bare mirror per repository below
   `/var/lib/pinka/git` (0700).
3. **Stage** each source with `git archive <commit>:<path>` into a fresh run
   directory below `/var/lib/pinka/staging` (0700): no `.git`, no links that
   leave the tree.
4. **Validate** the sources (no overlapping paths in one repository), the
   content packages against the manifest, the manifest and the release
   (contract v2), and the one-platform rule. A refusal prints every reason,
   deletes the run directory and ends bootstrap with exit code 65; nothing
   else on the host changed. `--inspect-platform` stops here.
5. **Build**: `python-uv` releases run `uv sync --frozen` as `pinka-build` in
   a `systemd-run` sandbox that may write only the release and the uv cache;
   the release is bound at its final path, so the environment's absolute
   paths are right. A build that changes a file of the release is refused.
   Training job images are pulled by digest. Optional tools of the manifest
   come from infra's pinned list (Quarto).
6. **Publish** read-only: the release to
   `/srv/pinka/platforms/<platform>/releases/<name>/` (root, 0755/0644),
   restricted content packages only to
   `/srv/pinka/restricted/<platform>/content/<package>/<name>/` (root:
   `pinka-restricted`, 0750/0640), other content to
   `/srv/pinka/platforms/<platform>/content/<package>/<name>/`, each with
   `DEPLOYED_FROM.json`. `<name>` is the release tag, or the full commit of a
   branch or commit.
7. **Replace** (`--replace-platform <old>` only): the old platform's units,
   socket directories and CLI link go, its accounts are locked, its database
   roles become `NOLOGIN` and its Incus certificates leave the trust store.
   Its releases, state, restricted area and database stay until `pinka
   platform purge <old>`.
8. **Switch**: the `current` links, `/etc/pinka/platform/active.json` and the
   deployment record in `/var/lib/pinka/platforms/<platform>/`.

Rendering from the profiles (accounts, groups, units, sockets, database
roles, Incus certificates, OPA, dashboards, the CLI link) and the hooks of
the switch follow in plan steps 3d.3 and 3d.4. Until then the contract v1
unit template and its helpers (`templates/potka-platform.service.j2`,
`files/pinka_socket_perms.py`, `files/pinka_platform_config_check.py`) stay in
the role with their tests, unused.

`tasks_from: rotate_signing` (used by `rotate.yml --tags signing`) replaces the
signing key and publishes the new public key; old ones stay.

## Variables

See `meta/argument_specs.yml` and the source settings in
`group_vars/all/main.yml` (`platform`, `content`, `pinka_source_known_hosts`,
`require_signed_tags`, `pinka_allowed_signers`). Bootstrap sets
`platform_replace` (`--replace-platform`) and `platform_inspect`
(`--inspect-platform`).

## Feature flags

None; the role runs where the platform runs (`full` and `lab` nodes).

## Idempotency

A published release or content package is never extracted, built or
published again (a moved tag is refused), job images already present with
their digest are not pulled, and the switch and the records change only when
the deployment does.
