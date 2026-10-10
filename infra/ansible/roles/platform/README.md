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
   (contract v2), the services' configuration (`platform_settings`) against
   their schemas in the release, and the one-platform rule. A refusal prints
   every reason, deletes the run directory and ends bootstrap with exit code
   65; a vault key the release needs but the vault lacks ends it with 77
   (the summary lists them). Nothing else on the host changed.
   `--inspect-platform` stops here.
5. **Build**: `python-uv` releases run `uv sync --frozen` as `pinka-build` in
   two `systemd-run` sandboxes that may write only the release and the uv
   cache: first with network, installing only the locked wheels without
   building or compiling anything (no package code runs), then without
   network, building the release with the backend from its `build`
   dependency group and compiling the bytecode. The release is bound at its
   final path, so the environment's absolute paths are right. A build that
   changes a file of the release, or leaves a device file, FIFO, socket or a
   setuid or setgid bit, is refused. Training job images are pulled by
   digest. Optional tools of the manifest come from infra's pinned list
   (Quarto).
6. **Publish** read-only: the release to
   `/srv/pinka/platforms/<platform>/releases/<name>/` (root, 0755/0644),
   restricted content packages only to
   `/srv/pinka/restricted/<platform>/content/<package>/<name>/` (root:
   `pinka-restricted`, 0750/0640), other content to
   `/srv/pinka/platforms/<platform>/content/<package>/<name>/`, each with
   `DEPLOYED_FROM.json`. `<name>` is the release tag, or the full commit of a
   branch or commit.
7. **Replace** (`--replace-platform <old>` only): the old platform's units
   and sockets, socket directories, configuration files, CLI link and
   wrapper go (from what was rendered for it, `rendered.json`), its accounts
   are locked, its database roles become `NOLOGIN` and its Incus
   certificates leave the trust store. Its releases, state, restricted area
   and database stay until `pinka platform purge <old>`.
8. **Render** (`tasks/render.yml`): `pinka-platform deploy render` computes
   everything the profiles grant, and the role applies it with ordinary
   modules:
   - groups (the database socket group `<platform>-db`, one client group per
     socket) and system accounts `<platform>-<service>`,
     `<platform>-<job>` and `<platform>-migrate` with exactly the groups
     their profiles allow (the tool refuses any membership §12.4 forbids:
     the agent and training jobs never join `pinka-restricted`, the
     database socket group or `pinka-query`);
   - directories (state 0700 per service, secrets 0750 `root:<account>`, the
     signing directory, the QA directories), the services' configuration
     (`/etc/pinka/platform/config/<service>.json`, 0640 `root:<account>`) and
     secrets from the vault (`vault_platform_secrets`, 0640);
   - one hardened service unit per service (the baseline of §7.14 in spec
     order, the profile's network rules, the agent's empty lab directories
     with only its allowlist bound in) and one socket unit per socket
     (`<platform>-<service>-<socket>.socket`: owner the serving account,
     group the client group, 0660, passed to the service by socket
     activation under the socket's name, POSIX ACL entries for `pinka` and
     `pinka-admin` where humans may connect), grouped in
     `pinka-platform.target`; installed, not started (the switch with the
     hooks follows in plan step 3d.4); units of the previous release that
     the new one lacks are stopped and removed;
   - `principals.json` and `endpoints.toml` (`deploy principals`, once the
     accounts exist), the signing key, the Incus certificates, the database
     (roles, schemas, passwords, the closed `restricted` schema and its
     check, through the postgres role), the run-network helper's socket
     owner (the controller), and the CLI wrapper
     `/usr/local/lib/pinka/platform/cli/<cli>` with its link in
     `/usr/local/bin`.
   The roles after this one (`opa`, `observability`, `vllm`, `backup`,
   `doctor`) read the same rendering from `/etc/pinka/platform/rendered.json`
   through the `active_platform` role.
9. **Switch**: the `current` links, `/etc/pinka/platform/active.json`,
   `/etc/pinka/platform/rendered.json` (without the configuration contents)
   and the deployment record in `/var/lib/pinka/platforms/<platform>/`.

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
