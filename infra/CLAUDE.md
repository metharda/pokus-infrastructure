# CLAUDE.md — pokus-infra

Read `../PROGRESS.md` (monorepo root) first, then `docs/BUILD_PROMPT.md` (the
binding build prompt), `docs/DOCS_SPEC.md` and `docs/contracts.md`. Approved
deviations from the build prompt are listed in `../PROGRESS.md`; they win over
the prompt text where they differ.

## Working rules (BUILD_PROMPT §0)

- Never invent version numbers, digests, checksums, package versions, URLs or
  key fingerprints. Verify them at build time and record them in
  `versions.lock.yml` with `verified_on` and `source`, or write `TODO(verify)`.
  `make lock-check` fails while any `TODO(verify)` remains and re-verifies
  every digest, checksum and key fingerprint; `make verify-todos` lists every
  marker in the repository; `docs/how-to/update-versions.md` is the procedure.
- Never provision the machine you work on: no `bootstrap.sh`, no
  `ansible-playbook` without `--check`, no `mkfs`/`wipefs`, no
  `incus admin init`, no `tailscale up`, nothing touching `/etc`, disks or
  network configuration. Linters, unit tests, `--syntax-check` and preflight
  with `--root <fixture>` are fine.
- Idempotency: a second full bootstrap reports `changed=0`; volatile tasks are
  tagged `volatile` and documented.
- Documentation is written alongside the code in every step and follows
  `docs/DOCS_SPEC.md`: front matter, one task per page, `qa:run`/`qa:skip`
  markers, generated references, strict build, coverage and drift checks.
- `make lint test docs` must pass before a step is proposed for commit.
- Never commit, push or tag on your own; the owner's process in
  `../PROGRESS.md` applies.

## Preflight gate (BUILD_PROMPT §2)

- Preflight (`preflight/pokus_preflight/`) is Python standard library only and
  source-compatible with Python 3.9. Every filesystem and command access goes
  through the `fs`/`runner` abstractions so `--root <fixture>` works.
- Decisions are data-driven from `preflight/support-matrix.yml` (compiled to
  committed, drift-checked JSON). No hard-coded `if` chains.
- WSL detection runs before container classification; `wsl` is never a container.
- Exit codes: 0 supported, 10 degraded, 20 unsupported, 30 needs_action,
  40 preflight_error. Bootstrap adds 75 (WSL restart), 76 (reboot/MOK),
  77 (operator action), passes doctor's 0/1/2 through, and (proposed,
  ADR-0002) 11, 50, 60, 64, 70.
- Storage must be `btrfs` or LVM thin; no `dir` backend.
- The `preflight_gate` role fails if outcome, provider or features differ from
  `/etc/pokus/host-profile.json` without `--refresh-profile`.

## Safety invariants (BUILD_PROMPT §13)

1. Nothing is formatted, wiped or repartitioned unless
   `incus_pool_source: device`, `incus_pool_device_confirm` equals
   `incus_pool_device` exactly, and the device is unmounted and carries no
   signature (or `incus_pool_device_force_wipe: true`); interactive
   confirmation unless non-interactive with all variables set.
2. Never install a Linux NVIDIA kernel driver on WSL; never modify the Windows
   side; nothing in the core calls Windows executables.
3. Never enable Tailscale Funnel; lab services bind only to loopback, Unix
   sockets or internal Docker networks (sshd excepted, firewalled).
4. Humans are never added to `docker` or `incus-admin` implicitly; the agent
   account never gets Docker, Incus, catalog, artifacts or secrets access.
5. Secrets are never committed in plaintext, never logged (`no_log: true`),
   never baked into images.
6. Every external artifact is pinned and verified; no `latest`; no `curl | bash`.
7. No automatic reboots without `--allow-reboot`; no automatic kernel or
   driver upgrades.
8. Idempotent: the second run reports `changed=0`.
9. Never flush or rewrite firewall tables owned by Docker, Incus, firewalld or ufw.
10. `lab_mode: staging` hosts have their own vault, `/srv/pokus`, Tailscale
    identity (`tag:pokus-qa`) and signing key, and never pair with or write to
    a production host. QA trusted keys go to `/etc/pokus/qa/trusted-keys/`;
    attestations to `/etc/pokus/qa/attestations/`.
11. Every override (`--force-unsupported`, `--accept-degraded`,
    `--accept-experimental`, `--allow-dirty`, break-glass groups) is recorded
    in the host profile and the environment fingerprint.
12. **Privacy:** no personal or installation-specific data anywhere in the
    repository (names, usernames, e-mail addresses, organizations, hostnames,
    domains, IP/MAC addresses, serials, UUIDs, SSH keys, Tailscale
    identities). Use `researcher1`, `example-org`, `example.com`, the
    documentation IP ranges, `00:00:5e:00:53:00/24` MACs and zeroed serials.
    Real deployment data lives only in the deployer's private fork and is
    excluded by `.gitignore`. `make privacy` (part of `make lint`) enforces it.

## Generated files and fixtures

- Never edit generated files by hand: `preflight/support-matrix.json`,
  `preflight/endpoints.json`, `docs/support-matrix.md`,
  `docs/reference/{cli/pokus-preflight,cli/bootstrap,exit-codes,host-profile}.md`,
  `docs/reference/{versions,ansible-variables,component-sources,accounts}.md`,
  the layout table between the `generated:layout` markers of
  `docs/architecture.md`, `bootstrap/lib/pins.sh`,
  `bootstrap/requirements.in`, `ansible/requirements.yml` and
  `ansible/group_vars/all/versions.yml` come from `tools/gen`
  (`make generate`); `make docs` fails on drift or missing reference entries.
  Every top-level key of `group_vars/all/main.yml` needs a comment (above it or
  at the end of its line): it becomes the key's reference text.
  `bootstrap/requirements.lock` is compiled with `make bootstrap-lock` (the
  drift check fails if it does not pin the versions of `requirements.in`).
- Bootstrap exit codes are defined once, in `bootstrap/lib/exitcodes.sh`
  (`readonly EXIT_NAME=<code> # <title>: <meaning>`); every code needs a
  section in `docs/how-to/troubleshoot.md`.
- Bootstrap tests never provision: `bootstrap.sh` runs only unprivileged or
  with every `stage_*` function stubbed (`bootstrap/tests/`).
- Preflight fixtures are built from `preflight/tests/fixture_defs.py`
  (synthetic layers only). Data copied from a real host must pass
  `preflight/tests/sanitize_fixture.py` and a manual review first.
- Summary snapshots change only with `POKUS_UPDATE_SNAPSHOTS=1`; review the diff.

## Monorepo specifics (see ../PROGRESS.md)

- Platform and catalog are deployed from a root-only bare mirror with
  `git archive` of one subtree; no `.git` and no catalog content ever appears
  in a deployed path outside `/srv/pokus/catalog` (B2).
- Bootstrap records the repository commit and the `infra/` tree hash; the
  dirty-tree check covers `infra/` only (B3).
- Release tags are `infra-vX.Y.Z` (B4).
- `docs/contracts.md` must stay byte-identical to `../platform/docs/contracts.md`.

## Ansible portability (BUILD_PROMPT §19)

- Only `platform_facts` interprets the distribution; every other role uses the
  `pokus_*` facts. `make lint-portability` fails on `ansible_distribution*`,
  `ansible_os_family`, `ansible_pkg_mgr` or `ansible_lsb` anywhere else.
- Distribution differences live only in the support matrix,
  `ansible/component-sources.yml`, `roles/<role>/vars/<family>.yml` and
  `roles/<role>/tasks/<name>_<family>.yml`, for all four families at once.
- Packages are installed, pinned and held only through the `pkg` role.
  Anything unverified in `component-sources.yml` carries
  `verify: "TODO(verify): ..."` and is refused by the play.
- `ansible/tests/` checks role structure, templates (rendered like the
  template module; nft rulesets with `nft -c` in a private network
  namespace) and data consistency; add a render test for every new template.
- Incus objects are only changed through the incus role's module
  `pokus_incus_object` (create or update, never delete); the pool and the
  networks are created once and never rewritten. Only the docker role writes
  `/etc/docker/daemon.json`.

## Code style (BUILD_PROMPT §14)

- Bash: `set -Eeuo pipefail`, `local` in functions, quoted expansions,
  `readonly` constants, one logging helper (`log_info`, `log_warn`,
  `log_error`) with UTC timestamps; ShellCheck- and shfmt-clean.
- Python: type hints everywhere, dataclasses for report structures, no global
  state, pure detection functions taking `(fs, runner)`; ruff and
  `mypy --strict` clean; Google-style docstrings.
- Ansible: FQCN modules, `name:` on every task, handlers for restarts,
  `block`/`rescue` for multi-step operations, no `ignore_errors: true` without
  a justification comment, role-prefixed variables, `meta/argument_specs.yml`
  and `README.md` for every role.
- Templates start with
  `# Managed by pokus-infra (<template path>). Local changes will be overwritten.`
- All timestamps UTC ISO 8601 with `Z`. Documentation in English.
