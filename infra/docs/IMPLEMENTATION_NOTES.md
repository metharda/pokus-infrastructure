# Implementation notes — pokus-infra

Working notes required by BUILD_PROMPT §0.1: my understanding of the scope, the
files I intend to create, every open question with its answer, and what is
still unverified. This file is excluded from the documentation site. The full
record of answers, approved deviations and step status is `../PROGRESS.md` in
the monorepo; IDs such as B2 or E9 refer to it.

## Scope as understood

pokus-infra provisions a single Linux host (bare metal, VM or WSL2; with or
without an NVIDIA GPU) into a Pokus lab host with one command, and keeps it
reproducible and auditable:

- **Preflight gate** (§2): stdlib-only Python ≥ 3.9 detector of OS,
  environment class, hardware, kernel features, GPU, conflicts; data-driven
  decision from `support-matrix.yml`; exit codes 0/10/20/30/40; host profile
  JSON validated against a JSON Schema; full fixture-based test suite (§11).
- **Bootstrap** (§5): `bootstrap.sh` with pre-preflight, pinned uv and CPython
  in `/opt/pokus`, hash-locked Ansible environment, signal files mapped to exit
  codes 75/76/77, resumable, a TUI front-end with exactly one provisioning path.
- **Ansible roles** (§7, §19): platform facts and the component source matrix
  for four families (debian, redhat, suse, archlinux), providers (wsl2,
  baremetal, vm), base, MAC, users and groups, SSH, Tailscale, firewall (three
  backends), Docker, NVIDIA Container Toolkit, Incus, layout, Postgres,
  observability, OPA, vLLM, models, golden images, catalog, platform, backup,
  lab session, doctor, inference control.
- **Operator CLI** `pokus-lab` (§9) with doctor (incl. `--smoke`), fingerprint,
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
| B2 | Platform and catalog are deployed from a root-only bare mirror (`/var/lib/pokus/git/`) with `git archive` of one subtree; `DEPLOYED_FROM.json`; doctor check and test that no deployed path contains `.git` or catalog content outside `/srv/pokus/catalog`. |
| B3 | Bootstrap records the repository commit and the `infra/` tree hash; the dirty-tree check covers `infra/` only. |
| B4 | Release tags `infra-vX.Y.Z`; first version 0.1.0. |
| B5 | Canonical CI in `.github/workflows/ci.yml`; the monorepo root has a thin wrapper. CI runs lint, unit tests, type checks, docs build/coverage/drift, lock-check and syntax/check-mode jobs only. |
| C1 | `docs/contracts.md` is the resolved contract, byte-identical in platform. |
| E3 | `pokus_source_repo` is a required deployer-supplied variable; examples use `git@github.com:example-org/pokus.git`. |

## Open questions and answers

Every question raised before implementation, with the answer. Full wording in
`../PROGRESS.md`.

| ID | Question (spec section) | Answer |
| --- | --- | --- |
| A2 | Who commits in the cloud environment | The assistant runs the approved `git add`/`commit`/`push` only on the owner's "commit"; never tags |
| A3 | Network access for verification | Owner allows the domains listed below; until then items stay `TODO(verify)` |
| A4 | Dev tools | User space only, pinned; PostgreSQL for tests via pinned micromamba + conda-forge |
| B1 | Spec copies | `docs/BUILD_PROMPT.md`, `docs/DOCS_SPEC.md`, excluded from MkDocs |
| C2–C10 | Contract conflicts and gaps | Resolved in `docs/contracts.md` |
| D1–D6 | External verification hooks | Implemented as defined in `docs/contracts.md`; `qa:` markers linted, never executed here |
| E1 (§6.1) | Researcher names | Placeholders `researcher1` (admin), `researcher2`, `researcher3`; no key files are ever committed |
| E2 (§7.4) | Tailscale tags | `tag:pokus-lab` (production), `tag:pokus-qa` (staging) |
| E3 (§7.14) | Git remote | Required variable `pokus_source_repo`; SSH deploy key generated on the host; Git host keys pinned by fingerprint; anonymous HTTPS supported; no personal tokens |
| E4 (§7.15) | Backup destination | "Configure later" by default; restic over SFTP primary, rest-server secondary, local path test-only; placeholder hosts only |
| E5 (§13.1) | Disk safety | As specified; example host vars use a loop pool |
| E6 (§8.1) | Host vars | Only `example-machine.yml` and `tests/answers/ci.yml` |
| E7 (§7.12) | Model candidates | Permissive, non-gated first; others listed separately with approval; presented in step 7 |
| E8 (§19.2) | Distro matrix | Non-EOL releases only; Ubuntu 26.04 as `tested` if released and supported by Docker, Incus, NVIDIA toolkit and Tailscale; supported tier stays Ubuntu 24.04 + Debian 13; newest Incus LTS packaged for supported distros; presented in step 2 |
| E9 (§11) | RHEL 8 | Explicit `unsupported` row for RHEL-compatible major 8 before the `ID_LIKE` rows; fixture expects unsupported |
| E10 (§4) | YAML in preflight | Generated, committed, drift-checked JSON |
| E11 (§4, §18.2) | Lockfiles | `pyproject.toml` + `uv.lock` for dev tools; installer and bootstrap keep their own lockfiles |
| E12 | Certificates and keys | `community.crypto`, pinned |
| E13 (§7.8) | Researcher database access | Separate reader socket directory (group `pokus`), reader credential 0640 group `pokus`; reader never sees `gt` (tested) |
| E14 (§7.9) | Metrics path | Collector Prometheus exporter, scraped |
| E15 (§7.6) | Compose units | `oneshot` + `RemainAfterExit` + `up -d --wait` / `down` |
| E16 (§7.14) | Signed tags | `require_signed_tags: false` by default; SSH-signed tags checked against a committed allowed-signers file when true; both paths tested |
| H1 | Licenses | Apache-2.0 code, CC-BY-4.0 documentation |
| H4 | Docs tooling | MkDocs Material pinned; Node tools pinned with `package-lock.json`; Mermaid validation with mermaid-cli; weekly link check |
| H5 | Page ownership | Table below, for approval |
| H9 | Publishing | Release tarball and `pokus-lab docs serve` on `127.0.0.1` |

### Questions raised by step 1 (awaiting answers)

1. **Reference agents for scenario validation.** DOCS_SPEC §A.3 requires
   "oracle/null agent checks" (tutorial 4, scenario-author how-to) and a "QA and
   validity controls: reference agents" explanation, but neither build prompt
   defines an oracle or null agent. Options: (a) document them as part of the
   external verification tooling (out of scope here) and end tutorial 4 at
   `pokus catalog lint` plus a fake-pipeline run; (b) add two minimal scripted
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
| Connect over Tailscale and SSH; Grafana; ad-hoc LLM endpoint | infra | `docs/how-to/connect.md` (links to the platform's `pokus llm key`) |
| Write a campaign spec | platform | `docs/how-to/write-campaign-spec.md` |
| Monitor a campaign; invalid runs | platform | `docs/how-to/monitor-campaign.md` |
| Approve or deny actions | platform | `docs/how-to/approve-actions.md` |
| Inspect a run | platform | `docs/how-to/inspect-run.md` |
| Exploratory experiments (`pokus dev`) | platform | `docs/how-to/exploratory-runs.md` |
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
| CLI reference: `pokus-lab`, `pokus-preflight`, `bootstrap.sh` | infra | `docs/reference/cli/*.md` (generated) |
| CLI reference: `pokus` | platform | generated |
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
2. **Preflight:** `preflight/pokus-preflight`, `preflight/pokus_preflight/`
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
10. **Operator CLI:** `tools/pokus-lab/` (doctor incl. `--smoke`,
    fingerprint, `update-host`, `docs serve`), role `doctor`.
11. **Platform and catalog roles** with `*_enabled: false` defaults; the
    vLLM deploy helper and socket unit; role `inference_control`.
12. **Distro tests:** `make test-distros` harness and `tests/answers/ci.yml`.
13. **Installer TUI:** `installer/` (pyproject, lock, schema, app, screens,
    widgets, runner, theme, tests incl. snapshots), callback plugin
    `ansible/callback_plugins/pokus_events.py`.
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

Built: the stdlib-only `pokus_preflight` package (fs/runner abstractions, eight
detectors, matrix evaluator, report, runtime schema validator, summary, CLI),
the POSIX wrapper `preflight/pokus-preflight`, `support-matrix.yml` and
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
   (`pokus-lab preflight`, the `preflight_gate` role) from overwriting the
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
(`make bootstrap-lock`), the preflight helper `pokus_preflight.profile_tool`
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
   from `/etc/pokus/host-profile.json`, bootstrap stops with 77 until
   `--refresh-profile` is given. Exception, so that re-runs after 75/76/77
   "resume cleanly" (§5.2): when the previous run ended with a signal from the
   play, the change is accepted with a printed notice.
3. **`--force-unsupported` does not skip required actions:** a forced
   unsupported host that also has `needs_action` items stops with 30.
4. **State file at every exit.** `/etc/pokus/bootstrap-state.json` is written
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
   `/etc/pokus/secrets/vault-pass` if present (the TUI's optional root-only
   storage, §18.4 screen 14), then `--ask-vault-pass` on a terminal; otherwise
   77.
9. **Concurrency:** a lock in `/run/lock/pokus-bootstrap.lock` stops a second
   run with 77.
10. **Locale:** `ansible-playbook` runs with `LC_ALL=C.UTF-8` because
    ansible-core refuses non-UTF-8 locales; that the locale exists on every
    supported family is to be confirmed on real hosts (Phase 5).
11. **Until step 10**, `pokus-lab` does not exist, so a real (non-check) run
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
