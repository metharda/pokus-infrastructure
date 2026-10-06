# models

Pinned model weights and LoRA adapters (BUILD_PROMPT §7.12,
`docs/contracts.md#paths`), applied with `gpu_inference` on `full` and
`inference` nodes; tag `models`, skippable.

- Input: `models.lock.yml` (schema
  `tools/lockcheck/schema/models-lock.schema.json`): models
  `{id, hf_repo, revision, files: [{path, sha256}], license, gated,
  quantization, notes}` and adapters
  `{name, hf_repo, revision, files, weights, base_model_id, license, gated, notes}`.
  Revisions are full commit hashes; there is never an unverified value.
- The `hf` CLI of the pinned `huggingface_hub` (`versions.lock.yml`
  `python.model_tools`, hash-locked in `files/requirements.lock`, `make
  models-lock`) in `/opt/pokus/models-venv`, created with the pinned uv and
  CPython.
- `/usr/local/libexec/pokus/models-fetch` downloads what is missing into
  `/srv/pokus/models/<hf_repo>/<revision>/` (adapters into
  `/srv/pokus/models/adapters/<name>/<sha256 of the weights file>/`),
  verifies every sha256 (a mismatching file is deleted and the run fails),
  sets `pokus-orch:pokus`, files 0444, and writes `MANIFEST.json` (entry,
  files with hashes and sizes, download time in UTC, tool version).
- Gated entries use `vault_hf_token`, passed on stdin, never on a command line.

The candidates for a lab come from `models.catalog.yml`; an entry is added to
the lock only after the owner approved it and its revision and file hashes
were resolved (`docs/how-to/add-a-model.md`).

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference` (and `node_role` `full` or `inference`), applied in `site.yml`.

## Idempotency

An entry whose `MANIFEST.json` lists exactly the lock's files and hashes, and
whose files have the recorded sizes, is not touched: verified files are never
downloaded or hashed again (`restore.yml` re-verifies the hashes). The
environment is synchronized only when the hash-locked requirements change.
