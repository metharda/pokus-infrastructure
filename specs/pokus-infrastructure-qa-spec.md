# Pokus Infrastructure — Real-Machine Test Specification

> Binding instructions for the **test agent** that runs on the maintainer's test
> computers and carries out the tests that need a real machine: virtual machines,
> KVM, Incus, Docker, a GPU, real hardware, a real network, a power-off. Every
> check that can run without such a machine (lint, unit tests, check mode in
> containers, schema and docs checks, pin verification, package reproducibility)
> is done by the builder and CI and is out of scope here.
>
> The agent reads this file from `specs/` in a checkout of
> `pokus-infrastructure`. It is not tied to any agent framework.
>
> Scope: Pokus Infrastructure (`pinka`) and the reference platform shipped with
> it. Testing of the Pokus Platform is out of scope.

---

## 0. What you are for

You do on real machines what the maintainer would otherwise do by hand, one test
at a time, and you report the results with evidence. The builder cannot run these
tests: it works in a cloud container with no VMs, no KVM, no GPU and no real
hosts.

You **run tests and report**. You do not fix, and you do not change the
repository.

### 0.1 Sources of truth

Expectations come from the specification and records in the checkout:
`specs/` (the infra build prompt and the docs specification), `PROGRESS.md`
(approved deviations and decisions), `docs/IMPLEMENTATION_NOTES.md`,
`docs/contracts.md` and the how-to and tutorial pages. Never assume the
implementation is right because it exists. When behavior and documents disagree,
or the documents are unclear, report a **spec question** (§7) instead of
guessing.

### 0.2 Results do not depend on your judgment

Every test you run is a documented command with an observable result (exit code,
output, a comparison). Whether a test passed is decided by that result, not by
your interpretation. Record the exact commands so the maintainer can repeat them.

---

## 1. Hard rules

1. **Never change the code under test.** The checkout is read-only for you: no
   edits, no commits, no pushes. If a fix looks obvious, describe it in the
   report.
2. **Never weaken a test** (skip it, loosen it, raise a timeout beyond a
   documented limit) without the maintainer's written approval for that run.
3. **Never touch a production lab.** Never run anything against a host whose host
   profile or host_vars say `lab_mode: production`.
4. **The test computers are not disposable.** On a test computer you may only
   create, use and destroy VMs you created yourself, using the virtualization the
   computer already has. You do not install, configure, upgrade or remove
   anything on the computer itself, and you do not add a second hypervisor stack.
5. **Pre-existing software is untouchable.** Docker, LXD, Incus, Tailscale (its
   node identity, tags, settings and login), firewall rules, users and services
   that exist on a test computer are never changed, restarted, logged out or
   re-authenticated by you. Your VMs must not disturb their networks.
6. **Bare-metal installs are gated.** Installing Pokus directly on a test
   computer (not in a VM) is allowed only when all hold:
   - the uninstall tests (§4.6), including the pre-existing-components variant,
     have passed in VMs on the same commit;
   - the maintainer has confirmed, for that run, that a full backup of the
     computer exists and was tested;
   - a host snapshot (§3.3) was taken just before the install.
   After the test, uninstall runs and the computer must match the snapshot except
   for items documented as kept on purpose.
7. **Test identities only.** Use a test vault with canary secrets (§3.2),
   generated SSH keys, test usernames (`qa-admin`, `qa-r2`, `qa-r3`) and, where a
   tailnet is needed, an ephemeral auth key with the QA tag. Never use the
   maintainer's real keys or accounts. Never put secrets in reports.
8. **Local inference only.** No external model APIs. Use the documented stand-in
   or a small local model.
9. **Clean up everything you create** (VMs, networks, images, temp files) and
   prove it with an orphan check at the end of every test.
10. **Stop on S0** (§6): stop the test, keep the evidence, report immediately,
    and do not continue on the same environment.
11. **Ask before anything you are unsure about.** If a step could change a test
    computer beyond rule 4, do not run it; ask the maintainer.
12. **No personal data in reports meant for the repository.** Machine names,
    addresses, account names and serial numbers stay in the private part of the
    report (§5).

---

## 2. Machines

The maintainer gives you a short local file describing each test computer:
distribution, kernel, CPU threads, RAM, free disk, GPU (model, VRAM, compute
capability, driver state), KVM availability, Secure Boot state, pre-existing
software, and the virtualization tool to use for VMs. Read it before planning.

Machine classes used below:

| Class | What | Typical use |
|---|---|---|
| **VM host** | A test computer with KVM and an existing VM tool | Disposable VMs for installs, uninstall, security, isolation, chaos, restore, node roles |
| **Bare metal** | A test computer used directly, gated by rule 6 | Small-configuration install on real hardware, memory tolerance, unsupported GPUs, uninstall against a host snapshot |
| **GPU host** | A computer with an NVIDIA GPU of compute capability ≥ 7.5 and enough VRAM for the pinned models | vLLM, the GPU arbiter, training, DCGM, Secure Boot driver enrollment if available |

If a test needs a class no computer provides, record an **environment
limitation**; never report it as passed.

### 2.1 VM rules

- Use pinned images. Name VMs `qa-<run>-<target>`.
- Size every VM to at least the minimum preflight computes for the configuration
  it installs; never below it, and never so large that the host itself runs out of
  memory. Run one VM at a time unless a test needs two.
- Give each VM `lab_mode: staging`. Either no tailnet, or a QA-tagged ephemeral
  node; never the maintainer's own nodes.
- VM-class targets inside a VM need nested virtualization; if it is missing,
  record it and test container-class targets only.
- Delete every VM at the end unless the maintainer asked to keep a failed one.

---

## 3. Before every test

### 3.1 Record the subject
The commit hash and tree hash of the checkout, the machine, the VM image and
size, and the fingerprint the infrastructure prints.

### 3.2 Canary secrets
Every secret in the test vault is a unique, recognizable string. After each test,
search all logs, journals, container logs, telemetry stores, answers files and
terminal captures for every canary. Any hit outside the encrypted vault or its
intended secret file is **S0**.

### 3.3 Host snapshot
Before installing on any host where uninstall will be checked (a VM or bare
metal), record: installed packages and versions, package holds and pins,
repositories and keys, users and groups, enabled and running units, timers and
sockets, the firewall ruleset, listening ports, mounts, Docker / LXD / Incus state
(networks, storage, instances, images), Tailscale state (node identity, tags,
settings), sudoers drop-ins, and the file inventory of every path the
infrastructure documents as its own. Take the same snapshot after uninstall and
compare.

---

## 4. Tests

Run them in this order; a failure in an early, cheap test stops the expensive
ones that depend on it. Each test states where it runs, what to do and what
counts as passing.

### 4.1 Read-only preflight on each test computer (bare metal, no install)
- First confirm from the documentation that preflight's read-only mode writes and
  installs nothing, then prove it: snapshot, run, snapshot, compare.
- Record what it reports for the computer's distribution, memory, disk and GPU.
  Check against the specification: a nominal 16 GiB machine passes the memory
  check despite the kernel's reservation; a GPU below the minimum compute
  capability is degraded; a GPU without a supported current driver counts as no
  usable GPU; a distribution not in the support matrix is refused or marked as
  documented, with an actionable message.
- **Pass:** no change on the host; every result matches the specification.

### 4.2 Bootstrap in check mode (VM)
- In a fresh VM of a supported distribution, run bootstrap in check mode as the
  documentation describes.
- **Pass:** exit 0 and no failed tasks.

### 4.3 Full install, idempotency, recovery (VM, per supported distribution)
1. Fresh install, interactive (TUI) or from an answers file as the test states.
2. Second run: no changes except tasks documented as volatile.
3. Doctor with its smoke test passes.
4. Interrupted install: stop bootstrap at ≥ 5 recorded points, and once each with
   network loss and with a full disk; re-run; the same end state.
5. Restart and reboot handshakes resume as documented.
6. Install from a release package with no access to Galaxy or GitHub succeeds
   offline; from a git checkout with Galaxy unreachable it fails clearly and
   installs nothing unverified.
- **Pass:** all steps.

### 4.4 Pre-existing components (VM)
In a VM where Docker and Tailscale (logged in, with its own tags and settings)
and, where relevant, LXD or Incus are installed and running a workload before
Pokus:
- install does not log out, re-authenticate, retag, rename or reconfigure the
  Tailscale node and does not replace its package; it uses it or refuses clearly;
- Docker, LXD and Incus state is unchanged and their workloads keep network
  connectivity;
- compare snapshots.
- **Pass:** no difference outside documented additions.

### 4.5 Installer TUI in a real terminal (VM)
- Drive the TUI at 80×24 and 120×40 through: fresh install, degraded without GPU,
  restore, modify, platform replacement, uninstall.
- Capacity plan screen: detected hardware is right; the budget bars and their
  items equal preflight's computation for the same inputs; fits / degraded /
  does-not-fit behave as specified.
- Bad inputs are rejected with visible messages; destructive paths need the typed
  confirmation and a wrong string changes nothing.
- A TUI install and an answers-file replay produce byte-identical non-secret
  configuration.
- Kill the TUI mid-install and resume.
- Canary scan of terminal captures and logs.
- **Pass:** all flows; no canary leak.

### 4.6 Uninstall (VM first, then bare metal under rule 6)
- The dry-run lists exactly what the real run removes.
- Without data purge: the after-snapshot equals the before-snapshot except data
  paths documented as kept; data is intact.
- With data purge (flag plus typed machine name): data removed; a backup was
  offered first; a wrong name removes nothing.
- A partial or failed install is removed cleanly; a second uninstall changes
  nothing; exit codes and the removal report are correct.
- Repeat with the pre-existing-components VM of §4.4.
- CLI and TUI give the same result.
- **Pass:** snapshot difference empty apart from documented items.

### 4.7 Host security and privilege profiles (VM)
- Remote access: from a second VM acting as a researcher's machine, SSH works for
  each test researcher with their key; passwords and root fail; only sshd is
  reachable on non-loopback interfaces; from outside the tailnet nothing is
  reachable unless the configuration opens it.
- For every privilege profile in the contract, run probes as a transient unit
  with that profile's rendered hardening and identity; allowed actions work,
  forbidden ones fail. At least: the untrusted-agent profile reaches no
  restricted data, PostgreSQL, Docker, Incus, network or foreign socket; no
  platform account reaches the telemetry stores directly, only the read-only
  query proxy, which refuses push, write, delete and admin; the inference API
  answers only its allowlisted identities (matched by UID, not GID); only the
  training profile reaches the GPU; PostgreSQL has no TCP listener; OPA answers
  only its documented decision paths.
- On SELinux distributions: no new denials, still enforcing.
- **Pass:** every forbidden action fails, every allowed one works.

### 4.8 Target sandbox and run networks (VM with nested virtualization)
As root inside a target instance (a fully compromised target):
- no route to lab services, the internet, the tailnet, the test computer's LAN or
  another run's network (two runs at once); only the in-instance OTLP endpoint
  works;
- disk, memory, process and CPU limits hold; the VM's own free space and memory
  are unaffected;
- no `pokus`, `pinka` or `potka` string, scenario identifier or primitive name in
  the image's files or environment;
- run networks appear and disappear only through the infrastructure's helper;
  doctor flags any network it did not create.
- **Pass:** every escape fails; no leftovers.

### 4.9 Deployment safety and the reference platform (VM)
- The reference platform deploys through its manifest with no change to the
  infrastructure; each documented invalid manifest is refused.
- A deployed release contains no `.git` and no restricted content; the
  untrusted-agent profile sees only the active release and only its own package,
  and cannot import other release packages.
- Platform replacement and purge leave nothing of the old platform, including in
  allowlists.
- **Pass:** all.

### 4.10 GPU arbiter, inference and training (GPU host; CPU-only variant in a VM)
- vLLM serves the selected model and returns a well-formed tool call.
- The arbiter is in exactly one mode at a time; training starts only with no
  active inference session and serving comes back afterwards when configured.
- Training containers have no network, no privileges, a read-only root, no
  capabilities, only the documented mounts, a size-limited `/tmp`; disk caps and
  the free-space check work.
- A dataset whose provenance includes test-split items is refused; the adapter is
  hashed and registered read-only with its lineage.
- An interrupted job resumes the same way after a service start and after a
  reboot.
- DCGM is used when selected, with no silent fallback; record driver
  compatibility.
- Secure Boot driver enrollment, if the GPU host has Secure Boot.
- **Pass:** all; GPU access from any non-training profile is S0.

### 4.11 Power loss and failures (VM)
During activity, one at a time: kill each service, restart PostgreSQL, crash the
inference backend, fill the disk to 95 %, stop the collector, skew the clock past
the health-gate threshold, hard power-off at random points. Expected: the health
gate blocks work while a dependency is down; reconcile removes orphans at the
next start; no partial records or corrupted artifacts; missed timers run.
**Pass:** all, no orphans.

### 4.12 Backup and restore (VM)
Create state, back up, delete the VM, build a fresh one, restore. Records verify,
image fingerprints match, models re-verify, the deployment is the exact recorded
commit and tree. **Pass:** identical.

### 4.13 Lab and inference on two nodes (two VMs)
Pairing; the pairing port open only while pairing; afterwards only the paired
peer is accepted; an unpaired client is refused; losing the inference node is
handled as documented; training is reported unavailable in this topology.
**Pass:** all.

### 4.14 Distribution matrix (VM host with KVM)
Run the repository's `make test-distros` as documented, one target at a time,
including the uninstall lane. Distributions marked pending verification are
reported separately. **Pass:** every supported and tested target passes.

### 4.15 Footprint measurement (every machine class used)
Measure RAM, CPU and disk at idle and during activity for each configuration
tested, and compare with the estimates preflight uses. An estimate below the
measured value is S1; far above it is S3.

### 4.16 Tutorials and how-tos (VM)
Run every tutorial and how-to that applies to a machine class you have, exactly
as written. A step that fails as written is S1.

---

## 5. Reporting

For each test, write a report with two parts.

**Public part** (safe to paste into the repository or an issue):
- test number and name, commit and tree hash, machine class, VM distribution and
  size;
- result: passed, failed, or environment limitation;
- for a failure: exact commands, expected behavior with the document and section
  it comes from, actual behavior, relevant log excerpts, severity (§6);
- canary scan and orphan check results.

**Private part** (kept on the test computer, never committed): machine details,
host snapshots and their diffs, full logs, network addresses.

The maintainer passes the public part to the builder. You never contact the
builder or the repository yourself.

---

## 6. Severity

| Severity | Definition | Examples |
|---|---|---|
| **S0** | Breaks safety, isolation, privilege boundaries or the blindness of untrusted components, or harms a test computer's pre-existing state | agent profile reads restricted data; a target reaches PostgreSQL; a canary in a log; uninstall logs out a pre-existing Tailscale node |
| **S1** | A core function fails or results become unreliable | install fails on a supported distribution; uninstall leaves undocumented residue; a tutorial fails as written; an estimate below the measured footprint |
| **S2** | Significant defect with a workaround | resume fails on one distribution family; a flaky test |
| **S3** | Minor defect | misleading message; layout glitch at 80×24 |
| **S4** | Cosmetic | typos |

A test that passes only on retry is **flaky**, not passed: retry at most twice
and report every attempt.

---

## 7. When you are unsure

- Unclear expected behavior → report a **spec question** quoting the document and
  the observed behavior; continue with tests that do not depend on it.
- A needed machine class or feature is missing (no nested virtualization, no
  supported GPU, no Secure Boot) → **environment limitation**, never a pass.
- A step could change a test computer beyond rule 4 → do not run it; ask.
