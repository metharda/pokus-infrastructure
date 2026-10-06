# vllm

vLLM for the lab (BUILD_PROMPT §7.11, `docs/contracts.md#inference`),
applied with `gpu_inference` on `full` and `inference` nodes:

- Compose project `inference` with the pinned `vllm/vllm-openai` image, host
  network, the API on `127.0.0.1:18704` only, models read-only from
  `/srv/pokus/models`, no Hub access (`HF_HUB_OFFLINE=1`), read-only root
  file system with the caches in `/srv/pokus/state/vllm-cache`, the GPU
  through CDI or the NVIDIA runtime (Compose profile from `daemon.json`);
- serving profiles from `vllm/profiles/` in `/etc/pokus/vllm/profiles/`
  (vLLM `serve` options, allowed weight formats, the keys a deployment may
  override);
- deployments `/etc/pokus/vllm/deployments/<id>@<version>.yml` (immutable);
  `current.yml` links the active one; `/usr/local/libexec/pokus/vllm-deploy`
  renders `current.yaml` (the `vllm serve --config` file) and `current.env`;
- the deploy helper (C3): `pokus-vllm-deploy.socket`
  (`/run/pokus/vllm-deploy.sock`, 0600 `pokus-orch`) starts one root helper
  per connection; it checks the peer is `pokus-orch`, validates the request
  and the deployment, switches and restarts `pokus-inference.service`. No sudo;
- the default deployment from `vllm_default_deployment` (tool-call parser
  from the model's catalog entry unless overridden), served after
  installation unless `start_after_install: false`; without a deployment the
  unit is installed but disabled;
- the serving verification `/usr/local/libexec/pokus/vllm-verify`: health,
  model list, one deterministic chat completion and one `get_time` tool call;
  report in `/var/lib/pokus/verify/vllm-<id>@<version>.json`.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference` (and `node_role` `full` or `inference`), applied in `site.yml`.

## Idempotency

Once `current.yml` exists, the role re-renders that deployment and never
replaces the orchestrator's choice with the default. The helper writes files
only when their content changes; `current.env` carries the hash of
`current.yaml`, so Compose recreates vLLM exactly when the rendering changed.
The verification reports no change.
