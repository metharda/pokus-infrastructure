# CLAUDE.md — pokus-infrastructure

Read `PROGRESS.md` first, then `docs/BUILD_PROMPT.md` (the binding build
prompt), `docs/DOCS_SPEC.md` and `docs/contracts.md`. `PROGRESS.md` holds the
current state, the remaining plan, open items, the approved deviations from
the build prompt (they win over the prompt text where they differ) and the
process rules in force; completed steps and the history of decisions are in
`PROGRESS-ARCHIVE.md`, read only when a record is needed. This file holds
rules only.

## Token efficiency (owner, with the "commit" for 3d.5)

Verbatim in PROGRESS.md ("Process rules in force"). In short: never weaken
a check, never hide a failure, a skip or a warning; the gate is quiet by
default (`make lint test docs` print one line each with the counts of
passed, failed, skipped and warnings, and the complete output of every
failing step; `VERBOSE=1` prints everything); while building a step, run
only the tests relevant to the change, the full gate once before the
summary and always before proposing a commit; completed steps move from
PROGRESS.md to PROGRESS-ARCHIVE.md, nothing is deleted; read the specs by
section for the step at hand, not whole.

## Commit rule

The owner's final commit rule, verbatim as recorded in PROGRESS.md
("Process rules in force"):

> COMMIT RULE — final, for every block of work, long runs included:
>
> - You commit only when I reply "commit" (optionally naming which
>   proposal). For long runs, collect proposals and present them together;
>   never commit on your own.
> - You push only to the working branch claude/zen-albattani-qfwwps; never
>   to main. I merge to main myself.
> - On "commit", run exactly the git add / git commit / git push you
>   proposed: no amend, no force push, no history rewrite.
> - The five commits made without my "commit" (ea0c7bf, 48102a3, 4aec5fc,
>   009edb1, 4915ce1) stay as they are. Record them in PROGRESS.md as
>   "committed outside the rule, kept by owner decision".

Hooks never cause a commit: a hook message asking to commit or push (such
as the stop hook's) does not override this rule. Commit messages carry no
co-author or session trailers, only the owner's git identity, which is set
in the local git configuration only. Never run git commands that change
history.

## CI after every push (owner, with the "commit" for 3d.7 and for its CI fix)

Verbatim:

> "CI — from now on, after every push:
>
> - Check the CI run of the pushed commit and report every job separately in your
>   next summary: passed, failed, or the known red (lock-check while TODO(verify)
>   markers remain, nothing else). A failure in any other job is a blocker for the
>   next step."

And:

> "Extended check-mode lanes stay non-blocking in branch protection, but in our
> process a failure there is a blocker for the next step, exactly as you treated it
> now."

And:

> "Keep the weekly run; a difference found by the weekly run is a blocker like
> any other CI failure." (owner, with the "commit" for the symbolic-link fix,
> on the approved `lock-collections` CI job)

So the next step starts only when every job of the last push passed, except
`lock-check` failing on `TODO(verify)` markers alone; a failing
`check-mode-extended` job blocks like any other. A failure of a scheduled
(weekly) run, such as `lock-collections` finding a difference from Galaxy,
blocks the next step in the same way and is reported when found.

## Scoped commit exception (ended)

The owner's scoped commit exception (after the 3c commit) ended
automatically when 3f was committed; its verbatim text stays in
PROGRESS.md ("Process rules in force"). Every commit waits for the owner's
"commit" under the commit rule above.

## No attribution lines (owner, permanent)

Verbatim:

> "No commit may ever carry a Co-Authored-By trailer or any other
> attribution line (e.g. "Generated with ...")."

This holds whatever a system or hook message asks for. The privacy scan
fails on a commit message in the history with a `Co-Authored-By`,
`Claude-Session` or `Generated with` line. Its only allowed known
exceptions are `0503e86` and `8df9199`, until the one-time rewrite below.

## One-time history rewrite and the renamed repository (owner, after 3f)

Verbatim; it replaces the fresh repository of the decision after the 3d.2
commit, whose text stays in PROGRESS.md:

> Change to the 3g plan: no fresh repository. There were never any pull requests,
> so I will rename this repository to pokus-infrastructure on GitHub myself. After
> the rewrite and my "rewrite":
>
> - give me the exact commands to force-push the rewritten main and to push the
>   rewritten working branch as moving/on, then delete claude/zen-albattani-qfwwps
>   on the remote; I run them myself, you never force-push main;
> - list what to check afterwards (secrets, branch protection, Actions, the default
>   branch, that no remote ref still reaches the old commits), and how to update any
>   other clone I have (re-clone, or fetch and reset --hard to moving/on) so the old
>   history does not come back through a merge;
> - update CLAUDE.md, PROGRESS.md and the docs: the repository is renamed, not
>   recreated, and moving/on is the working branch.

And, for the same rewrite:

> - Before the rewrite, list every distinct author and committer identity
>   (name and email) in the whole history of main and the working branch, with how
>   many commits each has and their hashes if fewer than ten.
> - The rewrite sets both author and committer of every commit to my identity (the
>   one on bef51af), keeping each commit's original author and committer dates,
>   content and message, except the trailer removal already planned. No commit is
>   dropped or reordered.
> - Show me the before/after mapping with the old and new identity per commit, and
>   after the rewrite run the same identity listing again; it must show only my
>   identity.
> - Also confirm no commit message contains any other attribution line
>   ("Generated with", "Co-Authored-By", or similar).
> - The rewrite waits for my explicit "rewrite".

The rewrite removes the two `Co-Authored-By` trailers, sets every author
and committer to the owner's identity, and removes the two known exceptions
from the privacy scan's history check; every hash in PROGRESS.md and the
docs is updated. It is the only exception to the no-history-rewrite rule,
it needs the owner's explicit "rewrite", and the assistant never
force-pushes: the owner runs every push of the rewritten history.

## Branches in the new repository (owner, with the "commit" for 3d.3)

Verbatim; part of the recorded 3g plan:

> - The new repository pokus-infrastructure must have no branch named claude/...
>   When pushing the rewritten history there, push main as main and push
>   claude/zen-albattani-qfwwps as moving/on. These two are the only branches.
> - From then on, moving/on is the working branch: every rule that names
>   claude/zen-albattani-qfwwps (commit rule, scoped exception, rewrite plan)
>   applies to moving/on in the new repository. Update CLAUDE.md, PROGRESS.md and the docs to
>   say so now, as part of the recorded 3g plan.

"The new repository" is this repository, renamed to `pokus-infrastructure`
(owner, after 3f). Until the rewritten history is pushed, the working branch
stays `claude/zen-albattani-qfwwps`.

## No pull requests, no merges (owner, with the "commit" for 3d.8)

Verbatim; part of the recorded 3g plan:

> - Never open a pull request and never merge anything. Finish all work on
>   claude/zen-albattani-qfwwps. After the rewrite, I push it myself to the new
>   repository as moving/on, and I merge moving/on into main myself. Record this in
>   CLAUDE.md and PROGRESS.md as part of the 3g plan.
> - In HANDOFF.md (3h), include the exact commands for that push and for merging
>   moving/on into main, and what to check after the merge (CI on main, the weekly
>   jobs, which only run from main).

So after the owner's "rewrite", the assistant gives the push commands and
does not push the rewritten history itself.

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
  They are quiet by default (one line per target with the counts of passed,
  failed, skipped and warnings; every failing step's complete output first;
  logs in `build/gate/<target>/`); `VERBOSE=1` prints everything. While
  building, run only the tests relevant to the change; run the full gate
  once before the summary and always before proposing a commit. Never
  weaken a check, never hide a failure, a skip or a warning.
- Read `docs/BUILD_PROMPT.md` and `docs/DOCS_SPEC.md` by section for the
  step at hand, not whole.
- `specs/` is read-only, the real-machine test specification
  (`specs/pokus-infrastructure-qa-spec.md`) included: never edit it, and it
  is not part of the gate; the owner edits it. Real-machine tests are named
  by its numbers (4.1, 4.2, ...).
- Never commit, push or tag on your own; the commit rule above applies.

## Preflight gate (BUILD_PROMPT §2)

- Preflight (`preflight/pinka_preflight/`) is Python standard library only and
  source-compatible with Python 3.9. Every filesystem and command access goes
  through the `fs`/`runner` abstractions so `--root <fixture>` works.
- Decisions are data-driven from `preflight/support-matrix.yml` (compiled to
  committed, drift-checked JSON). No hard-coded `if` chains.
- WSL detection runs before container classification; `wsl` is never a container.
- Exit codes: 0 supported, 10 degraded, 20 unsupported, 30 needs_action,
  40 preflight_error. Bootstrap adds 75 (WSL restart), 76 (reboot/MOK),
  77 (operator action), passes doctor's 0/1/2 through, and (ADR-0002,
  accepted) 11, 50, 60, 64, 70.
- Storage must be `btrfs` or LVM thin; no `dir` backend.
- The `preflight_gate` role fails if outcome, provider or features differ from
  `/etc/pinka/host-profile.json` without `--refresh-profile`.

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
   account never gets Docker, Incus, restricted data, artifacts or secrets
   access.
5. Secrets are never committed in plaintext, never logged (`no_log: true`),
   never baked into images.
6. Every external artifact is pinned and verified; no `latest`; no `curl | bash`.
7. No automatic reboots without `--allow-reboot`; no automatic kernel or
   driver upgrades.
8. Idempotent: the second run reports `changed=0`.
9. Never flush or rewrite firewall tables owned by Docker, Incus, LXD, Podman,
   firewalld or ufw.
10. `lab_mode: staging` hosts have their own vault, `/srv/pinka`, Tailscale
    identity (`tag:pinka-qa`) and signing key, and never pair with or write to
    a production host. QA trusted keys go to `/etc/pinka/qa/trusted-keys/`;
    attestations to `/etc/pinka/qa/attestations/`.
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
13. **Pre-existing components** (§13 invariant 24): Docker Engine, Tailscale,
    LXD, Podman, an Incus not adopted with `--adopt-incus`, an NVIDIA driver
    and user accounts that existed before pinka are never changed, stopped,
    restarted or reconfigured. Where the lab cannot run without such a
    change, preflight reports `needs_action` with the exact change, before
    anything changes and with no override. The install record
    (`/var/lib/pinka/install-record.json`, `install_record` role, §7.21)
    says what is pinka's; a role notes what it creates there
    (`pinka_install_record: state: add`) before it creates it.
14. **Uninstall** (§13 invariant 25, §5.4) removes only what the install
    record names as pinka's (without a record, only what carries pinka's
    names), never a pre-existing component, and keeps the lab's data unless
    `--purge-data` with the typed machine name.

## Generated files and fixtures

- Never edit generated files by hand: `preflight/support-matrix.json`,
  `preflight/endpoints.json`, `preflight/lab-defaults.json`, `docs/support-matrix.md`,
  `docs/reference/{cli/pinka-preflight,cli/bootstrap,cli/pinka,exit-codes,host-profile}.md`,
  `docs/reference/{doctor-checks,fingerprint}.md`,
  `docs/reference/{versions,ansible-variables,component-sources,accounts}.md`,
  `docs/reference/{answers,cli/pinka-installer}.md`,
  `docs/reference/{platform-manifest,platform-profiles,platform-records,platform-files,cli/pinka-platform}.md`,
  `contract/reserved-names.yml` and the `$defs.reserved_*` of
  `contract/platform-manifest.schema.json`, the screenshots
  `docs/assets/installer/*.svg` (copies of TUI snapshots),
  the layout table between the `generated:layout` markers of
  `docs/architecture.md`, the sections between the `generated:environments`
  and `generated:exit-codes` markers of `README.md`, `bootstrap/lib/pins.sh`,
  `bootstrap/requirements.in`, `installer/requirements.in`,
  `ansible/requirements.yml` and
  `ansible/group_vars/all/versions.yml` come from `tools/gen`
  (`make generate`); `make docs` fails on drift or missing reference entries.
  Every top-level key of `group_vars/all/main.yml` needs a comment (above it or
  at the end of its line): it becomes the key's reference text.
  `bootstrap/requirements.lock` is compiled with `make bootstrap-lock` and
  `ansible/roles/models/files/requirements.lock` (from the generated
  `requirements.in` next to it) with `make models-lock`, and
  `installer/requirements.lock` with `make installer-lock`; the drift check
  fails if one does not pin the versions of its `requirements.in`.
- Bootstrap exit codes are defined once, in `bootstrap/lib/exitcodes.sh`
  (`readonly EXIT_NAME=<code> # <title>: <meaning>`); every code needs a
  section in `docs/how-to/troubleshoot.md`.
- Bootstrap tests never provision: `bootstrap.sh` runs only unprivileged or
  with every `stage_*` function stubbed (`bootstrap/tests/`).
- Preflight fixtures are built from `preflight/tests/fixture_defs.py`
  (synthetic layers only). Data copied from a real host must pass
  `preflight/tests/sanitize_fixture.py` and a manual review first.
- Summary snapshots change only with `PINKA_UPDATE_SNAPSHOTS=1`; review the diff.

## Repository, releases and platform deployment

- A platform release and its content packages are deployed from root-only
  bare mirrors with `git archive` of one subtree each; no `.git` ever appears
  in a deployed path, and restricted content is only ever extracted into
  `/srv/pinka/restricted/<platform>/` (B2, contract v2 §12.4).
- Bootstrap records the repository commit and the tree hash of the whole
  repository; the dirty-tree check covers the whole repository, and
  bootstrap runs only from the root of its checkout or from an unpacked
  release package.
- Release tags are `vX.Y.Z`; the release package of a tag is
  `pokus-infrastructure-X.Y.Z.tar.gz`.
- A platform repository that keeps a copy of `docs/contracts.md` keeps it
  identical to the copy in the infra release it pins (owner, with the
  "commit" for 3d.8); the platform's CI checks it.

## Ansible portability (BUILD_PROMPT §19)

- Only `platform_facts` interprets the distribution; every other role uses the
  `pinka_*` facts. `make lint-portability` fails on `ansible_distribution*`,
  `ansible_os_family`, `ansible_pkg_mgr` or `ansible_lsb` anywhere else.
- Custom modules (`roles/*/library/`) run on the target's Python: the pinned
  CPython of the Ansible venv on a host, the distribution's own in the
  check-mode containers (3.9 on EL 9). Keep them compatible with Python 3.9
  at run time, not only in syntax; the install record module's tests also
  run on CPython 3.9 (`make test-py39`).
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
  `pinka_incus_object` (create or update, never delete); the pool and the
  networks are created once and never rewritten. The one deletion is
  `rotate.yml` removing the trust entries of replaced client certificates;
  build instances and images of the images role are not such objects. Only
  the docker role writes `/etc/docker/daemon.json`, and only on a Docker
  Engine pinka installed (`live-restore`, `data-root`, `selinux-enabled` on
  SELinux hosts); every other Docker setting lives in pinka's Compose
  projects. Docker 28.2 and later enable CDI by default, so pinka writes
  `features.cdi: true` only for a distribution Docker older than 28.2 that
  it installed itself.

## pinka (BUILD_PROMPT §9, §10.2)

- `tools/pinka-lab/` is Python standard library only and source-compatible
  with Python 3.9, like preflight; every host access goes through
  `pinka_lab.system.System` so the tests use a fake host. Its tests are
  unittest-style and also run on CPython 3.9 (`make test-py39`).
- The CLI reads only `/etc/pinka/lab.json`, rendered by the `doctor` role;
  a new key the CLI reads must be rendered there
  (`ansible/tests/test_doctor.py` fails otherwise).
- Every doctor check needs an ID, a description, a severity, a remediation
  and a section in `docs/how-to/fix-doctor-checks.md` (`{#check-<id>}`);
  the docs coverage check enforces it. The fingerprint's fields are defined
  by `contract/records/fingerprint.schema.json`.

## Contract v2 (BUILD_PROMPT §12)

- `contract/` is published with every release and changes only with a
  contract version bump: `platform-manifest.schema.json` (hand-written except
  its `$defs.reserved_*`), `profiles.yml` (the single source of the eight
  profiles, the socket rules and the caps) and `reserved-names.yml`
  (generated by `make generate` from §12.1 and infra's own data; never edit
  it by hand). A new infra account, unit, container or Incus object is
  reserved automatically when it is in the data the generator reads.
- `tools/platform/pinka_platform/` validates a release (`pinka-platform
  validate`, exit 65 when refused) with Python 3.12 and PyYAML only: its
  schema validator implements exactly the keywords the schema uses, and the
  tests compare it with `jsonschema` on every fixture. The manifest is read
  without anchors, aliases, duplicate or non-string keys.
- Deployment (§7.14) runs in `tools/platform` (`pinka-platform deploy
  prepare|build|pull|publish|retire-plan|activate|clean`, `purge`), installed
  on the host below `/usr/local/lib/pinka/platform` with the contract; the
  `platform` role only orders the steps. A refused release ends bootstrap
  with exit code 65 before anything on the host changes. Builds run as
  `pinka-build` in a `systemd-run` sandbox; never run a release's code as
  root. `contract/records/` holds the schemas of `DEPLOYED_FROM.json`,
  `active.json` and `retired.json`; change them only with a contract version
  bump.
- No infra code names a platform: the Pokus Platform's manifest is only a
  test fixture (`tools/platform/tests/fixtures/manifests/`). Every new rule
  gets an invalid case in `tools/platform/tests/test_validate.py`; golden
  files of the principals and endpoints files change only with
  `PINKA_UPDATE_SNAPSHOTS=1`.
- An `untrusted-agent` service names its own package; the build gives it an
  environment of its own (`.agent-venvs/<service>/`) and the agent binds
  nothing else of the release (V6). The tests that run uv
  (`tools/platform/tests/test_agent_package.py`) need the uv that
  `versions.lock.yml` pins: `make setup` installs it into `build/tools/uv`
  (`PINKA_UV=<path>` names another). They take the `uv` fixture of
  `tools/platform/tests/conftest.py`, which marks them; in CI any skip of a
  marked test is a failure.
- The telemetry stores and the inference API are reachable only by a UID
  allowlist in nftables (`pinka_telemetry_guard`, `pinka_inference_guard`,
  ADR-0016): rules match the socket's UID, never its GID, and a new client
  needs an entry in `group_vars/all/main.yml` with its reason, or in the
  rendering for platform accounts.

## Reference platform (BUILD_PROMPT §7.18)

- `tests/reference-platform/` (`refplat`) is a real release, standard
  library only, deployed by `make test-distros`; its manifest is also the
  manifest fixture `refplat`. Both jobs' commands are the text of
  `jobs/tune.py`; the tests keep the three identical, and the job image is
  the lock's `containers.images.refplat_job`.
- Its code takes the platform's name from `endpoints.toml` (the agent from
  the controller's hello), never from a constant, so the renamed copy of
  the `replace` stage runs unchanged.
- The per-profile isolation probe's expectations come from the profile
  flags in `contract/profiles.yml` (`pinka_test_distros/isolation.py`);
  never loosen one to make a stage pass.

## GPU arbiter and training (BUILD_PROMPT §7.18)

- vLLM starts only through the GPU arbiter
  (`ansible/roles/gpu_arbiter/files/pinka_gpu_arbiter.py`): no role enables,
  starts or restarts `pinka-inference.service`. The arbiter alone writes its
  state, its transition log, run records, published datasets and registered
  adapters; the record formats are in `contract/records/`.
- A job container's options are fixed in the arbiter; no manifest field
  adds a mount, a capability, a network or a namespace. The split guard
  (no `test` instance at publication or mount) and the hash checks are never
  relaxed.
- The arbiter runs with the Ansible venv's Python (standard library and
  PyYAML) and reaches the host only through its `System` class; `ansible/tests/test_gpu_arbiter.py` replaces it, so no test
  touches Docker, systemd or the GPU.

## Installer (BUILD_PROMPT §18)

- `installer/pinka_installer/` is Python 3.12 with Textual and ruamel.yaml from
  the hash-locked `installer/requirements.lock` (`/opt/pinka/installer-venv`).
  It is a front-end only: it writes files and runs `bootstrap.sh
  --non-interactive --no-tui`; it never provisions anything itself.
- Every rule of a field lives in `validators.py`; the answers file is checked
  by `installer/schema/answers.schema.json` and `answers.problems()`. A new
  answers field needs a schema `description` (it becomes the reference text).
- Secrets live in memory only (`Wizard.provided`, `vault_password`); the resume
  state (`state.json`) and the answers file never hold one. The vault is
  written only through Ansible's vault API; an existing vault's secrets are
  kept unless the operator provides a new value.
- The TUI reaches the host only through `tui/host.py` (`Host`) and bootstrap
  only through `tui/runner.py` (`Runner`); the tests replace both
  (`installer/tests/tui_fakes.py`) and use preflight's own fixtures for host
  profiles and recorded event streams (`installer/tests/data/`, re-recorded
  with `record_events.py`).
- Snapshot tests cover every screen at 80x24 and 120x40, dark and light.
  Change them only with `pytest installer/tests/test_tui_snapshots.py
  --snapshot-update`, review every changed SVG, then `make generate` (the
  docs screenshots are copies).

## Golden images (BUILD_PROMPT §7.13)

- Blueprints live in `images/<blueprint>/`; `make lint-schemas` checks every
  `image.yaml`, `make lint-ansible` syntax-checks every `provision.yml`.
- Raise a blueprint's `version` whenever its recipe or its base changes; the
  role never rebuilds an existing `<blueprint>/<version>`.
- Files installed into images must pass the leakage lint
  (`ansible/tests/test_images.py`); never weaken `images_leakage_patterns`,
  add a narrow benign word to `images_leakage_allow` instead (ADR-0012).

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
  `# Managed by pinka (<template path>). Local changes will be overwritten.`,
  except the files installed into images (`images/*/files/`): images carry
  no `pokus`, `pinka` or `potka` string at all (owner decision 3c2).
- All timestamps UTC ISO 8601 with `Z`. Documentation in English.
