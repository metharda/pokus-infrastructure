# pokus-infra

Infrastructure-as-code for the Pokus research lab: it turns a Linux machine
into a lab host for benchmarking AI diagnostic agents on reproducible, injected
Linux faults. It provides host checks (preflight), accounts, remote access over
Tailscale, the Incus target sandbox, local model serving with vLLM,
observability, backups, and the `pokus-lab` operator tool. The research
services come from pokus-platform and run on top of it.

> **Status:** under construction. This README grows with each build step; the
> quickstart, the supported environments table and the exit codes table are
> added with the components they describe.

## Repository layout

| Path | Content |
| --- | --- |
| `docs/` | Documentation (MkDocs). `docs/BUILD_PROMPT.md` is the binding build specification, `docs/DOCS_SPEC.md` the documentation specification, `docs/contracts.md` the interface to pokus-platform. |
| `bootstrap/` | `bootstrap.sh`, the single entry point that provisions a lab host, its shell libraries, the hash-locked Ansible requirements and tests. |
| `preflight/` | Host detection and the support decision (`pokus-preflight`), the support matrix, the host profile schema, fixtures and tests. |
| `installer/` | The installer TUI and the answers replay that `bootstrap.sh` runs (`pokus_installer`), the answers schema, its hash-locked requirements and tests. |
| `ansible/` | Inventory, playbooks, roles and the progress callback that bootstrap applies. |
| `images/` | Golden image blueprints (recipes of the benchmark targets' base images). |
| `compose/`, `config/` | Compose files and configuration of the lab services (Postgres, OPA, the telemetry stack, vLLM), rendered by their roles. |
| `incus/` | Incus preseed, profiles and projects. |
| `vllm/` | vLLM serving profiles per GPU class. |
| `tests/` | The CI answers file and the distribution test targets and results. |
| `contrib/windows/` | An optional Windows-side helper for WSL hosts. |
| `tools/pokus-lab/` | `pokus-lab`, the operator CLI of a provisioned host (doctor, fingerprint, start and stop, backups, pairing). |
| `tools/test-distros/` | `make test-distros`: the distribution tests in Incus VMs and the CI fast lane. |
| `tools/gen/` | Generators: compiled matrix JSON, bootstrap pins, Ansible `versions.yml` and the generated reference pages (`make generate`, drift and coverage checks). |
| `tools/lockcheck/` | `make lock-check` and `make verify-todos`: schema, unverified markers, and network re-verification of every pinned digest, checksum and key. |
| `tools/repo-checks/` | Repository checks: privacy scan, docs lint, link check, Mermaid extraction. |
| `versions.lock.yml` | Every pinned external artifact with its verification source (`docs/reference/versions.md`). |
| `.github/workflows/ci.yml` | CI: lint, unit tests, strict docs build, weekly link check. CI never provisions anything. |

## Development

Requirements: [uv](https://docs.astral.sh/uv/) at the version pinned in
`pyproject.toml`, Node.js at the version in `.node-version`, and a Chromium or
Chrome binary for Mermaid validation (`PUPPETEER_EXECUTABLE_PATH`).

```bash
make setup      # install pinned tools into .venv and node_modules (user space)
make lint       # linters, docs lint, Mermaid validation, privacy scan
make test       # unit tests
make docs       # drift check, docs coverage check and strict documentation build
make generate   # rewrite generated files after changing their sources
```

To check whether a machine can host the lab without changing anything on it,
run `./preflight/pokus-preflight --no-write` (see
`docs/how-to/check-a-host.md`).

None of these targets changes the host. Provisioning and role tests run only
when invoked explicitly and never in CI.

## Privacy

Pokus is meant to be deployed and customized by anyone, so this repository
contains no personal or installation-specific data. Real deployment data
(host variables of real machines, researchers' SSH keys, installer answers,
vaults) belongs in your lab's own private fork or configuration repository and
is excluded by `.gitignore`. `make privacy` enforces this; CI can add a private
denylist through the `POKUS_PRIVACY_DENYLIST` secret.

## License

Code: Apache License 2.0 (`LICENSE`). Documentation: Creative Commons
Attribution 4.0 International (`LICENSE-docs`).
