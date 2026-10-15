"""refplat's toy training (the jobs tune and tunecpu): deterministic and resumable.

The arbiter runs it with ``python3 -I -c``: the manifest carries this file's
text in both jobs, and the tests keep the three identical. It hashes the
training dataset with the seed into a state, advances the state one step at a
time with a checkpoint after each step, and writes the final state as its
adapter. A resumed run (``PINKA_JOB_RESUME=1``) continues from the checkpoint
and ends with the same adapter as an uninterrupted one.
"""

import hashlib
import json
import os
import pathlib
import time

seed = os.environ["PINKA_JOB_SEED"]
params = json.loads(os.environ.get("PINKA_JOB_HYPERPARAMETERS") or "{}")
steps, pause = int(params.get("steps", 8)), float(params.get("step_seconds", 0))
root = pathlib.Path("/datasets/train")
data = hashlib.sha256()
files = sorted(
    (p for p in root.rglob("*") if p.is_file()),
    key=lambda p: p.relative_to(root).as_posix().encode(),
)
for path in files:
    data.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
state, start = data.hexdigest() + seed, 0
checkpoint = pathlib.Path("/checkpoints/state.json")
if os.environ.get("PINKA_JOB_RESUME") == "1" and checkpoint.is_file():
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    state, start = saved["state"], saved["step"]
for step in range(start, steps):
    state = hashlib.sha256(f"{state}:{step}".encode()).hexdigest()
    temp = checkpoint.with_suffix(".tmp")
    temp.write_text(json.dumps({"step": step + 1, "state": state}), encoding="utf-8")
    temp.replace(checkpoint)
    print(f"step {step + 1}/{steps}", flush=True)
    time.sleep(pause)
out = pathlib.Path("/output/adapter")
out.mkdir(parents=True, exist_ok=True)
(out / "adapter_model.bin").write_bytes(bytes.fromhex(state))
config = {"seed": seed, "steps": steps, "toy": True}
(out / "adapter_config.json").write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
