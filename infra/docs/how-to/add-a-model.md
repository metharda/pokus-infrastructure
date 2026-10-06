---
title: How to add a model, verify it, and create and activate a deployment
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Pin a model in models.lock.yml, download and verify it, serve it with vLLM as a deployment, and switch deployments by hand.
---

# How to add a model, verify it, and create and activate a deployment

vLLM serves only models that are pinned in `models.lock.yml` (a full commit
hash and a sha256 for every file) and that the `models` role downloaded and
verified. What vLLM serves is a *deployment*: a file
`/etc/pokus/vllm/deployments/<id>@<version>.yml` that names the model, the
serving profile and a few allowed overrides. This guide adds a model, serves
it, and switches deployments by hand. The orchestrator switches deployments
through the same helper during campaigns
([integration contract](../contracts.md#inference)).

!!! note "Prerequisites"
    - A lab host with the `gpu_inference` feature and `node_role` `full` or
      `inference`, provisioned at least once.
    - An admin account on it (member of `pokus-admin`) and the infra directory
      bootstrap runs from.
    - The model approved by the lab's owner: candidates from
      `models.catalog.yml`, which lists permissive licenses first and gated or
      custom-license models separately (owner answer E7).
    - For a gated model, a Hugging Face token in `vault_hf_token`.

!!! warning "The catalog is a proposal"
    Every catalog entry is `status: proposed` until the owner approves it, and
    its `revision` is `TODO(verify)`: the candidates were chosen without
    access to the Hub. Resolve the revision and the hashes yourself as below;
    never copy an unverified value into `models.lock.yml`.

## Pin the model

1. Find the commit to pin and the files vLLM needs (weights, configuration,
   tokenizer, chat template). The Hub's API lists the tree of a commit; for
   files stored with Git LFS (the weights) it includes the file's sha256:

    <!-- qa:skip reason="needs access to the Hugging Face Hub" -->
    ```bash
    curl -fsS "https://huggingface.co/api/models/<hf_repo>/revision/main" | jq -r .sha
    curl -fsS "https://huggingface.co/api/models/<hf_repo>/tree/<revision>?recursive=1" \
      | jq -r '.[] | select(.type == "file") | [.path, (.lfs.oid // "not-lfs")] | @tsv'
    ```

    `<hf_repo>` is the repository (the catalog entry's `hf_repo`),
    `<revision>` the 40-character commit the first command prints.
    <!-- TODO(verify): field names of the Hub's tree API (lfs.oid as the sha256); the Hub was not reachable from the build environment -->

2. Hash the small files that are not in LFS (`not-lfs`) by downloading them at
   the pinned commit:

    <!-- qa:skip reason="needs access to the Hugging Face Hub" -->
    ```bash
    curl -fsSL "https://huggingface.co/<hf_repo>/resolve/<revision>/<path>" | sha256sum
    ```

    `<hf_repo>` and `<revision>` are the values from step 1, `<path>` the
    file's path in the repository.

3. Add the entry to `models.lock.yml` in the lab's configuration (schema:
   `tools/lockcheck/schema/models-lock.schema.json`):

    <!-- qa:skip reason="example of a lock entry" -->
    ```yaml
    models:
      - id: <model-id>
        hf_repo: <hf_repo>
        revision: <revision>
        files:
          - {path: config.json, sha256: <sha256>}
          - {path: model.safetensors, sha256: <sha256>}
          - {path: tokenizer.json, sha256: <sha256>}
          - {path: tokenizer_config.json, sha256: <sha256>}
        license: apache-2.0
        gated: false
        quantization: awq_marlin
        notes: approved on <date> by the lab owner
    ```

    `<model-id>` is the id vLLM serves the model under (the catalog's `id`),
    `<hf_repo>` and `<revision>` come from step 1, each `<sha256>` from steps 1
    and 2, and `<date>` is the day of the owner's approval. `quantization` is
    the vLLM weight format and must be one the serving profile allows
    (`vllm/profiles/<profile>.yml`, `quantizations`).

4. Check the lock against its schema:

    <!-- qa:run env=E2 -->
    ```bash
    make lint-schemas
    ```

## Serve it as the default deployment

1. Set the default deployment in `ansible/inventory/host_vars/<machine>.yml`:

    <!-- qa:skip reason="example of a host_vars file" -->
    ```yaml
    vllm_profile: ada-12g
    vllm_default_deployment:
      model_id: <model-id>
      start_after_install: true
    ```

    `<model-id>` is the id from the lock entry. The deployment is called
    `default-<profile>@1`. Its tool-call parser comes from the model's catalog
    entry; set `overrides: {tool_call_parser: ...}` for a model that is not in
    the catalog. Deployment files never change: to change the spec, also set
    `version: 2` (and so on).

2. Download, verify and serve:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags models,vllm
    ```

    `<machine>` is the name of this lab host's host_vars file. The `models`
    role downloads only what is missing into
    `/srv/pokus/models/<hf_repo>/<revision>/`, checks every sha256 (a
    mismatch deletes the file and fails), makes the files read-only and writes
    `MANIFEST.json`. The `vllm` role renders the deployment, starts vLLM and
    runs the serving verification.

## Create and activate another deployment

1. Write the deployment file as root (mode 0644; the helper refuses files
   that anyone but root can write):

    <!-- qa:skip reason="changes the lab host's configuration" -->
    ```bash
    sudo tee /etc/pokus/vllm/deployments/<id>@<version>.yml >/dev/null <<'EOF'
    id: <id>
    version: <version>
    model_id: <model-id>
    adapters: []
    profile: ada-12g
    overrides: {tool_call_parser: hermes, max_model_len: 4096}
    sampling_defaults: {temperature: 0.2}
    EOF
    ```

    `<id>` uses lowercase letters, digits and hyphens, `<version>` is a
    number, `<model-id>` a verified model of the lock. Allowed `overrides`
    are the keys under `overridable` in the profile; `sampling_defaults`
    accepts `temperature`, `top_p`, `top_k`, `min_p`, `repetition_penalty`,
    `presence_penalty`, `frequency_penalty` and `max_new_tokens`.

2. Check what it renders to, then activate it (this restarts vLLM):

    <!-- qa:skip reason="restarts the inference service" -->
    ```bash
    sudo /usr/local/libexec/pokus/vllm-deploy render <id>@<version>
    sudo /usr/local/libexec/pokus/vllm-deploy activate <id>@<version>
    ```

    `<id>@<version>` names the file from step 1. The helper validates the
    file, the profile and the verified model, writes
    `/etc/pokus/vllm/current.yaml` and `current.env`, points `current.yml` at
    the deployment and restarts `pokus-inference.service`. It prints
    `{"ok": true, "changed": true}`; on a validation error nothing changes.
    Bootstrap re-renders the current deployment on later runs and never
    replaces it with the default one.

3. Run the serving verification for it:

    <!-- qa:skip reason="sends requests to the inference service" -->
    ```bash
    sudo /usr/local/libexec/pokus/vllm-verify --model <model-id> \
      --deployment <id>@<version> --out /var/lib/pokus/verify/vllm-<id>@<version>.json
    ```

    `<model-id>`, `<id>` and `<version>` are the values from step 1.

## Verify the deployment

The serving verification waits for `/health`, lists the models, sends one
deterministic chat completion and one request with a `get_time` tool, and
fails unless a well-formed tool call comes back. Read its report:

<!-- qa:run env=E2 -->
```bash
sudo jq '{ok, models, tool_call, error}' /var/lib/pokus/verify/vllm-<id>@<version>.json
```

`<id>@<version>` is the deployment (`default-<profile>@1` for the default
one, with `<profile>` being `vllm_profile`). `ok` must be `true`. When it is
`false` and the error points to the tool-call parser, compare
`tool_call_parser` with the model's catalog entry and check that the model's
chat template supports tools.

Related: [How telemetry, metrics and logs flow through the lab](../explanation/telemetry.md),
[Integration contract](../contracts.md#inference).
