# vllm

vLLM for the lab (BUILD_PROMPT §7.11, `docs/contracts.md#inference`),
applied with `gpu_inference` on `full` and `inference` nodes:

- Compose project `inference` with the pinned `vllm/vllm-openai` image, host
  network, the API on `127.0.0.1:18704` only, models read-only from
  `/srv/pinka/models`, no Hub access (`HF_HUB_OFFLINE=1`), read-only root
  file system with the caches in `/srv/pinka/state/vllm-cache`, the GPU
  through CDI or the NVIDIA runtime (Compose profile from `daemon.json`);
- serving profiles from `vllm/profiles/` in `/etc/pinka/vllm/profiles/`
  (vLLM `serve` options, allowed weight formats, the keys a deployment may
  override);
- deployments `/etc/pinka/vllm/deployments/<id>@<version>.yml` (immutable;
  `adapter_ids` names registered adapters as `<name>@<sha256>`);
  `current.yml` links the active one; the GPU arbiter
  (`ansible/roles/gpu_arbiter`, BUILD_PROMPT §7.18) renders `current.yaml`
  (the `vllm serve --config` file) and `current.env` and is the only way
  vLLM starts: `pinka-inference.service` is installed disabled;
- the default deployment from `vllm_default_deployment` (tool-call parser
  from the model's catalog entry unless overridden), served through the
  arbiter after installation unless `start_after_install: false`;
- the serving verification `/usr/local/libexec/pinka/vllm-verify`: health,
  model list, one deterministic chat completion and one `get_time` tool call;
  report in `/var/lib/pinka/verify/vllm-<id>@<version>.json`.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference` (and `node_role` `full` or `inference`), applied in `site.yml`.

## Idempotency

Once `current.yml` exists, the arbiter re-renders that deployment and never
replaces the platform's choice with the default. It writes files only when
their content changes; `current.env` carries the hash of `current.yaml`, so
vLLM restarts exactly when the rendering changed. The verification reports
no change.
