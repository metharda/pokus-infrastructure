# gpu_arbiter

The GPU arbiter (`docs/explanation/gpu-arbiter.md`),
applied on `full` and `inference` nodes. It replaces contract v1's vLLM
deploy helper and decides whether the GPU serves, trains or idles:

- `/usr/local/libexec/pinka/gpu-arbiter` (root, standard library and PyYAML
  of `/opt/pinka/ansible-venv`), behind `pinka-gpu-arbiter.socket`
  (`/run/pinka/gpu.sock`, 0600) that belongs to `gpu_arbiter_peer`: the
  active platform's controller, `pinka-inference` on an inference node,
  `root` without a platform. One arbiter instance per connection
  (`pinka-gpu-arbiter@.service`) checks the peer and answers `status`,
  `serve`, `idle`, `train` and `publish-dataset`; the control endpoint of an
  inference node may only ask for `status` and `serve`. Root uses
  `pinka gpu`;
- `pinka-gpu-arbiter.service`, wanted by `pinka-lab.target`: whenever the
  lab starts (after a reboot and on `pinka start`) a run the lab's stop
  suspended or a crash left in training resumes (resumable jobs) or ends
  interrupted; otherwise the default deployment is served or the GPU stays
  idle (`gpu_arbiter.after_boot`); vLLM (`pinka-inference.service`) starts
  only through the arbiter;
- `pinka-gpu-job@<run>.service`: one training run in the container
  `pinka-job-<run>` with infra's fixed options, then the adapter's
  registration under `/srv/pinka/models/adapters/<name>/<sha256>/`;
- `/etc/pinka/gpu/arbiter.json`: node role, peer, whether it serves and
  trains, `gpu_arbiter`, the training caps (`memory_max: auto` replaced by
  the cap preflight computed), how a container reaches the GPU (as for
  vLLM), ports and timeouts. The state is in `/var/lib/pinka/gpu/` (`state.json`,
  `transitions.jsonl`, `runs/`).

## Variables

See `meta/argument_specs.yml`.

## Feature flags

Applied on `node_role` `full` and `inference` in `site.yml`, with the
flags after the switches (`preflight_gate`). Without
`gpu_inference` (no usable GPU, or `inference: off`) the arbiter serves
nothing, and a deployment served before is stopped (`idle
--unless-training`). Training runs only on `full` nodes where
`pinka_training` is not `off`: on the GPU with `gpu_training`, otherwise the
CPU-only jobs (`resources.gpus: 0`) with `training: cpu`; with training off
the arbiter refuses every run and dataset publication.

## Idempotency

Files are written only when their content changes. The chosen deployment is
served through the arbiter, which restarts vLLM only when its rendering
changed or vLLM does not answer; a running or suspended training run is
never interrupted (`serve --unless-training`, `idle --unless-training`).
