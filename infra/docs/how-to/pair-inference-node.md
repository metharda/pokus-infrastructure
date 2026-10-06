---
title: How to run inference on a separate GPU node
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Provision a lab node and an inference node, pair them with a one-time code, and check that the lab node reaches vLLM over mutual TLS.
---

# How to run inference on a separate GPU node

A lab host without a suitable GPU can use vLLM on another Pokus host: the lab
node (`node_role: lab`) runs everything except vLLM, the inference node
(`node_role: inference`) runs only vLLM, its exporters and the control
endpoint, and forwards its telemetry to the lab node. The two nodes trust
each other's certificate after a one-time pairing over the tailnet.
[Node roles](../node-roles.md) explains the design and what it means for
measurements.

!!! note "Prerequisites"
    - Two hosts on the same tailnet with the same `lab_mode`; staging and
      production nodes never pair.
    - On the inference node, preflight reports the `gpu_inference` feature.
    - An admin account on both hosts (member of `pokus-admin`).
    - The lab's private configuration (host_vars files) for both hosts.

## Provision both nodes

1. In the inference node's host_vars file, set its role and its default
   deployment ([How to add a model](add-a-model.md)):

    <!-- qa:skip reason="example of a host_vars file" -->
    ```yaml
    node_role: inference
    vllm_profile: ada-12g
    vllm_default_deployment:
      model_id: <model-id>
      start_after_install: true
    ```

    `<model-id>` is the model's id in `models.lock.yml`.

2. In the lab node's host_vars file, set `node_role: lab`.

3. Run bootstrap on each node as usual. Each node gets its identity
   certificate in `/etc/pokus/inference/`; the inference node installs
   `pokus-inference-control.service`, which stays stopped until the node is
   paired.

4. If the tailnet policy restricts traffic between tagged nodes, allow the
   split-inference ports between lab nodes in the Tailscale admin console:

    <!-- qa:skip reason="edited in the Tailscale admin console" -->
    ```json
    {
      "action": "accept",
      "src": ["tag:pokus-lab"],
      "dst": ["tag:pokus-lab:18704,18710,4317"]
    }
    ```

    Use `tag:pokus-qa` for staging nodes. Ports 18704 (vLLM API) and 18710
    (control) are on the inference node, 4317 (telemetry) on the lab node.
    The firewall of each node opens them on `tailscale0` only, and mutual
    TLS admits only the paired node.

## Pair the nodes

1. On the inference node, show a pairing code:

    <!-- qa:skip reason="waits for a second node" -->
    ```bash
    sudo pokus-lab pair show
    ```

    It stops the control endpoint, prints a code such as `ABCD-EFGH-JKMN`
    and waits up to 600 seconds (`--timeout`) on the tailnet address.

2. On the lab node, enter the inference node's tailnet name and the code:

    <!-- qa:skip reason="needs the code shown on the inference node" -->
    ```bash
    sudo pokus-lab pair connect <inference-node> <code>
    ```

    `<inference-node>` is the inference node's tailnet name and `<code>` the
    code it shows.
    Both nodes exchange their certificates over TLS and prove the code with
    an HMAC over both certificates' fingerprints, so a certificate swapped in
    between is detected. Each node writes the other's certificate to
    `/etc/pokus/inference/peer.crt` and a record to `peer.json`. Five wrong
    attempts end the pairing on the inference node; start again with a new
    code.

3. Name the peers in the host_vars files: on the inference node
   `inference_lab_node: <lab-node>`, on the lab node
   `inference_remote_node: <inference-node>`.

4. Apply it on the inference node:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags inference_control,observability,firewall
    ```

    `<machine>` is the name of the inference node's host_vars file.

5. Apply it on the lab node:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags inference_control,platform,observability,firewall
    ```

    `<machine>` is the name of the lab node's host_vars file.
    The lab node's collector now accepts the inference node's telemetry on
    port 4317 of its tailnet address; the platform, if enabled, uses the
    remote inference mode.

## Pair again or remove the pairing

- To pair with another node, or after a certificate was renewed, run both
  pairing commands again; they replace `peer.crt` and `peer.json`. Update
  the host_vars and apply them as above.
- To remove the pairing, delete `/etc/pokus/inference/peer.crt` and `peer.json` on both
  nodes, remove `inference_lab_node` or `inference_remote_node` from the
  host_vars and apply them. The control endpoint does not start without
  `peer.crt`, and an unpaired inference node sends its telemetry nowhere.

## Verify the pairing

1. Run doctor on both nodes; `inference.pairing` must pass:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pokus-lab doctor
    ```

2. On the lab node, ask the inference node's control endpoint for its health
   with the lab node's client certificate:

    <!-- qa:skip reason="needs a paired inference node" -->
    ```bash
    sudo curl --silent --cert /etc/pokus/inference/self.crt --key /etc/pokus/inference/self.key \
      --cacert /etc/pokus/inference/peer.crt https://<inference-node>:18710/health
    ```

    `<inference-node>` is the inference node's tailnet name. The answer names
    the active deployment and the inference node's fingerprint hash:

    <!-- qa:skip reason="example output" -->
    ```text
    {"deployment": {"id": "default-ada-12g", "model_id": "<model-id>", "profile": "ada-12g", "version": "1"}, "fingerprint": {"computed_at": "2026-01-01T00:00:00Z", "hash": "<hash>"}, "ok": true, "vllm_status": 200}
    ```

    `<model-id>` is the served model's id and `<hash>` the inference node's
    fingerprint hash, which runs record as `inference_fingerprint_hash`.

    The control endpoint also answers `GET /model` (the served model and its
    verified `MANIFEST.json`), `GET /metrics`, `POST /activate` with
    `{"deployment": "<id>@<version>"}` and `POST /verify`; nothing else.
    Deployment files live on the inference node, in
    `/etc/pokus/vllm/deployments/`.

3. In Grafana on the lab node, the inference node's metrics and logs carry
   the label `pokus_machine="<inference-node>"`.
