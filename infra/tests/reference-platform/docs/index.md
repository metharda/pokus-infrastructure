# refplat

The reference platform of Pokus Infrastructure's tests: a second platform
next to the Pokus Platform, written with the Python standard library only,
that infra deploys through the same code as any platform.

- `ctl` (controller) answers on the socket `api` for researchers and root
  and on the socket `session` for the agent only. It builds a dataset from
  the `train` cases of its restricted content package `cases`, publishes it
  through the GPU arbiter, submits the job `tune` (or `tunecpu`), follows
  the run and imports the adapter's lineage. Its tables are
  `public.refplat_runs`, `public.refplat_datasets` and
  `restricted.refplat_cases`.
- `agent` (untrusted agent) says hello on `session`, runs the isolation
  probe and reports what it could reach (nothing, when the isolation
  holds), and probes again every hour.
- `tune` is a toy training job: it hashes its dataset with the seed, takes
  one checkpoint per step and writes a 32-byte adapter. `tunecpu` runs the
  same script without a GPU and without a base model. A run interrupted by
  the lab's stop or a reboot resumes from its checkpoint and ends with the
  same adapter.

The hooks (`refplat-admin migrate`, `reconcile`, `drain`, `health`) print
one JSON line each. The code takes the platform's name from
`endpoints.toml` and the agent from the controller's hello, so a copy
deployed under another name runs unchanged.

Researchers use `refplat status`, `refplat dataset`, `refplat train [--job
tune|tunecpu] [--seed N] [--steps N] [--step-seconds S] [--wait SECONDS]`,
`refplat runs`, `refplat lineage <run>` and `refplat probe-report`; every
answer is JSON.
