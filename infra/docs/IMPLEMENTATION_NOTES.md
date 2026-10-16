# Implementation notes — pokus-infrastructure

Working notes required by BUILD_PROMPT §0.1: my understanding of the scope, the
files I intend to create, every open question with its answer, and what is
still unverified. This file is excluded from the documentation site. The full
record of answers, approved deviations and step status is `../PROGRESS.md` in
the monorepo; IDs such as B2 or E9 refer to it.

## Scope as understood

Pokus Infrastructure (pinka) provisions a single Linux host (bare metal, VM or WSL2; with or
without an NVIDIA GPU) into a Pokus lab host with one command, and keeps it
reproducible and auditable:

- **Preflight gate** (§2): stdlib-only Python ≥ 3.9 detector of OS,
  environment class, hardware, kernel features, GPU, conflicts; data-driven
  decision from `support-matrix.yml`; exit codes 0/10/20/30/40; host profile
  JSON validated against a JSON Schema; full fixture-based test suite (§11).
- **Bootstrap** (§5): `bootstrap.sh` with pre-preflight, pinned uv and CPython
  in `/opt/pinka`, hash-locked Ansible environment, signal files mapped to exit
  codes 75/76/77, resumable, a TUI front-end with exactly one provisioning path.
- **Ansible roles** (§7, §19): platform facts and the component source matrix
  for four families (debian, redhat, suse, archlinux), providers (wsl2,
  baremetal, vm), base, MAC, users and groups, SSH, Tailscale, firewall (three
  backends), Docker, NVIDIA Container Toolkit, Incus, layout, Postgres,
  observability, OPA, vLLM, models, golden images, catalog, platform, backup,
  lab session, doctor, inference control.
- **Operator CLI** `pinka` (§9) with doctor (incl. `--smoke`), fingerprint,
  backup/restore wrappers, image helpers, `update-host` (§19.5) and
  `docs serve` (owner answer H9).
- **Reproducibility** (§10): `versions.lock.yml` with verified values,
  `make lock-check`, environment fingerprint.
- **Installer TUI** (§18) with answers-file replay.
- **Portability proof** (§19.9): `make test-distros`, run only on explicit
  request in a disposable environment, never in CI.
- **Documentation** following `DOCS_SPEC.md`, written step by step.

## Monorepo adaptations that affect infra

| ID | Adaptation |
| --- | --- |
| B2 | Platform and catalog are deployed from a root-only bare mirror (`/var/lib/pinka/git/`) with `git archive` of one subtree; `DEPLOYED_FROM.json`; doctor check and test that no deployed path contains `.git` or catalog content outside `/srv/pinka/catalog`. |
| B3 | Bootstrap records the repository commit and the `infra/` tree hash; the dirty-tree check covers `infra/` only. |
| B4 | Release tags `infra-vX.Y.Z`; first version 0.1.0. |
| B5 | Canonical CI in `.github/workflows/ci.yml`; the monorepo root has a thin wrapper. CI runs lint, unit tests, type checks, docs build/coverage/drift, lock-check and syntax/check-mode jobs only. |
| C1 | `docs/contracts.md` is the resolved contract, byte-identical in platform. |
| E3 | `pinka_source_repo` is a required deployer-supplied variable; examples use `git@github.com:example-org/pokus-platform.git`. |

## Open questions and answers

Every question raised before implementation, with the answer. Full wording in
`../PROGRESS-ARCHIVE.md` (owner answers).

| ID | Question (spec section) | Answer |
| --- | --- | --- |
| A2 | Who commits in the cloud environment | The assistant runs the approved `git add`/`commit`/`push` only on the owner's "commit"; never tags |
| A3 | Network access for verification | Owner allows the domains listed below; until then items stay `TODO(verify)` |
| A4 | Dev tools | User space only, pinned; PostgreSQL for tests via pinned micromamba + conda-forge |
| B1 | Spec copies | `docs/BUILD_PROMPT.md`, `docs/DOCS_SPEC.md`, excluded from MkDocs |
| C2–C10 | Contract conflicts and gaps | Resolved in `docs/contracts.md` |
| D1–D6 | External verification hooks | Implemented as defined in `docs/contracts.md`; `qa:` markers linted, never executed here |
| E1 (§6.1) | Researcher names | Placeholders `researcher1` (admin), `researcher2`, `researcher3`; no key files are ever committed |
| E2 (§7.4) | Tailscale tags | `tag:pinka-lab` (production), `tag:pinka-qa` (staging) |
| E3 (§7.14) | Git remote | Required variable `pinka_source_repo`; SSH deploy key generated on the host; Git host keys pinned by fingerprint; anonymous HTTPS supported; no personal tokens |
| E4 (§7.15) | Backup destination | "Configure later" by default; restic over SFTP primary, rest-server secondary, local path test-only; placeholder hosts only |
| E5 (§13.1) | Disk safety | As specified; example host vars use a loop pool |
| E6 (§8.1) | Host vars | Only `example-machine.yml` and `tests/answers/ci.yml` |
| E7 (§7.12) | Model candidates | Permissive, non-gated first; others listed separately with approval; presented in step 7 |
| E8 (§19.2) | Distro matrix | Non-EOL releases only; Ubuntu 26.04 as `tested` if released and supported by Docker, Incus, NVIDIA toolkit and Tailscale; supported tier stays Ubuntu 24.04 + Debian 13; newest Incus LTS packaged for supported distros; presented in step 2 |
| E9 (§11) | RHEL 8 | Explicit `unsupported` row for RHEL-compatible major 8 before the `ID_LIKE` rows; fixture expects unsupported |
| E10 (§4) | YAML in preflight | Generated, committed, drift-checked JSON |
| E11 (§4, §18.2) | Lockfiles | `pyproject.toml` + `uv.lock` for dev tools; installer and bootstrap keep their own lockfiles |
| E12 | Certificates and keys | `community.crypto`, pinned |
| E13 (§7.8) | Researcher database access | Separate reader socket directory (group `pinka`), reader credential 0640 group `pinka`; reader never sees `gt` (tested) |
| E14 (§7.9) | Metrics path | Collector Prometheus exporter, scraped |
| E15 (§7.6) | Compose units | `oneshot` + `RemainAfterExit` + `up -d --wait` / `down` |
| E16 (§7.14) | Signed tags | `require_signed_tags: false` by default; SSH-signed tags checked against a committed allowed-signers file when true; both paths tested |
| H1 | Licenses | Apache-2.0 code, CC-BY-4.0 documentation |
| H4 | Docs tooling | MkDocs Material pinned; Node tools pinned with `package-lock.json`; Mermaid validation with mermaid-cli; weekly link check |
| H5 | Page ownership | Table below, for approval |
| H9 | Publishing | Release tarball and `pinka docs serve` on `127.0.0.1` |

### Questions raised by step 1 (awaiting answers)

1. **Reference agents for scenario validation.** DOCS_SPEC §A.3 requires
   "oracle/null agent checks" (tutorial 4, scenario-author how-to) and a "QA and
   validity controls: reference agents" explanation, but neither build prompt
   defines an oracle or null agent. Options: (a) document them as part of the
   external verification tooling (out of scope here) and end tutorial 4 at
   `potka catalog lint` plus a fake-pipeline run; (b) add two minimal scripted
   reference agents to the platform (null: `finish` without acting; oracle:
   applies the ground truth's acceptable actions) used only by a validation
   command. Recommendation: (a), because (b) adds behavior no build prompt
   specifies.
2. **QA results schema.** DOCS_SPEC §A.3 lists a "QA results" schema in the
   reference. The repository that produces QA results is out of scope (D), so
   recommendation: list only the attestation schema (D2) and mark "QA results"
   as owned by the external verification repository.
3. **Project files at the monorepo root.** `CITATION.cff` (H2) and the
   community files `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md` (H3)
   are recognized by GitHub at the repository root or in `.github/`.
   Recommendation: put them in the monorepo's root `.github/` (and
   `CITATION.cff` at the root), with the docs-site rendering their content in
   "About"; this adds root-level files beyond PROGRESS.md and CLAUDE.md.
4. **Port table.** `docs/contracts.md#ports` proposes the ports for C10.

## Documentation page ownership (H5, for approval)

Each page of DOCS_SPEC §A.3 has one home. Spec-named files keep their paths;
how-to and tutorial pages go into `docs/how-to/` and `docs/tutorials/` of the
owning component. "site" means `docs-site/`.

| DOCS_SPEC §A.3 page | Owner | Path |
| --- | --- | --- |
| What Pokus is; concepts and glossary; 10-minute tour | site | `docs/getting-started/*.md` |
| Tutorial 1: install on WSL2 | infra | `docs/tutorials/install-wsl2.md` |
| Tutorial 2: install on bare metal | infra | `docs/tutorials/install-baremetal.md` |
| Tutorial 3: first campaign | platform | `docs/tutorials/first-campaign.md` |
| Tutorial 4: first scenario | catalog | `docs/tutorials/first-scenario.md` |
| Tutorial 5: compare two models | platform | `docs/tutorials/compare-models.md` |
| Tutorial 6: base model vs LoRA adapter | platform | `docs/tutorials/compare-lora-adapter.md` |
| Tutorial 7: reproduce a published result | platform | `docs/tutorials/reproduce-result.md` |
| Operations overview (infra §15 `operations.md`) | infra | `docs/operations.md`, linking to the how-tos below |
| Start, stop and drain; power loss | infra | `docs/how-to/start-stop-drain.md` |
| Add, remove, lock a researcher; rotate a key | infra | `docs/how-to/manage-researchers.md` |
| Update the host (`update-host`) | infra | `docs/how-to/update-host.md` |
| Pin, update and verify versions | infra | `docs/how-to/update-pinned-versions.md` |
| Add a model; create and activate a deployment | infra | `docs/how-to/add-model-and-deployment.md` |
| Build, test and export golden images | infra | `docs/how-to/golden-images.md` |
| Back up, restore, restore drill | infra | `docs/how-to/backup-and-restore.md` |
| Migrate to a new machine | infra | `docs/how-to/migrate-lab.md` |
| Pair an inference node | infra | `docs/how-to/pair-inference-node.md` |
| Firewall backends; SELinux enforcing | infra | `docs/how-to/firewall-and-selinux.md` |
| Maintenance window and staging | infra | `docs/how-to/staging-window.md` |
| Publish (install) an attestation | infra | `docs/how-to/install-attestation.md` |
| Rotate secrets and certificates | infra | `docs/how-to/rotate-secrets.md` |
| Troubleshoot by exit code and doctor check | infra | `docs/how-to/troubleshoot.md` |
| Connect over Tailscale and SSH; Grafana; ad-hoc LLM endpoint | infra | `docs/how-to/connect.md` (links to the platform's `potka llm key`) |
| Write a campaign spec | platform | `docs/how-to/write-campaign-spec.md` |
| Monitor a campaign; invalid runs | platform | `docs/how-to/monitor-campaign.md` |
| Approve or deny actions | platform | `docs/how-to/approve-actions.md` |
| Inspect a run | platform | `docs/how-to/inspect-run.md` |
| Exploratory experiments (`potka dev`) | platform | `docs/how-to/exploratory-runs.md` |
| Evaluations, snapshots, reports, comparisons | platform | `docs/how-to/evaluate-and-report.md` |
| Export data | platform | `docs/how-to/export-data.md` |
| Cite a result | platform | `docs/how-to/cite-result.md` |
| Scenario-author how-tos (anatomy, parameters and seeds, primitives and probes, tickets, control scenarios, validating, versioning) | catalog | `docs/how-to/*.md`, one page each |
| Development environment with fakes; repository layout | platform | `docs/how-to/dev-environment.md`, `docs/how-to/repository-layout.md` |
| Add a tool / primitive / probe / policy rule | platform | four pages in `docs/how-to/` |
| Add a DB migration | platform | `docs/how-to/add-migration.md` |
| Propose a contract change | platform | `docs/how-to/propose-contract-change.md` |
| Add a distro family | infra | `docs/distros/adding-a-family.md` (infra §19.11) |
| Release process | site | `docs/about/release-process.md` |
| CLI reference: `pinka`, `pinka-preflight`, `bootstrap.sh` | infra | `docs/reference/cli/*.md` (generated) |
| CLI reference: `potka` | platform | generated |
| HTTP APIs, platform TOML settings, platform schemas, tools, primitives and probes, policy rules, database, events, telemetry, metrics and statistics, CLI exit codes | platform | `docs/reference/*.md` (generated where marked ⚙) |
| Ansible variables, host_vars/group_vars keys, answers file, host profile and fingerprint schemas, support and component source matrix, pinned versions, bootstrap/preflight/doctor exit codes, doctor checks, filesystem/accounts/sockets | infra | `docs/reference/*.md` and `docs/support-matrix.md` (generated) |
| Architecture | site (overview), infra (`docs/architecture.md`, host trust zones), platform (`docs/architecture.md`, services) | |
| Run lifecycle; ground-truth blindness; agent boundary; benchmark methodology; QA and validity controls | platform | `docs/explanation/*.md` |
| Target isolation; portability; threat model and security; providers; node roles | infra | `docs/explanation/target-isolation.md`, `docs/explanation/portability.md`, `docs/security.md`, `docs/providers/*.md`, `docs/node-roles.md` |
| Reproducibility | infra (`docs/reproducibility.md`: pinning, fingerprints, sessions) and platform (`docs/reproducibility.md`: seeds, sealing, comparability) | |
| ADR index | site (generated from every component's `docs/adr/`) | |
| Changelogs | each component | `CHANGELOG.md` |
| Release notes; how to cite; contributing; code of conduct; license | site | `docs/about/*.md` (see question 3) |

## Files to create, by implementation step (§16)

1. **Skeleton and tooling (this step):** `README.md`, `CLAUDE.md`, `LICENSE`,
   `LICENSE-docs`, `Makefile`, `.editorconfig`, `.gitattributes`,
   `.gitignore`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`,
   `pyproject.toml`, `uv.lock`, `.python-version`, `package.json`,
   `package-lock.json`, `.node-version`, `.puppeteerrc.cjs`, `.yamllint.yml`,
   `.markdownlint-cli2.yaml`, `cspell.json`, `project-words.txt`,
   `mkdocs.yml`, `docs/{index,contracts,BUILD_PROMPT,DOCS_SPEC,IMPLEMENTATION_NOTES}.md`,
   `tools/repo-checks/` (package and tests).
2. **Preflight:** `preflight/pinka-preflight`, `preflight/pinka_preflight/`
   (`__init__`, `__main__`, `cli`, `fs`, `runner`, `detect/*`, `matrix`,
   `report`, `render`), `preflight/support-matrix.yml` + generated JSON,
   `preflight/endpoints.yml` + generated JSON,
   `preflight/schema/host-profile.schema.json`, `preflight/tests/` with every
   fixture of §11 plus a sanitizer for fixtures captured from real hosts,
   `docs/support-matrix.md` (generated), CLI and exit-code references, ADR
   "stdlib-only preflight".
3. **Bootstrap:** `bootstrap/bootstrap.sh`, `bootstrap/lib/*.sh` (logging,
   args, exit codes, `prepreflight.sh`), Ansible stubs, bootstrap reference.
4. **Versions:** `versions.lock.yml`, its JSON Schema, `make lock-check`,
   `ansible/group_vars/all/versions.yml` generator, versions reference page.
5. **Ansible foundation:** `ansible/{ansible.cfg,requirements.yml,component-sources.yml,site.yml}`,
   inventory with `example-machine.yml`, `group_vars/all/{main.yml,vault.yml.example}`,
   roles `platform_facts`, `pkg` (if needed), `preflight_gate`,
   `provider_{wsl2,baremetal,vm}`, `base`, `mac`, `users_groups`, `ssh`,
   `layout`, `firewall`, `tailscale` with `vars/<family>.yml`,
   `meta/argument_specs.yml`, README each; the distro-branching lint rule;
   `docs/providers/*.md`, `docs/distros/*.md`; ADRs (provider model,
   family-based portability, uv-managed Python, firewall integration).
6. **Containers and sandbox:** roles `docker`, `nvidia_container_toolkit`,
   `incus`; `incus/{preseed.yaml.j2,profiles/,projects/}`; ADRs (Incus,
   native Docker Engine, btrfs/LVM-thin).
7. **Services:** roles `postgres`, `observability`, `opa`, `vllm`, `models`;
   `compose/*/compose.yml`, `config/**`, Grafana dashboards,
   `vllm/profiles/*.yml`, `models.catalog.yml`, `models.lock.yml`; ADRs
   (Postgres socket-only, OTel telemetry).
8. **Golden images:** `images/_base`, `images/{web-nginx-flask,pg-primary,redis-cache,docker-host,probe-client}`
   with recipes, tests and the leakage lint.
9. **Session and backups:** roles `lab_session`, `backup`; `restore.yml`, `rotate.yml`.
10. **Operator CLI:** `tools/pinka-lab/` (doctor incl. `--smoke`,
    fingerprint, `update-host`, `docs serve`), role `doctor`.
11. **Platform and catalog roles** with `*_enabled: false` defaults; the
    vLLM deploy helper and socket unit; role `inference_control`.
12. **Distro tests:** `make test-distros` harness and `tests/answers/ci.yml`.
13. **Installer TUI:** `installer/` (pyproject, lock, schema, app, screens,
    widgets, runner, theme, tests incl. snapshots), callback plugin
    `ansible/callback_plugins/pinka_events.py`.
14. **Documentation completion:** remaining pages, ADR set, regenerated
    references; final `make lint test docs lock-check`.

## Network domains needed for verification (A3)

Hosts the build must reach to verify versions, digests, checksums, keys and
upstream documentation. Already reachable from the build environment: PyPI,
npm, Docker Hub registry, GHCR, download.docker.com, raw.githubusercontent.com,
archive.ubuntu.com, conda.anaconda.org, and git over HTTPS to github.com; since
step 3 also GitHub release downloads, `api.github.com` and
`security.ubuntu.com`.

| Purpose | Domains |
| --- | --- |
| Model hub (metadata and LFS hashes; weights are never downloaded here) | `huggingface.co`, `hf.co`, `cdn-lfs.huggingface.co`, `cdn-lfs.hf.co`, `cas-bridge.xethub.hf.co` |
| GitHub releases and API (uv, restic, Quarto, OPA, micromamba, CPython builds, Git host keys) | `github.com`, `api.github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`, `codeload.github.com` |
| CPython builds installed by uv (found in step 3) | `releases.astral.sh` |
| Quarto | `quarto.org` |
| OPA | `openpolicyagent.org`, `www.openpolicyagent.org` |
| Docker | `docs.docker.com` |
| NVIDIA | `nvidia.github.io`, `developer.download.nvidia.com`, `docs.nvidia.com`, `nvcr.io` |
| Zabbly (Incus for Debian and Ubuntu) | `pkgs.zabbly.com` |
| Tailscale | `pkgs.tailscale.com`, `tailscale.com` |
| Linux Containers (Incus images and documentation) | `images.linuxcontainers.org`, `linuxcontainers.org` |
| Ubuntu | `security.ubuntu.com`, `ports.ubuntu.com`, `changelogs.ubuntu.com`, `releases.ubuntu.com` |
| Debian | `deb.debian.org`, `security.debian.org`, `www.debian.org` |
| Fedora, EPEL, COPR, RPM Fusion | `mirrors.fedoraproject.org`, `dl.fedoraproject.org`, `fedoraproject.org`, `bodhi.fedoraproject.org`, `copr.fedorainfracloud.org`, `download1.rpmfusion.org`, `mirrors.rpmfusion.org` |
| RHEL-compatible | `repo.almalinux.org`, `mirrors.almalinux.org`, `dl.rockylinux.org`, `mirror.stream.centos.org`, `mirrors.centos.org`, `access.redhat.com` |
| openSUSE and SLES | `download.opensuse.org`, `mirrorcache-eu.opensuse.org`, `mirrorcache-us.opensuse.org`, `en.opensuse.org`, `www.suse.com` |
| Arch Linux | `archlinux.org`, `geo.mirror.pkgbuild.com`, `archive.archlinux.org` |
| Release lifecycle data | `endoflife.date` |
| Container registries (beyond Docker Hub and GHCR) | `auth.docker.io`, `production.cloudflare.docker.com`, `hub.docker.com`, `quay.io`, `cdn01.quay.io`, `cdn02.quay.io`, `cdn03.quay.io`, `gcr.io`, `storage.googleapis.com`, `pkg-containers.githubusercontent.com` |
| Ansible collections | `galaxy.ansible.com` |
| micromamba (test PostgreSQL, A4) | `micro.mamba.pm`, `api.anaconda.org` |
| Upstream documentation | `docs.vllm.ai`, `opentelemetry.io`, `grafana.com`, `prometheus.io`, `www.postgresql.org`, `textual.textualize.io`, `docs.ansible.com`, `docs.astral.sh`, `squidfunk.github.io`, `www.mkdocs.org`, `www.freedesktop.org`, `learn.microsoft.com`, `docs.github.com` |

## Verification status

Verified in this step: every development tool version from PyPI and npm
(resolved into `uv.lock` and `package-lock.json` with hashes), the uv version
(PyPI), GitHub Action commit SHAs (resolved with `git ls-remote`), the runner
image's Chrome and Node.js (runner-images documentation), and the license
texts (SPDX license-list-data v3.29.0).

Open `TODO(verify)` items: none introduced by step 1. Items for later steps
are listed in `../PROGRESS.md`.

## Risks noted in step 1

- **MkDocs maintenance.** MkDocs 1.6.1 (pinned) has had no release since
  2024-08-30, and Material for MkDocs announces that MkDocs 2.0 removes the
  plugin system. Pinning 1.x keeps every build working; the replacement
  decision belongs to the docs-site plugin review (DOCS_SPEC §B.2) and needs an
  ADR and owner approval.
- **mypy and Python 3.9.** Resolved in step 2: the pinned mypy cannot target
  Python 3.9 (minimum 3.10). Preflight is type-checked with
  `--python-version 3.10`, linted by ruff with a `py39` target, and its whole
  test suite runs on CPython 3.9 in `make test` (ADR-0001).

## Step 2: preflight

Built: the stdlib-only `pinka_preflight` package (fs/runner abstractions, eight
detectors, matrix evaluator, report, runtime schema validator, summary, CLI),
the POSIX wrapper `preflight/pinka-preflight`, `support-matrix.yml` and
`endpoints.yml` with generated JSON, the host profile JSON Schema, 35 fixture
roots built from `preflight/tests/fixture_defs.py`, the fixture sanitizer,
summary snapshots, the generators in `tools/gen/` (matrix JSON, support matrix
page, CLI/exit-code/host-profile references, drift and coverage checks), an
explanation page, two how-to guides and ADR-0001.

### Support matrix verification (owner answer E8)

Sources reachable from the build environment: Debian/Ubuntu
`distro-info-data` 2026.08.20-1 and `osinfo-db` 0.20251212+git20260710 (both
from archive.ubuntu.com, package hashes in `support-matrix.yml`), and the
Docker repository listings at download.docker.com.

| Release | Finding | Change |
| --- | --- | --- |
| Ubuntu 24.04 | regular support until 2029-05-31 | none (supported) |
| Debian 13 | regular support until 2028-08-09 | none (supported) |
| Ubuntu 22.04 | regular support until 2027-06-01 | none (tested) |
| Debian 12 | regular security support ended 2026-07-11; Debian LTS until 2028-06-30 | removed from `tested`; explicit `unsupported` row with that reason (awaiting confirmation) |
| Ubuntu 26.04 | released 2026-04-23, support until 2031-05-29; Docker publishes packages | not added yet: Incus, NVIDIA Container Toolkit and Tailscale support cannot be verified from here (`TODO(verify)`) |
| Fedora | 42 end of life 2026-05-13; 43 until 2026-12-09; 44 released 2026-04-26 | current and previous = 43, 44 (`versions.lock.yml`) |
| RHEL-compatible 9 and 10 | 9.x and 10.x releases present in osinfo-db; Docker publishes for 9 and 10 | none (tested) |
| RHEL-compatible 8 | — | explicit `unsupported` row (E9) |
| openSUSE Leap 15.6 | no end-of-life date in the sources available here | `TODO(verify)`; row kept until verified |
| Incus LTS line | package repositories not reachable from here | verified in step 6 |

### Interpretations awaiting owner confirmation

1. **Debian 12** (above): E8 says "drop EOL releases". Debian's own data marks
   the end of regular support as 2026-07-11, while Debian LTS continues. I
   applied the end of regular support. The alternative is to keep Debian 12 as
   `tested` until 2028-06-30.
2. **`debian13-baremetal` fixture.** BUILD_PROMPT §11 expects "experimental
   tier", but the matrix in §2.3, §19.2 and owner answer E8 keep Debian 13
   `supported`. The fixture asserts `supported`.
3. **Unlisted releases of implemented distributions** (for example Ubuntu
   26.04 or Fedora 42) fall through to the `ID_LIKE` rows or the catch-all,
   whose reason reads "Distribution family not implemented". Proposal: an
   explicit `unsupported` row per implemented distribution ("release not in the
   support matrix") placed before the `ID_LIKE` rows.
4. **Unreachable endpoints.** §2.2 says "do not fail hard on optional
   endpoints". Required endpoints that are unreachable make the outcome
   `needs_action` (exit 30), with a remediation text; optional ones warn.
5. **Storing the profile.** §2.1 says a real run writes the profile. It is
   written only when running as root, and `--no-write` keeps read-only callers
   (`pinka preflight`, the `preflight_gate` role) from overwriting the
   profile they compare against.
6. **`qa:run` environment labels** (D4: opaque strings). Proposal for page
   authors: E0 = development checkout without a lab, E1 = candidate host before
   bootstrap, E2 = provisioned lab host, E3 = provisioned lab host with a GPU
   and a served model.

### Open `TODO(verify)` items after step 2

- Ubuntu 26.04 support by Incus, NVIDIA Container Toolkit and Tailscale
  (`support-matrix.yml`).
- openSUSE Leap 15.6 end of support (`support-matrix.yml`).
- 16 endpoint URLs in `preflight/endpoints.yml` (hosts not reachable from the
  build environment; step 3 verified `security.ubuntu.com` and added
  `releases.astral.sh`).
- Output formats of `timedatectl timesync-status` and `chronyc -n tracking`
  (`detect/timesync.py`).
- Detection of an incomplete dnf transaction (`detect/conflicts.py`).

Assumed command output formats modeled in the fixtures and to be confirmed on
real hosts (Phase 5): `mokutil --sb-state`, `dkms status`, `ldd --version` on
musl, `getenforce`, GNU `df --output`, the `WSLInterop` binfmt entry,
`systemd-detect-virt` on WSL (`wsl` for plain and `--container`).

## Step 3: bootstrap

Built: `bootstrap/bootstrap.sh` and its libraries (`exitcodes.sh`, `log.sh`,
`args.sh`, `gitstate.sh`, `tooling.sh`, `stages.sh`, the POSIX
`prepreflight.sh`), the generated `bootstrap/lib/pins.sh`,
`bootstrap/requirements.in` and `ansible/group_vars/all/versions.yml` (all
from `versions.lock.yml`), the hash-locked `bootstrap/requirements.lock`
(`make bootstrap-lock`), the preflight helper `pinka_preflight.profile_tool`
(get, consent, diff, override, store), the record-only preflight option
`--allow-dirty` (`overrides.allow_dirty` in the host profile, §13.11), and
temporary Ansible stubs (`ansible.cfg`, `inventory/hosts.yml`,
`requirements.yml` with no collections, a `site.yml` that only checks the
variables bootstrap passes, a `restore.yml` that stops). Development tools
gained `ansible-core` 2.21.5 and `ansible-lint` 26.9.0; `make lint` runs
ansible-lint (production profile) and `--syntax-check` of every playbook.
Documentation: the generated `bootstrap.sh` reference, the bootstrap table of
the exit-code reference, `how-to/troubleshoot.md` (one section per exit
code), `explanation/bootstrap.md` and ADR-0002 (proposed).

The `versions.yml` generator and `pins.sh` belong to step 4 in the file plan
above; they were needed now because bootstrap verifies `versions.yml`
(§5.2 step 7) and reads the uv and CPython pins before any Python exists.

### Verified in step 3

- GitHub release downloads became reachable from the build environment.
  uv 0.12.23: tag `0.12.23` resolves to commit
  `46b84fd0bfec23b72f29e8e2185ba68a65052f48` (`git ls-remote`); both Linux
  archives (`x86_64`, `aarch64`, `-unknown-linux-gnu.tar.gz`) were downloaded
  and hashed, and the hashes equal the published `.sha256` files. The
  archive layout (`uv-<arch>-unknown-linux-gnu/{uv,uvx}`) and
  `uv --version` output (`uv 0.12.23 (x86_64-unknown-linux-gnu)`) match what
  `install_uv` expects.
- GitHub answers release downloads with a redirect to
  `release-assets.githubusercontent.com`; the endpoint list now probes that
  host instead of `objects.githubusercontent.com`.
- CPython 3.12.15 is the newest 3.12 that uv 0.12.23 offers; uv downloads the
  python-build-standalone 20261003 build from `releases.astral.sh` (blocked
  here, so the endpoint is `TODO(verify)`) and checks it against the sha256 in
  its own metadata.
- `ansible-core` 2.21.5 (requires Python >= 3.12), `jmespath` 1.1.0,
  `cryptography` 50.0.2 and `ansible-lint` 26.9.0 on PyPI; the configuration
  keys in `ansible/ansible.cfg` with `ansible-config list` of ansible-core
  2.21.5.
- `security.ubuntu.com` answers (HTTP 200); its endpoint marker was removed.

### Interpretations awaiting owner confirmation (step 3)

1. **Additional exit codes** 11 (experimental tier without consent), 50
   (tooling), 60 (play failed without a signal), 64 (usage) and 70 (internal
   error). BUILD_PROMPT defines no code for these cases; ADR-0002 (status
   proposed) gives the reasons.
2. **Changed host profile.** If outcome, provider or a feature flag differs
   from `/etc/pinka/host-profile.json`, bootstrap stops with 77 until
   `--refresh-profile` is given. Exception, so that re-runs after 75/76/77
   "resume cleanly" (§5.2): when the previous run ended with a signal from the
   play, the change is accepted with a printed notice.
3. **`--force-unsupported` does not skip required actions:** a forced
   unsupported host that also has `needs_action` items stops with 30.
4. **State file at every exit.** `/etc/pinka/bootstrap-state.json` is written
   whenever bootstrap ends after the checkout is known, also after failures,
   with `result` (`success`, `doctor_warnings`, `doctor_failures`, `signal`,
   `failed`), `exit_code`, the last stage and the signal. Consumers of the
   contract's infra provenance (the platform's official check) should
   require `result` `success` or `doctor_warnings`; this needs a contract
   addition (proposal, not yet in `contracts.md`).
5. **Check mode** (`--check`): preflight runs, but neither the host profile
   nor the state file is written, and doctor is skipped; uv, CPython and the
   Ansible environment are still installed because the check run needs them.
6. **Pre-preflight additions:** `tar` is required (it unpacks the pinned uv)
   and reported like a missing `curl`; a missing `git` ends with 30. On WSL
   without systemd, `prepreflight.sh` offers interactively to add
   `[boot] systemd=true` to `/etc/wsl.conf` (only if the file has no `[boot]`
   section, so nothing is rewritten) and then ends with 75; otherwise it ends
   with 30 and prints the steps. The `wsl2` support-matrix rule already says
   "the bootstrap can write it".
7. **Installer stubs until step 13:** `--tui` and `--answers` stop with 77;
   without them bootstrap uses its plain front-end.
8. **Vault password:** only needed when `ansible/group_vars/all/vault.yml`
   exists. Order: `--vault-password-file`, then
   `/etc/pinka/secrets/vault-pass` if present (the TUI's optional root-only
   storage, §18.4 screen 14), then `--ask-vault-pass` on a terminal; otherwise
   77.
9. **Concurrency:** a lock in `/run/lock/pinka-bootstrap.lock` stops a second
   run with 77.
10. **Locale:** `ansible-playbook` runs with `LC_ALL=C.UTF-8` because
    ansible-core refuses non-UTF-8 locales; that the locale exists on every
    supported family is to be confirmed on real hosts (Phase 5).
11. **Until step 10**, `pinka` does not exist, so a real (non-check) run
    ends with 60 after the play.

### Tests

`bootstrap/tests/` drives the shell code from pytest without provisioning
anything: `bootstrap.sh` runs only as an unprivileged user (it stops at the
root check), `main` runs only with every `stage_*` function replaced by a
recording stub, and `prepreflight.sh` runs against a fake root with stub
commands on `PATH`. Covered: the command line and usage errors, the root
check, the event stream, every gate decision including interactive consent,
signal mapping, the profile-change rule, the state file and the Ansible
variables, vault options, checksum verification, the versions sync check,
git provenance in a temporary repository (including the owner check), stage
order and exit-code propagation, and every pre-preflight scenario.

## Step 4: versions.lock.yml and lock-check

Built: `versions.lock.yml` with every section of BUILD_PROMPT §10.1 and §19.4
(binaries, Python, container images, repository keys, packages per component
and release, Ansible collections, Incus base images, GitHub SSH host keys);
its JSON Schema `tools/lockcheck/schema/versions-lock.schema.json` and the
schema of the support matrix `preflight/schema/support-matrix.schema.json`
(BUILD_PROMPT §11 asks for both; the matrix schema was missing after step 2),
both validated by `make lint` (`lint-schemas`); the lock checker
`tools/lockcheck` (`make lock-check`: schema, unverified markers, and network
re-verification of every image digest, binary checksum and repository key
fingerprint with its own OpenPGP fingerprint parser), `make verify-todos`
(every marker in the repository), `make lock-update` (prints the manual
procedure); a `lock-check` CI job; the generated `ansible/requirements.yml`
and `docs/reference/versions.md`; `docs/how-to/update-versions.md`.

### Verified in step 4

All values were read from their upstream sources on 2026-10-05:

- **Container images** (13 of 14): digest of the multi-platform index by tag,
  with `linux/amd64` and `linux/arm64` present: PostgreSQL 18.6 (`trixie`;
  19 is in beta), OpenTelemetry Collector contrib 0.162.0, Prometheus 3.15.0,
  Loki 3.7.8, Tempo 2.10.8, Grafana 13.0.10, node_exporter 1.12.1, cAdvisor
  0.60.6 (published on GHCR), postgres_exporter 0.20.1 (same digest on Docker
  Hub and GHCR), nvidia_gpu_exporter 1.15.1, OPA 1.21.1 (`-static`), vLLM
  0.31.0 and the CUDA test image `13.0.2-base-ubuntu24.04`. The vLLM image is
  built on CUDA 13.0.2 (its `CUDA_VERSION`), so the GPU test image uses the
  same CUDA version; a CUDA 12.9 variant of vLLM (`v0.31.0-cu129`) exists.
- **Binaries**: restic 0.19.1 and Quarto 1.10.18, both architectures
  downloaded and hashed, matching the releases' checksum files. Quarto 1.11.5
  is the newest tag but a pre-release (quarto-web `_prerelease.json`);
  1.10.18 is the current stable release (`_download.json`).
- **Repository keys**: Docker's apt key `9DC8…CD88` (Ubuntu and Debian files
  identical) and rpm key `060A…9F35` (Fedora and CentOS files identical);
  NVIDIA's key `C95B…ECB0`, read from the gh-pages branch that
  `nvidia.github.io/libnvidia-container` serves. Fingerprints computed with gpg
  and with the lock checker's parser.
- **Packages**: Docker Engine 29.8.2, containerd 2.3.6, buildx 0.37.1 and
  Compose 5.6.0 for Ubuntu 24.04/22.04, Debian 13, Fedora 43/44 and EL 9/10
  (apt `Packages` and zstd-compressed rpm `repodata`, both architectures);
  NVIDIA Container Toolkit 1.20.1 for every apt and rpm release (one generic
  repository each).
- **Ansible collections**: the set bundled in the community package ansible
  14.4.0 (built on ansible-core 2.21.4): ansible.posix 2.2.2, community.crypto
  3.4.0, community.docker 5.3.0, community.general 13.4.0; each tag exists and
  its `requires_ansible` allows 2.21.
- `make lock-check` passes every network check that the build environment can
  reach: all 13 digests resolve, all six binary assets match, both Docker keys
  match; it fails on the 32 remaining markers and on the blocked
  `nvidia.github.io`.

### Findings to report

- **Incus LTS.** Tags `v7.0.0` and `v7.0.1` exist in lxc/incus, but the
  support policy in the repository (`doc/support.md`, also at those tags)
  still names only 6.0 as LTS ("supported until June 2029"), and
  linuxcontainers.org is not reachable. The lock stays on the 6.0 LTS line
  that BUILD_PROMPT §7.7 names (owner answer E8: newest LTS line, report the
  finding). Ubuntu's own packages are 6.0.0 on 24.04 (`6.0.0-1ubuntu0.3`),
  missing on 22.04 and 6.0.5 on 26.04 (`6.0.5-8`), so Debian and Ubuntu take
  Incus from the Zabbly LTS repository, as §7.7 allows.
- **Ubuntu 26.04** (still `TODO(verify)` in the support matrix): Docker
  publishes 29.8.2 for it, Ubuntu ships Incus 6.0.5 and the NVIDIA toolkit's
  repository is release-independent; Tailscale's support remains unverified,
  so the row is not added yet (owner answer E8 requires all four).
- **NVIDIA rpm repository.** NVIDIA's own `.repo` file sets `gpgcheck=0` and
  `repo_gpgcheck=1`; BUILD_PROMPT §7.2 forbids `gpgcheck=0`. Step 6 has to
  check whether the RPMs are signed and set `gpgcheck=1` (or raise a
  deviation).
- **Docker Hub rate limits.** Anonymous manifest requests returned HTTP 429
  during verification. The lock checker uses `HEAD` requests only and backs
  off on 429; CI runs may still hit the limit.
- **PostgreSQL 18** is the pinned major, which also fixes the conda-forge
  `postgresql` major for the platform's integration tests (owner answer A4).
- Bootstrap now installs the four collections from Galaxy (the
  `ansible-galaxy` endpoint is already required in `preflight/endpoints.yml`).

### Interpretations awaiting owner confirmation (step 4)

1. **Lock structure.** Sections carry `verified_on` and `source`; binaries and
   images carry their own source where it differs. Packages follow
   `packages.<component>.<distro>-<release>`; `el-9` and `el-10` cover RHEL,
   AlmaLinux, Rocky Linux and CentOS Stream; rolling releases are not pinned
   (hold after install, §19.4). Repository keys hold download URLs and the
   primary key fingerprint; base URLs and package names go to
   `ansible/component-sources.yml` in step 5.
2. **Release choice.** The newest stable release of each artifact on
   2026-10-05 (not an LTS line where one exists, for example Prometheus); vLLM
   0.31.0 with CUDA 13.0, which needs an NVIDIA driver that supports CUDA 13.0.
   The `-cu129` variant would support older drivers.
3. **Registry checks** use `HEAD` only: the digest must exist, a tag that has
   moved is a warning. Platform lists were verified when pinning; `lock-check`
   does not repeat that (it would need `GET` requests, which count against
   Docker Hub's anonymous limit).
4. **`make verify-todos`** lists markers in every file git tracks, except the
   specification copies, notes and pages that discuss the marker, the files
   generated from the lock, and the tools and tests that use it as data.
5. **The `lock-check` CI job** fails until every marker is resolved (owner
   answer A3).

### Open `TODO(verify)` items after step 4

`make verify-todos` lists 52 markers: 32 lines in `versions.lock.yml` (DCGM
exporter image; Tailscale and Zabbly keys; Docker packages for openSUSE Leap;
Incus and Tailscale packages for every release; Incus base image
fingerprints; GitHub SSH host keys), 16 endpoint URLs, 2 support-matrix
items, and the two command formats from step 2.

## Step 5: Ansible foundation roles

Built: the shared data (`group_vars/all/main.yml` with groups, accounts,
layout and ports; `vault.yml.example`; the commented
`inventory/host_vars/example-machine.yml`; `ansible/component-sources.yml`);
the filter plugin `ansible/filter_plugins/pinka.py` (release identity,
component source lookup, lock pins, package specs, repository URLs, apt pins,
key fingerprints); `site.yml` with the role order of BUILD_PROMPT §5.2; roles
`platform_facts`, `pkg`, `bootstrap_signal`, `preflight_gate`,
`provider_wsl2`, `provider_baremetal`, `provider_vm`, `base`, `mac`,
`users_groups`, `ssh`, `layout`, `firewall` (nftables, firewalld, ufw) and
`tailscale`, each with `README.md`, `meta/argument_specs.yml` and family vars
for all four families where the role needs them; the distribution-fact check
(`make lint-portability`); bootstrap's reboot scheduling for `--allow-reboot`;
`contrib/windows/` (keep-alive helper and `.wslconfig` template); generated
`docs/reference/{ansible-variables,component-sources,accounts}.md` and the
layout table in `docs/architecture.md`; `docs/explanation/portability.md`,
`docs/providers/*.md`, `docs/distros/*.md`, two how-tos (researchers,
firewall and SELinux) and ADRs 0003-0006.

### Verified in step 5

- `ansible-lint` (production profile) passes on every role and playbook,
  `--syntax-check` passes for `site.yml` and `restore.yml`. The pinned
  collections were installed for linting from their release tags on GitHub
  into `.ansible-collections/` (Galaxy is not reachable from the build
  environment); `make setup` installs them from Galaxy elsewhere.
- Tests in `ansible/tests/` (311): the filters on the preflight fixture
  profiles; consistency of the component source matrix with
  `versions.lock.yml` and with the supported and tested releases of the
  support matrix; role structure (required files, managed headers, prefixed
  and documented defaults, complete family files, every dynamically included
  task file, role and notified handler exists, every role applied by
  `site.yml` with its tag); group_vars against BUILD_PROMPT §6 and the port
  table of `contracts.md`; every role template rendered like
  `ansible.builtin.template`, with the nftables ruleset checked by
  `nft -c -f` in a private network namespace, the Docker/Incus script by
  ShellCheck and `sh -n`, and the sudoers drop-in by `visudo -c`.
- Ubuntu NVIDIA driver pins (`nvidia-driver-580-open` 580.178.04 for 24.04 and
  22.04) from the archive indexes; Ubuntu guest agent package names.
- `contrib/windows/pinka-wsl.ps1` parses with PowerShell 7.5.9 (release
  archive checked against its published sha256, used only in a scratch
  directory) and its `wsl` wrapper starts the keep-alive only for the lab
  distribution (tested with the Windows calls stubbed). It has not run on
  Windows (`TODO(verify)` in the file).

### Findings to report (step 5)

- **Run networks on firewalld and ufw hosts.** Per-run bridges `r-<short>` are
  created at run time, so they cannot be listed in a firewalld zone or as ufw
  rules in advance; DHCP/DNS to the host and forward isolation for them rely
  on Incus (its own firewall rules and the `pinka-run-default` ACL) on those
  backends, while the nftables backend also matches `r-*` itself. Step 6
  verifies Incus's documented firewalld and ufw setup (including interface
  wildcards) and closes the gap if a backend allows it.
- **NVIDIA rpm repository** (from step 4): `repo_rpm` always writes
  `gpgcheck=1`; step 6 checks whether NVIDIA signs the RPMs.
- **Secure Boot on Ubuntu:** how the MOK password is set during a
  non-interactive driver install, and whether Ubuntu's signed prebuilt
  modules avoid the MOK step, can only be verified on hardware
  (`TODO(verify)` in `provider_baremetal/tasks/nvidia.yml`).
- **Unverified family data** (mirrors of Fedora, EL, openSUSE and Arch are not
  reachable): base, MAC and guest-agent package names, dnf5 behavior
  (`dnf-automatic`, the `versionlock` plugin, `needs-restarting`) and `zypper` exit codes
  are marked `TODO(verify)` where they are used.

### Interpretations awaiting owner confirmation (step 5)

1. **Unverified sources are data.** Entries of `component-sources.yml` that
   could not be verified carry `verify: "TODO(verify): ..."` instead of a
   comment; the `tailscale` role and the `pkg` role refuse them, and the NVIDIA
   driver tasks treat them like "no verified method" (exit code 77). The
   generated component sources page shows them as unverified.
2. **Bridge pattern.** BUILD_PROMPT §7.5 names a variable
   `incus_bridge_prefix`; the contract fixes the bridge names (`r-*` and
   `pinka-build0`), so the roles use `pinka_incus_bridge_patterns` from
   group_vars, checked against `contracts.md` by a test.
3. **Distribution-fact check** as a repository check (`make lint-portability`,
   BUILD_PROMPT §19.1 allows a grep check) rather than a custom ansible-lint
   rule. It also flags commented-out uses.
4. **Rebooting.** The play cannot reboot its own host mid-run; with
   `--allow-reboot` bootstrap schedules `shutdown -r +1` after a reboot
   signal (76), writes its state file and exits with 76. Never in check mode.
5. **Removed researchers** are also taken out of `pinka-admin` (BUILD_PROMPT
   §6.1 only requires locking); their data stays.
6. **Incus's per-user group** for researchers is added by the `incus` role
   (step 6): the group exists only once Incus is installed.
7. **Generated pages.** Lab-wide variables are documented by the comment above
   (or at the end of) each key in `group_vars/all/main.yml`; generation fails
   on an undocumented key. Groups and service accounts carry a `description`
   field for the accounts page. The layout table lives in
   `docs/architecture.md` (BUILD_PROMPT §6.2, §15) between generated markers;
   `docs/reference/accounts.md` links to it instead of repeating it.
8. **Role tests.** Step 5 tests the roles without running them (rendering,
   structure, data); check-mode runs in disposable containers (`make
   test-roles`) belong to step 12 with the distribution test harness.

### Open `TODO(verify)` items after step 5

`make verify-todos` lists 97 markers: 39 lines in `versions.lock.yml` (the 32
from step 4 and the NVIDIA driver pins of the releases other than Ubuntu), 17
entries of `ansible/component-sources.yml`, 16 endpoint URLs, 2 support-matrix
items, 2 preflight command formats, 20 notes in role files (package and
service names per family, dnf5, zypper, Incus's firewalld/ufw and Docker
guidance, the WSL library path, the `tailscale serve` status format, the
Secure Boot MOK flow) and the Windows helper.

## Step 6: docker, nvidia_container_toolkit, incus

Built: roles `docker` (pinned and held engine, Compose and buildx;
`daemon.json` with the local log driver, live-restore, address pools,
data-root, SELinux and GPU access; break-glass group membership),
`nvidia_container_toolkit` (pinned and held; CDI through the toolkit's
`nvidia-cdi-refresh` units, WSL mode on WSL2; NVIDIA runtime only for Docker
before 25.0; a GPU check with the pinned CUDA image by digest) and `incus`
(install, ownership check with `--adopt-incus`, the pool with the device
safety checks, `pinka-build0`, the ACL `pinka-run-default`, projects
`bench`, `build` and one per researcher, the target, probe and build
profiles, restricted client certificates per service and a metrics
certificate, the HTTPS API on `127.0.0.1:8443`,
`pinka-incus-events.service`, the marker file); `incus/preseed.yaml.j2`,
`incus/projects/*.yaml.j2`, `incus/profiles/*.yaml.j2`; the role module
`pinka_incus_object` (create or update one Incus object through
`incus query`, never delete); firewall rules for run, build and personal
bridges on all three backends; `docs/explanation/target-isolation.md`;
ADRs 0007-0009.

### Verified in step 6

From upstream sources reachable through git and the Ubuntu archive:

- **Incus 6.0.6** (lxc/incus tag `v6.0.6`): `security.guestapi` is the key for
  `/dev/incus` (contract item); proxy devices on VMs support NAT mode only and
  NAT mode requires `bind=host` (contract item C4: VMs need the ACL-restricted
  route); bridge ACLs apply at the bridge-to-host boundary, so an ACL without
  rules rejects everything leaving a run bridge while Incus's DHCP and DNS
  baseline rules still apply; only OVN networks may live in network-enabled
  projects; `incus-user` creates `user-<uid>` projects with a bridge
  `incusbr-<uid>` and a profile on a pool named `default`, without limits;
  `incus monitor` flags (`--all-projects`, `--type`, `--format json`);
  `incus config trust add-certificate` flags; `incus admin init --preseed`
  creates or updates pools, networks, projects and profiles; the Docker
  coexistence rules and the firewalld/ufw guidance of
  `doc/howto/network_bridge_firewalld.md`. Ubuntu's `incus` 6.0.0-1 package
  (noble, sha256 checked against the archive index) creates the groups
  `incus` and `incus-admin` and the sockets `/var/lib/incus/unix.socket`
  (group `incus-admin`) and `/var/lib/incus/unix.socket.user` (group `incus`).
- **Docker Engine 29.8.2** (moby tag `docker-v29.8.2`): `daemon.json` keys
  (`labels`, `log-driver`, `log-opts`, `live-restore`,
  `default-address-pools` with `base`/`size`, `data-root`, `features.cdi`,
  `selinux-enabled`, `runtimes`); CDI is enabled by default on Linux unless
  `features.cdi` is false; the local driver's defaults are 20 MiB and 5 files;
  `dockerd --validate --config-file` exists; engine labels only need
  `key=value`.
- **NVIDIA Container Toolkit 1.20.1** (tag `v1.20.1`):
  `nvidia-cdi-refresh.path`/`.service` in `nvidia-container-toolkit-base`
  write `/var/run/cdi/nvidia.yaml`; auto mode resolves to `wsl` on WSL;
  `NVIDIA_CTK_CDI_GENERATE_MODE` sets the mode; `nvidia-ctk runtime
  configure` writes `runtimes.nvidia` with `path` and empty `args`.
- **NVIDIA rpm signatures** (finding of step 4): the 1.20.1 RPMs in NVIDIA's
  repository carry an RSA header signature whose issuer fingerprint is the
  pinned key `C95B321B61E88C1809C4F759DDCAE044F796ECB0`, so `gpgcheck=1`
  works; the role keeps it.
- **firewalld** accepts interface names ending in `+` as wildcards (nftables
  backend since 0.7.0); **ufw** 0.36.2 (Ubuntu 24.04 package) accepts `+` in
  interface names and passes it to iptables.
- Tests: the module against an in-memory Incus API (create, update, create
  only, project-scoped profiles, ACL rules, server config, certificates by
  fingerprint, check mode); `desired.yml` evaluated task by task as Ansible
  runs it; every new template rendered (`daemon.json` for each GPU method,
  the CDI drop-in, the events unit, every Incus project and profile, the
  preseed).

### Findings to report (step 6)

- **Run networks and restricted certificates (contract).** With
  `features.networks=false` (required, because bridges can only exist in the
  default project), a certificate restricted to `bench` can create a run
  network but cannot change or delete it: Incus authorizes network objects
  against the network's project, which is `default`. The platform, as the
  contract stands, could not delete run networks. Proposed resolution (needs
  owner approval and a contract change; nothing is implemented yet): a
  socket-activated root helper like C3 that creates and deletes `r-<short>`
  bridges with the fixed settings (no NAT, no uplink, ACL `pinka-run-default`),
  reachable only by `potka-orch` (peer credentials). Alternatives: a second
  restricted project for networks is not possible for bridges; giving the
  orchestrator's certificate the default project would let it change golden
  images and personal networks.
- **Incus 7.0.** Tags up to `v7.5.1` and a `stable-7.0` branch exist and the
  support page mentions "Incus 7.0 LTS' release", while its list still names
  only 6.0 as LTS. BUILD_PROMPT §7.7 asks for 6.0.x; the roles stay on 6.0.
- **Bench network access.** `restricted.networks.access` takes exact names, so
  `bench` cannot be limited to `r-*` networks; the platform's services must
  only attach run networks (its integrity check can verify it from the events
  feed).
- **VM telemetry.** Confirmed that the contract's alternative (C4) applies;
  the dedicated collector listener and its ACL rule are defined with the
  collector in step 7.

### Interpretations awaiting owner confirmation (step 6)

1. **Personal projects in advance.** Pokus Infrastructure (pinka) creates `user-<uid>`,
   `incusbr-<uid>` and the project's default profile before `incus-user` does,
   with incus-user's restrictions plus limits (`incus_user_limits`) and
   without GPU access (incus-user allows GPUs; the GPU is reserved for vLLM).
   incus-user then only adds the researcher's certificate.
2. **Personal bridges** get DHCP, DNS and forwarding like the build bridge
   (trusted zone on firewalld, allow and route rules on ufw), as Incus
   documents; they are infra-internal and not part of the contract.
3. **daemon.json header.** JSON has no comments and dockerd rejects unknown
   keys, so the managed header is the engine label `org.pinka.managed=...`;
   preflight's marker check finds it there.
4. **GPU check.** BUILD_PROMPT §7.6 says `--gpus all`; with CDI the check
   requests `--device nvidia.com/gpu=all` (what the services use), with the
   NVIDIA runtime `--runtime nvidia --gpus all`. It records stable
   `nvidia-smi --query-gpu` fields so a second run changes nothing.
5. **Preseed plus convergence.** The preseed initializes a new installation
   once; every run converges the same objects with `pinka_incus_object`
   (re-applying a preseed would rewrite `auto` network addresses). The pool
   and networks are created once and never updated.
6. **Bench limits** default to 60 % of the host's memory and all effective
   CPUs but two; `limits.virtual-machines` is 0 without `vm_targets`, else
   `incus_max_virtual_machines` (4).
7. **Compose units** (`pinka-core.service` and the others, BUILD_PROMPT §7.6)
   are written in step 7 with the services they run.
8. **Fingerprint inputs.** The installed Docker and Incus versions are read by
   the fingerprint in step 10; Incus's source and channel are recorded in
   `/etc/pinka/incus-source.json`.

### Open `TODO(verify)` items after step 6

98 markers: as after step 5, minus the three firewall and Docker coexistence
notes resolved from Incus's documentation, plus the Incus group, unit and path
names of Zabbly's packages and of the Red Hat, SUSE and Arch families.

## Step 7: postgres, observability, opa, vllm, models

Built: the helper role `compose` (Compose project under
`/srv/pinka/services/<project>/`, `.env` with images by digest, ports, the
groups' GIDs, the Docker data root, the GPU profile and one configuration hash
per `config/<name>/` directory used as a service label, so a changed file
recreates exactly its service; `pinka-<project>.service`, oneshot with
`RemainAfterExit`, `up --wait`/`down`, part of `pinka-lab.target`); roles
`postgres` (socket directories, generated superuser and exporter passwords,
password files, the reader's pgpass, the bootstrap SQL, SCRAM password
convergence, durability assertions), `opa` (authorization policy, deny-all
placeholder, policy link), `observability` (collector gateway and journald
collector, Prometheus, Loki, Tempo, Grafana with data sources, five
dashboards and researcher accounts, node_exporter, cAdvisor, the nvidia-smi
GPU exporter, the VM listener of C4), `vllm` (serving profile `ada-12g`,
deploy helper behind `pinka-vllm-deploy.socket`, default deployment, serving
verification) and `models` (pinned `hf` CLI in a hash-locked uv environment,
`models-fetch`); `compose/{core,observability,inference}/compose.yml`;
`config/{postgres,opa,otel,prometheus,loki,tempo,grafana}/`;
`models.lock.yml`, `models.catalog.yml` (proposed) and their schemas; filters
`pinka_scram_verifier`, `pinka_scram_matches`, `pinka_config_hashes`;
`pinka-lockcheck fetch` and the pinned OPA CLI for tests; the firewall rule
and the run ACL rule of the VM listener; per-account primary groups in
`users_groups`; `docs/explanation/telemetry.md`,
`docs/how-to/add-a-model.md`, `docs/how-to/reach-grafana.md`, ADRs 0010-0011.

### Verified in step 7

- **Configurations against the pinned releases** (downloaded into a scratch
  directory, checksums matched against the published lists, nothing
  committed): `otelcol-contrib 0.162.0 validate` accepts both collector
  configurations (the journald one up to the host root, which only exists in
  the container); `promtool check config` (Prometheus 3.15.0); `loki
  -verify-config` (3.7.8); `tempo -config.verify` (2.10.8). Loki, Tempo,
  Prometheus and the gateway were then run with these configurations: every
  listening socket was on `127.0.0.1` (no ring or cluster port), and an
  OTLP log, span and metric sent to the gateway arrived in Loki (filterable by
  `potka_run_id`), Tempo and the Prometheus exporter (label `potka_run_id`).
- **OPA 1.21.1**: the static binaries of the release match their `.sha256`
  files (pinned as `binaries.opa` for tests); the authorizer's input is
  `{path: [segments], method}`; `decision_logs.console` exists. The Rego unit
  tests and a live server test show that decision queries and `/health` work
  and every policy or data write, the policy listing and other APIs get 401.
- **PostgreSQL**: the rendered `postgresql.conf` and `pg_hba.conf` and the
  bootstrap SQL ran against a real server (PostgreSQL 16 of the build
  environment and of CI; the pinned image runs 18, the SQL is the same):
  the bootstrap is idempotent, `pg_hba` admits only the contract's roles,
  `potka_reader` and `potka_grafana` are denied `gt` and a mistaken grant is
  revoked on the next run, the SCRAM filters match secrets PostgreSQL stored
  and PostgreSQL accepts the secrets they compute. The SCRAM implementation
  also reproduces the RFC 7677 test vector. libpq (REL_18) ignores a password
  file with group access (finding below). The official image's entrypoint
  reads `POSTGRES_PASSWORD_FILE` as root and as `postgres` (UID 999).
- **Grafana 13.0.10**: provisioning files expand `$__file{}` (and trim the
  file), `$$` is a literal `$`, and the result passes through
  `os.ExpandEnv` (hence no `$` in the database password); the PostgreSQL data
  source takes a socket directory as URL; `grafana cli admin
  reset-admin-password --password-from-stdin` exists; the admin user API
  routes used by the role exist.
- **postgres_exporter 0.20.1** builds `postgresql://user:pass@` +
  `DATA_SOURCE_URI` and opens it with lib/pq 1.10.9, whose `ParseURL` sorts
  its parameters, so the socket is given with an empty URL host
  (`/pinka?host=/run/pinka/postgres`).
- **Image users** from the images' Dockerfile at each pinned tag: Prometheus and
  node_exporter `nobody`, Loki, Tempo and the collector 10001, Grafana 472:0,
  the GPU exporter 65534, cAdvisor root; vLLM runs as root (`vllm serve`
  entrypoint).
- **Incus 6.0.6** generates its server certificate with the host's addresses
  (127.0.0.1 included), so Prometheus verifies it as `ca_file`.
- **Compose 5.6.0** prints `Created`, `Recreated` and `Started` events in its
  plain progress output (the role's change detection).
- **vLLM 0.31.0**: `vllm serve --config <yaml>` with `model` in the file;
  booleans, lists and nested mappings in the file; the tool-call parser names
  used (`hermes`, `phi4_mini_json`, `llama3_json`);
  `--override-generation-config`, `--otlp-traces-endpoint` (OpenTelemetry is
  part of vLLM's common requirements), `--enable-lora`, `--max-loras`,
  `--max-lora-rank`; metric names for the dashboards.
- **huggingface_hub 2.1.1**: the wheel matches PyPI's sha256; it installs the
  `hf` CLI with `hf download REPO FILES --revision --local-dir` (token from
  `HF_TOKEN`); the hash-locked environment installs on CPython 3.12.15 with
  uv, and a second `uv pip sync` reports "Checked", which the role treats as
  no change.

### Findings to report (step 7)

- **Reader credential and libpq.** Owner answer E13 makes the reader
  credential 0640 root:pinka, but libpq ignores a password file that its group
  may read. The file keeps its contract path and pgpass format; researchers
  copy it to `~/.pgpass` with mode 0600 (documented), the platform reads the
  password itself.
- **pg_hba and two socket directories.** `pg_hba` cannot distinguish socket
  directories, so the contract's "role `potka_reader` only (`pg_hba`)" for
  the reader directory cannot be enforced by `pg_hba`. The directory groups
  decide who reaches a socket, the password files decide the role; the
  contract's socket table now says so (proposed contract change).
- **Service account groups.** The contract owns sockets and password files by
  groups named after service accounts (`potka-orch`, `potka-gw`, ...), which
  `useradd` creates only where `USERGROUPS_ENAB` is set (not on openSUSE).
  `users_groups` now creates them explicitly.
- **Model candidates.** Hugging Face is blocked from the build environment, so
  the catalog's candidates (Qwen3-8B-AWQ, Qwen3-4B-AWQ,
  Qwen2.5-7B-Instruct-AWQ, Phi-4-mini-instruct; Llama 3.1 8B AWQ under
  `restricted`) are proposals from prior knowledge with `TODO(verify)`
  revisions and estimated VRAM; they need the owner's approval (E7) and a
  check on the Hub before any enters the lock.
- **Run explorer and platform tables.** The run explorer's database panel
  queries `public.runs` by `run_id`; the platform's migrations decide the real
  table (to align in platform phase).

### Interpretations awaiting owner confirmation (step 7)

1. **Observability on the host network.** BUILD_PROMPT §7.9 asks Prometheus to
   scrape the Incus metrics with the metrics certificate; Incus listens on
   `127.0.0.1` only, which no container on a Docker bridge can reach. All
   observability containers therefore use the host network and bind
   `127.0.0.1` explicitly (no published ports); tests check every listen
   address (ADR-0011).
2. **Grafana and the exporter on sockets.** BUILD_PROMPT §7.8 lets Grafana
   connect over an internal Docker network; with the host network it connects
   through the reader socket directory instead (as `potka_grafana`), the
   exporter through the service directory (as `pinka_monitor`), and
   PostgreSQL has no network at all (ADR-0010; proposed contract change for
   the socket peers).
3. **A second collector for the journal.** The journald receiver needs the
   host root file system; it runs in a separate collector instance that
   forwards to the gateway, so the instance that accepts OTLP from targets has
   no access to host files.
4. **VM listener.** `198.18.0.1/32` (RFC 2544 benchmarking range, configurable
   as `pinka_vm_otlp`) on the dummy interface `pinka-otlp0`, created before
   Docker starts; the gateway listens there with `vm_targets` only. It is the
   one non-loopback lab listener, admitted by C4 and restricted by the run
   ACL and the firewall to run networks and the OTLP ports.
5. **vLLM on 127.0.0.1 only.** BUILD_PROMPT §7.11 mentions an internal Docker
   network plus `127.0.0.1`; vLLM uses the host network (so its OTLP traces
   reach the gateway on loopback) and binds `127.0.0.1:18704` only.
6. **Rendered vLLM configuration.** The deploy helper renders
   `current.yaml` (a `vllm serve --config` file) next to the contract's
   `current.env`, which carries the configuration's hash so that Compose
   recreates vLLM on a change. Overrides are limited to the profile's
   `overridable` keys and sampling defaults to numeric generation keys;
   chat templates must be verified files of the model.
7. **Deployment files are immutable**: the role refuses a different default
   spec under an existing `<id>@<version>`; once `current.yml` exists, the
   role re-renders it and never replaces the orchestrator's choice.
8. **Generated database passwords.** The superuser and exporter passwords are
   not in the vault (BUILD_PROMPT §8.2 lists only the contract roles); they
   are generated on the host once. Container secrets live in
   `/etc/pinka/secrets/containers/` (0700 root), each file 0400 owned by the
   UID of the container that reads it.
9. **Adapters in `models.lock.yml`.** C9e defines adapter directories by the
   sha256 of the weights; the lock lists adapters like models, with a
   `weights` file and `base_model_id`, and `models-fetch` downloads and
   verifies them.
10. **Pinned OPA CLI for tests.** `binaries.opa` (format `binary`, a new
    lock format) is a development and CI tool, fetched and checked by `make
    setup`; it is the release of the pinned OPA image.
11. **GPU services per method.** Compose cannot add a device conditionally;
    each GPU service exists twice, in the profiles `gpu-cdi` (CDI device) and
    `gpu-runtime` (NVIDIA runtime), and `.env` enables the one matching
    `daemon.json`.
12. **Models and vLLM only on serving nodes.** Both roles run with
    `gpu_inference` on `full` and `inference` nodes; how the other roles
    behave on `inference` nodes is step 11 (`inference_control`).

### Open `TODO(verify)` items after step 7

107 markers: the 98 of step 6, the five catalog revisions and two
explanatory lines of `models.catalog.yml`, and two in
`docs/how-to/add-a-model.md` (the Hub's tree API fields, and the catalog
warning).

## Step 8: images

Built: the `images` role (per blueprint: build when neither alias nor export
exists, import the export when only the alias is missing, export when only
the export is missing; the pinned upstream base copied by fingerprint and
exported once to `/srv/pinka/images/_upstream/<base>/<fingerprint>/`; the
build in project `build` with launch, cloud-init wait, leakage baseline,
nested `ansible-playbook` over `community.general.incus`, tests, package
record, leakage check, clean, stop, `incus publish` with the contract's
properties, export with `SHA256SUMS`, `packages.txt` and `build.json`); the
leakage lint `files/pinka_leakage_lint.py` (runs inside the build instance);
the export helper `/usr/local/libexec/pinka/image-files`; the clean script;
six blueprints in `images/` (`_base`, `web-nginx-flask`, `pg-primary`,
`redis-cache`, `docker-host`, `probe-client`) with tests, and
`images/_base/agent.yml`, which configures the in-image OpenTelemetry agent
after every blueprint; the blueprint schema
`tools/lockcheck/schema/image-blueprint.schema.json` (checked by `make
lint-schemas`, the playbooks by `make lint-ansible`'s syntax check); the lock
entries `binaries.otelcol_contrib` (format `deb`) and
`containers.target_images.docker_host_web`, which `make lock-check` verifies
like the other images; the filter `pinka_image_by_alias`;
`docs/how-to/build-golden-images.md`, `docs/explanation/golden-images.md` and
ADR-0012.

Also in this step: the collector configurations of step 7 use the component
names of otelcol-contrib 0.162 (`otlp_grpc`, `otlp_http`, `host_metrics`,
`resource_detection`, `file_log`); the old names are deprecated aliases that
0.162 accepts with a warning. Both configurations validate again without
warnings.

### Verified in step 8

- **otelcol-contrib 0.162.0 packages**: both `.deb` files downloaded, sha256
  equal to the release's per-file `.sha256` files (pinned with `verified_on`).
  The agent configuration rendered for `_base`, `web-nginx-flask` (with
  `file_log`) and `pg-primary` passes `otelcol-contrib validate` without
  warnings.
- **nginx 1.30.5-alpine**: index digest read from the registry API (nginx's
  stable line), platforms amd64 and arm64.
- **Incus 6.0.6** (source of the tag): the CLI options used (`launch
  --profile --config --vm`, global `--project`, `file push --create-dirs
  --recursive --mode`, `publish --alias --reuse key=value...`, `image copy
  <remote>:<fingerprint> local: --vm`, `image import <metadata> <root file
  system> --alias`, `image alias create`); `image export <fingerprint> <dir>/` writes
  `<fingerprint><ext>` for a unified image and `meta-<fingerprint><ext>` plus
  `<fingerprint><ext>` for a split one, whose fingerprint is the sha256 of the
  metadata followed by the root file system; `publish` starts from the
  instance's metadata (inherited from its base image) and merges the given
  properties, which travel inside the metadata through export and import;
  `requirements.nesting=true` stops an instance without `security.nesting`
  from starting; a project with `features.images=false` uses the default
  project's images.
- **cloud-init 26.1** (Ubuntu 24.04 updates): `cloud-init clean --logs
  --machine-id --seed` exists (the machine-id becomes `uninitialized`, which
  systemd replaces on first boot); `cc_ssh` deletes and regenerates the SSH
  host keys of every new instance by default.
- **Leakage lint calibration.** The specified patterns, applied as
  substrings, find 523 tokens in a stock Ubuntu 24.04 cloud root file system
  (`default` and its compounds, `ISO-8859-*`, `TIS-620`, `FIPS-140-2`,
  `CVE-*`, `scenario` in `faillock.conf`, `fault` in `lvm.conf`); all of them
  are baseline. The 33 packages that the six blueprints add to that root
  (dependency closure over the noble, noble-updates and universe indexes,
  downloaded from archive.ubuntu.com and checked against the indexes' sha256)
  plus the agent package add 26 tokens: `default` compounds and
  `scenario`/`scenarios` in the comments of `/etc/redis/redis.conf`. With the
  two default allow rules the check reports none. The rendered recipe files
  pass the lint (a test).
- **Package contents**: `/usr/bin/gunicorn` (universe; the cloud images
  enable main, universe, restricted and multiverse), the units `nginx`,
  `postgresql`, `redis-server`, `docker` and `containerd`, `ab`,
  `redis-cli` and `psql` where the tests expect them.

### Findings to report (step 8)

- **No build was run.** This environment has no Incus daemon and must not be
  provisioned; the build flow is checked by syntax checks, ansible-lint, unit
  tests of its decisions and expressions, and the CLI behavior read from the
  Incus source. The first real build happens on a lab host (Phase 5
  self-test).
- **Every build waits for the base fingerprint.** `incus_base_images` is
  still `TODO(verify)` (images.linuxcontainers.org is blocked here); the role
  refuses to build until it is pinned. The how-to shows how to read it.
- **In-image files name the project.** Files installed into images carry the
  repository's managed-file header ("Managed by pinka (...)"), as
  BUILD_PROMPT §14 requires of templates. The word `pokus` is not one of the
  specified leakage patterns, so the lint does not object; adding `(?i)pokus`
  to `images_leakage_patterns` and dropping the header from in-image files is
  possible if the owner wants images not to name the project.

### Interpretations awaiting owner confirmation (step 8)

1. **Leakage lint design** (ADR-0012): a baseline of the base image taken
   inside the build instance before provisioning; findings are whole tokens;
   allow rules name benign words that are cut out before the patterns are
   applied again. The specified patterns are kept as substrings, `fault`,
   `inject` and `scenario` case-insensitive and `ground_truth` also as
   `ground-truth`/`groundtruth` (stricter than specified). Default allow
   rules: `(?i)default`, and `(?i)scenarios?` in `etc/redis/redis.conf`.
2. **Distribution packages are not version-pinned** in blueprints (the Ubuntu
   archive keeps one version per package); the installed versions are recorded
   in the export's `packages.txt`, and the base is pinned by fingerprint.
3. **Image properties beyond the three of the contract**: Incus's
   `requirements.nesting` (true for blueprints with `nesting`, so the platform
   knows to launch them with `security.nesting=true`; proposed contract
   addition) and `description`; both are always set because a published image
   inherits its base's metadata.
4. **`pinka.recipe_commit`** is the repository commit bootstrap records, with
   `-dirty` appended when the infra tree had changes (`--allow-dirty`).
5. **Exports**: the sum file is `SHA256SUMS` in `sha256sum` format; every
   export has `build.json` (written last; its presence marks a complete
   export); the upstream base goes to `_upstream/<base>/<fingerprint>/` with
   `source.json`; files 0640 and directories 0750, root:pinka-admin.
6. **Missing alias with an export** is restored by importing the export (or
   by re-creating the alias when the image is still in the store), never by
   building a different image; `images_rebuild` forces a build.
7. **A failed build keeps its instance** `build-<blueprint>` for inspection;
   the next run deletes and replaces it. Successful builds delete theirs.
8. **Nesting per build instance**: the `build` profile enables nesting; each
   build instance sets `security.nesting` to its blueprint's value.
9. **Telemetry settings**: the agent always reads the whole journal; a
   blueprint adds log files that do not go to the journal
   (`telemetry.log_files`). `_base/agent.yml` renders the agent configuration
   after every blueprint's provisioning, so each image has its own.
10. **Tests**: a build runs its own blueprint's tests; derived blueprints do
    not repeat `_base`'s.
11. **Where the role runs**: `node_role` `full` and `lab`, last in `site.yml`;
    in check mode it reports each blueprint's planned action and changes
    nothing.

### Open `TODO(verify)` items after step 8

107 markers, unchanged: step 8 resolved nothing and added nothing. The Incus
base image fingerprints (two lock lines) now block every image build.

## Step 9: lab_session, backup, restore.yml, rotate.yml

Built: the `lab_session` role (`pinka-lab.target`, `pinka-health-gate.service`
with `/usr/local/libexec/pinka/health-gate` and `/etc/pinka/health-gate.json`,
`pinka-session-start.service` with `/usr/local/libexec/pinka/session-start`);
the `backup` role (pinned restic, repository and password files, SFTP key and
pinned host keys, `/usr/local/libexec/pinka/backup` with `pgdump`, `run`,
`prune`, `snapshots`, `restore`, `pgrestore` and `passwd`, three services with
persistent timers, textfile metrics); `restore.yml` (replacing the step 3
stub); `rotate.yml`; the entry points `images/tasks/restore.yml`,
`postgres/tasks/rotate.yml`, `incus/tasks/rotate_certificates.yml` and
`backup/tasks/rotate.yml`; `models-fetch --reverify` (`models_reverify`);
`docs/how-to/backup-and-restore.md`, `docs/how-to/rotate-secrets.md`,
`docs/explanation/lab-session.md`.

### Verified in step 9

- **restic 0.19.1** (source of the tag): `RESTIC_REPOSITORY_FILE`,
  `RESTIC_PASSWORD_FILE`, `RESTIC_CACHE_DIR`; exit codes 0, 1, 3 (backup could
  not read some data), 10 (repository does not exist, since 0.17.0), 11, 12,
  130; `cat config` to test for a repository; the `--json` summary message of
  `backup` (`message_type: summary`, `snapshot_id`, `data_added`,
  `total_files_processed`, `total_duration`) and the fields of `snapshots
  --json`; `backup --files-from --exclude-file --tag --host`; `forget
  --keep-daily --keep-weekly --keep-monthly --host --tag --prune`; `restore
  <id> --target --include` (an included directory brings its contents, the
  pattern `/foo` matches `/foo/bar`); `latest`; `key passwd
  --new-password-file` (adds a key, validates it, removes the old key); the
  SFTP option `sftp.args`, split like a shell command, which cannot be
  combined with `sftp.command`.
- **Incus 6.0.6**: `incus config trust remove <fingerprint>`; `trust list`
  columns (`--columns nfi` for name, fingerprint and issue date).
- **otelcol-contrib 0.162.0**: the `health_check` extension answers `GET /`
  with 200 and `{"status":"Server available",...}` (run with a minimal
  configuration).
- The SNTP client of the health gate against a local fake server (test).

### Findings to report (step 9)

- **Nothing here could be run against real services**: no systemd, Docker,
  PostgreSQL container, Incus or restic repository are available, and the
  host must not be provisioned. The helpers are tested with fake runners,
  the plays with syntax checks, ansible-lint and expression tests.
- **The session record needs `pinka`** (infra step 10) for the
  fingerprint and the fresh preflight. Until it exists, records are written
  with `fingerprint`, `fingerprint_hash` and `preflight_outcome` set to
  `null` and the reason under `errors`. Step 10 must print
  `{"hash": ..., ...}` from `pinka fingerprint --json` and a preflight
  report with `outcome` from `pinka preflight --json`.
- **Doctor at the end of a restore** runs only when `/usr/local/bin/pinka`
  exists (step 10); before that the play says so.
- **Restore drill and the repository.** A spare machine restored for a drill
  uses the production repository under the same machine name; its backup and
  prune timers must be disabled right after the restore (documented), or its
  snapshots would mix with production's and its retention job would count
  them together.

### Interpretations awaiting owner confirmation (step 9)

1. **What a restore brings back.** All directories of the snapshot below
   `/srv/pinka`, and from `/etc/pinka` only what bootstrap cannot render:
   `vllm/deployments`, `vllm/current.yml` and `qa/`. Host profile, bootstrap
   state, nftables rules, Incus certificates and other rendered files come
   from the bootstrap run that precedes the restore. Files newer than the
   snapshot are kept (no `--delete`). Secrets are never restored, even when
   they were backed up.
2. **Which files count as secrets** for `backup_include_secrets`:
   `/etc/pinka/secrets`, `/etc/pinka/postgres-reader`, `/etc/pinka/incus`,
   `/etc/pinka/signing` and `/etc/pinka/inference` (private keys and
   passwords), not only `/etc/pinka/secrets`.
3. **Snapshot choice.** An interactive restore prompts (empty answer: the
   newest); a non-interactive one needs `restore_snapshot` (ID, prefix of at
   least eight characters, or `latest`) in host_vars and otherwise stops.
4. **Repository password.** The password file is written once; a changed
   `vault_restic_password` is reported by bootstrap and applied only by
   `rotate.yml --tags restic` (`restic key passwd`), because rewriting the
   file would lock the lab out of its repository.
5. **The repository is initialized on first use** by the backup helper (exit
   10 of `restic cat config`), not by the role, so that an unreachable backup
   server does not stop bootstrap.
6. **SFTP authentication**: a dedicated ed25519 key generated on the host and
   host keys from `backup_sftp_known_hosts` (`StrictHostKeyChecking=yes`);
   bootstrap refuses an SFTP repository without host keys.
7. **Health gate checks and thresholds**: timeout 600 s, interval 5 s; WSL
   clock limit 1 s against `pool.ntp.org` (configurable, skipped when it does
   not answer); PostgreSQL and Incus checked on `full` and `lab` nodes; the GPU
   checked with `docker run --pull never` of the pinned CUDA image with the GPU
   method of `daemon.json`.
8. **Autostart**: `multi-user.target` wants `pinka-lab.target` by default
   (`lab_session_autostart`); bootstrap starts the target, which waits for the
   gate and the session record.
9. **`rotate.yml` scope**: host-generated secrets (database superuser with a
   staged change, exporter), Incus client certificates (the one Incus
   deletion: the replaced trust entries) and the restic password. Vault
   passwords change by editing the vault and running bootstrap. The signing
   key belongs to the platform role (step 11) and its rotation is added there.
   `rotate.yml` is run with `ansible-playbook` directly (bootstrap has no
   rotate option) and reads the host profile itself.
10. **Schedules and retention**: dump 02:30, backup 03:00, prune Sunday
    04:00 (each with up to 5 minutes of random delay), keep 7 daily, 4 weekly
    and 12 monthly snapshots, 7 local dumps.
11. **Timers outside the lab target**: backups run whether or not the lab is
    up.

### Open `TODO(verify)` items after step 9

107 markers, unchanged.

## Step 10: pinka CLI with doctor and fingerprint

Built: `tools/pinka-lab/` (the `pinka_lab` package, standard library only and
source-compatible with Python 3.9: `cli`, `config`, `system`, `doctor`,
`checks` with 28 checks, `smoke`, `fingerprint`, `commands`; the POSIX shell
wrapper `pinka`; the JSON Schema `schema/fingerprint.schema.json`; 113
unittest-style tests that also run on CPython 3.9 in `make test-py39`); the
`doctor` role (installs the package and a copy of preflight under
`/usr/local/lib/pinka/`, the wrapper as `/usr/local/bin/pinka`, and
renders the lab description `/etc/pinka/lab.json`), placed in `site.yml`
before `lab_session`; environment selection of blueprints in the images role
(`PINKA_IMAGES_BLUEPRINTS`, `PINKA_IMAGES_REBUILD`) for `pinka images
build`; generated reference pages `reference/cli/pinka.md`,
`reference/doctor-checks.md`, `reference/fingerprint.md` and a pinka
section in `reference/exit-codes.md`, all covered by the docs coverage check
(every command, option, check, troubleshooting section and schema field);
`docs/reproducibility.md`, `how-to/start-stop-drain.md`,
`how-to/update-host.md`, `how-to/fix-doctor-checks.md` (one section per check
ID); `pinka user` shortcuts in `how-to/manage-researchers.md`.

Fixed on the way: the sleep inhibitor of `power_policy: lab_session_inhibit`
(step 5) was installed but never enabled, so `pinka-lab.target` never pulled
it in; it is now enabled, and removed together with its wants link when the
policy changes. CLI reference usage lines no longer depend on the terminal
width of the machine that generates them (`COLUMNS`).

### Verified in step 10

- The package imports and its tests pass on CPython 3.9 and 3.12; the shell
  wrapper runs isolated (`-I`), ignores `PYTHONPATH` and reaches the CLI
  (test).
- `RealSystem` against real processes, files, a loopback HTTP server and an
  HTTP server on a Unix socket (tests); a failed Unix socket connection no
  longer leaks the socket.
- argparse on 3.9, 3.11 and 3.12 puts every option after `--` into
  `pinka restore`'s bootstrap options; `argparse.REMAINDER` did not take
  a leading option and was replaced.
- The rendered lab description: every key path the CLI reads exists for a
  `full` node, and node roles, WSL, GPU methods and VM targets change it as
  expected (Ansible's own templating, `ansible/tests/test_doctor.py`).
- Fingerprint documents (bare metal, WSL, minimal profile) validate against
  the fingerprint schema with preflight's validator.
- Statically imported roles expose their defaults to the doctor role even
  with `--tags doctor`; dynamically included roles (images, vllm) do not, so
  the role reads the images role's defaults and vars files explicitly
  (`ansible-playbook --check` of a scratch play).
- The gateway collector's `resource_to_telemetry_conversion` and Loki's
  structured metadata make `potka_run_id` queryable in Prometheus and Loki,
  which the telemetry round trip and the smoke checks use.

### Findings to report (step 10)

- **Nothing could be run against a real lab**: no systemd, Docker, Incus,
  Tailscale, PostgreSQL or GPU here. Every check is tested against a fake
  host built from tables; the command lines and output formats they parse
  (`ss -H -lntup`, `getfacl`, `tailscale status --json`, `incus query`,
  `systemctl show`, Loki, Tempo and Prometheus query APIs) follow their
  documentation and the upstream sources read in earlier steps, but a first
  `pinka doctor` on a provisioned host is the real test.
- **Contract additions (proposed)**: the fingerprint document format and its
  schema, and the drain endpoints `POST /lab/drain` and `GET /lab/status` on
  the orchestrator's `api.sock` (`contracts.md`, "Session records and
  fingerprint"). The platform must implement the drain endpoints for
  `pinka stop` to drain; until then there is no socket and nothing to
  drain.
- **PostgreSQL and OPA on inference nodes**: `site.yml` still applies the
  `postgres` and `opa` roles on every node; BUILD_PROMPT §19.10 gives an
  inference node only the inference stack. The lab description and doctor
  already treat inference nodes that way; the plays are aligned in step 11
  together with `inference_control`.
- **`pinka docs serve`** (owner answer H9, port 18706) belongs to the
  documentation work and is added in step 14.

### Interpretations awaiting owner confirmation (step 10)

1. **The lab description `/etc/pinka/lab.json`** is the CLI's only input
   besides the host: rendered by the doctor role from the host's variables
   (units, containers with pinned digests, layout, accounts, Incus objects,
   PostgreSQL roles, endpoints, blueprints, thresholds). `pinka` never
   reads the repository or the vault.
2. **Installation**: package copies under `/usr/local/lib/pinka/` (root, 0644)
   instead of running from the checkout, with a copy of preflight so that
   `pinka preflight` and doctor do not depend on the checkout; modules
   removed from the sources are deleted. The wrapper prefers the pinned
   CPython and falls back to `python3` 3.9 or newer.
3. **Doctor needs root** (several checks read root-only state) and exits 2
   when it cannot run at all (no lab description, not root). Other commands
   exit 0 or 1; usage errors 2.
4. **Severities**: warnings are `preflight.drift`, `incus.pool`,
   `incus.metrics`, `incus.events`, `backup.recent`, `lab.session`,
   `lab.rolling_drift` and `wsl.config`; everything else is an error. Without
   a backup repository `backup.recent` fails as a warning (owner answer E4),
   so a fresh lab ends bootstrap with exit code 1 until backups are set up.
5. **Listeners**: besides sshd, the processes `tailscaled`, `dnsmasq`
   (Incus's DHCP and DNS on its bridges), `systemd-network`, `dhclient`,
   `NetworkManager` and `chronyd` may listen outside loopback
   (`doctor_listener_processes`), and with `vm_targets` the collector's VM
   listener address.
6. **Telemetry round trip**: one log record, span and gauge sent as OTLP/HTTP
   JSON to the gateway with `potka.run_id=doctor-<12 hex>`, then queried in
   Loki, Tempo and Prometheus for up to `doctor_telemetry_wait` (60 s).
7. **Smoke checks**: VM blueprints are skipped; the telemetry assertion is a
   log line in Loki tagged with the throwaway run ID (end to end through the
   gateway); "no route to lab services" is tested by connecting from the
   target to the run network's gateway address on the lab's ports
   (OPA, Grafana, Prometheus, Loki, Tempo, vLLM); a missing `curl` or `nc`
   counts as a failure, not a pass.
8. **Unit and container lists**: the inference Compose project is not in the
   unit list (it runs only while a deployment is active; `gpu.vllm` covers
   it); `pinka-incus-events.service` is checked separately because it is not
   part of `pinka-lab.target`.
9. **`pinka restore`** takes bootstrap options after `--`
   (`pinka restore -- --non-interactive`).
10. **`pinka status`** shows units to everyone; containers and Incus
    instances need root (researchers are never in `docker` or `incus-admin`),
    and it says so.
11. **`update-host`**: the update commands per family as in
    `how-to/update-host.md`; the reboot notice uses
    `/var/run/reboot-required` (Debian family only; the page lists the other
    families' commands).
12. **`pinka user`** edits `users/<name>.pub` and the host_vars file only
    when the `researchers` list is written with one flow mapping per line; it
    refuses otherwise instead of rewriting the file.

### Open `TODO(verify)` items after step 10

107 markers, unchanged.

## Step 11: catalog, platform and split inference

Built: the helper role `deploy_source` (included only; the root-only bare
mirror `/var/lib/pinka/git/potka.git`, an SSH deploy key generated on the
host or an anonymous HTTPS source, pinned host keys, SSH-signed tags with
`require_signed_tags`, `git archive <commit>:<component>` extracted with
Python's `data` filter through a temporary directory, `DEPLOYED_FROM.json`,
an atomic `current` switch; helper `/usr/local/libexec/pinka/deploy-source`);
the roles `catalog` (root:pinka-gt, 0750/0640) and `platform` (uv
environment of the release, `endpoints.toml` and one `<svc>.toml` per
service checked against the release's own schemas, secrets directories, the
run-manifest signing key with its published public key and `rotate.yml
--tags signing`, QA trusted keys, the pinned Quarto CLI, eight hardened
units with the socket helper `socket-perms`, migrations once per commit,
`/usr/local/bin/potka`); the role `inference_control` (identity certificates
on lab and inference nodes, the gateway's ACL on lab nodes, the inference
control endpoint `pinka-inference-control.service` on inference nodes);
`pinka pair show` and `pair connect` with the pairing protocol
(`pinka_lab/pairing.py`); split-inference telemetry in the observability role
(Compose profile `stores`, forwarding and receiving over mutual TLS, a
private copy of the pairing material for the collector); the
split-inference ports in the firewall; node-role gating in `site.yml`
(`incus`, `postgres`, `catalog`, `platform`, `opa` and `images` on `full`
and `lab` nodes only, `inference_control` on `lab` and `inference` nodes);
the filters `pinka_to_toml`, `pinka_socket_specs` and
`pinka_unit_hardening`; in the lab description the platform's and the
control endpoint's hardening, the platform's socket expectations (from
`/etc/pinka/platform/sockets.json`), the `inference` section and the
tailnet ports; the doctor check `inference.pairing` (29 checks); docs
`how-to/deploy-platform.md`, `how-to/pair-inference-node.md`,
`node-roles.md`, a split-inference section in `explanation/telemetry.md`
and updates to `how-to/fix-doctor-checks.md`, `how-to/rotate-secrets.md`
and `architecture.md`.

Fixed on the way: doctor's `systemd.hardening` compared `InaccessiblePaths`
with systemd's `-` prefixes still on; doctor's `sockets.permissions` now
compares the effective mode and ACL (with ACL entries the group bits are the
mask) and matches whole `getfacl` entries; the variables reference renders
multi-line defaults as code blocks instead of broken list items.

### Verified in step 11

- vLLM 0.31.0's TLS options in its source
  (`vllm/entrypoints/launchers/cli_args.py`): `--ssl-keyfile`,
  `--ssl-certfile`, `--ssl-ca-certs`, `--enable-ssl-refresh` and
  `--ssl-cert-reqs` (an integer, `ssl.CERT_NONE` by default). They are not
  used (interpretation 9).
- `git archive <commit>:<subdirectory>` writes the subtree without a prefix,
  and `git tag --points-at` finds the component tag (tests against a real
  repository).
- uv 0.12.23 has every `uv sync` option the platform role uses.
- The Quarto 1.10.18 archive matches its locked sha256 and unpacks to
  `quarto-1.10.18/bin/quarto` with Typst bundled under `bin/tools/<arch>/`.
- `systemd-analyze verify` (systemd 255) accepts all eight platform units
  and the control endpoint's unit without a warning, and reports unknown
  keys and bad values (tests).
- The TOML writer's output reads back with `tomllib` unchanged (tests).
- The pinned collector 0.162.0 validates the gateway configuration of a
  full node, an unpaired and a paired inference node and a paired lab node,
  and rejects an unknown TLS key. Two collectors run from those
  configurations with certificates made by community.crypto 3.4.0 exactly as
  the role requests them: the inference node's metrics arrive on the lab
  node labelled `pinka_machine`; a certificate with the same name and
  another key is refused ("unknown certificate authority").
- Those certificates carry the subject alternative name, server and client
  authentication, a critical `CA:FALSE` and a subject key identifier, and no
  authority key identifier. OpenSSL 3 through CPython 3.12 and 3.9, with
  `VERIFY_X509_STRICT` (the default of `ssl.create_default_context` from
  Python 3.13), accepts each one as its own trust anchor and refuses any
  other certificate.
- The control endpoint, run with real mutual TLS: forwarding to vLLM, every
  control request, the request limits, and refusal of other certificates
  and of clients without a certificate (tests). Pairing over TLS: wrong
  codes, a certificate swapped in between, a `lab_mode` mismatch and the
  limit of five attempts (tests, also on CPython 3.9).
- `ansible-lint` (production profile) and `--syntax-check` pass with the
  new roles; the collections' module options were read with `ansible-doc`.

### Findings to report (step 11)

- **Nothing ran against a real host** (no systemd manager, Docker, Tailscale,
  `setfacl` or GPU here). The ACL mask arithmetic follows setfacl(1); the
  `systemctl show` values doctor compares follow systemd's documentation.
- **The SSH-signed tag test is skipped here** (no `ssh-keygen` in this
  environment); it runs where OpenSSH is installed, as in CI.
- **GitHub's SSH host keys stay `TODO(verify)`** (`api.github.com/meta` and
  `docs.github.com` are not reachable from here). The role refuses an SSH
  source on github.com until they are verified, unless the deployer supplies
  `pinka_source_known_hosts`; HTTPS sources work.
- **Contract gap, gateway access to the mTLS material**: the contract gives
  `/etc/pinka/inference/` to root:potka-orch 0750, but the LLM gateway
  (`potka-gw`) talks to the remote inference node too. Infra adds
  `u:potka-gw:r-x` on the directory and `u:potka-gw:r--` on `self.key` on
  lab nodes (proposed contract addition).
- **Contract question, the agent's secrets**: the contract creates
  `/etc/pinka/secrets/<svc>/` for every service and also puts
  `/etc/pinka/secrets` into the agent runner's `InaccessiblePaths` (C2), so
  the agent cannot read `/etc/pinka/secrets/agent/`. Infra implements both
  as written; if the agent needs a secret, systemd's `LoadCredential=` would
  hand it over without opening the directory (proposal).
- **Further contract additions (proposed)**: the control API (requests and
  JSON fields as in `node-roles.md`), pairing on port 18710 while the
  control endpoint is stopped, and OTLP gRPC on port 4317 of a paired lab
  node's tailnet address for the inference node's telemetry.
- **`InaccessiblePaths` needs existing paths**: a path is masked only if it
  exists when the unit starts, so the agent runner starts after, and is
  `PartOf`, the services whose sockets it must not reach.
- **Existing hosts**: the gateway collector now runs as 10001:10001 with the
  pairing mount, and the four stores carry the `stores` profile; the first
  run after this step may recreate those containers once.

### Interpretations awaiting owner confirmation (step 11)

1. **Deployment directories** are named after the component tag
   (`platform-v…`, `catalog-v…`) the commit carries, otherwise after the full
   commit; a tag that moved to another commit is refused; a ref already
   deployed with the same commit and tree is not extracted again; old
   deployments stay until removed by hand.
2. **Sources**: an SSH URL with a read-only deploy key generated on the host
   (shown once by the play) or an anonymous HTTPS URL; URLs with credentials
   and personal tokens are refused. Signed tags use git's SSH signature
   format with an `allowed_signers` file.
3. **The platform environment** is created with `uv sync --frozen
   --all-packages --no-dev --no-editable --compile-bytecode` and the pinned
   CPython, without Python downloads, and the play checks that every console
   script of the contract exists.
4. **Configuration checks** (C9b) run each `<svc>.toml` through the
   release's `schemas/config/<svc>.schema.json` with the release's own
   interpreter before installing it.
5. **Socket permissions** (C8): the units keep `UMask=0077`; a root helper
   from `ExecStartPost=+` waits for each socket and sets owner, group, mode
   and ACL; without ACL support on `/run` (probed once) the contract's
   fallback groups apply, and `sockets.json` records what doctor expects.
6. **Migrations** run with the new release before `current` switches, once
   per commit (`/var/lib/pinka/platform/migrated-commit`).
7. **The evaluator** runs without `MemoryDenyWriteExecute`, because Quarto's
   Deno and Typst generate code at run time; every other unit keeps it.
8. **Disabling the platform** stops and disables its installed units but
   removes nothing.
9. **vLLM stays on 127.0.0.1 on an inference node**; the control endpoint
   terminates mutual TLS on the tailnet address and forwards port 18704 byte
   for byte (contract wording: "bound to `tailscale0` with mutual TLS"). One
   TLS terminator checks the exact peer certificate for both ports, and the
   loopback listener that the deploy helper's restart, the serving
   verification, doctor and the scrapes use stays as on a full node.
10. **Pairing protocol**: self-signed ECDSA P-256 identities valid for
    3650 days (subject alternative name = tailnet name), a 12-character code
    from a 32-symbol alphabet (60 bits), HMAC-SHA256 keyed with the code over
    `pinka-pair-v1|side|own fingerprint|peer fingerprint|node|lab_mode`, at
    most five attempts, equal `lab_mode` required; each node trusts exactly
    the other's certificate. A new certificate means pairing again.
11. **Inference node contents**: no Incus, PostgreSQL, OPA, catalog,
    platform, golden images or stores; `gpu_inference` is required. Its
    collector scrapes the local exporters and vLLM and forwards every signal
    to the lab node; before pairing it exports to `nop`. The lab node sets
    `pinka.machine` of received data to the configured peer.
12. **Tailnet addresses** are read at bootstrap (`tailscale ip -4`, and
    `tailscale ip -4 <peer>` for the collector's export target with the peer
    name as TLS server name) instead of resolving MagicDNS names inside
    containers.
13. **Firewall**: ports 18704 and 18710 on an inference node and 4317 on a
    paired lab node are open on `tailscale0` to any tailnet peer; mutual TLS
    and the tailnet policy restrict them (the how-to shows an ACL rule).
14. **Doctor on split nodes**: `inference.pairing` (error) fails on missing
    identity files, a pairing the host_vars do not name, a mismatch, a
    missing `peer.crt` or a certificate expiring within 30 days, and is
    skipped before pairing; the telemetry round trip is skipped on inference
    nodes; listeners on the tailnet address are allowed only on the
    split-inference ports.

### Open `TODO(verify)` items after step 11

107 markers, unchanged; the planned note on vLLM's TLS options was verified
instead (see above).

## Step 12: distribution tests (`make test-distros`) and the CI fast lane

Built: `tools/test-distros/` (`pinka_test_distros`: targets, an `incus`
command-line wrapper, the test pipeline, the results file and the
command line), `tests/distros/targets.yml` (16 targets: every `supported`
and `tested` release, ufw, firewalld and nftables variants, SELinux
enforcing on Fedora 43 and AlmaLinux 9), `tests/distros/results.yml`
(empty), the CI answers template `tests/answers/ci.yml`, `make
test-distros`; the generated *Last test results* table of
`docs/support-matrix.md`; the CI fast lane: `containers.ci_images` in
`versions.lock.yml` (six base images pinned by digest, verified with the
registry API), `tests/distros/containers/` (systemd container recipe),
`ansible/check-mode.yml`, `make check-mode` and a `check-mode` job with one
entry per base image in both CI workflows; docs
`how-to/test-distributions.md` and updates to `distros/adding-a-family.md`
and `explanation/portability.md`.

Fixed after step 11 (separate commit `7955dde`): CI's `test` and `docs` jobs
had failed since step 7 because this environment runs as root and CI does
not (see PROGRESS, known issues).

### Verified in step 12

- The six CI base images resolve to the recorded digests (registry API,
  2026-10-06); lock-check verifies them from now on (`ci_images` group).
- Preflight inside a container yields a usable host profile (family,
  release, package manager, firewall backend from the real image; no
  provider), and `platform_facts` derives its facts from it (a check-mode
  run against this build environment, which went as far as the first package
  module; that module failed on the environment's own Python bindings, so the
  fast lane's real proof is the CI job).
- The harness pipeline, the results file, the generated table and the
  fast-lane command against fake `incus` and `docker` tools (20 tests).

### Findings to report (step 12)

- **No distribution test has run**: there is no Incus here, and the image
  aliases of the `images` remote cannot be verified from this environment
  (one `TODO(verify)` marker in `tests/distros/targets.yml`; the harness runs
  only with `--accept-unverified-images` until then). The first run needs a
  machine with Incus VM support and an ephemeral auth key of a test tailnet.
- **The harness depends on the installer's answers replay (step 13)**:
  bootstrap stops with 77 for `--answers` until the installer exists, so
  `make test-distros` can run end to end only after step 13; the CI answers
  file follows the answers format that step 13 defines (and checks).
- **The fast lane runs for the first time with this commit in CI**; role
  findings it reports are fixed in follow-up commits.
- **Golden images and GPU paths are not part of the distribution tests**:
  the CI answers build no images (their base image fingerprints are still
  unverified) and select no model.
- **No derivative target**: none of the derivative images on the `images`
  remote could be verified here; one is added once verified (BUILD_PROMPT
  §19.9 asks for one "if an image is available").

### Fast lane findings (follow-up commits)

1. **First run**: every family stopped before Ansible because preflight was
   copied into the container's `/tmp`, a tmpfs, which `docker cp` cannot
   write into. Preflight now goes to `/opt/pinka-ci/preflight`, and an empty
   report names preflight's exit status and error output.
2. **Check mode on a fresh host** (found by reading the modules while the
   first run was red; BUILD_PROMPT §17 also asks for `bootstrap.sh --check`
   on a fresh test host):
   - ansible-core 2.21's `systemd_service` fails in check mode on a unit
     that does not exist, so it fails on units whose package or unit file an
     earlier task would install;
   - `lineinfile` fails on a missing file (`sshd_config` before
     openssh-server);
   - `authorized_key` fails for an account the run would create;
   - `acl` fails on a directory the run would create;
   - `getent` fails on a group the run would create.

   `platform_facts` now records in check mode what exists when the play
   starts (`pinka_present`). The fast-lane roles skip such tasks in check
   mode while the object is missing; `getent` no longer fails on a missing
   key. `ansible/tests/test_roles.py` fails when a fast-lane role manages a
   unit, `lineinfile` target, authorized key or ACL without such a guard.
   The other roles get the same treatment before the infra acceptance run
   (`bootstrap.sh --check`).
3. **Package modules in check mode**: `apt` and `dnf5` install their Python
   bindings on demand, but not in check mode, and `apt` does not refresh its
   lists in check mode. The CI containers therefore carry python3-apt or
   python3-libdnf5 and keep their package metadata; they also carry procps,
   whose `sysctl` the base role's `sysctl` module needs and which every real
   host has.
4. **Second run**: Ubuntu 24.04, Debian 13, Fedora 44, openSUSE Leap 15.6
   and Arch Linux pass. AlmaLinux 9 found a real role bug: minimal, cloud and
   container installs of EL 9 (and Fedora) ship `curl-minimal`, which
   conflicts with the `curl` package the base role installed. The redhat
   family now names curl by its file, `/usr/bin/curl`, which either package
   satisfies.

### Interpretations awaiting owner confirmation (step 12)

1. **Images**: test VMs use the `images` remote's aliases, resolved when the
   test runs; the result records the fingerprint used (the remote keeps only
   recent builds, so fixed fingerprints would expire within days).
2. **Results** stay valid for 90 days from the test date; the generated table
   shows a *Valid until* date instead of computing "expired" at generation
   time, so the generated page does not change by itself (drift check).
3. **What a test is**: launch, prepare (firewall variant, SELinux, copy of a
   clone of `HEAD`, answers with the VM name as machine name, a generated
   vault password, the test researcher's key), bootstrap with
   `--answers installer-answers/<vm>.yml --accept-degraded` (exit 0 or 1, and
   the host profile must show the expected firewall backend), a second
   bootstrap whose PLAY RECAP must show `changed=0`, then `pinka backup
   now` and `pinka doctor --smoke` with exit 0. The first backup runs
   before doctor because `backup.recent` warns on a lab that never backed up.
4. **Test VMs are staging labs** (`lab_mode: staging`, `tag:pinka-qa`) that
   join a test tailnet with an ephemeral auth key from
   `PINKA_TEST_TAILSCALE_AUTHKEY`; the key reaches the VM in a 0600 file,
   never on a command line, and the VM logs out of the tailnet before it is
   deleted.
5. **Safety**: the harness refuses to run on a lab host while
   `pinka-lab.target` is active, uses its own Incus project
   (`pinka-test-distros`, sharing the default project's images and
   profiles) and tests only committed `HEAD`.
6. **Fast lane**: the roles `platform_facts`, `base`, `users_groups`, `ssh`,
   `layout`, `mac` and `firewall` with `--check` against a container per
   family (Ubuntu 24.04, Debian 13, Fedora 44, AlmaLinux 9, openSUSE Leap
   15.6, Arch Linux), with the host profile preflight writes inside the
   container and the provider set to `baremetal`. Leap rather than
   Tumbleweed because Docker Hub's only Tumbleweed tag is `latest`, which the
   lock schema refuses; Python 3.11 on Leap because its `python3` is too old
   for Ansible. The containers' own packages (systemd, Python, sudo) are not
   pinned; they exist for one CI job.

### Open `TODO(verify)` items after step 12

108 markers: one more, the image aliases of `tests/distros/targets.yml`.

## Step 13: the installer TUI and the answers replay

Built: `installer/` (BUILD_PROMPT §18): the answers schema
`installer/schema/answers.schema.json`; `pinka_installer` with the field
rules (`validators.py`), the answers model and its cross-field checks
(`answers.py`), the writers of host_vars (round trip from the example file,
comments kept), of the researchers of `group_vars/all/main.yml` (refilled in
place), of `users/<name>.pub` and of the vault (Ansible's vault API), the
resume state, the plan that the Review screen shows and Install writes
(`apply.py`), model pinning from the Hugging Face Hub (`models.py`) and the
command line `python -m pinka_installer {validate,describe,apply,tui}`; the
Textual application `pinka_installer/tui/` with screens 1-18, the host
interface, the bootstrap runner and the event folding; the hash-locked
installer environment (`installer/requirements.in` generated from
`versions.lock.yml`, `installer/requirements.lock` from `make
installer-lock`); the Ansible callback `ansible/callback_plugins/pinka_events.py`
(enabled in `ansible/ansible.cfg`); bootstrap's front-end stage (installer
environment, TUI hand-over, answers replay, terminal check, `--machine`
optional with `--answers` or the TUI); the observability role's per-researcher
Grafana switch; generated references `docs/reference/answers.md` and
`docs/reference/cli/pinka-installer.md`, and the screenshots
`docs/assets/installer/*.svg` copied from the snapshot tests; how-to guides
`install-with-the-tui.md` and `install-without-the-tui.md`, updates to
`explanation/bootstrap.md` and `how-to/troubleshoot.md`.

Tests: 201 installer tests (validators, answers, files and vault, pins, 20
pilot flows, 76 screen snapshots, event replay, runner, suggestions), 4
callback tests, the bootstrap front-end tests.

### Verified in step 13

- Textual 8.2.8, ruamel.yaml 0.19.1, jsonschema 4.26.0 and segno 1.6.6 from
  PyPI (2026-10-06); the installer lock pins them with hashes, together with
  the ansible-core of the Ansible environment for the vault API.
- The vault round trip with ansible-core's `VaultLib` (encrypt, decrypt,
  wrong password), and that an existing vault's secrets survive a modify run.
- The event streams the tests replay are real recordings
  (`installer/tests/data/record_events.py`): bootstrap's own `emit_event` and
  `ansible-playbook --check` with the callback on a stand-in play whose tasks
  only print or fail.
- The host profiles the pilot tests use come from preflight itself, run
  read-only on its synthetic fixtures (WSL2 with GPU, bare metal with and
  without GPU, Secure Boot, unsupported storage).
- Doctor reports in the tests come from pinka's own JSON renderer.
- An install through the TUI and a replay of its answers file with `apply`
  write byte-identical host_vars, group_vars, answers and key files; only the
  vault differs (salted encryption, generated secrets).
- The runner's password hand-over file is 0600 in a 0700 directory and gone
  after the run; cancelling stops bootstrap and its children (process group).

### Findings to report (step 13)

Found by the pilot tests and fixed in this step:

1. **The callback named tasks `role : task`** (Ansible's `get_name()`), so the
   TUI could never recognize the Tailscale login task and kept the login URL
   on screen. It now emits the task's own name; the role is a field of its
   own (callback test added).
2. **Ansible's color module probes standard output with curses on import**,
   which crashed the TUI the first time it opened the vault (Textual replaces
   standard output). The installer imports the vault code before the
   application starts.
3. **Modify mode would have replaced the restic password**: the Backups
   screen generated a new one even when the vault already had one, which
   locks out every existing backup. A vault's restic password is now kept; a
   password the vault build generates is shown once on the Review screen
   before anything is written.
4. **Resume lost the answers** whenever it happened before the Review screen,
   because saved answers had to pass the full schema. The resume state now
   keeps incomplete answers; Quit also keeps the fields of the current screen.
5. **Modify mode asked for the Tailscale auth key again** although the vault
   holds it; an empty field now keeps the vault's key.
6. **The phase tree** listed the WSL provider role twice and showed helper
   roles (`pkg`, `compose`, `deploy_source`, `bootstrap_signal`) as phases of
   their own; the provider is resolved from the host profile and helper roles
   count for the role that includes them.

Not covered here:

- **The TUI has not driven a real bootstrap** (the build environment must not
  be provisioned); the pilot tests replay recorded runs. Phase 5 runs it on
  the reference hosts.
- **Estimates on the Review and Golden images screens** are counts only (how
  many pinned images, which model, how many golden images): build times, disk
  use and download sizes need measured data, which no build has produced yet.
- **MOK enrollment** (`mokutil --import` with the password on standard input,
  the DKMS key path on Ubuntu) is unverified (one `TODO(verify)`).
- **Model pinning** needs the Hugging Face Hub, unreachable from here; the
  Hub API field names it reads are unverified (one `TODO(verify)`, as in
  step 7's how-to).
- `make test-distros` can now replay its answers; it still needs a machine
  with Incus VMs (step 12).

### Interpretations awaiting owner confirmation (step 13)

1. **`--machine` is optional** with `--answers` (the machine comes from the
   file and must match if both are given) or when the TUI starts; without
   either it is still required (64).
2. **The TUI runs bootstrap again** non-interactively (`--non-interactive
   --no-tui --vault-password-file <0600 file in /run/pinka/installer>`) for
   the installation, after releasing the first run's lock, so the TUI and an
   answers replay take exactly the same path (BUILD_PROMPT §18.1 "exactly as
   a non-interactive run would").
3. **One installer lockfile** (E11) that also pins ansible-core and its
   dependencies, because the vault is written through Ansible's vault API.
4. **segno** (QR codes of the Tailscale login URL) is an added pinned
   dependency; it has no dependencies of its own.
5. **pytest 8.4.2** in the development environment: pytest-textual-snapshot
   1.1.0 requires pytest below 9.
6. **The Grafana account toggle** per researcher (`grafana: false`) needed a
   change of the observability role: such researchers get no Grafana user and
   no generated password.
7. **`split_inference.peer` is optional**: the other node can be named later
   by pairing (`pinka pair`), which the Finish screen offers and which then
   re-runs the affected roles (`--tags=inference_control,observability,firewall`).
8. **The CI answers** (`tests/answers/ci.yml`) use `power_policy: os_default`
   and build no golden image (their base fingerprints are unverified).
9. **E3 means no HTTPS tokens**: the Platform screen offers a generated
   deploy key for SSH URLs and anonymous `https://` URLs, no token.
10. **Exit codes of the TUI**: doctor's 0, 1 or 2 after Finish; 75 or 76
    after the restart instructions; 77 when the operator quits before the end
    or the terminal is unsuitable (80x24, `TERM`, a terminal on both ends).
11. **Mouse support is optional** in the sense of "not required": every
    action has a key; the mouse works where the terminal sends its events.
    `NO_COLOR` is handled by Textual (monochrome); F2 switches between the
    dark, light and high-contrast themes. Textual's command palette is off,
    because its Quit would skip the confirmation.
12. **The commit offered on the Finish screen** runs `git` as the invoking
    user (`sudo -u`), on branch `machine/<name>`, and never pushes.

### Open `TODO(verify)` items after step 13

110 markers: two more, the Hugging Face Hub API field names in
`installer/pinka_installer/models.py` and the MOK enrollment in
`installer/pinka_installer/tui/screens_install.py`.

## Follow-up after step 13: CPU sets of services and targets

`cpuset_services` and `cpuset_targets` (BUILD_PROMPT §8.1: "lab services
pinned here", "Incus targets pinned here") were collected since step 5 and by
the installer's Resources screen, but no role applied them. Now:

- `group_vars/all/main.yml` defaults both to empty (no pinning) and names the
  slice `pinka_services_slice` (`pinka-services.slice`); the host_vars example
  shows the empty defaults with the BUILD_PROMPT's split as an example.
- The base role checks that the two sets do not overlap and name only CPUs
  the host has (`hardware.cpu_threads` of the host profile; new filter
  `pinka_cpuset_cpus`), and writes `pinka-services.slice` with
  `AllowedCPUs=` (systemd.resource-control) when `cpuset_services` is set; it
  reloads systemd at once, before Docker and the lab units use the slice.
- Every unit of the lab (those `PartOf=pinka-lab.target`, the backup job,
  the Incus event forwarder and the vLLM deploy helper) has
  `Slice=pinka-services.slice`; a template test enforces it. Docker places
  every container in the slice (`cgroup-parent` in `daemon.json`, from
  Docker's dockerd reference; with the systemd cgroup driver the parent must
  be a `.slice`).
- The `bench` project's `limits.cpu` is the number of CPUs in
  `cpuset_targets` when it is set (else, as before, the host's CPUs minus 2).
- `endpoints.toml` carries `[incus] cpuset_targets` for the platform, which
  pins targets by setting `limits.cpu` to CPUs inside it: Incus documents
  `limits.cpu` as "a number or a specific range of CPUs", and a number is
  balanced over all of the host's CPUs (Incus 6.0 option reference, read
  2026-10-07).
- Doctor check `resources.cpusets` (warning): both sets exist on the host
  (`/sys/devices/system/cpu/online`) and the slice's
  `cpuset.cpus.effective` equals `cpuset_services`.

### Findings to report (CPU sets)

- **Targets can only be pinned by the platform**: a profile cannot pin them
  without breaking the project limit (a range of 14 CPUs counts as 14 of the
  project's `limits.cpu`) or the per-blueprint sizes the platform sets.
  Instances of the build project and of personal projects stay unpinned.
- **A changed set reaches running services at the next lab start**; doctor
  reports the difference until then. Existing containers move into the slice
  when they are recreated (the next `pinka stop`/`start`).
- Nothing was run on a real host (no systemd host and no Docker daemon here);
  the slice, unit and `daemon.json` renderings are tested.

### Interpretations awaiting owner confirmation (CPU sets)

1. Empty defaults (no pinning) rather than a computed split: the installer
   suggests a split, and hand-written host_vars keep working.
2. The services slice holds the lab's units and all Docker containers; system
   daemons (sshd, dockerd, incusd, tailscaled) stay unpinned.
3. **Proposed contract addition**: `[incus] cpuset_targets` in
   `endpoints.toml`, and the rule that the platform sets bench instances'
   `limits.cpu` to CPUs inside it (written into `docs/contracts.md`).
4. The fingerprint needs no new field: `host_vars_sha256` already changes
   with the split.

## Follow-up after step 13: check mode of the whole play

BUILD_PROMPT §17 asks for `bootstrap.sh --check` end to end on a fresh test
host, and the step 12 notes promised the check-mode treatment for the roles
outside the fast lane. Now:

- `platform_facts` also records in check mode which component commands exist
  when the play starts (`pinka_present.commands`: `dockerd`, `incus`,
  `tailscale`, `nvidia-ctk`).
- Check mode cannot resolve packages from a vendor repository it did not
  really add. The docker, tailscale, incus and nvidia_container_toolkit roles
  therefore end after their repository step (`meta: end_role`) in check mode
  while their command is missing; the source lookup, the refusal of
  unverified sources and the download and fingerprint check of the
  repository key still run. The images role ends before the blueprints while
  Incus is missing (it lists the image store). The incus role's own
  stat-and-block skip is replaced by the same rule.
- Units created earlier in the run (compose projects, lab session units, the
  sleep inhibitor, the VM listener address, the vLLM deploy socket, the
  inference control endpoint, the guest agent, the Incus event forwarder) are
  managed in check mode only when they exist, handlers included. Key files and
  downloads whose directory an earlier task creates (`openssh_keypair`,
  `openssl_privatekey`/`publickey`, `get_url`) wait in check mode while that
  task reports a change; the models role reads its accounts with
  `fail_key` off in check mode; rpm imports a repository key from the
  verified download instead of the copy check mode does not write.
- `ansible/tests/test_roles.py` walks every task file `site.yml` reaches
  (includes, included roles, handlers, `end_role`) and fails on an unguarded
  task of those kinds; the old test covered only the fast-lane roles.
- **Real-run fix found on the way**: `deb822_repository` (the pkg role's apt
  repositories) needs python3-debian, which nothing installed; minimal Debian
  hosts lack it. The base role installs it on the Debian family (the package
  name ansible-core's module itself installs, from its source).
- The CI fast lane now runs `ansible/site.yml` instead of the reduced
  `ansible/check-mode.yml` (removed). The container stands in for
  `localhost`; the trees the play reads on the target (preflight,
  pinka) are copied to the same absolute paths; preflight's profile is
  written where `pinka_bootstrap.profile_path` points, so `preflight_gate`
  really re-runs preflight and compares; the host profile carries
  `force_unsupported` (preflight classifies containers as unsupported); the
  example vault supplies the secrets, with a local backup repository. Roles
  whose component source, repository, key or package pins are `TODO(verify)`
  on the release are skipped by tag and listed in the output (computed with
  the filter plugin's own functions). The containers also carry
  python3-debian, gpg and the CA certificates, which the base role installs
  and check mode cannot.

### Findings to report (check mode of the whole play)

- **The fast lane checks less than a real run on every release**: Tailscale
  and Incus are skipped on every CI image (their repositories or pins are
  `TODO(verify)`), and Docker on openSUSE Leap and Arch Linux. They run as
  soon as their entries are verified.
- **Check mode on a fresh Debian-family host needs python3-apt and
  python3-debian**, which a real run installs (Ansible's package modules
  cannot install them in check mode); Ubuntu installs carry both.
- Nothing was run on a real host here (no Docker daemon and no systemd host
  in this environment); the CI fast lane is the proof, and its findings are
  fixed in follow-up commits as before.
- **First run of the whole play** (about 150 tasks per family): every family
  stopped at the observability role's secrets check, whose `$` test was a
  quoted string and therefore always true, so a `$` in the Grafana database
  password was never refused; ansible-core 2.21 rejects the non-boolean
  result. Fixed, and a role test now flags quoted expressions in conditionals.
- **Second run**: openSUSE Leap's `python3` (3.6) could not run preflight's
  re-run; the container now gets `/opt/pinka/python/bin/python3`, where
  bootstrap puts its pinned CPython. The example vault had a Grafana password
  for one of the three example researchers, which the observability role
  refuses; it now has one per researcher.
- **Third run** (about 200 tasks per family) found a bug of every real run:
  the backup role installs its units in a loop over (job, unit kind) pairs,
  but the unit templates read the job's fields from `item`, and their test
  rendered them with the job alone. The task now binds the job to
  `backup_job`, which the templates and the test use.
- **Fourth run** (`8b5a0e8`): `site.yml` passes in check mode on all six
  CI images (Ubuntu 24.04, Debian 13, Fedora 44, AlmaLinux 9, openSUSE Leap
  15.6, Arch Linux), with the roles of unverified sources skipped.

### Interpretations awaiting owner confirmation (check mode of the whole play)

1. The fast lane runs the whole provisioning play rather than only the
   provider-agnostic roles that BUILD_PROMPT §19.9 names, so that it also
   proves `bootstrap.sh --check` (§17); the container counts as bare metal.
2. Roles refused for unverified sources are skipped in the fast lane rather
   than failing it, since lock-check already fails while any `TODO(verify)`
   remains.

## Step 14: documentation completion

Built:

- Tutorials `tutorials/install-wsl2.md` (the reference setup) and
  `tutorials/install-baremetal.md`.
- `operations.md` (the operator's map of BUILD_PROMPT §15, every task linked
  to its guide), `security.md` (threats, mechanisms, network exposure, the
  tailnet policy, integrity, out of scope), and the how-tos
  `how-to/connect.md` (Tailscale and SSH, port forwarding of Grafana, the
  ad-hoc LLM endpoint and the documentation), `how-to/migrate-lab.md` (last
  backup, tailnet name, fresh build with `--restore`, signing key handover,
  re-pairing), `how-to/staging-window.md` and `how-to/install-attestation.md`.
- ADR-0013 *No orchestration frameworks*, the last ADR BUILD_PROMPT §15 lists.
- `pinka docs serve` (owner answer H9): serves a built documentation site
  read-only on `127.0.0.1` (port `pinka_ports.docs_serve`, 18706) until
  interrupted; the directory and port come from the new `docs` keys of
  `lab.json`, or `--dir` and `--port`, and it works without a lab
  description. The doctor role creates `/usr/local/share/pinka/docs/` empty;
  the operator extracts a release's documentation tarball there.
- `README.md` per BUILD_PROMPT §15, with two generated sections (the
  supported and tested environments, and the bootstrap exit codes) kept in
  sync by `make generate` and the drift check; the home page `index.md` now
  points to the tutorials and the operator and researcher entry points.

### Verified in step 14

- `docs serve`: unit tests against the fake host, the CLI without a lab
  description, and the real static server on an ephemeral loopback port
  (GET of a file, 404, POST refused with 501).
- Strict docs build, docs lint (markers, placeholders, front matter),
  spelling, Markdown lint and the drift check over the new pages.

### Findings to report (step 14)

- **Secure Boot without an NVIDIA driver blocks the installation**
  (resolved by the approved deviation I1, see its section below). The
  support matrix of BUILD_PROMPT §2 (rule `secure_boot_mok`, and the fixture
  `ubuntu2404-baremetal-nvidia-no-driver-secureboot` expected as
  `needs_action`) makes preflight report `needs_action` on such a host, and
  bootstrap stops with 30 before the play that would install the driver and
  ask for the MOK enrollment (BUILD_PROMPT §3.2, §18 screen 7). As written,
  the automatic driver installation can never run on a Secure Boot host. The
  bare-metal tutorial tells the operator to install the driver by hand until
  this is decided. Proposal: make `secure_boot_mok` a warning (the play
  already stops with 76 for the enrollment), or let consent to the MOK flow
  resolve it. This changes the spec's matrix and a fixture expectation, so it
  needs the owner's decision.
- The staging and attestation pages cover the lab host's part only; the QA
  procedure and the attestation format belong to the QA instructions and the
  platform.

### Interpretations awaiting owner confirmation (step 14)

1. `pinka docs serve` is a foreground command any account may run (the
   site is not secret), bound to `127.0.0.1` only, reached over SSH port
   forwarding; there is no unit that serves the documentation permanently.
2. The documentation of a release is installed by extracting its tarball into
   `/usr/local/share/pinka/docs/`; nothing downloads it automatically.
3. The README excerpt lists the `supported` and `tested` rules only; the
   full matrix stays in `docs/support-matrix.md`.
4. "Open a QA maintenance window" is read as providing a separate
   `lab_mode: staging` lab for the QA team and stopping it afterwards.

### Open `TODO(verify)` items after step 14

110 markers, as after step 13 (none added).

## Approved deviation I2: required and extended fast lane

The owner decided the two interpretations of "check mode of the whole play"
(PROGRESS.md, approved deviation I2): the required CI job stays exactly as
BUILD_PROMPT §11 and §19.9 specify, and the whole-play run continues only as a
separate, non-blocking job. Now:

- `ansible/check-mode.yml` is back as the required job's play: the
  provider-agnostic roles that §11 names (`base`, `users_groups`, `ssh`,
  `layout`), with `platform_facts` first because it only derives the facts
  they read. It refuses to run outside check mode. `mac` and `firewall`, which
  the step 12 version also ran, are left to the extended job, so the required
  job is exactly the roles §11 lists. Nothing is skipped: none of the four
  installs a component of `component-sources.yml` (a test asserts it).
- `make check-mode CI_IMAGE=<key>` runs that play; `make check-mode-extended
  CI_IMAGE=<key>` runs `ansible/site.yml` with the roles of unverified
  sources skipped by tag (`--extended` of `pinka_test_distros check-mode`).
  The extended run ends with a Markdown summary: whether the roles that ran
  passed, and one row per skipped component marked "SKIPPED, not tested" with
  the reason. A skipped component is never reported as passed. The summary is
  written to `build/check-mode/<key>-extended-summary.md` and appended to
  `$GITHUB_STEP_SUMMARY` in CI (or to `--summary <file>`). The skip list is
  computed from the verification state on every run, so a skip ends by itself
  once a component's sources are verified.
- CI (`infra/.github/workflows/ci.yml` and the root wrapper): the
  `check-mode` job runs the required play on all six CI images and blocks; the
  new `check-mode-extended` job, named
  `check-mode-extended (non-blocking, <image>)`, runs the whole play with `continue-on-error: true`.
- Tests: the required run passes no `--skip-tags` and uses
  `ansible/check-mode.yml`; the required play imports exactly
  `platform_facts` plus the four roles; the extended run's skips equal the
  refused roles, and its summary lists each of them as not tested in both
  outcomes of the play.

Nothing was run in a container here (no Docker daemon in this environment);
the CI jobs on the push are the proof. CI on that push: the required
`check-mode` job passed on all six images, the six
`check-mode-extended (non-blocking, <image>)` jobs completed, and
`lock-check` failed as before (the open `TODO(verify)` markers).

## Approved deviation I1: Secure Boot MOK consent

PROGRESS.md row I1. On a host with an NVIDIA GPU, Secure Boot on and no
driver installed, preflight reported `needs_action` (`secure_boot_mok`) and
bootstrap stopped with 30 before the play that installs the driver, so
`install_nvidia_driver: auto` could never work there. Now the operator's
consent turns the action into a planned one that bootstrap carries out.

- **Preflight** (`--nvidia-driver auto|manual|skip`, default `auto`, and
  `--accept-mok-enrollment`): the choices are facts of the support matrix
  (`nvidia_driver_mode`, `mok_enrollment_accepted`). A `needs_action` rule
  may carry `planned_when` and `planned`; when both conditions hold, the
  action goes to the new host profile list `planned_actions` and to the
  warnings instead of `needs_action`. `secure_boot_mok` now applies only to
  `auto` and `manual` and is planned with `auto` plus consent. The profile
  records the setting (`gpu.install_driver`) and the consent
  (`overrides.accept_mok_enrollment`, so every fingerprint carries it). The
  generated support-matrix page shows the planned row.
- **Outcomes** (fixtures for all four cases): no consent → 30 with the
  remediation (`...-secureboot`); `auto` with consent → `degraded`, the
  action planned (`...-secureboot-consent`); `manual` → 30, consent or not
  (`...-secureboot-manual`); `skip` → `degraded` without `gpu_inference`
  (`...-secureboot-skip`). A fixture's extra preflight options live in its
  `args.json`; every test that runs preflight on fixtures reads it.
- **Bootstrap** reads `install_nvidia_driver` from the machine's host_vars
  (else `group_vars/all/main.yml`, else `auto`) as a plain scalar, without
  YAML (the Ansible environment does not exist yet when preflight runs), and
  passes it to preflight with `--accept-mok-enrollment` (option, answers
  file key, or the installer). When the profile plans `secure_boot_mok`,
  bootstrap needs the one-time MOK password before the play: asked twice,
  masked, on a terminal, or the first line of standard input with
  `--mok-password-stdin`; otherwise 77 before anything changes. The play's
  signal 76 may name a key (`mok_key`); bootstrap then runs
  `mokutil --import <key>` with the password on its standard input (a
  `printf` builtin in a pipe: never on a command line, in the log or on
  disk), clears it, and exits 76 (`--allow-reboot` reboots only after the
  import). Without a password or when `mokutil` fails: 77 with the manual
  `mokutil --import` command. The state file records the consent.
- **Ansible**: `provider_baremetal` looks for the module signing key
  (`provider_baremetal_mok_keys`, first that exists) when Secure Boot is on
  and the driver is not usable; a pending enrollment still only signals 76;
  a key that `mokutil --test-key` does not report as enrolled is named in a
  76 signal (`bootstrap_signal_mok_key`). `preflight_gate` runs preflight
  with the same choices (`--nvidia-driver install_nvidia_driver`, consent
  from the host profile) and stops with 77 if `install_nvidia_driver`
  differs from what bootstrap gave preflight (a value bootstrap cannot read).
  The check-mode harness passes the CI machine's setting to preflight in
  the containers the same way.
- **Installer**: the Preflight screen explains the MOK flow when it applies
  and offers the consent (preflight runs again with it) or *Continue without
  the driver* (`skip`). The GPU screen asks for the password only when the
  enrollment is needed, refuses `manual` there and asks for the consent if
  `auto` lacks it. The TUI no longer calls `mokutil` itself: it passes
  `--accept-mok-enrollment --mok-password-stdin` and writes the password to
  bootstrap's standard input (`RunRequest.mok_password`), so the TUI, an
  answers replay and a plain terminal run take the same path. The answers
  file has `gpu.accept_mok_enrollment` (only with `install_driver: auto`;
  `installer describe` reports it to bootstrap); it never holds the password.
  A recorded event stream of a 76 run (`events-signal76.jsonl`) was added.
- **Interpretations** (to confirm): the outcome with consent is `degraded`,
  as for any host whose driver is still to be installed (the GPU is not
  usable before the reboot), so the operator also accepts the missing
  `gpu_inference` once; a missing password is refused before the play
  rather than after the driver install; consent given together with
  `manual` is recorded but has no effect.
- **Docs**: the bare-metal tutorial now takes the automatic path with
  consent and shows installing the driver yourself as the alternative;
  Bare-metal hosts, troubleshooting (30, 76, 77), both install how-tos, the
  host check how-to, the bootstrap explanation and the generated references.

`TODO(verify)`: 111 markers (one more): the DKMS key locations
(`provider_baremetal_mok_keys`); the existing one in `nvidia.yml` now also
covers the wording of `mokutil --test-key`; the one about `mokutil` reading
the password from standard input moved from the installer to
`bootstrap/lib/stages.sh`. All of them need the reference hardware.

## Rename (plan step 3c): `pokus` → `pinka` and `potka`

The mechanical rename of the approved rename table (owner decisions Q1-Q15
in `../PROGRESS-ARCHIVE.md`, section J): infra's own names become `pinka`, names
derived from the platform become `potka`, "Pokus" stays the project's name.
Files and directories moved with `git mv`; generated files came from their
generators (`make generate`, `uv lock` with no version change, the TUI
snapshots and docs screenshots, the recorded installer event streams and the
preflight summary snapshots). The spec copies `docs/BUILD_PROMPT.md` and
`docs/DOCS_SPEC.md` are unchanged. No behavior, limit or test changed, with
the two exceptions the owner decided: OPA's authorization (Q7) and the
installer's refused username prefixes.

- **OPA (Q7)**: `config/opa/authz.rego` allows decision queries (`GET`,
  `POST`) at exactly `/v1/data/potka/action/decision` and
  `/v1/data/pinka/placeholder/active`, plus the health checks; before, it
  allowed everything below `/v1/data/pokus`. A Rego test proves that paths
  above, below and beside the two are rejected (seven Rego tests now), and
  the HTTP test rejects three more requests. The placeholder files moved to
  `placeholder/potka/action.rego` and `placeholder/pinka/placeholder.rego`.
- **Usernames**: the installer refuses the prefixes `pinka-`, `potka-` and
  `pokus-` (before: `pokus-`); the tests gained the two new cases.
- **Incus client certificates**: their names were built as
  `pokus-<short>`. The certificate entries now carry their `name`
  (`potka-orch`, `potka-exec`, `potka-inject`, `potka-verify`,
  `pinka-metrics`), used for the common name, the trust entry and doctor's
  trust check.
- **Re-recorded test data**: the installer's event streams differ from the
  old ones only in the renamed strings, timestamps and task durations.

### Interpretations awaiting owner confirmation (3c)

1. "Pokus lab" and "Pokus host" stay as written in prose and in user-facing
   strings (unit descriptions, CLI help, play names): the lab runs the whole
   project, infra's and the platform's parts. Infra's own components are
   "pinka" (its units, nftables table, firewalld zones and installer).
2. Markers that infra leaves on a host name the product's technical name,
   like the managed header (Q1): ufw rule comments `pinka`, `blockinfile`
   markers `# BEGIN pinka <name>`, the zypper repository description
   "(managed by pinka)", preflight's managed marker `Managed by pinka`.
3. Docs pages name the product "Pokus Infrastructure (pinka)" at the first
   mention and "Pokus Infrastructure" after it; repository references stay
   `pokus-infrastructure` and `pokus-platform`; generated references take
   the wording of the code ("pinka").
4. The checkout directory in examples is `pokus-infrastructure` (the
   repository's name); the test-distros harness's internal checkout name is
   `pinka`.
5. `journalctl -u 'pokus-*'` covered infra's and the platform's units; it
   became `journalctl -u 'pinka-*' -u 'potka-*'`.
6. The installed CLI package directory is `/usr/local/lib/pinka/pinka-lab`
   (like `tools/pinka-lab/`, Q2); the wrapper is `/usr/local/bin/pinka`.

### Findings to report (3c)

- Doctor's firewall check asks firewalld for the zone `pinka` (before:
  `pokus`), but the firewall role creates `pinka-tailnet` and `pinka-run`,
  so on a firewalld host the check always fails. The rename keeps the
  behavior; fixed in 3c3, its own commit after 3c2 (owner decision): doctor
  checks the two zones the role creates, and a test keeps both lists equal.
- The workflows read the CI secret `PINKA_PRIVACY_DENYLIST` (3c2; the
  secret `POKUS_PRIVACY_DENYLIST` named before never existed). Until the
  owner creates it, the scan runs without a denylist, as it always has.

## Images free of project names (plan step 3c2)

Owner decision, infra §7.13: target images carry none of the strings
`pokus`, `pinka` or `potka`, with no exception. `images_leakage_patterns`
gains `(?i)pokus`, `(?i)pinka` and `(?i)potka` (no allow rule), and the files
installed into images (`images/*/files/`) no longer carry the managed-file
header; the other recipe files (`image.yaml`, `cloud-init.yaml`,
`provision.yml`, `tests/`) keep it, because they stay on the host or are
removed by the clean step. A test checks both sides, and the pattern test
covers the three names. The blueprint versions stay `1.0.0`: no image has
been built yet. The patterns are applied after the base image's baseline,
like the others, so only what a build adds counts; whether the packages of
the blueprints add one of the three strings below `/etc`, `/opt`, `/srv` or
`/usr/local` shows at the first real build (Phase 5 self-test).

## Contract v2, step 1: the contract core (plan step 3d.1)

BUILD_PROMPT §16 step 15 ("Contract v2 core"): the manifest schema with the
reserved lists, the eight profiles, the validator and invariant checks, and
the principals and endpoints files. Nothing on a host uses it yet: the
deployment (step 2) calls the validator, the rendering (step 3) writes the
principals and endpoints files.

- **`contract/`**: `platform-manifest.schema.json` (written by hand; `make
  generate` fills only its `$defs.reserved_*`), `profiles.yml` (the profile
  table of §12.2 verbatim, the machine-readable grants, the socket rules of
  §12.3, the caps and the one relaxation) and `reserved-names.yml`
  (generated from the lists of §12.1 and from infra's own data: the `pinka*`
  groups, the unit templates, the compose projects and containers, the backup
  jobs, the Incus pool, network, ACL and dummy interface, the PostgreSQL
  monitor role and the `/srv/pinka` layout). `contract/records/` follows in
  the steps that define its records (§16 steps 16, 18 and 19).
- **`tools/platform/pinka_platform/`** (`pinka-platform validate <release>`,
  exit 0 valid, 65 refused): the strict YAML loader, a schema validator for
  exactly the keywords the schema uses, the invariant checks, the derived
  names with the reserved-name, clash and host checks, the release
  directory checks, a summary for `--inspect-platform` and the installer,
  and the renderers of `principals.json` and `endpoints.toml`.
- **Tests** (`tools/platform/tests/`, 227): the reference platform, the Pokus
  Platform's manifest (a fixture, as §12.1 asks) and a manifest using all
  eight profiles validate; one invalid case per rule (71 cases: the list of
  §11 and every further rule), each also run through `jsonschema`, which
  must agree on whether the schema refuses it; a reserved name in each
  namespace and the host checks; five unsafe YAML forms; the source overlap
  rule; release directory checks; the CLI; golden files of the principals
  and endpoints files.
- **Generated references**: `docs/reference/platform-manifest.md`,
  `platform-profiles.md` and `cli/pinka-platform.md`, covered by the docs
  coverage check; a how-to for platform developers
  (`docs/how-to/validate-a-platform-release.md`).

### Interpretations awaiting owner confirmation (3d.1)

1. **Validator runtime.** Python 3.12 with PyYAML only (both are in
   bootstrap's Ansible environment) and an own schema validator that
   implements exactly the schema's keywords and refuses any other; the tests
   compare it with `jsonschema` on every case. Alternative: add `jsonschema`
   and its dependencies (among them the compiled `rpds-py`) to bootstrap's
   hash-locked requirements.
2. **YAML.** Anchors, aliases, duplicate keys and non-string keys are
   refused; the manifest may have at most 256 KiB.
3. **Names.** A platform name starts with a letter (account names must);
   service and job names are 1-10 lowercase letters and digits without
   hyphens (so `<platform>-<service>` stays unambiguous); socket names have
   1-16 characters with hyphens; a client group longer than 32 characters
   is refused. Derived names are also compared with each other: a service
   named `migrate`, `db`, `migrator`, `reader`, `grafana` or like a profile
   clashes with a fixed name and is refused.
4. **Caps** (the spec names none): service memory 16G and 4096 tasks
   (defaults 2G and 512), hook timeout 3600 s, job timeout 86400 s, job
   memory 64G, 32 CPUs, 1 GPU, 4 telemetry labels.
5. **Commands** are a relative path to a regular file inside the release
   plus arguments. For `python-uv`, commands below `.venv/` exist only after
   the build, so they are checked again after it (`--built`); everything
   else is checked before.
6. **Policy.** `policy: {bundle, decision_path}`; the decision path is
   relative to `/v1/data` (`<platform>/action/decision`) and may not start
   with `pinka` or `system`.
7. **CLI.** `cli` needs `run` as well as `name` (§12.1: "name and command");
   the Pokus Platform fixture uses `run: [.venv/bin/potka]`.
8. **Sockets.** The connection table is applied literally: `admins` may
   use only sockets of `controller` and `observer` (the inference broker's
   row names researchers only; admins are researchers anyway).
9. **Host check.** Accounts, groups (client groups included) and commands
   on the `PATH` that already exist refuse the manifest, except names the
   same platform's active deployment recorded in
   `/etc/pinka/platform/active.json` (`names`), which step 2 writes.
10. **Reserved path segments** are compared per parent directory; with
    today's layout no derived name lands at a reserved level, so the check
    only guards future layout changes.
11. **Endpoints file**: one table per facility the manifest `requires`
    (`postgres`, `telemetry`, `inference`, `opa`, `incus`, `gpu`) plus `lab`,
    `signing` (`key_file` `/etc/pinka/signing/<platform>/signing.key`,
    `public_key_file`, `published_keys_dir`), `attestations`, `sessions` and
    `ports` with the effective values. `inference.mode = remote` points at
    the forwarder on the loopback; `scripted` is refused outside staging.
12. **Principals file**: `services` and `jobs` with account and UID;
    `sockets` with path, owner, client group, mode, whether it is dedicated
    to the agent, and the clients as profiles, their accounts, human classes
    with their groups, and root; `groups` names the researcher and admin
    groups.
13. **Invalid fixtures** are mutations of the valid fixtures, one table row
    per rule, instead of a file per case.

### Findings to report (3d.1)

- §12.1 checks content packages only against the release path. Two content
  packages from the same repository can still overlap each other, so a
  restricted package could be extracted as part of a non-restricted one.
  Proposed for step 2: refuse an overlap between a restricted package and
  any other source path in the same repository too.
- §12.1 does not tie `requires` to what the manifest uses. Proposed: refuse
  a manifest whose services or fields need an undeclared facility (an
  Incus profile without `incus`, `policy` without `opa`, a GPU job without
  `gpu`), so the health gate checks everything the platform uses.

### Owner decisions on 3d.1 (with the "commit")

- Interpretations 1-13 approved: the own schema validator, the YAML rules,
  the names, the caps, the post-build command check, the policy path,
  `cli.run`, the literal socket table, the same-platform host exemption, the
  layouts of `endpoints.toml` and `principals.json`, the signing key at
  `/etc/pinka/signing/<platform>/signing.key`, and mutated fixtures.
- Added in 3d.2 as asked: a test that fails when the manifest schema uses a
  keyword the validator does not implement (found independently of the
  validator, plus one enforcing case per implemented keyword; the contract
  also refuses to load such a schema), and a drift test that `profiles.yml`
  matches the profile table of BUILD_PROMPT §12.2 cell by cell.
- Both findings approved as stricter validation, recorded as approved
  deviations for the next spec edit: content packages from one repository
  may not overlap each other either (`source-overlap`), and a manifest that
  uses a facility it does not declare in `requires` is refused
  (`facility-undeclared`; `profiles.yml` `facilities` names the profiles and
  fields that need each facility).

## Contract v2, step 2: deployment (plan step 3d.2)

BUILD_PROMPT §16 step 16 ("Deployment: sources and content packages,
staging, sandboxed build, job image pulls by digest, provenance, the
one-platform rule, replace and purge"), §7.14 steps 1-6 and 9, the switch of
step 8 without hooks, and §12.10.

- **Engine** (`tools/platform/pinka_platform/`): `sources.py` (the
  `platform` and `content` settings), `mirror.py` (root-only mirrors, refs,
  signed tags, safe extraction; ported from the contract v1 helper),
  `deploy.py` (prepare, build, pull, publish, retire plan, activate, clean,
  purge) and `pinka-platform deploy <step>` / `pinka-platform purge`.
  Installed on the host below `/usr/local/lib/pinka/platform` with the
  contract; command `/usr/local/libexec/pinka/pinka-platform`.
- **Ansible**: the `platform` role orders the steps (`engine.yml`,
  `sources.yml`, `prepare.yml`, `deploy.yml`, `retire.yml`); the
  `deploy_source` and `catalog` roles of contract v1 are gone.
  `users_groups` creates `infra_accounts` (`pinka-build`); `pinka-gt` is now
  `pinka-restricted`; the layout gains `platforms`, `restricted` and the
  root-only `/var/lib/pinka/{git,staging,platforms}` and the build's uv cache.
- **Bootstrap**: `--inspect-platform`, `--replace-platform NAME`, exit code
  65 (`EXIT_RELEASE_REFUSED`) through the signal file.
- **pinka**: `pinka platform purge <name> [--confirm <name>]`.
- **Records**: `contract/records/{deployed-from,active-platform,retired-platform}.schema.json`
  and the generated `docs/reference/platform-records.md`.
- **Tests**: 86 deployment tests in `tools/platform/tests/test_deploy*.py`
  with real Git repositories (the 17 tests of the removed
  `ansible/tests/test_deploy_source.py` are ported there); role variable,
  layout, bootstrap and `pinka` tests.

### Interpretations awaiting owner confirmation (3d.2)

1. **Where 3d.2 ends.** Contract v1's deployment is replaced now (mirror,
   extraction, catalog role, the v1 steps of the platform role). Contract
   v1's rendering (the potka unit template, the socket and config helpers,
   the signing tasks) stays in the role with its tests but unused until 3d.3
   replaces it with rendering from the profiles; doctor's v1 platform
   checks, the fingerprint's platform provenance, the OPA bundle path and
   root-only backups of the restricted area follow in 3d.3 and 3d.4.
   Alternatives: keep v1 rendering running on top of the v2 deployment
   (glue code thrown away in 3d.3), or delete it now with its tests.
2. **Engine in Python** in `tools/platform`, run with the Ansible
   environment's Python 3.12 (PyYAML), next to the validator it calls.
   Alternative: Ansible tasks and a module.
3. **Settings.** `tag_prefix` is the prefix of the source's release tags:
   the tag recorded in the provenance is the newest such tag on the commit;
   with `require_signed_tags` the ref must be such a tag and is verified for
   every source (release and packages). A deployed directory is named after
   the tag when the ref is that tag, otherwise after the full commit (the v1
   rule).
4. **Mirrors**: one per repository (the URL without a trailing `/` or
   `.git`), named `<last segment>-<12 hex digits of its SHA-256>.git`; SSH
   sources share the host's one deploy key.
5. **`file://` repositories** must be owned by root (or the running user),
   and neither they nor a parent directory may be writable by group or
   others; a sticky world-writable parent such as `/tmp` is accepted.
6. **Refused (65) or failed (60).** Every problem with the release, its
   sources or the settings refuses (unknown ref or path, signature, overlap,
   content packages against the manifest, a moved tag, the manifest and the
   release, the one-platform rule, a build that changes the release, the
   checks after the build). Fetch, network, credentials, a failed build or
   image pull fail the play (60).
7. **`--replace-platform NAME`** must name the active platform; it is
   refused without an active platform, for another name, and when the
   release is the active platform itself.
8. **Build sandbox** (`systemd-run --wait --pipe --collect`): `User=` and
   `Group=pinka-build`, `ProtectSystem=strict`, `ProtectHome`,
   `PrivateTmp`, `PrivateDevices`, kernel and control-group protections,
   `NoNewPrivileges`, no capabilities, `RuntimeMaxSec=3600`, the infra
   internals inaccessible (mirrors, secrets, signing, Incus, inference and
   QA directories, the restricted area, backups, training, Docker, Incus
   and `/run/pinka`). The staged release is bound at its final path, so the
   environment's absolute paths are right after publishing; it may write
   only there, in the uv cache (`/var/cache/pinka/uv`, `pinka-build` 0700)
   and its private `/tmp`. Network access stays (uv downloads the locked
   packages). `uv sync --frozen --all-packages --no-dev --no-editable
   --compile-bytecode --python /opt/pinka/python/bin/python3` with
   `UV_PYTHON_DOWNLOADS=never` and `UV_NO_CONFIG=1`.
9. **The build may not change the release**: every entry that existed before
   the build must be unchanged afterwards (`build-modified-release`); new
   entries outside `.venv/` are allowed but checked for links leaving the
   release, and links in `.venv/` (to infra's Python) are allowed.
10. **Job images**: pulled only when `docker image inspect` does not show
    the digest in `RepoDigests`, and checked again after the pull.
11. **Publishing**: copies with links preserved, renamed into place; the
    release and non-restricted content root:root 0755/0644, restricted
    content root:`pinka-restricted` 0750/0640 (executables keep the
    executable bit); a published directory with the same commit and tree is
    left alone. `current` links: `releases/current` and
    `content/<package>/current`.
12. **Records**: `DEPLOYED_FROM.json` (kind, package, restricted, source,
    commit, tree, tag, signature check, platform name, version, runtime,
    manifest SHA-256, time); `active.json` (also the services with their
    profiles, the jobs, the CLI and every derived name by namespace, which
    the next host check exempts); `retired.json` and an append-only
    `deployments.jsonl` in `/var/lib/pinka/platforms/<platform>/` (0700).
13. **Replacing** works from the old `active.json`: units
    `<platform>-<service>.service` and `/run/pinka/platform/<service>`
    removed, the CLI link removed if it is a link, the accounts of services,
    jobs and `<platform>-migrate` locked (password and shell), the recorded
    database roles `NOLOGIN`, the Incus trust entries named
    `<platform>-<service>` of Incus-capable profiles removed. The drain hook
    joins in 3d.4.
14. **Purge**: only a replaced platform (`retired.json`), never the active
    one, with its name typed (prompt or `--confirm`). The database is dropped
    first (`DROP DATABASE ... WITH (FORCE)`, then the recorded roles), so a
    database failure removes nothing; then `/srv/pinka/platforms/<platform>`
    and `/srv/pinka/restricted/<platform>`. Accounts stay locked, published
    signing keys stay, the records stay with `purged_at`. Alternative: also
    delete the accounts, groups and secrets directories.
15. **`--inspect-platform`** runs only the platform step (tag `platform`),
    writes no bootstrap state and runs no doctor; it excludes `--check`,
    `--restore`, `--tags` and `--skip-tags`. The JSON summary for the
    installer comes with 3d.7.
16. **Check mode** skips every step that fetches or writes (prepare and the
    later steps).
17. **Consumers of the old paths**: `backup_include` backs up `restricted`
    instead of `catalog`; the inference control service hides
    `/srv/pinka/restricted` instead of `/srv/pinka/catalog`.
18. **Attribution check** (owner rule): `Co-Authored-By`, `Claude-Session`
    and lines starting with `Generated with` or `Generated by` in any commit
    message reachable from `HEAD`; a shallow clone is a finding, outside git
    the history is not checked; the jobs that run the scan fetch the whole
    history.

### Findings to report (3d.2)

- `0503e86` and `8df9199` are both on `main`, and `0503e86` is `main`'s root
  commit. Rewriting the working branch to drop their trailers changes the
  hash of every commit, `main`'s history included; `main` keeps the trailers
  unless it is replaced by the rewritten branch, which the rewrite exception
  does not cover.
- `git archive` itself refuses a tree with a `.git` entry ("invalid path");
  the engine reports that as `source-git-entry`, and its tar filter refuses
  such entries too.
- The installer still writes the contract v1 keys (`platform_enabled`,
  `platform_ref`, `catalog_ref`, `pinka_source_repo`), which no longer have
  any effect; its sources screen follows in 3d.7. `docs/contracts.md` still
  describes contract v1 and is rewritten in 3d.8.
- Not verified on a real host (needs a VM, 3e or `make test-distros`): the
  build sandbox (`BindPaths` into `/srv` under `ProtectSystem=strict`, uv
  with network access as `pinka-build`), the Incus trust list's `name` and
  `fingerprint` fields, and a full deployment of `refplat`.
  (`DROP DATABASE ... WITH (FORCE)` exists since PostgreSQL 13; 18 is
  pinned.)

## Contract v2, step 3: rendering from the profiles (plan step 3d.3)

BUILD_PROMPT §16 step 17 ("Rendering from profiles: accounts, groups, units,
sockets (dedicated agent sockets included), ACLs, database and `restricted`
schema, Incus certificates, the run-network helper, OPA, dashboards, tools,
CLI link; platform special cases removed; the neutrality lint; tests proving
that the agent has no IP access and no database socket group"), §7.14 step
7, and the follow-ups the owner asked for with the "commit" for 3d.2.

- **Engine** (`tools/platform/pinka_platform/`): `accounts.py` (accounts,
  groups and memberships from the profiles, with the §12.4 refusals),
  `units.py` (service, socket and target units), `database.py` (the
  bootstrap and restricted-check SQL), `config.py` (the services'
  configuration against their schemas), `rendering.py` (one document with
  everything the role applies); deployment steps `deploy render` and `deploy
  principals`; `rendered.json`; the vault keys a release needs; the policy
  bundle check.
- **Ansible**: `platform` applies the rendering (`tasks/render.yml`); the new
  helper role `active_platform` gives the other roles the rendering; the
  `postgres` role sets up the platform database (`tasks_from: platform`);
  the `incus` role gets the run-network helper and issues the certificates
  from the rendering; `opa`, `observability`, `compose`, `vllm`, `backup`
  and `doctor` read the active platform instead of naming one;
  `users_groups` refuses researchers named like a lab account;
  `inference_control` runs as the new infra account `pinka-inference`;
  model files are `root:pinka`.
- **Removed platform special cases**: the contract v1 unit template, the
  socket and configuration helpers of v1 (`pinka_socket_perms.py` became the
  generic `pinka_socket_acl.py`), the potka OPA placeholder, the run explorer
  dashboard, `service_accounts` and the potka database roles of
  `group_vars`, the gateway's ACL on the inference client key, the
  orchestrator's drain call in `pinka stop`, and every platform name in
  infra's code and tests. The Pokus Platform's manifest stays a test fixture
  (`pokus-platform.yaml`).
- **Neutrality lint** (`make lint-neutral`, part of `make lint`):
  `tools/repo-checks/pinka_repo_checks/neutrality.py` with its lists in
  `neutrality.json`.
- **3d.2 follow-ups**: device files, FIFOs, sockets and setuid or setgid bits
  refused in staged sources, content packages and build output; the
  two-phase build; purge keeps accounts, secrets and published keys by
  default, lists what remains, and removes the secrets directories with
  `--remove-secrets`; CI asserts `ssh-keygen`, the PostgreSQL server
  binaries and `systemd-analyze`, so the signed-tag test, the SQL tests and
  the unit check skip only outside CI.
- **Tests**: `tools/platform/tests/test_rendering.py` (68, golden files of units,
  accounts, SQL and the document per fixture, `systemd-analyze verify` of
  every rendered unit), `test_deploy_render.py` (34),
  `ansible/tests/test_postgres_sql.py` (24, the SQL against a real
  PostgreSQL server), `test_run_network.py` (26),
  `tools/repo-checks/tests/test_neutrality.py` (38) and the updated role,
  doctor, fingerprint, installer and generator tests; `make test` runs
  2324 tests.

### Interpretations awaiting owner confirmation (3d.3)

1. **Where rendering happens.** `pinka-platform deploy render` computes one
   document from the manifest, the profiles and the host settings; the
   platform role applies it with ordinary modules (`group`, `user`, `file`,
   `copy`) and the postgres and incus roles. The public part of the document
   stays as `/etc/pinka/platform/rendered.json` (0644, without the
   configuration contents), which the other roles read through
   `active_platform` and which replacing uses to find what to remove. It is
   infra-internal, not part of the contract. Alternatives: Jinja templates
   per object in the roles (the profile rules then live in two languages),
   or the tool applying everything itself as root.
2. **Sockets by socket activation.** Each socket of the manifest is a socket
   unit `<platform>-<service>-<socket>.socket` (`SocketUser` the serving
   account, `SocketGroup` the client group, 0660, `DirectoryMode=0755`,
   `RemoveOnStop=yes`, `FileDescriptorName=<socket>`,
   `Service=<platform>-<service>.service`). The service receives its
   sockets as file descriptors (`LISTEN_FDS`, `LISTEN_FDNAMES`) and never
   creates them. This touches the contract: a platform service must accept
   its sockets from systemd; `docs/contracts.md` gets it in 3d.8, the
   validate how-to has it now. Alternative: services bind the sockets
   themselves and infra fixes owner, group, mode and ACL after start, which
   races with clients and needs a writable directory per service.
3. **No `RuntimeDirectory=` (proposed deviation V4 from the §7.14
   baseline).** The baseline lists `RuntimeDirectory=pinka/platform/<service>`.
   With socket units it cannot be used: systemd removes the runtime
   directory, sockets included, when the service stops, and changes the
   ownership of everything in it to the service's account when it starts,
   which resets the client groups of the sockets. The socket units create
   `/run/pinka/platform/<service>/` (root, 0755) instead; §6.2's
   "`/run/pinka/` (runtime sockets via systemd `RuntimeDirectory=`)" holds
   for infra's own units.
4. **ACL entries** for `pinka` and `pinka-admin` are set by the socket unit
   (`ExecStartPost`, `/usr/local/libexec/pinka/socket-acl`) only on sockets
   whose clients include researchers or admins. Without ACL support on
   `/run` the helper makes the socket 0666 and the platform's peer-credential
   check alone separates the clients (§12.3). Sockets of `restricted-*`
   services never have human clients, so they never fall back.
5. **The hardening baseline** is rendered in the order of §7.14, from the
   profile only: `IPAddressAllow=localhost` for the profiles with loopback
   access, none for the others; `SocketBindDeny=any` everywhere and
   `SocketBindAllow=tcp:<gateway_adhoc>` for the inference broker only;
   `MemoryDenyWriteExecute=no` only with the relaxation `wx-memory`, which
   the validator refuses for the agent; `Restart=on-failure`; ordered after
   the health gate and the facilities the manifest requires, and after the
   services it lists in `after`; part of `pinka-platform.target`.
6. **Infra internals hidden from every profile** (`InaccessiblePaths`, each
   with `-` so a missing path is no error): the Git mirrors, staging,
   `/var/lib/incus`, `/var/lib/docker` and the Docker sockets, infra's
   container and backup secrets, the inference node keys, the backups, the
   GPU arbiter's and the run-network helper's sockets (not for the
   controller), the restricted area (not for the three restricted
   profiles), the database socket directories (without a database role),
   the Incus certificates (without Incus access) and the signing directory
   (not for the controller).
7. **The agent's allowlist**: `PrivateNetwork=yes`,
   `RestrictAddressFamilies=AF_UNIX`, `IPAddressDeny=any` without any allow
   rule, `TemporaryFileSystem=` (read-only) over `/srv/pinka`, `/etc/pinka`,
   `/var/lib/pinka` and `/run/pinka`, with only the platform's `releases`
   directory (all published releases; `current` is a link into it), its own
   configuration file, its dedicated sockets and the agent OTLP socket
   (`-/run/pinka/otlp/agent.sock`, optional until 3d.4) bound in read-only
   and its own state read-write; its only supplementary groups are the
   client groups of its dedicated sockets; it `Requires` those socket units
   and stops with them. Tests prove no IP access, no database socket group,
   no other group and nothing else visible.
8. **Accounts**: `<platform>-<service>`, `<platform>-<job>` and
   `<platform>-migrate` are system accounts with shell `nologin`, home
   `/var/lib/pinka/<account>` (not created; the units use `ProtectHome`),
   a locked password, a primary group of their own name and exactly the
   groups their profile grants (`append: false`): `pinka-restricted` for
   the restricted profiles and the controller, `pinka-query` for the
   profiles that query, `<platform>-db` for the profiles with a database
   role, `pinka-datasets` for training jobs, the client groups of the
   sockets they may use. The tool refuses host groups (`docker`, `incus*`,
   `sudo`, `wheel`, `adm`, `pinka`, `pinka-admin`, ...), any of the three
   restricted groups for the agent and training jobs, any group but the
   dedicated client groups for the agent, and unknown groups.
9. **Database**: one database `<platform>` per platform; a login role
   `<platform>_<service>` per service whose profile has a database role,
   each in a `NOLOGIN` group role per profile (`<platform>_<profile>`); the
   migrator `<platform>_migrator` owns the database and the schemas
   `public` and `restricted`; the reader and Grafana roles; `CONNECT` only
   for these roles; roles a previous release had become `NOLOGIN`. The
   restricted check revokes and reports every privilege outside the allowed
   set (the migrator, the group and login roles of `controller`,
   `restricted-operator` and `restricted-reader`), including column and
   function privileges, default privileges, views outside the schema that
   read it, `SECURITY DEFINER` functions of allowed roles and memberships
   in allowed roles; `PUBLIC`'s default `EXECUTE` on functions in the
   schema is revoked as a change, not reported. Every statement was run
   against PostgreSQL 16 here; the lab pins 18.
10. **Passwords** come from the vault (`vault_postgres_passwords.<role>`,
    `vault_platform_secrets.<service>.<name>`); `deploy prepare` lists the
    keys a release needs, and a missing one stops bootstrap with exit code 77
    before anything changes. Infra generates no platform password.
    Alternative: generate missing passwords on the host like infra's own
    (then the installer could not show them and a reinstall would change
    them).
11. **Configuration**: `platform_settings.<service>` in the host settings,
    checked at prepare against the service's schema in the release with the
    keywords of the manifest schema plus annotations; an unknown service, a
    setting for a service without a schema and any other keyword are
    refused (65). Written as JSON to
    `/etc/pinka/platform/config/<service>.json` (0640 `root:<account>`).
12. **Incus certificates**: one per service whose profile has Incus projects,
    named `<platform>-<service>` (its account), in
    `/etc/pinka/incus/<platform>-<service>/`, restricted to the profile's
    projects; issued by the incus role's certificate tasks from the
    rendering.
13. **Run-network helper**: socket-activated with `Accept=yes`, one root
    instance per connection, no network, only `AF_UNIX`, no capabilities,
    writing only `/var/lib/pinka/run-networks/`. The bridge keys
    (`ipv4.address=auto`, `ipv4.nat=false`, `ipv6.address=none`,
    `security.acls`) were checked against the Incus 6.0 option list
    (`doc/config_options.txt` on the `stable-6.0` branch); a network that
    exists with other settings, an uplink included, is refused. The socket
    belongs to the active platform's controller account, root without a
    platform.
14. **OPA**: the authorization policy allows exactly the manifest's
    decision path and infra's placeholder path; the bundle is mounted from
    the published release. A bundle module may not declare a package below
    `system` or `pinka`, and no data file may sit below `system/` or
    `pinka/` or put those keys at the top of `data` (refused, 65).
15. **Dashboards**: the JSON files of the manifest's directory, copied
    read-only into a Grafana folder named after the platform; the
    PostgreSQL datasource exists only with a platform database and uses
    `<platform>_grafana` through the reader socket directory.
16. **CLI**: a wrapper `/usr/local/lib/pinka/platform/cli/<cli>` (a shell
    script that `exec`s `cli.run` in `releases/current`, every word quoted)
    and a link to it in `/usr/local/bin`; replacing removes both.
17. **Units are installed, not started**: no unit is enabled or started
    until the switch with the hooks (3d.4); `pinka stop` reports that the
    drain hook is not run yet.
18. **Signing key**: `/etc/pinka/signing/<platform>/` (`root:<controller>`
    0750), published as `<platform>-<machine>-<YYYYMMDD>.pub` in
    `/srv/pinka/shared/signing-keys/` and kept there.
19. **The neutrality lint** scans every file git tracks or would track,
    reporting the Pokus Platform's name, its service and unit names as
    whole tokens, and the project name outside its allowed forms. Excluded,
    each with a reason: the documentation and READMEs, test fixtures and
    golden files, the reserved-name list, the leakage lint's patterns, lock
    files, and, until 3d.7 rewrites them, the installer and its answers
    files (proposed deviation V5). Two files may name them on lines that
    are exactly one listed word (the reserved list's source and the leakage
    lint's test words).
20. **Researcher names**: `users_groups` refuses a researcher named like an
    infra or platform account or group before it changes anything (the
    user module would otherwise make that account a human one).
21. **The installer's validator** refuses only `pinka-*` and infra's own
    accounts; it no longer knows the platform's names. Platform accounts
    are protected by the host check of the deployment (a name that exists
    on the host and is not the same platform's is refused) and by item 20.
22. **The two-phase build** (owner request on 3d.2): phase 1 `uv sync
    --frozen --no-install-project --no-install-workspace --no-build` with
    network installs only the locked wheels, verified against `uv.lock`; it
    builds nothing and compiles nothing, so no interpreter starts on the new
    environment (a wheel's `.pth` file would run then). Phase 2 runs with
    `PrivateNetwork=yes` and `--offline --no-build-isolation
    --compile-bytecode`: the release itself is built with the backend that
    phase 1 installed and the whole environment is compiled. A release with
    a `[build-system]` must therefore lock its backend in a dependency
    group `build`, and every dependency must be available as a wheel
    (proposed deviation V3). The flags were checked with `uv sync --help` of
    the pinned uv 0.12.23. Residual risk: phase 2 runs the release's own
    build backend and hooks, without network; a backend left in the runtime
    environment.

### Findings to report (3d.3)

- The manifest's `telemetry.labels` are rendered, but the collector does not
  yet turn them into Loki labels: Loki's OTLP configuration must be checked
  against the pinned release first. It follows with the telemetry work of
  3d.4.
- Backups do not yet include the platform's `backup.paths`; the database
  dump follows `backup.database` already. Both belong to 3d.4 (backup and
  restore).
- Doctor's v1 platform checks (unit hardening and socket permissions from
  fixed lists) are switched off (`doctor_platform: false`, so those checks
  report "skip"); the checks from the profiles and the manifest follow in
  3d.4.
- `ruff format` run on a wrong path list reformatted the vendored lint
  collections in the gitignored `.ansible-collections/`; their code is
  unchanged (ruff keeps the syntax tree), Galaxy is blocked here, so they
  stay so until the next `make setup` on a machine that reaches Galaxy.
- Not verified on a real host (needs a VM, 3e or `make test-distros`):
  starting the rendered units and socket activation, the socket-ACL helper
  on `/run`, the run-network helper against Incus, the certificates' trust
  entries, the two build phases, and the SQL on PostgreSQL 18.

### Owner decisions on 3d.3 (with the "commit")

- V3 approved, and carried into the requests for the next platform spec edit
  (wheels only, the build backend locked in a `build` dependency group).
- V4 approved on one condition: a service that needs its own runtime
  directory gets one at a separate path that contains no sockets (3d.4).
- V5 approved only as an explicit, named exclusion in the lint configuration
  with a comment pointing to 3d.7; 3d.7 must remove it.
- Ground-truth blindness: the agent must see only the active release,
  resolved at render time; the answer and the proposal (V6) are in
  `../PROGRESS-ARCHIVE.md`; the fix follows in 3d.4.
- 3d.4 also records which sockets fell back to 0666 (doctor warns), runs the
  restricted-schema SQL tests against PostgreSQL 18 from PGDG in CI, and makes
  `make setup` verify the lint collections against the lock.
- The new repository gets only `main` and `moving/on`, which becomes the
  working branch (root `CLAUDE.md`).

## Contract v2, step 4: operations (plan step 3d.4)

BUILD_PROMPT §16 step 18, the 3d.3 follow-ups and the answer on
ground-truth blindness:

- **Hooks (§12.9).** Every hook runs as a transient unit
  `pinka-hook@<platform>-<hook>-<id>.service` (`systemd-run --wait --collect
  --service-type=exec`) with the hardening of the account it runs as,
  `RuntimeMaxSec` from the manifest, `StandardOutput=file:` a result file in
  `/run/pinka/hooks/` and `StandardError=journal`; `pinka-platform hook-exec`
  inside it judges the one JSON line (`ok` or `drained` true and exit 0).
  `reconcile` is a oneshot unit ordered before the services. The switch
  (§7.14 step 8) drains and stops early on a release or content change,
  migrates a new release, runs the restricted-schema check, activates and
  starts; `start.yml` ends `site.yml` and waits for `health`.
  `pinka start`, `stop`, `status` and `update-host` call the hooks.
- **Health gate.** The clock always, then exactly the manifest's `requires`
  read from `rendered.json` at every start; a facility this node lacks
  fails the gate.
- **Backup and restore (§7.15).** The platform's `backup.paths`, the fixed
  restricted area, training data and adapters; doctor's
  `backup.platform_state` lists state directories left out. Restore drains
  first, redeploys the recorded release and content refs and waits for
  `health`.
- **Telemetry isolation (§7.9 option b, ADR-0016, proposed).** Platform
  accounts get UIDs from 60578-61183; `inet pinka_telemetry_guard` drops
  their connections to the stores and the query proxy's loopback port. The
  query proxy (nginx, the golden images' pinned release) serves
  `/run/pinka/query.sock` (a link to `/run/pinka/query/query.sock`, 0660
  `pinka-query`) and `127.0.0.1:18733` for Grafana, with an allowlist of
  query routes. The agent OTLP socket starts `systemd-socket-proxyd` to the
  collector's `otlp/agent` receiver, which stamps `pinka.trust=untrusted`;
  Loki indexes `pinka.trust` and the manifest's `telemetry.labels`
  (`limits_config.otlp_config`).
- **Driver CUDA (§2.2).** `cuda` of the vLLM image in `versions.lock.yml`
  (re-verified: `CUDA_VERSION=13.0.2` in both platform images'
  configuration, `NVIDIA_REQUIRE_CUDA` `cuda>=13.0`); preflight reads the
  driver's CUDA version from `nvidia-smi --version` (falling back to the
  banner of a plain `nvidia-smi`), `driver_cuda_min` compares major and minor
  and a per-environment remediation names the update (on WSL2 on Windows);
  doctor repeats it (`gpu.driver_cuda`); fixtures
  `ubuntu2404-baremetal-nvidia-old-driver` and `ubuntu2404-wsl2-gpu-old-driver`.
- **Ports (§12.11).** `pinka_default_ports`, the host_vars `ports:`
  overrides and the effective `pinka_ports` (validated in
  `platform_facts`); `vm_collector_address` with its check. Bootstrap passes
  the overrides to preflight (`--port`, `--vm-collector-address`), and so do
  the preflight gate and doctor. Preflight reports a lab port another program
  listens on (`lab_port_in_use`, needs_action) and a VM collector address
  already assigned, routed, or inside the Docker pools, an Incus network or
  the tailnet range (`vm_collector_address_conflict`); fixtures
  `ubuntu2404-lab-port-in-use` and `ubuntu2404-vm-collector-address-routed`.
  Unlisted releases of the implemented distributions are now explicitly
  unsupported (St2-3).
- **Doctor from the rendering (§7.17).** `systemd.hardening` compares the
  `systemctl show` values of each platform unit with `hardening` in
  `rendered.json` (per profile); `sockets.permissions` reads the rendered
  sockets and names the agent's; `sockets.agent_view` lists the sockets the
  running agent finds through `/proc/<pid>/root`; `platform.manifest`
  validates the active release again and compares its manifest hash with
  `active.json` and the provenance. The v1 `sockets.json` path is gone.
- **Records.** `contract/records/session-record.schema.json` (new) and the
  fingerprint schema, moved from `tools/pinka-lab/schema/` to
  `contract/records/fingerprint.schema.json`.
- **Docs service (§7.19, P3c).** The `docs` role, `pinka-docs.service`
  (`pinka docs serve` as a dynamic user), `pinka docs install` (checksum,
  safe unpacking, `current` switched in one step, three versions kept) and
  `docs rollback`, `docs_release`, `docs_tailscale_serve`, doctor
  `docs.service`.
- **Maintenance windows (§7.20, P3d).** `pinka window open|close|expire`,
  the record, refusals in `pinka start`, the health gate's window rule, the
  expiry timer (staging on shared hardware), doctor `lab.window`.
- **Agent view.** The agent binds only the release being deployed, resolved at
  render time (`releases/<name>`, never the `current` link or other
  releases), and runs from it; a test lists what it may see and fails for
  other releases, content packages, other services' state, configuration,
  sockets and runtime directories, secrets and infra's internals.

### Verified in 3d.4

- systemd v252 and v255 sources: `systemd-run --pipe` overrides
  `StandardError=` (hence the result files); `systemctl show` prints booleans
  as `yes`/`no`, string properties as written and an address-family allow
  list as names (deny lists with `~`); `docs/UIDS-GIDS.md` lists
  60578-61183 as unused.
- iproute2 (commit `e11870d5b9414b2e0770463bd6fc2399744dc734`): the JSON keys
  `ifname`, `addr_info`, `family`, `local`, `prefixlen`, `dst` and `dev`, and
  `dst` `default` for the default route.
- nvidia_gpu_exporter v1.15.1 (commit `87afc069c5ce6a37b13db58989ec0f30d9b87217`):
  the `nvidia-smi --version` line `CUDA Version : 13.1` and the banner's
  `CUDA Version: 13.1` in its captures of drivers 590 and 595; its parser
  also reads `CUDA UMD version` (drivers of the 610 branch); the
  `--query-compute-apps` fields `pid` and `process_name`.
- The vLLM image's configuration (registry API, 2026-10-10): `CUDA_VERSION`
  and `NVIDIA_REQUIRE_CUDA` above.
- restic v0.19.1: `dump` of a recorded file for `platform-record`.
- Prometheus v3.15.0, Loki v3.7.8 and Tempo v2.10.8 sources: the query
  routes of the proxy's allowlist and Loki's `otlp_config`.
- Incus: `config` of `incus network list --format json` with
  `ipv4.address` in CIDR form.

### Interpretations awaiting owner confirmation (3d.4)

- ADR-0016 (telemetry isolation by UID range and nftables guard, option b):
  proposed. Researchers and infra's own accounts can still query the stores
  on the loopback directly; an allowlist of infra UIDs would close that.
- The query socket is a link (`/run/pinka/query.sock` →
  `/run/pinka/query/query.sock`): the proxy container cannot create a
  socket in root-owned `/run/pinka`. The agent socket speaks OTLP/HTTP.
- The restricted-schema check runs after `migrate` and also at render.
- Restore redeploys the recorded release and content refs from the mirrors
  of the current host_vars repositories.
- Driver CUDA: major and minor compared (nvidia-smi reports no patch level;
  the image requires `cuda>=13.0`); a GPU whose driver reports no CUDA
  version keeps `gpu_inference` off with the "unknown" remediation.
- Ports: preflight reads listeners from `/proc/net/tcp*` and their owners
  from `/proc/<pid>/fd` instead of `ss` (stable kernel interface, no parsing
  of `ss` output); a process belongs to the lab when its cgroup is
  `pinka-services.slice` or a `pinka-` unit, or it is the Docker or Incus
  daemon the lab manages; a listener whose owner preflight cannot see is a
  warning. Bootstrap reads `ports:` only as indented lines or a one-line
  mapping. The VM address is checked only when KVM is usable. The Docker
  pools checked are the lab's default plus those in `daemon.json`.
- Doctor's hardening check compares the properties `systemctl show` prints
  as written (a fixed list), the address families and the inaccessible
  paths; `platform.manifest` validates with `--built --no-host-check`.
- Docs service: version directories are named after the tarball (or
  `--version`); `tailscale serve` uses HTTPS port 10000; `docs.service` is a
  warning.
- Windows: `window open` refuses on staging with its own hardware;
  `window close` on staging stops a running staging lab first; the health
  gate fails at once on a window (also for staging outside its window at
  boot); the expiry timer checks every minute; the staging start check counts
  any listener on a configured port that is not this host's lab, an unknown
  owner included; staging on its own hardware ignores the record entirely
  (also a broken one).
- The installer keeps an explicit `null` in `group_vars/all/main.yml`
  (`docs_release: null`): its round trip now writes every null as `null`, so
  a key an operator left empty comes back as `key: null` (same value).
- The contract document's port table (`docs/contracts.md`) is updated with
  the contract v2 documentation in 3d.8.
- V6 proper (proposal in `../PROGRESS-ARCHIVE.md`): the agent sees the whole active
  release now, not yet only its own package and environment. Narrowing to
  the package needs a manifest field for agent services and a build change
  (an environment of their own), a contract change for the owner.

### Findings to report (3d.4)

- The vLLM image's `TORCH_CUDA_ARCH_LIST` on amd64 starts at 7.5, while the
  support matrix (and §2.3) accepts compute capability 7.0 for
  `gpu_inference`: a 7.0 GPU passes preflight but vLLM has no kernels for it.
- There is no `platform/docs/contracts.md` in this repository, although
  `CLAUDE.md` asks for byte identity with it (C1 goes in 3f).
- The installer's recorded event streams (`installer/tests/data/`) still
  carried the `catalog` role removed in 3d.2; re-recorded with the `docs`
  role (only timings differ otherwise), and the progress screenshots with
  them.
- Not verified on a real host: hooks in transient units, the telemetry
  guard and the query proxy, the agent socket, the docs service under
  `DynamicUser=`, the window rules at boot, the port and address checks on a
  running lab, the agent's view through `/proc/<pid>/root`.

### Open `TODO(verify)` items after 3d.4

- `tailscale serve --https=10000` in the pinned Tailscale (docs service).

### Owner decisions on 3d.4 (with the "commit")

- V6 approved as its own step 3d.4b: the agent's own package and
  environment, refusal of an agent package that depends on another package
  of the release, tests for visibility and for imports.
- ADR-0016: the alternative; only an allowlist of infra UIDs reaches the
  telemetry stores directly, everyone else (researchers included) goes
  through the query proxy; a test per account class (3d.4b).
- Restore deploys exactly the recorded commit and tree hash, or refuses
  (3d.4b).
- V7: GPU compute capability 7.5 at least; preflight refuses below it
  (3d.4b).
- The query socket link is approved, provided doctor checks the link's
  target and the owner of the target directory (3d.4b).
- Every other choice of 3d.4 is approved.

## Contract v2, step 4b: the owner's 3d.4 decisions (plan step 3d.4b)

- **V6, the agent's own package.** An `untrusted-agent` service names the
  directory of its own package (`package`, required for that profile only:
  `own_package` in `contract/profiles.yml`). For `python-uv` the directory
  is a workspace member locked in `uv.lock`; every package it depends on,
  directly or through others, must come from an `https://` package index,
  otherwise the release is refused (`agent-package-depends`). The build
  gives each agent an environment of its own, `.agent-venvs/<service>/`:
  `uv sync --package <name>` with the `build` group (no build isolation),
  then once more without the group, which removes the build backend; after
  the build, an environment holding anything but the package and its locked
  dependencies is refused (`agent-env-extra`), whatever uv did. A start
  command below `.venv/` runs from that environment. The agent's unit binds
  only the package directory and its environment of the release and runs
  from the package directory; doctor's `sockets.agent_view` also lists what
  the running agent sees of the release. A release that ships `.venv` or
  `.agent-venvs` is refused (`built-entry`).
- **Telemetry stores (ADR-0016 accepted with the owner's alternative).** Only
  infra's own clients reach Prometheus, Loki and Tempo directly
  (`pinka_telemetry_clients`): root, the collector, Prometheus, Loki, Tempo,
  Grafana and the query proxy, each container running as an infra account
  of its own (`pinka-otelcol`, `pinka-prometheus`, `pinka-loki`,
  `pinka-tempo`, `pinka-grafana`, `pinka-query-proxy`). The guard's set
  holds their UIDs and resets everyone else's loopback connections to the
  guarded ports. Doctor compares the loaded set (`nft -j`) with the
  accounts' UIDs.
- **Restore** pins every recorded source to its commit and tree (`commit`,
  `tree` in the source settings) and checks them with `deploy prepare
  --inspect` before it stops anything; the deployment refuses a commit the
  mirror lacks (`source-ref`), a moved tag (`source-commit`) or another tree
  (`source-tree`).
- **V7.** `gpu_inference` needs compute capability 7.5; below it preflight
  refuses GPU inference with a remediation naming the GPU's capability;
  fixture `ubuntu2404-baremetal-nvidia-compute-7-0`.
- **Query link.** Doctor's `telemetry.isolation` checks that
  `/run/pinka/query.sock` links to `query/query.sock` and that
  `/run/pinka/query` is `pinka-query-proxy:pinka-query` 0750.

### Verified in 3d.4b

- uv 0.12.23 (the pinned binary, its checksum from `versions.lock.yml`) on a
  workspace built offline: `uv sync --package <member>` with `--group build`
  installs the member, its dependencies and the workspace root's `build`
  group (a single selected member inherits the root's groups,
  `crates/uv/src/commands/project/install_target.rs`); a second sync without
  the group and with `--no-build` uninstalls the group's packages and keeps
  the member without rebuilding it; `UV_PROJECT_ENVIRONMENT` with an
  absolute path creates the environment there. `UV_NO_CONFIG=1` also
  ignores `[tool.uv]` in `pyproject.toml` (the build installs from the lock
  anyway). uv 0.8.17 does not inherit the root's groups: the tests that run
  uv require the pinned version (`PINKA_UV`, skipped otherwise outside CI).
- nftables 1.0.9: a `type uid` set with `meta skuid != @set` and `reject
  with tcp reset`; `nft -j list table` shows the set's elements as numbers.
  The per-account-class test ran against the kernel here (root in a private
  network namespace).

### Interpretations awaiting owner confirmation (3d.4b)

- `package` names the package's directory, not its distribution name; the
  distribution is read from its `pyproject.toml` and must be the workspace
  member locked at that directory.
- "Another package of the same release" is read strictly: a dependency from
  a path, a Git repository, a URL or a local wheel directory is refused too,
  because it can hold the release's own code; only an `https://` index
  counts. Markers are not evaluated (a declared dependency counts on every
  platform); extras count only where requested.
- The agent binds its package directory as well as its environment and
  starts in the package directory; with `static`, the package is the
  directory it runs from, and its start command must lie inside.
- A workspace member with a `[build-system]` now needs the root's `build`
  group too (V3 checked only the root before; the build would fail
  otherwise).
- The store containers run as new infra accounts instead of their images'
  users; Grafana keeps group 0, which its image grants its own paths to.
  Data directories created earlier under the old UIDs keep their owners
  (no lab has been deployed yet).
- The guard matches connections on the loopback interface only and resets
  them instead of dropping them; the platform's UID range stays for the
  accounts but no longer matters to the guard. Researchers query through
  Grafana; they are not added to `pinka-query` (§7.9: exactly the profiles
  that query).
- Restore deploys through the recorded tag only when `require_signed_tags`
  is on (the tag must still point to the recorded commit); otherwise through
  the commit. Content packages the snapshot does not record keep their
  host_vars refs, as before.
- V7: a GPU below 7.5 leaves the host degraded (GPU inference off, no
  override), not unsupported.

### Findings to report (3d.4b)

- The Pokus Platform's spec places the agent runner in `potka-agent` next to
  the shared `potka-core` (socket client, schemas, telemetry). Under V6 the
  agent package may not depend on `potka-core`: the platform must move what
  the agent needs into `potka-agent` (a platform spec change).
- A source tree could ship its own `.venv`, which the build would have
  synced into; now refused.
- The image default users were a real risk for an allowlist: Incus's
  dnsmasq can run as `nobody`, Prometheus's image user.
- CI now installs nftables and asserts `nft` and `sudo -n`, so the guard's
  kernel test runs there.
- Not verified on a real host: the agent environment's build inside the
  `systemd-run` sandbox, the store containers under their new accounts
  (Grafana above all), the guard with the real accounts, a restore end to
  end.

### Owner decisions on 3d.4b (with the "commit")

- Grafana comes off the telemetry allowlist (first item of 3d.5): only the
  collector, the stores where they talk to each other, the query proxy and
  root reach the stores; Grafana's data sources go through the query proxy.
- The guard matches by UID only; a test shows that an account with GID 0
  outside the allowlist is reset.
- Approved: strict dependency sources, `package` as a directory, restore
  through signed tags, a GPU below 7.5 as degraded, and the two stricter
  checks.
- `make setup` installs the pinned uv; a skipped uv test fails CI.
- Every approved deviation the platform must follow is listed in
  `../PROGRESS.md`, "Platform spec changes pending".

## Contract v2, step 5: the GPU arbiter and training (plan step 3d.5)

### Built in 3d.5: the owner's 3d.4b decisions

- The telemetry guard has two sets: `telemetry_clients` (root, the
  collector, Prometheus, Loki, Tempo, the query proxy) for the stores' ports
  and the collector's metrics exporter, and `query_port_clients` (root,
  Grafana) for the query proxy's loopback port. Both are `type uid` and the
  rules match `meta skuid`; nothing in the guard names a group. The kernel
  test connects as Grafana (its UID with group 0), as Grafana's image default
  (472 with group 0) and as an account with GID 0 that is on neither list:
  the last two are reset everywhere except the OTLP receivers, Grafana
  everywhere except the query port and the OTLP receivers.
- Doctor compares each set with its accounts and checks that the set's own
  rule (a reject for the UIDs outside it) covers its ports; a port that is
  only in the other set's rule counts as open.
- `make setup` installs the uv of `versions.lock.yml` into `build/tools/uv`
  with `pinka_lockcheck fetch uv --member uv`: the archive's sha256 is
  checked first, then the one regular file named `uv` is installed (an
  archive with none, several, or a link of that name is refused). The
  Makefile exports `PINKA_UV`, and the tests find `build/tools/uv` without
  it, so a plain `pytest` runs them too.
- In CI a skipped uv test fails: every test that uses the `uv` fixture,
  directly or through another fixture, carries the `uv` marker, and
  `conftest.py` reports a skip of a marked test as a failure when `CI=true`,
  whatever the skip's reason. A test runs the hooks in a child pytest with a
  uv fixture that always skips.
- That `conftest.py` is the second one of the tree (after
  `installer/tests/`): mypy checks `tools/platform/tests` in a run of its
  own (two `conftest` modules cannot share one), and the installer's flow
  tests take `copy_repo` from `installer_testlib.py` instead of importing
  `conftest` by name, which resolved to the other file in a full run.

### Built in 3d.5: GPU arbiter and training (BUILD_PROMPT §16 step 19)

- **The arbiter** (`ansible/roles/gpu_arbiter/`,
  `/usr/local/libexec/pinka/gpu-arbiter`, root, the Ansible venv's Python)
  replaces the deploy helper. `pinka-gpu-arbiter.socket`
  (`/run/pinka/gpu.sock`, 0600, `SocketUser` the active platform's
  controller, `pinka-inference` on an inference node, `root` before a
  platform is deployed) with `Accept=yes` starts one
  `pinka-gpu-arbiter@.service` per connection; the peer is checked with
  `SO_PEERCRED` before the request is read. Requests `status`, `serve`,
  `idle`, `train`, `publish-dataset` (one JSON line each way, at most 1 MiB,
  unknown keys refused); the control endpoint of an inference node may only
  ask for `status` and `serve`. A lock file serializes transitions; the mode
  is in `/var/lib/pinka/gpu/state.json`, every transition in
  `transitions.jsonl` and the journal, every run in `runs/<run>.json`.
- **vLLM** (`pinka-inference.service`) is installed but not enabled; the
  arbiter renders `current.yaml` and `current.env` from the deployment spec
  (the rendering moved from the deploy helper unchanged), starts the unit,
  waits for `/health` and only then reports `serving`; a deployment that
  does not come up leaves the GPU idle. The vllm role no longer starts
  anything: bootstrap ends with `gpu-arbiter serve <target> --unless-training`
  (or `prepare` when vLLM is not to start), then the serving verification.
  The gpu_arbiter role stops and removes the deploy helper's units and
  program from a host that has them.
- **Deployment specs** name adapters in `adapter_ids` as `<name>@<sha256>`;
  the arbiter refuses one that is not registered, whose files no longer
  match its hash, or whose lineage names another base model. A v1 spec with
  `adapters` is refused (unknown key).
- **Boot.** `pinka-gpu-arbiter.service` (oneshot, `WantedBy` and `PartOf`
  `pinka-lab.target`) settles the state once per lab start. After a new
  boot a run left `training` is interrupted: it resumes (`PINKA_JOB_RESUME=1`,
  same checkpoints, attempt counted) if its job is `resumable`, otherwise it
  ends `interrupted` and the deployment served before the run comes back
  (or the GPU stays idle, per the run's `restore`). Without such a run the
  default deployment is served (`gpu_arbiter.after_boot: serve`) or the GPU
  stays idle (`idle`). Within the same boot (`pinka start` after
  `pinka stop`) an idle GPU follows `after_boot` and a `serving` state whose
  vLLM stopped is brought back.
- **Training runs.** `train` checks that the host trains (`full` node), the
  job exists in the active manifest, the submission (exactly the job's
  dataset kinds, each a published hash; `base_model` a verified model of
  `models.lock.yml` when the job mounts one; `seed` an integer;
  `hyperparameters` an object), the caps (`training_caps`:
  `timeout_seconds`, `memory_max`, `cpus`), every dataset's hash and split
  guard, the base model's file hashes, and, while serving, that vLLM has no
  running or waiting request (`vllm:num_requests_running` and
  `vllm:num_requests_waiting` from its `/metrics`) and the controller set
  `sessions_drained`. The job-spec hash is the SHA-256 of the canonical JSON
  of `job`, `base_model`, `datasets`, `hyperparameters` and `seed`; the run
  ID is `<job>-<YYYYmmddtHHMMSS>-<hash[:8]>`. The arbiter stops vLLM, writes
  the run record and starts `pinka-gpu-job@<run>.service` (`Type=exec`,
  `KillMode=mixed`, `PartOf=pinka-lab.target`), whose `run-job` runs the
  container with the fixed options of
  [GPU arbiter requests](reference/gpu-arbiter.md#the-jobs-container); after
  the job's `timeout_seconds` it stops the container (`docker stop` with
  `stop_grace`) and the run fails. `idle` and a stopped unit
  (SIGTERM: `docker stop` with `stop_grace`) end the run `interrupted`
  (resumable job) or `failed`.
- **Datasets** (`publish-dataset`): `source` is below one of the
  controller's state directories; the copy walks directory file descriptors
  with `O_NOFOLLOW`, accepts only regular files and directories owned by the
  controller, and lands in a temporary directory that is renamed to
  `/srv/pinka/training/datasets/<sha256>/` (root:`pinka-datasets` 0750,
  files 0640) with `PROVENANCE.json` (instances with their split, source
  runs, the platform's commit, the hash). An instance of the `test` split is
  refused at publication and at mount; the hash is checked at mount.
  `pinka-datasets` is a new system group; the job's account is added to it
  only inside the container (`--group-add`).
- **Adapters.** A successful run's `output/adapter/` is copied the same way
  (owner: the job's account), hashed and registered read-only under
  `/srv/pinka/models/adapters/<name>/<sha256>/` (root, 0555, files 0444) with
  `MANIFEST.json` and `LINEAGE.json` (base model and its file hashes,
  dataset hashes, image digest, job-spec hash, hyperparameters, seed, the
  platform's and infra's commits, start and end). An existing registration
  is kept. The tree hash is the SHA-256 of `sha256sum`-style lines sorted
  by path, without the record files; doctor computes it the same way (a
  test cross-checks both). Adapters left `models.lock.yml` and its schema,
  and the models role refuses a repository below `adapters/`.
- **Schemas** `contract/records/dataset-provenance.schema.json`,
  `adapter-manifest.schema.json` and `adapter-lineage.schema.json`, in the
  generated [Platform records](reference/platform-records.md).
- **Rendering** adds `training` (each job's unit, container, account, image
  digest, inputs, outputs and resources) and `inference_clients` (the
  accounts of profiles with `inference_api`) to `rendered.json`.
- **Control API.** `POST /activate` on the inference node sends `serve` to
  the arbiter and answers when it does (`activate_timeout`, 1500 s).
- **Inference forwarder** (lab node, `pinka-inference-forwarder.service`,
  root): listens on `127.0.0.1` on vLLM's and the control API's ports and
  passes every connection to the paired node over TLS 1.3 with this node's
  certificate, accepting only the paired certificate (pinned by SHA-256,
  the name not checked). Hardened unit; `IPAddressAllow` the loopback and
  Tailscale's ranges.
- **Inference guard** (firewall role, `pinka-inference-guard.service`,
  table `inet pinka_inference_guard`): sets `inference_clients` (root,
  infra's scrapers for the node role, the platform's `inference_clients`)
  on vLLM's port and `inference_control_clients` (root, the controller) on
  the forwarder's control port, on a lab node; a `type uid` set and
  `meta skuid` like the telemetry guard (ADR-0016, decision 6). The loader
  resolves the accounts from `/etc/pinka/nftables/inference-guard.json` and
  `rendered.json` each time; the platform role restarts it after every
  switch and retirement. A kernel test connects as one account per class.
- **`pinka gpu`** (`status`, `serve`, `idle`, `train --submission`,
  `publish-dataset`) talks to the socket as root; `pinka status` shows the
  mode, the last transitions and whether the host trains; `pinka stop` and
  `pinka update-host` ask for `idle` after the platform's drain.
- **Doctor:** `gpu.arbiter`, `training.containers`, `training.adapters`,
  `training.datasets`, `training.availability`, `inference.isolation`.
- `site.yml` applies the role after vLLM (tags `gpu_arbiter`, `vllm`); the
  installer's progress screen lists it: the recorded successful run
  (`installer/tests/data/events-success.jsonl`) was recorded again with
  `record_events.py` (the role's events added, timestamps new), and the
  progress screen's four snapshots (80x24 and 120x40, dark and light)
  changed by that row and the totals (ok 31, changed 26) only.

### Verified in 3d.5

- vLLM v0.31.0 (`vllm/entrypoints/launchers/cli_args.py`, `launcher.py`,
  read on 2026-10-12): `--api-key` guards only `/v1`, `/v2` and
  `/inference`; `/invocations` stays open; `--uds` exists. Recorded in
  ADR-0016, decision 6.
- Tailscale v1.104.0 `net/tsaddr/tsaddr.go`: `CGNATRange` is
  `100.64.0.0/10` and `TailscaleULARange` `fd7a:115c:a1e0::/48` (the
  forwarder's `IPAddressAllow`).
- The unit files pass `systemd-analyze verify` here (with stub binaries);
  the inference guard's ruleset passes `nft -c` and its kernel test ran in a
  private network namespace.

### Interpretations awaiting owner confirmation (3d.5)

- The inference API's mechanism is the UID allowlist (option (b)), not
  `--uds` with a group directory (option (a)), for the reasons in ADR-0016,
  decision 6.
- One arbiter process per connection (`Accept=yes`) with a lock file,
  rather than a long-running daemon: nothing stays in memory between
  requests, and a crash loses no state.
- A run's unit is `PartOf=pinka-lab.target`: stopping the lab interrupts a
  run (resumable jobs resume at the next start only after a reboot; within
  the same boot the run stays `interrupted`).
- The job container gets a `/tmp` tmpfs (`rw,nosuid,nodev`), a process limit
  of 4096 (`gpu_arbiter_pids_limit`) and the environment
  `PINKA_JOB_RUN`, `PINKA_JOB_SEED`, `PINKA_JOB_HYPERPARAMETERS`,
  `PINKA_JOB_RESUME`; mounts `/model`, `/datasets/<kind>`, `/output`,
  `/checkpoints`; the adapter is `/output/adapter/`. §7.18 names the mounts
  but not their paths or the job's interface.
- The tree hash (`sha256sum` lines sorted by path) and the run ID format are
  infra's choice; the spec asks only for a hash and a run.
- Every file of a published dataset or adapter must belong to the
  controller (the job's account for an adapter); there is no size cap
  besides the disk.
- Adapters left `models.lock.yml`: §7.18 says only the arbiter registers
  adapters. No lab has pinned one.
- `training_caps` is checked when a run is submitted, not when the platform
  is deployed: a manifest whose job exceeds the caps deploys, and every run
  of that job is refused.
- A `lab` node has no arbiter; a `full` node without a GPU has one, for
  CPU-only jobs (`resources.gpus: 0`) and so that `pinka gpu status` works.
- Infra's inference clients depend on the node role: `pinka-inference` only
  on an inference node, Prometheus only on a full node, the collector on
  full and inference nodes.
- The node's private key stays `root:pinka-inference` 0640 on both node
  roles: the control endpoint (an inference node) serves TLS with it. On a
  lab node only the forwarder (root) uses it.
- `pinka platform purge` does not reload the inference guard; the next
  deployment or boot does (the purged accounts no longer resolve, so the set
  shrinks).

### Findings to report (3d.5)

- vLLM's `--api-key` would have left `/invocations` open: an API key alone
  never protected the inference API.
- The platform must use the arbiter's protocol: `serve` instead of the
  deploy helper's socket, `train` with a submission and `publish-dataset`
  for its datasets, and the job interface above (a platform spec change,
  listed in `../PROGRESS.md`).
- Not verified on a real host: vLLM started by the arbiter, a training
  container with the GPU (CDI or the NVIDIA runtime), the boot paths after a
  real reboot, the forwarder with a paired node, the inference guard with
  the platform's real accounts.

### Open `TODO(verify)` items after 3d.5

None new.

### Owner decisions on 3d.5 (with the "commit")

- Approved: the inference API's UID allowlist, the arbiter's process model,
  the job interface (mounts, environment, adapter path), the run ID format,
  the topology, and `training_caps` checked at submission.
- For 3d.6, with tests: size caps in `training_caps` (dataset size;
  combined size of `/output` and `/checkpoints` per run; a free disk space
  check before a run; a run over a cap stopped and failed; `/tmp` a
  size-limited tmpfs counted against the job's memory) as the first item;
  resume after `pinka start` as after a reboot, logged as a transition; the
  lab node's private key 0600 root; the inference guard reloaded by
  `pinka platform purge`; the hash format specified and tested (paths
  sorted byte by byte, C locale, the exact line format); validation and doctor
  warn when a deployed job's resources exceed the host's `training_caps`.
- The platform impact (the arbiter's protocol, the job interface, the
  inference API's clients) is approved and marked so in `../PROGRESS.md`.

## Token efficiency: the quiet gate (after 3d.5)

- `make lint`, `make test` and `make docs` run through
  `pinka_repo_checks gate` (standard library only) unless `VERBOSE=1`: it
  runs `make setup` once, then each step as its own make target with
  `GATE_SETUP_DONE=1` (which makes `setup` a no-op for that call), keeps
  going after a failure, and writes each step's output to
  `build/gate/<target>/<step>.log`. On success it prints one line with the
  counts of passed, failed and skipped items and of warnings; on failure the
  complete output of every failing step first, then the line marked
  `FAILED`. pytest runs with `--tb=long` (`PYTEST_ADDOPTS`).
- New step targets: `test-pytest` (the pytest part of `test`) and
  `docs-build` (the mkdocs build of `docs`); with `VERBOSE=1` the targets
  run exactly the steps and order they ran before.
- Counting: pytest's and unittest's results count tests; any other step
  counts as one check; a non-zero exit always counts a failure. Warnings are
  pytest's and ansible-lint's counts plus every line that is a warning by
  its form (documented in the module); a warning a tool both prints and
  counts may be counted twice, never fewer. The Material for MkDocs notice
  about MkDocs 2.0 counts as the docs build's one warning.

### Interpretations awaiting owner confirmation (quiet gate)

- CI keeps the full output (`make lint VERBOSE=1` and the same for `test`
  and `docs`): CI logs are read by people, and nothing there costs tokens.
- On success the line gives counts and the log directory, not the text of
  each skip or warning; the logs keep them.
- Each step runs in its own make process, so a failing step no longer stops
  the target: every failure of one run is reported.

## Contract v2, step 6: the reference platform (plan step 3d.6)

### Built in 3d.6: the owner's 3d.5 decisions

- Size caps: `training_caps` gains `dataset_max` (a published dataset),
  `run_storage_max` (`/output` and `/checkpoints` of one run together) and
  `tmp_max` (the job's `/tmp`, a tmpfs never larger than the job's memory,
  which the kernel charges to it). The arbiter checks the free disk space
  before it starts a run and stops a run over a cap, which ends `failed`.
- Resume: `pinka start` asks the arbiter to resume (`idle` with `resume`, a
  key only root may send); a run that systemd stopped with the lab or the
  host is `suspended` and resumes at the next start or boot, logged as a
  transition with the outcome `resumed` and the requester `boot`.
- The lab node's private key is 0600 root; `root:pinka-inference` 0640 only
  on an inference node, whose control endpoint serves TLS with it; doctor
  checks the owner and mode.
- `pinka platform purge` reloads the inference guard.
- The tree hash: one line `<sha256>  <path>\n` per file, sorted by the
  UTF-8 bytes of the path (as `LC_ALL=C sort`); a name that is not UTF-8 or
  holds a control character or a backslash is refused
  ([GPU arbiter reference](reference/gpu-arbiter.md#the-tree-hash)).
- `pinka-platform validate` and doctor (`training.caps`) warn when a job's
  resources exceed the host's `training_caps`.
- The SMT warning of P3a: the installer's resources screen and doctor's
  CPU set check warn when the threads of one core are split between
  `cpuset_services` and `cpuset_targets` (`thread_siblings_list`).
- Interpretation 11.13 as changed by the owner: the split guard
  (`inet pinka_split_guard`, its own table and unit on every firewall
  backend) admits only the paired peer's tailnet address to the
  split-inference ports once paired, and opens the pairing port only for the
  pairing window; doctor's `inference.split_guard`. Its kernel test proves
  that an unpaired tailnet address is refused after pairing.
- Setup tooling: the Galaxy fallback (`ansible_collections.fallback`
  in the lock; `pinka_lockcheck collections`) and the uv bootstrap
  (`install_uv` into `build/tools/`); `README.md`, "What `make setup`
  downloads".

### Built in 3d.6: the reference platform (BUILD_PROMPT §16 step 20)

- `tests/reference-platform/`: `refplat`, standard library only, runtime
  `python-uv` with an empty dependency set (only the build backend
  `uv_build` in the `build` group). The manifest is §12.1's example plus the
  CPU-only job `tunecpu`; both jobs run `jobs/tune.py`, whose text the
  manifest carries and the tests keep identical. The controller `ctl`
  (sockets `api` and `session`, the four hooks, `public.refplat_runs`,
  `public.refplat_datasets`, `restricted.refplat_cases`), the agent's own
  package `refplat-agent` with the isolation probe, the CLI `refplat`, and
  the restricted content package `tests/reference-platform-cases/`. The
  code takes the platform's name from `endpoints.toml` (the agent from the
  controller's hello), so a renamed copy runs unchanged.
- The isolation probe tries every action §7.18 lists: the restricted area
  and content, the database sockets, a socket not dedicated to it, TCP to
  the telemetry stores, the inference API and an address outside the host
  (the default gateway's DNS port), infra's secrets, the Git mirrors, the
  datasets area, the arbiter's and the run-network helper's sockets, the
  query proxy, and writes to the platform's state and its own environment.
- `tools/platform/tests/test_refplat.py`: validation, the lock's image,
  the identical job scripts, the build with the build's own uv commands
  (the agent's environment holds only its package), the toy training's
  determinism and resume, the dataset and the arbiter's split guard, the
  controller through a fake arbiter speaking the arbiter's protocol and
  submission check, the hooks' JSON lines, the probe's targets against the
  rendered files and every kind of action, and the PostgreSQL client,
  migration, restricted check and the controller's writes against a
  throwaway server with infra's database of the platform. The manifest
  fixture `refplat` is now the real release's manifest.
- `make test-distros` deploys `refplat` after `smoke` and adds the stages
  `platform`, `isolation`, `training`, `resume`, `redeploy`, `replace` and
  `restore` ([How to test the distributions](how-to/test-distributions.md#what-the-reference-platform-stages-check)).
  The per-profile probe (`pinka_test_distros/isolation.py`) runs as a
  transient unit with the hardening and groups infra renders for a service
  of each loopback-capable profile; the in-VM helper
  (`pinka_test_distros/guest.py`) enables the platform in the host_vars and
  the vault.
- `make check-mode-extended` runs the whole play with `refplat` enabled and
  placeholder secrets.

### Verified in 3d.6

- The release builds with the pinned uv 0.12.23 and `uv-build` 0.12.24 from
  PyPI (locked with hashes in `uv.lock`) and passes `validate --built`.
- The PostgreSQL client logs in with SCRAM-SHA-256 against PostgreSQL 16
  here; the restricted check reports nothing after the migration.
- The job image: `public.ecr.aws/docker/library/python:3.12-slim-trixie`,
  index digest read with the registry API on 2026-10-14 (linux/amd64 and
  linux/arm64), recorded in `versions.lock.yml` (`containers.images.refplat_job`).

### Interpretations awaiting owner confirmation (3d.6)

- The per-profile probe runs as the platform's own account where the
  platform has a service of the profile (the controller), and otherwise as a
  temporary account from the platform UID range with the rendered groups:
  the inference guard, the GPU arbiter and the run-network helper admit the
  rendered accounts, not a profile. So in the VMs the inference API's
  positive case is proven for the controller only; for `inference-broker`
  (no such service in `refplat`) the probe proves that an account outside
  the rendering is refused.
- While nothing serves on the vLLM port (no GPU in the VMs), a root
  listener stands in for vLLM during the `isolation` stage, so the
  inference guard is what decides.
- The VMs run `tunecpu`; serving an adapter with vLLM is left to GPU hosts.
- `refplat`'s job image comes from the ECR Public mirror of Docker's
  official images (Docker Hub answered 429).
- The renamed copy (`refplatb`) is deployed from a repository of its own in
  the VM, so the tested clone never changes; the replacement uses
  `--replace-platform refplat`.
- The `restore` stage removes one controller record, restores the latest
  snapshot (`restore_snapshot: latest`) and checks that the record is back.
- Answers cannot enable a contract v2 platform before 3d.7, so the harness
  writes `platform`, `content`, `platform_settings` and the vault entries
  itself.
- `refplat`'s `drain` waits for submitted and running runs, not for a
  suspended one (it waits for the lab, not for the platform).
- The `idle` request's `resume` key is root's only; the split guard is a
  table and unit of its own, and the pairing window is the pairing's
  timeout plus 60 seconds.

### Findings to report (3d.6)

- The arbiter read the platform's name from `active.json` under `platform`
  instead of `name`, so every lineage record named no platform. Fixed, with
  a test.
- Infra's database bootstrap gives a platform's controller no privilege on
  the schemas; its migrations grant it (refplat's `migrate` does). The
  platform spec should say so.
- The arbiter's `status` shows only its last ten transitions; a controller
  that polls rarely must read the current run and treat a run with no trace
  as ended (refplat does).
- `docs/security.md` still names the first platform's former accounts
  (`potka-agent`, `pinka-gt`) in its threat table; 3d.8 rewrites it.
- Not run here: the new `make test-distros` stages (no Incus in this
  environment), `make check-mode-extended` with the platform (no Docker),
  and the PostgreSQL 18 run of the SQL tests (CI only).

### Open `TODO(verify)` items after 3d.6

- `ansible_collections.fallback` of `versions.lock.yml`: the release
  tarball URL and sha256 of each collection (GitHub answers 403 here for
  that organization).

### Owner decisions on 3d.6 (with the "commit")

- Approved: the job image from the ECR Public mirror, pinned by digest,
  provided the digest is the official image's. Checked on 2026-10-15 with
  Docker Hub's registry API: `library/python` serves
  `sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`,
  the pinned digest, and its tag `3.12-slim-trixie` points to it.
- Approved: the vLLM stand-in, the renamed copy from its own repository,
  the harness writing host_vars and the vault until 3d.7, the root-only
  `resume` key, and the platform spec change on the `migrate` hook's grants.
- For 3d.7: pinned collection artifacts with sha256 for bootstrap,
  transitive dependencies included (`--offline --no-deps`, then
  `verify --offline`); the maintainer script `make lock-collections`; each
  golden image's package list in its fingerprint record; a minimal
  `inference-broker` service in `refplat`. The fallback waits for the
  owner's decision on its artifact; bundling the collections with infra is
  a proposal for the owner. No snapshot repositories for golden images.

## Contract v2, step 7: the installer (plan step 3d.7)

### Built in 3d.7: the owner's 3d.6 decisions

- Bootstrap's Ansible collections: `ansible_collections.artifacts` in
  `versions.lock.yml` pins Galaxy's artifact (URL and sha256) of every
  collection, the transitive dependency
  `community.library_inventory_filtering_v1` 1.1.5 included (it is a
  dependency of `community.docker`). Bootstrap downloads each with the
  verified downloader, checks its sha256, installs them with
  `ansible-galaxy collection install --offline --no-deps`, then runs
  `ansible-galaxy collection verify --offline`; installed collections that
  already verify are kept. Bootstrap's Ansible commands run with
  `PYTHONDONTWRITEBYTECODE=1`, because `verify` counts a `__pycache__`
  directory as a file that `FILES.json` does not list. The pins are
  generated into `bootstrap/lib/pins.sh`; an unverified pin ends bootstrap
  with exit 50 and names it.
- `make lock-collections` (`pinka_lockcheck.lockcollections`): maintainer
  tooling that asks Galaxy for each locked version as `ansible-galaxy`
  does, downloads the artifact, computes its sha256 (which must equal
  Galaxy's), checks `MANIFEST.json` and `FILES.json` as `verify` does and
  that every declared dependency is locked at a satisfying version, and
  prints the pins in the lock's format with each artifact's size and
  license files. It installs and changes nothing; documented in
  [How to pin the Ansible collections](how-to/pin-ansible-collections.md).
- The fallback: nothing built (owner decision pending). The GitHub
  releases of these collections carry no Galaxy artifact; GitHub's source
  archives are different files without `MANIFEST.json` and `FILES.json`, so
  `verify` cannot check them and a sha256 of them would only check the
  archive against itself.
- Golden images: each image's build records its full package list (name,
  version, architecture, from `dpkg-query` with only installed packages
  kept) in its export record `build.json`; `pinka` adds it to the
  fingerprint as `software.golden_images.<alias>` = `{fingerprint,
  packages}`, only when the record's fingerprint is the image's.
- `refplat`'s `broker` (profile `inference-broker`, socket `infer` for
  researchers and root): it asks the inference API of `endpoints.toml` for
  its model list; `refplat infer` prints it. In the VMs the stand-in on
  the vLLM port is now an enabled unit that answers `/health` and
  `/v1/models` and outlives the `resume` reboot, so the `platform` stage
  proves that the inference API answers the broker.

### Built in 3d.7: the installer (BUILD_PROMPT §16 step 21)

- `pinka-platform deploy inspect-release`: fetches and validates the
  release alone and reports what it needs (content packages, the secrets
  its services name, each service's config schema, the vault keys); the
  summary also names the state that is backed up, the backed-up database
  and the job caps.
- Answers (schema v1, extended): `platform` and `content` (sources as in
  host_vars §8.1), `source_trust` (`known_hosts`, `require_signed_tags`,
  `allowed_signers`), `platform_settings`, `gpu_arbiter` (`after_boot`,
  `restore_after_training`), `training_caps` and `secrets.platform`
  (`<service>.<name>`: `generate` or `provided` through
  `PINKA_SECRET_PLATFORM_<SERVICE>_<NAME>`). The vault gets
  `vault_platform_secrets` and the platform's database roles from the
  inspection's `vault_keys`.
- `pinka_installer.release`: the inspection with the platform tool's own
  code and the settings the platform role would write (the host's deploy
  key, GitHub's pinned host keys and the answers' own lines, the allowed
  signers, all in a root-only temporary directory); `full` runs `deploy
  prepare --inspect`. The release's code never runs.
- Screens: Platform (12: no platform, or the release's source, host keys,
  signed tags, **Deploy key**, **Test access**, **Inspect release**, then
  the content sources, secrets and settings the release needs), Platform
  review (13: the deployment's checks and what the platform will be, with
  an approval that any later change of the platform answers withdraws),
  the GPU screen's arbiter and caps, and the platform and GPU lines of the
  Review screen. The answers replay inspects the release before it writes
  anything.
- `--replace-platform` reaches the inspection: `pinka-installer apply`
  takes it (bootstrap passes its own), and the TUI takes it from the
  options bootstrap passes on; without it, a release of another platform
  than the active one is refused, as the deployment refuses it.
- V5: the temporary exclusion of the installer and `tests/answers/` from
  the neutrality lint is gone; both are scanned, and a test keeps it so.
- `make test-distros` enables, replaces and restores the platform through
  the answers (`mode: modify`, then `mode: restore` with
  `restore.snapshot: latest`), with `--replace-platform` for the
  replacement; the guest helper's host_vars and vault commands, approved
  until 3d.7, are removed. Each stage's answers are kept in the results.
- Docs: the TUI and answers how-tos (Platform, Platform review, GPU
  arbiter, the deploy key without the TUI, platform secrets), the answers
  and CLI references, and the Platform review screenshot.

### Fixed in 3d.7

- CI's `infra` workflow was red on `a686e28`: (1) every check-mode lane
  stopped at `users_groups`, whose argument spec still required
  `service_accounts`, removed from group_vars in 3d.3; the option is gone,
  and a test checks that every required role option without a default has
  a source (vars files, the role's defaults or vars, or a listed runtime
  source); (2) the uv tests failed because the Makefile installed uv to
  `infra//home/runner/...` in CI; (3) the drift check failed because the
  generated `pinka-platform` reference showed this checkout's path as the
  `--contract` default; subcommand defaults are now shown relative to the
  checkout, and a test checks that no generated file names the checkout's
  path.
- Bootstrap's options with a value also take `--option=value`; the TUI
  passed `--skip-tags=images` and `--tags=...` that way, which bootstrap
  refused as unknown options (since step 13). A test runs the TUI's own
  options through bootstrap's parser.

### Interpretations awaiting owner confirmation (3d.7)

- The installer imports the platform tool (`infra/tools/platform` on its
  `PYTHONPATH`) instead of running its CLI, so the inspection and the
  deployment share one code path.
- The inspection uses the platform role's root-only mirrors and staging
  directory and deletes what it staged.
- The deploy key is created by the installer (**Deploy key**) or, without
  the TUI, by the operator with `ssh-keygen` at the platform role's path
  (`/var/lib/pinka/git/deploy_key`), which the role keeps; no answer names
  an existing key file (the roles do not read `vault_git_deploy_keys`).
- The GPU arbiter and the caps are answered on the GPU screen; without a
  GPU they keep their defaults (serve after boot, restore after training,
  86400 s, 8 CPUs, 24G memory, 8G dataset, 16G run storage, 1G tmpfs).
- The TUI does not offer replacing an active platform by itself; the
  operator starts bootstrap with `--replace-platform`, which the TUI then
  uses.
- Platform secrets without a choice in the answers are generated.

### Findings to report (3d.7)

- The check-mode lanes have not run past `users_groups` since 3d.3; the
  roles after it are checked again only by the next CI run.
- Not run here: `make test-distros` (no Incus), the check-mode lanes (no
  Docker daemon), the PostgreSQL 18 SQL tests (CI only), and a run of the
  tests as an unprivileged user (the venv's interpreter lives below root's
  home); CI runs them unprivileged.

### Open `TODO(verify)` items after 3d.7

- `ansible_collections.artifacts`: the Galaxy artifact URL and sha256 of
  each of the five collections (10 lock lines, 5 generated lines in
  `bootstrap/lib/pins.sh`), from the owner's `make lock-collections` run.

### Fixed after the 3d.7 commit (CI of `0d72e54`)

- Every extended check-mode job stopped at the observability role:
  ansible-core 2.19 and later refuse a conditional whose result is not a
  boolean, and `when: observability_platform_dashboards` evaluates a
  mapping (`{}` without a platform), so a real provisioning run stopped
  there as well, with or without a platform. The platform role had ten
  more (`active_platform.database`, `.cli`, `.run_network`,
  `platform_plan.replace`, `platform_retire.cli_link`, `.cli_wrapper`),
  which check mode skips. They use `is truthy` now;
  `ansible/tests/test_conditionals.py` requires a known boolean source for
  every bare condition (a role option of type bool, a stat or async result,
  or a listed source with its reason) and runs ansible-core's own check on
  the dashboards condition. The scan finds all eleven in `0d72e54`.
- `versions.lock.yml`'s header comment named the marker itself, which
  `lock-check` counts on every line, so `lock-check` could not have turned
  green after every pin was verified (since step 4). Reworded; a test keeps
  markers off comment lines.

### Fixed after the 3d.7 commit: symbolic links in collection artifacts

- The owner's `make lock-collections` stopped at
  `community.general`'s `tests/utils/shippable/aix.sh`, a symbolic link:
  the artifact check accepted only regular files and directories. The check
  is now `tools/lockcheck/pinka_lockcheck/artifact.py`, standard library
  only, and the one rule for every path: `make lock-collections` imports
  it, `make setup` runs it on every artifact (from Galaxy it now downloads
  with `ansible-galaxy collection download --no-deps`, checks, then installs
  offline without dependencies; the fallback's tarballs get the same check),
  and bootstrap runs it as a script with the Ansible environment's Python
  (`python -I artifact.py NAME VERSION FILE`) after the sha256 and before
  anything is installed.
- A symbolic link passes only when its target is relative and, resolved
  from the link's own directory, stays inside the collection (the condition
  under which `ansible-galaxy` extracts a link) and names another member or
  a directory of the artifact; a link to a file counts with its target's
  content, as `FILES.json` lists it. Refused: absolute and escaping links,
  dangling links and loops, hard links, devices, FIFOs, members outside the
  collection.
- Tests: the rule itself (`tools/lockcheck/tests/test_artifact.py`, the
  script as bootstrap runs it included), `make lock-collections` with a
  `community.general`-shaped link, `make setup` with a collection really
  built by `ansible-galaxy collection build` with an internal link (checked,
  installed and verified by `ansible-galaxy`), and bootstrap's install with
  an internal link and with an absolute and an escaping one. The artifact
  builder of these tests is shared (`tools/lockcheck/tests/collection_testlib.py`).

### Fixed after the symbolic-link fix: license files of an artifact

- The owner's second `make lock-collections` run printed only `COPYING` as
  `ansible.posix`'s license file, while the artifact also carries
  `PSF-license.txt`: the rule took only names beginning with `COPYING` or
  `LICENSE`. Now (`artifact.license_texts`) a license file is any file in a
  `LICENSES` directory, or a text file (no extension, `.txt`, `.md`,
  `.rst`) whose name contains `COPYING`, `LICENSE`, `LICENCE` or `NOTICE` in
  any case; REUSE's `.license` files (annotations of the file they are named
  after) are not texts. On the file lists of the five pinned artifacts it
  finds exactly their license texts: `ansible.posix` `COPYING` and
  `PSF-license.txt`; `community.crypto` `COPYING` and `LICENSES/`
  Apache-2.0, BSD-3-Clause, GPL-3.0-or-later; `community.docker` `COPYING`
  and `LICENSES/` Apache-2.0, GPL-3.0-or-later; `community.general`
  `COPYING` and `LICENSES/` BSD-2-Clause, GPL-3.0-or-later, MIT;
  `community.library_inventory_filtering_v1` `COPYING` and `LICENSES/`
  GPL-3.0-or-later.

### The collection pins and the lock-collections CI job

- The owner's second `make lock-collections` run (2026-10-15T20:33:03Z,
  `https://galaxy.ansible.com`) is recorded in
  `ansible_collections.artifacts`: URL, sha256 and size of the five
  artifacts (4268595 bytes together), with `source` naming the run; the ten
  `TODO(verify)` values there are gone, `make generate` copied the pins
  into `bootstrap/lib/pins.sh`. The lock's schema now requires each
  artifact's `size`. The four `ansible_collections.fallback` entries keep
  their markers until 3d.8 removes the fallback (owner decision with the
  "commit" for 3d.7).
- `make lock-collections-check` (`lockcollections --check`) pins every
  locked collection again from Galaxy with the same checks, prints the
  output, and fails on every difference from the lock's artifacts (URL,
  sha256, size; an artifact missing or of a collection not locked); a
  refused artifact, an open dependency or an unreachable Galaxy fails it
  too. `--with` does not apply there.
- The CI job `lock-collections` runs it (owner, with the "commit" for the
  symbolic-link fix): `infra/.github/workflows/lock-collections.yml` and the
  monorepo wrapper `.github/workflows/infra-lock-collections.yml`, on every
  push and pull request that changes `versions.lock.yml` or
  `tools/lockcheck/` (native path filters, hence a workflow of its own),
  weekly (Mondays 04:41 UTC; scheduled runs use the default branch, so the
  weekly run starts once the workflow is on `main`) and by hand. A failure,
  of the weekly run too, blocks the next step. Tests: the comparison, a
  refused artifact, the real lock's URLs against the locked versions, and
  both workflow files.
