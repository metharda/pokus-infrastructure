---
title: Write a minimal platform
type: tutorial
audience: [developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Write miniplat, a two-service platform modeled on the reference platform, validate and tag its release, and deploy it on a lab.
---

# Write a minimal platform

In this tutorial you write `miniplat`, a platform for Pokus Infrastructure
(pinka) that has only what contract v2 needs. Its controller serves two
sockets: one for researchers and root, and one reserved for an untrusted
agent. The agent has its own package. A health hook tells infra whether the
platform works. You validate the release with `pinka-platform`, build it as
the deployment does, tag it, and deploy it on a lab, which then reports the
platform as healthy.

`miniplat` is a smaller copy of the reference platform `refplat` in
`tests/reference-platform/`. That is a complete release that infra's tests
deploy, with a broker, a database, a training job and a CLI. Keep it open
next to this page.

!!! note "Prerequisites"
    - A development checkout of this repository with `make setup` done. It
      installs the development environment and puts the uv that
      `versions.lock.yml` pins in `build/tools/uv`.
    - Git with your identity set (`user.name` and `user.email`), and access
      to the Python Package Index (PyPI), which `uv lock` and the build need.
    - For steps 8 and 9: a lab host provisioned by bootstrap with
      `node_role: full` or `lab` and no active platform, an admin account
      on it, and its host_vars file.

## Step 1: Create the release directory

Put the pinned uv first on your `PATH`, then create the repository of the
platform with the directories of its two packages:

<!-- qa:run env=E0 -->
```bash
export PATH="<checkout>/build/tools:$PATH"
uv --version
mkdir -p ~/miniplat && cd ~/miniplat
git init
mkdir -p src/miniplat packages/miniplat-agent/src/miniplat_agent
```

`<checkout>` is the directory where you cloned this repository.
`uv --version` prints the version under `binaries.uv` in
`versions.lock.yml`, the uv the lab builds platforms with. Keep this shell
open: the following steps use it.

In this tutorial the whole repository is the release: a deployment extracts
it and finds `platform-manifest.yaml` at its root.

## Step 2: Write the manifest

The manifest tells infra what to create for the platform: accounts, units,
sockets and the hooks it runs. Write it:

<!-- qa:run env=E0 -->
```bash
cat > platform-manifest.yaml <<'EOF'
contract_version: 2
name: miniplat
version: 0.1.0
runtime: python-uv
services:
  - name: ctl
    profile: controller
    start: [.venv/bin/miniplat-ctl]
  - name: agent
    profile: untrusted-agent
    package: packages/miniplat-agent/
    start: [.venv/bin/miniplat-agent]
    after: [ctl]
sockets:
  - {name: api, service: ctl, clients: [researchers, root]}
  - {name: session, service: ctl, clients: [untrusted-agent], untrusted_clients: true}
hooks:
  health: {run: [.venv/bin/miniplat-admin, health], timeout_seconds: 30}
EOF
```

What each part does:

- `contract_version: 2` and `runtime: python-uv`: the release is a uv
  project. The deployment runs `uv sync --frozen` from its `uv.lock`, so
  the commands below `.venv/` exist only after the build.
- `name: miniplat`: 2 to 12 lowercase letters and digits, starting with a
  letter. The names `infra`, `lab`, `pinka`, `platform`, `pokus`,
  `restricted`, `root`, `system` and `training` are reserved. Every name
  infra derives from it is checked against the reserved lists too, for
  example the accounts `miniplat-ctl` and `miniplat-agent`, the units
  `miniplat-ctl.service` and `miniplat-agent.service`, and the socket
  client groups `miniplat-ctl-api` and `miniplat-ctl-session`.
- `ctl` uses the `controller` profile. Every platform has exactly one
  controller. Every other service profile, researchers, admins and root
  may use its sockets, and so may the agent on a socket dedicated to it.
  Infra also runs the `health` hook as the controller's account.
- `agent` uses the `untrusted-agent` profile. This profile is for code
  whose actions you do not trust, such as an agent driven by a language
  model. It has no database role, no network (`PrivateNetwork=yes`), no
  secrets and no restricted data. Of the release it sees only its own
  package (`package`, required for this profile) and the environment the
  deployment builds from it. It reaches the platform only through sockets
  dedicated to it. `after: [ctl]` starts it after the controller.
- `api` is the controller's socket for researchers and root. `session` is
  dedicated to the agent: `untrusted_clients: true` with `untrusted-agent`
  as its only client. The validator refuses the agent on any other socket,
  and refuses the flag together with any other client. Infra creates each
  socket as `/run/pinka/platform/<service>/<socket>.sock`, owned by the
  serving account, with the socket's client group and mode 0660, and hands
  it to the service by socket activation.
- `hooks.health` is the only required hook. It prints one JSON line on
  stdout. Exit code 0 with `"ok": true` means healthy.
- There is no `requires`, because `miniplat` uses no infra facility: no
  database, Incus, telemetry, inference, OPA or GPU. The validator refuses
  a manifest that uses a facility it does not declare.

[Platform manifest](../reference/platform-manifest.md) describes every
field, and [Platform privilege profiles](../reference/platform-profiles.md)
describes what each profile may do and who may use whose sockets.
[Privilege profiles and restricted data](../explanation/privilege-profiles.md)
explains why the profiles are fixed and how infra enforces them.

## Step 3: Write the project files

The root project holds the controller and the hook. The agent is a
workspace member with a project of its own. Write both `pyproject.toml`
files:

<!-- qa:run env=E0 -->
```bash
cat > pyproject.toml <<'EOF'
[project]
name = "miniplat"
version = "0.1.0"
requires-python = "==3.12.*"
dependencies = []

[project.scripts]
miniplat-ctl = "miniplat.ctl:main"
miniplat-admin = "miniplat.admin:main"

[build-system]
requires = ["uv_build>=0.12.23,<0.13"]
build-backend = "uv_build"

[dependency-groups]
build = ["uv_build>=0.12.23,<0.13"]

[tool.uv.workspace]
members = ["packages/miniplat-agent"]
EOF
cat > packages/miniplat-agent/pyproject.toml <<'EOF'
[project]
name = "miniplat-agent"
version = "0.1.0"
requires-python = "==3.12.*"
dependencies = []

[project.scripts]
miniplat-agent = "miniplat_agent.main:main"

[build-system]
requires = ["uv_build>=0.12.23,<0.13"]
build-backend = "uv_build"
EOF
```

- The console scripts become the commands of the manifest:
  `.venv/bin/miniplat-ctl`, `.venv/bin/miniplat-admin` and
  `.venv/bin/miniplat-agent`.
- `requires-python` matches the CPython 3.12 that the lab builds with
  (`python` in `versions.lock.yml`).
- The `build` dependency group locks the build backend. The deployment
  builds without network, so the backend must come from `uv.lock`. The
  `uv_build` range is the one the reference platform uses.
- The agent's package depends on nothing. An agent package may depend
  only on packages from a package index: a dependency on another package
  of the release, or on a path, Git or URL source, is refused.

## Step 4: Write the controller and the hook

The controller answers one JSON line per connection: `status` on `api`,
`hello` on `session`. It takes both sockets from systemd. The file
descriptors start at 3, `LISTEN_FDS` gives their number and
`LISTEN_FDNAMES` their names from the manifest:

<!-- qa:run env=E0 -->
```bash
cat > src/miniplat/__init__.py <<'EOF'
"""miniplat: a minimal platform for Pokus Infrastructure."""
EOF
cat > src/miniplat/ctl.py <<'EOF'
"""miniplat-ctl, the controller: answers on the sockets api and session."""

import json
import os
import socket
import sys
import threading
from datetime import datetime, timezone

seen = {"agent_hello": None}


def handed_over():
    """The sockets systemd hands over, by their names in the manifest."""
    names = os.environ.get("LISTEN_FDNAMES", "").split(":")
    count = int(os.environ.get("LISTEN_FDS", "0"))
    return {names[i]: socket.socket(fileno=3 + i) for i in range(count)}


def api(request):
    """Researchers and root: status."""
    if request.get("command") == "status":
        return {"ok": True, "agent_hello": seen["agent_hello"]}
    return {"ok": False, "error": "api takes status only"}


def session(request):
    """The agent only: hello."""
    if request.get("command") == "hello":
        seen["agent_hello"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"ok": True}
    return {"ok": False, "error": "session takes hello only"}


def serve(listener, answer):
    """Answer one JSON line per connection with one JSON line."""
    while True:
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(10)
            try:
                request = json.loads(conn.makefile("rb").readline(65536))
                reply = answer(request) if isinstance(request, dict) else {"ok": False}
            except (OSError, ValueError):
                continue
            conn.sendall(json.dumps(reply).encode() + b"\n")


def main():
    """Serve both sockets until systemd stops the service."""
    sockets = handed_over()
    if {"api", "session"} - set(sockets):
        sys.exit("miniplat-ctl: needs the sockets api and session (socket activation)")
    for name, answer in (("api", api), ("session", session)):
        threading.Thread(target=serve, args=(sockets[name], answer), daemon=True).start()
    threading.Event().wait()
EOF
```

Then write the health hook. It asks the controller for its status on
`api` and prints its verdict as one JSON line. Infra runs it as the
controller's account, which owns the socket:

<!-- qa:run env=E0 -->
```bash
cat > src/miniplat/admin.py <<'EOF'
"""miniplat-admin health: the health hook, one JSON line on stdout."""

import json
import socket
import sys

API = "/run/pinka/platform/ctl/api.sock"


def main():
    """Ask the controller for its status and judge it."""
    if sys.argv[1:] != ["health"]:
        sys.exit("usage: miniplat-admin health")
    try:
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(10)
            conn.connect(API)
            conn.sendall(b'{"command": "status"}\n')
            reply = json.loads(conn.makefile("rb").readline(65536))
    except (OSError, ValueError) as exc:
        reply = {"ok": False, "error": str(exc)}
    ok = isinstance(reply, dict) and reply.get("ok") is True
    print(json.dumps({"ok": ok, "detail": reply}))
    sys.exit(0 if ok else 1)
EOF
```

Everything else the hook writes, such as the usage message, goes to stderr
and so to the journal. The socket path holds the service and socket names
of the manifest, not the platform's name.

## Step 5: Write the agent

The agent says hello on its dedicated socket once a minute and prints the
controller's answer, which ends up in its journal:

<!-- qa:run env=E0 -->
```bash
cat > packages/miniplat-agent/src/miniplat_agent/__init__.py <<'EOF'
"""miniplat-agent: the platform's untrusted agent."""
EOF
cat > packages/miniplat-agent/src/miniplat_agent/main.py <<'EOF'
"""miniplat-agent: says hello on its dedicated socket once a minute."""

import json
import socket
import sys
import time

SESSION = "/run/pinka/platform/ctl/session.sock"


def main():
    """Say hello and print the controller's answer, until stopped."""
    while True:
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(10)
                conn.connect(SESSION)
                conn.sendall(b'{"command": "hello"}\n')
                print(conn.makefile("rb").readline(65536).decode().strip(), flush=True)
        except OSError as exc:
            print(f"miniplat-agent: no session: {exc}", file=sys.stderr, flush=True)
        time.sleep(60)
EOF
```

In its sandbox the agent sees this socket and infra's telemetry socket for
agents, and no network at all.

## Step 6: Lock and validate the release

1. Lock the dependencies and keep the build's environments out of Git:

    <!-- qa:run env=E0 -->
    ```bash
    uv lock
    printf '%s\n' .venv/ .agent-venvs/ > .gitignore
    ```

    `uv lock` writes `uv.lock` with the two workspace members and the
    build backend. The release you tag never contains `.venv` or
    `.agent-venvs`, because the deployment builds both.

2. Check the release against contract v2:

    <!-- qa:run env=E0 -->
    ```bash
    cd <checkout>
    PYTHONPATH=tools/platform .venv/bin/python -m pinka_platform validate ~/miniplat --no-host-check
    echo "exit code: $?"
    ```

    `<checkout>` is the directory of step 1. `--no-host-check` skips the
    comparison with the accounts, groups and commands of your own machine.
    On the lab host, the deployment runs that check too. A valid release
    prints its summary and exits 0:

    ```text title="Output"
    platform miniplat 0.1.0 (python-uv)
    requires: nothing
    services:
      ctl: controller as miniplat-ctl (Incus bench/build; restricted data)
      agent: untrusted-agent as miniplat-agent
    socket ctl/api: researchers, root
    socket ctl/session: untrusted-agent  [dedicated to the agent]
    exit code: 0
    ```

    Each service line names the profile and the account, and in
    parentheses the sensitive grants the profile allows. A refused release prints one line per problem, with the place
    in the manifest and the rule, and exits 65.
    [How to validate a platform release](../how-to/validate-a-platform-release.md)
    explains the result and the other options.

## Step 7: Build the release as the deployment does

Build the shared environment and the agent's own environment, then check
again with `--built`:

<!-- qa:run env=E0 -->
```bash
cd ~/miniplat
uv sync --frozen --all-packages --no-dev --no-editable
UV_PROJECT_ENVIRONMENT=.agent-venvs/agent uv sync --frozen --package miniplat-agent --no-dev --no-editable
cd <checkout>
PYTHONPATH=tools/platform .venv/bin/python -m pinka_platform validate ~/miniplat --no-host-check --built
echo "exit code: $?"
```

`<checkout>` is the directory of step 1. The validator prints the same
summary and exits 0. Now the commands below `.venv/` must exist, and the
agent's environment `.agent-venvs/agent` may hold only its package and
its locked dependencies. On the lab the deployment builds the same way,
as `pinka-build` and without network for the actual build.

The finished release looks like this (`.venv` and `.agent-venvs` are
ignored by Git):

```text
miniplat/
├── .gitignore
├── platform-manifest.yaml
├── pyproject.toml
├── uv.lock
├── src/
│   └── miniplat/
│       ├── __init__.py
│       ├── admin.py
│       └── ctl.py
└── packages/
    └── miniplat-agent/
        ├── pyproject.toml
        └── src/
            └── miniplat_agent/
                ├── __init__.py
                └── main.py
```

`tests/reference-platform/` has the same shape, with more modules in
`src/refplat/`, a configuration schema in `schemas/`, the job script in
`jobs/` and its documentation in `docs/`.

## Step 8: Tag the release

Commit, tag and pack the repository into one file for the lab host:

<!-- qa:run env=E0 -->
```bash
cd ~/miniplat
git add -A
git commit -m "miniplat 0.1.0"
git tag -a v0.1.0 -m "miniplat 0.1.0"
git bundle create ~/miniplat.bundle --all
```

The tag `v0.1.0` is the ref the lab deploys. Its prefix `v` is the
`tag_prefix` of the next step. A deployed tag must never move to another
commit: the lab refuses a tag that names a different commit than before.

## Step 9: Point the lab at the release

A lab deploys a platform from a Git repository that it reaches by SSH,
anonymous HTTPS, or as a local `file:///` repository that only root can
change. `make test-distros` deploys `refplat` from a `file:///` repository
in the same way.

1. Copy `miniplat.bundle` to the lab host (for example with `scp`) and,
   on the lab host, clone it into a bare repository below `/root`:

    <!-- qa:skip reason="needs the lab host" -->
    ```bash
    sudo git clone --bare <bundle> /root/miniplat.git
    sudo git -C /root/miniplat.git tag --list
    ```

    `<bundle>` is the path of the copied `miniplat.bundle` on the lab
    host. The second command prints `v0.1.0`. The repository and every
    directory above it are owned by root and not writable by group or
    others; otherwise the lab refuses the source.

2. Name the release in the host_vars file of the lab host:

    <!-- qa:skip reason="example of a host_vars file" -->
    ```yaml
    platform:
      repo: file:///root/miniplat.git
      ref: v0.1.0
      path: ""
      tag_prefix: v
    ```

    `path: ""` means that the release is the repository root. `miniplat`
    has no secrets, no database and no configuration schema, so it needs
    no vault keys and no `platform_settings`.

3. In the checkout that bootstrap runs from, check the release on the
   host, then deploy it:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    cd <lab-checkout>
    sudo ./bootstrap/bootstrap.sh --machine <machine> --inspect-platform
    sudo ./bootstrap/bootstrap.sh --machine <machine>
    ```

    `<lab-checkout>` is the checkout on the lab host (`~/pinka` in the
    installation tutorials) and `<machine>` the name of its host_vars file.
    The first command fetches, stages and validates the release, prints
    the summary of step 6 and changes nothing else. The second deploys it:
    it builds the release, publishes it read-only below
    `/srv/pinka/platforms/miniplat/`, renders the accounts, units and
    sockets, and starts `pinka-platform.target`.
    [How to plug a platform into the lab](../how-to/deploy-platform.md) describes
    every step, updates and going back to an earlier release.

## Verify

1. On your checkout, the built release is still valid:

    <!-- qa:run env=E0 -->
    ```bash
    cd <checkout>
    PYTHONPATH=tools/platform .venv/bin/python -m pinka_platform validate ~/miniplat --no-host-check --built
    git -C ~/miniplat tag --list
    ```

    `<checkout>` is the directory of step 1. The validator exits 0 with
    the summary of step 6, and the tag list shows `v0.1.0`.

2. On the lab host, the platform is active and healthy:

    <!-- qa:skip reason="needs the lab host with miniplat deployed" -->
    ```bash
    sudo pinka status
    ```

    Among the other lines, the output shows:

    ```text title="Output"
    platform: miniplat 0.1.0 (release v0.1.0)
      pinka-platform.target                    active
      health: healthy
    ```

    `health: healthy` means that infra ran `miniplat-admin health` as the
    controller's account and got `"ok": true`.

3. The sockets exist with their owner, group and mode, and the agent
   reaches the controller over its dedicated socket:

    <!-- qa:skip reason="needs the lab host with miniplat deployed" -->
    ```bash
    sudo ls -l /run/pinka/platform/ctl/
    sudo journalctl --unit miniplat-agent.service --lines 3 --no-pager
    ```

    `api.sock` and `session.sock` belong to `miniplat-ctl`, with the groups
    `miniplat-ctl-api` and `miniplat-ctl-session` and mode `srw-rw----`.
    Where `/run` supports access control lists (ACLs), a `+` after the mode
    of `api.sock` marks the entries for researchers and admins. The journal
    shows `{"ok": true}` for each hello of the agent.

## What you built

A platform release that plugs into any lab through contract v2: a
controller that takes its sockets from systemd, an untrusted agent that
sees only its own package and reaches the controller through one dedicated
socket, and a health hook that
infra runs for `pinka start`, `pinka status` and doctor.

Next:

- Compare your release with `tests/reference-platform/`. Its
  `platform-manifest.yaml` adds an `inference-broker` service
  (`requires: [inference]`), a database with a `migrate` hook
  (`requires: [postgres]`), a configuration schema, a secret, a restricted
  content package, a CLI for researchers, and the training jobs `tune` and
  `tunecpu` (`requires: [gpu]`, image pinned by `@sha256:` digest).
- Read names and addresses from the files infra writes for the platform
  instead of hard-coding them, as `refplat` does:
  [Principals and endpoints files](../reference/platform-files.md).
- Run a training job and serve its adapter:
  [How to run a training job and serve its adapter](../how-to/run-training-job.md).
- Replace `miniplat` with another platform or remove it:
  [How to replace or purge a platform](../how-to/replace-platform.md).
- See what infra's tests check on the reference platform:
  [How to test the distributions](../how-to/test-distributions.md#what-the-reference-platform-stages-check).
