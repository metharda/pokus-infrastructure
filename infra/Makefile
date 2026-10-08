# Managed by pokus-infra (Makefile).
#
# Developer and CI entry points. Nothing here provisions a host: targets that
# would (role tests in throwaway Incus instances, distro VM tests) are added in
# later steps and run only when invoked explicitly.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-builtin-rules

UV ?= uv
NPM ?= npm
VENV := .venv
BIN := $(VENV)/bin
NODE_BIN := node_modules/.bin
REPO_CHECKS := PYTHONPATH=tools/repo-checks $(BIN)/python -m pokus_repo_checks
GEN := PYTHONPATH=tools/gen:preflight:tools/pokus-lab:tools/test-distros:installer $(BIN)/python -m pokus_infra_gen
LOCKCHECK := PYTHONPATH=tools/lockcheck $(BIN)/python -m pokus_lockcheck
# YAML sources validated against their JSON Schemas (BUILD_PROMPT §11).
SCHEMA_PAIRS := versions.lock.yml=tools/lockcheck/schema/versions-lock.schema.json \
  preflight/support-matrix.yml=preflight/schema/support-matrix.schema.json \
  models.lock.yml=tools/lockcheck/schema/models-lock.schema.json \
  models.catalog.yml=tools/lockcheck/schema/models-catalog.schema.json \
  tests/answers/ci.yml=installer/schema/answers.schema.json \
  $(foreach f,$(sort $(wildcard images/*/image.yaml)),$(f)=tools/lockcheck/schema/image-blueprint.schema.json)
# Oldest Python preflight must run on (BUILD_PROMPT §2.1).
PREFLIGHT_MIN_PYTHON := 3.9
BUILD := build

# Pages under docs/ that are kept verbatim or are working notes, not site pages.
DOCS_EXCLUDES := BUILD_PROMPT.md DOCS_SPEC.md IMPLEMENTATION_NOTES.md
DOCS_LINT_ARGS := --docs-dir docs --component infra $(addprefix --exclude ,$(DOCS_EXCLUDES))

PY_SOURCES := tools/repo-checks tools/gen tools/lockcheck preflight bootstrap/tests ansible/filter_plugins \
  ansible/roles/incus/library ansible/roles/models/files ansible/roles/vllm/files ansible/roles/images/files \
  ansible/roles/lab_session/files ansible/roles/backup/files ansible/roles/deploy_source/files \
  ansible/roles/platform/files ansible/roles/inference_control/files ansible/tests tools/pokus-lab \
  tools/test-distros installer ansible/callback_plugins
PLAYBOOKS := ansible/site.yml ansible/check-mode.yml ansible/restore.yml ansible/rotate.yml $(sort $(wildcard images/*/provision.yml)) images/_base/agent.yml
# Pinned Ansible collections for linting (ansible/requirements.yml).
DEV_COLLECTIONS := .ansible-collections
# Ansible runs in user space for linting: its state stays below build/.
ANSIBLE_ENV := PATH="$(CURDIR)/$(BIN):$$PATH" ANSIBLE_HOME=$(CURDIR)/$(BUILD)/ansible-home \
  ANSIBLE_CONFIG=ansible/ansible.cfg ANSIBLE_COLLECTIONS_PATH=$(CURDIR)/$(DEV_COLLECTIONS)
# CPython version of /opt/pokus/ansible-venv (generated from versions.lock.yml).
BOOTSTRAP_PYTHON = $(shell sed -n "s/^readonly POKUS_PYTHON_VERSION=//p" bootstrap/lib/pins.sh)
# A browser for mermaid-cli; CI sets it to the runner's Chrome.
PUPPETEER_EXECUTABLE_PATH ?= $(firstword $(wildcard /opt/pw-browsers/chromium-*/chrome-linux/chrome) /usr/bin/google-chrome)
export PUPPETEER_EXECUTABLE_PATH

# OPA CLI pinned in versions.lock.yml (binaries.opa) for the policy tests;
# installed only after its sha256 matched.
OPA := $(BUILD)/tools/opa
export POKUS_OPA := $(CURDIR)/$(OPA)

# Every file git would track (tracked plus untracked-but-not-ignored).
GIT_FILES = git ls-files -z --cached --others --exclude-standard

.PHONY: help
help: ## List targets
	@grep -E '^[a-z][a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

.PHONY: setup
setup: $(VENV)/.synced node_modules/.installed $(DEV_COLLECTIONS)/.installed $(OPA) ## Install pinned development tools (user space only)

$(VENV)/.synced: pyproject.toml uv.lock
	$(UV) sync --frozen
	@touch $@

$(DEV_COLLECTIONS)/.installed: ansible/requirements.yml $(VENV)/.synced
	$(ANSIBLE_ENV) $(BIN)/ansible-galaxy collection install -r ansible/requirements.yml -p $(DEV_COLLECTIONS)
	@touch $@

$(OPA): versions.lock.yml $(VENV)/.synced
	$(LOCKCHECK) fetch opa --dest $@
	@touch $@

node_modules/.installed: package.json package-lock.json
	PUPPETEER_SKIP_DOWNLOAD=1 $(NPM) ci --no-audit --no-fund
	@touch $@

.PHONY: lint
lint: lint-python lint-shell lint-yaml lint-schemas lint-ansible lint-portability lint-markdown lint-spelling lint-docs lint-mermaid privacy ## Run every linter and the privacy scan

.PHONY: lint-python
lint-python: setup ## ruff, ruff format, mypy --strict
	$(BIN)/ruff check $(PY_SOURCES)
	$(BIN)/ruff format --check $(PY_SOURCES)
	$(BIN)/mypy tools/repo-checks tools/gen tools/lockcheck bootstrap/tests ansible/filter_plugins \
	  ansible/roles/incus/library ansible/roles/models/files ansible/roles/vllm/files \
	  ansible/roles/images/files ansible/roles/lab_session/files ansible/roles/backup/files \
	  ansible/roles/deploy_source/files ansible/roles/platform/files ansible/roles/inference_control/files \
	  ansible/tests tools/test-distros/pokus_test_distros tools/test-distros/tests \
	  installer/pokus_installer installer/tests ansible/callback_plugins
	@# mypy's oldest target is 3.10; 3.9 compatibility is proven by test-py39.
	$(BIN)/mypy --python-version 3.10 preflight/pokus_preflight preflight/tests
	$(BIN)/mypy --python-version 3.10 tools/pokus-lab/pokus_lab tools/pokus-lab/tests

.PHONY: lint-shell
lint-shell: setup ## shellcheck and shfmt on every shell script
	@files="$$($(GIT_FILES) | xargs -0 -r $(BIN)/shfmt -f)"; \
	if [ -z "$$files" ]; then echo "lint-shell: no shell scripts"; exit 0; fi; \
	echo "$$files" | xargs $(BIN)/shellcheck --external-sources; \
	echo "$$files" | xargs $(BIN)/shfmt -d -i 2 -ci -bn

.PHONY: lint-yaml
lint-yaml: setup ## yamllint (strict)
	$(BIN)/yamllint --strict .

.PHONY: lint-schemas
lint-schemas: setup ## JSON Schema validation of the locks, the support matrix and the blueprints
	$(LOCKCHECK) validate $(SCHEMA_PAIRS)

.PHONY: lint-ansible
lint-ansible: setup ## ansible-lint (production profile) and --syntax-check of every playbook
	$(ANSIBLE_ENV) $(BIN)/ansible-lint
	@for playbook in $(PLAYBOOKS); do \
	  echo "syntax-check $$playbook"; \
	  $(ANSIBLE_ENV) $(BIN)/ansible-playbook --syntax-check -e machine=example-machine "$$playbook"; \
	done

.PHONY: lint-portability
lint-portability: ## Only platform_facts may read distribution facts (BUILD_PROMPT §19.1)
	PYTHONPATH=tools/repo-checks python3 -m pokus_repo_checks portability --ansible-dir ansible

.PHONY: lint-markdown
lint-markdown: setup ## markdownlint
	$(NODE_BIN)/markdownlint-cli2

.PHONY: lint-spelling
lint-spelling: setup ## cspell with the project dictionary
	$(NODE_BIN)/cspell lint --no-progress --no-must-find-files "**/*.md"

.PHONY: lint-docs
lint-docs: setup ## Front matter, code blocks and qa markers of every docs page
	$(REPO_CHECKS) docs-lint $(DOCS_LINT_ARGS)

.PHONY: lint-mermaid
lint-mermaid: setup ## Validate Mermaid diagram syntax with mermaid-cli
	@rm -rf $(BUILD)/mermaid
	@$(REPO_CHECKS) mermaid-extract --docs-dir docs $(addprefix --exclude ,$(DOCS_EXCLUDES)) --out $(BUILD)/mermaid
	@status=0; \
	while IFS=$$'\t' read -r name where; do \
	  if ! $(NODE_BIN)/mmdc -q -p tools/repo-checks/puppeteer.json \
	      -i "$(BUILD)/mermaid/$$name" -o "$(BUILD)/mermaid/$${name%.mmd}.svg" >"$(BUILD)/mermaid/$$name.log" 2>&1; then \
	    echo "$$where: invalid Mermaid diagram:"; cat "$(BUILD)/mermaid/$$name.log"; status=1; \
	  fi; \
	done < $(BUILD)/mermaid/index.tsv; \
	exit $$status

.PHONY: privacy
privacy: ## Privacy scan: no personal or installation-specific data (stdlib only)
	PYTHONPATH=tools/repo-checks python3 -m pokus_repo_checks privacy .

.PHONY: links
links: ## Check external links in Markdown files (network; weekly in CI)
	PYTHONPATH=tools/repo-checks python3 -m pokus_repo_checks links .

.PHONY: test
test: setup test-py39 ## Unit tests (pytest) and the preflight suite on the oldest supported Python
	$(BIN)/pytest

.PHONY: test-distros
test-distros: setup ## Bootstrap in disposable Incus VMs per tested distribution (§19.9); TEST_DISTROS_ARGS="--target ID ..."
	PYTHONPATH=tools/test-distros:preflight $(BIN)/python -m pokus_test_distros run $(TEST_DISTROS_ARGS)

.PHONY: check-mode
check-mode: setup ## Required CI fast lane (§11, §19.9): base, users_groups, ssh, layout in check mode in a systemd container, nothing skipped; CI_IMAGE=<containers.ci_images key>
	$(ANSIBLE_ENV) PYTHONPATH=tools/test-distros:preflight $(BIN)/python -m pokus_test_distros check-mode --image $(CI_IMAGE)

.PHONY: check-mode-extended
check-mode-extended: setup ## Extended, non-blocking: the whole site.yml in check mode; components with unverified sources are skipped and listed as not tested; CI_IMAGE=<key>
	$(ANSIBLE_ENV) PYTHONPATH=tools/test-distros:preflight $(BIN)/python -m pokus_test_distros check-mode --extended --image $(CI_IMAGE)

.PHONY: test-py39
test-py39: ## Preflight and pokus-lab test suites with plain unittest on CPython 3.9 (stdlib only)
	cd preflight && PYTHONPATH=.:tests $(UV) run --no-project --python $(PREFLIGHT_MIN_PYTHON) \
	  python -m unittest discover -s tests -t tests
	cd tools/pokus-lab && PYTHONPATH=.:tests:../../preflight $(UV) run --no-project \
	  --python $(PREFLIGHT_MIN_PYTHON) python -m unittest discover -s tests -t tests

.PHONY: generate
generate: setup ## Regenerate generated files (matrix JSON, pins, reference pages, fixtures)
	$(GEN) generate
	$(BIN)/python preflight/tests/fixture_defs.py --write

.PHONY: lock-check
lock-check: setup ## Verify versions.lock.yml: schema, no unverified markers, digests, checksums, key fingerprints (network)
	$(LOCKCHECK) check

.PHONY: verify-todos
verify-todos: setup ## List every unverified marker in the repository; fails while any remains
	$(LOCKCHECK) todos

.PHONY: lock-update
lock-update: ## Print the manual procedure for changing versions.lock.yml (nothing is bumped automatically)
	@echo "versions.lock.yml is changed by hand; nothing is bumped automatically."
	@echo "Full procedure: docs/how-to/update-versions.md. In short:"
	@echo "  1. Find the new release at its upstream source and verify its digest, checksum or key."
	@echo "  2. Edit versions.lock.yml: the value, verified_on and source."
	@echo "  3. make generate          (pins, Ansible files, reference page)"
	@echo "  4. make bootstrap-lock    (only when python.packages changed)"
	@echo "     make models-lock       (only when python.model_tools changed)"
	@echo "     make installer-lock    (only when python.installer or python.packages changed)"
	@echo "  5. make lock-check lint test docs, then commit."

.PHONY: bootstrap-lock
bootstrap-lock: setup ## Compile bootstrap/requirements.lock from requirements.in (needs PyPI)
	$(UV) pip compile --quiet --universal --generate-hashes --no-header \
	  --python-version $(BOOTSTRAP_PYTHON) bootstrap/requirements.in -o bootstrap/requirements.lock

.PHONY: models-lock
models-lock: setup ## Compile the models role's hash-locked requirements.lock (needs PyPI)
	$(UV) pip compile --quiet --universal --generate-hashes --no-header \
	  --python-version $(BOOTSTRAP_PYTHON) ansible/roles/models/files/requirements.in \
	  -o $(BUILD)/models-requirements.lock
	{ echo "# Managed by pokus-infra (ansible/roles/models/files/requirements.lock). Local changes will be overwritten."; \
	  echo "# Compiled from requirements.in by 'make models-lock' (uv pip compile --generate-hashes)."; \
	  cat $(BUILD)/models-requirements.lock; } > ansible/roles/models/files/requirements.lock

.PHONY: installer-lock
installer-lock: setup ## Compile installer/requirements.lock from requirements.in (needs PyPI)
	$(UV) pip compile --quiet --universal --generate-hashes --no-header \
	  --python-version $(BOOTSTRAP_PYTHON) installer/requirements.in -o $(BUILD)/installer-requirements.lock
	{ echo "# Managed by pokus-infra (installer/requirements.lock). Local changes will be overwritten."; \
	  echo "# Compiled from requirements.in by 'make installer-lock' (uv pip compile --generate-hashes)."; \
	  cat $(BUILD)/installer-requirements.lock; } > installer/requirements.lock

.PHONY: drift-check
drift-check: setup ## Fail if a generated file differs from a fresh generation
	$(GEN) check

.PHONY: docs-coverage
docs-coverage: setup ## Fail unless every CLI option, exit code and schema field has a reference entry
	$(GEN) coverage

.PHONY: docs
docs: drift-check docs-coverage ## Drift check, coverage check and strict documentation build
	$(BIN)/mkdocs build --strict --site-dir site

.PHONY: check-all
check-all: lint test docs ## What CI runs: lint, test, docs

.PHONY: clean
clean: ## Remove build outputs (keeps the tool environments)
	rm -rf $(BUILD) site
