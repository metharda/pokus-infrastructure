---
title: How to move a lab port or the VM collector address
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Give a lab listener another port, or the VM targets' collector another address, when another program on the machine already uses it.
---

# How to move a lab port or the VM collector address

Preflight stops bootstrap with exit code 30 when another program already
listens on one of the lab's ports (`lab_port_in_use`) or already uses the
address of the collector listener for VM targets
(`vm_collector_address_conflict`). Move the lab's listener in the machine's
host_vars instead of stopping the other program.

!!! note "Prerequisites"
    - The machine's `ansible/inventory/host_vars/<machine>.yml`.
    - The preflight summary that names the port or the address.

## Move a port

1. Pick a free port for the listener preflight named: one that no program on
   the machine listens on and that is not another lab port.

2. Add it to `ports:` in the machine's host_vars, by the listener's name in
   [contracts.md](../contracts.md#ports) (`grafana`, `otlp_grpc`, `vllm`,
   ...). Write the mapping as indented lines or on one line; bootstrap reads it
   before Ansible runs and accepts no other form:

    <!-- qa:skip reason="example host_vars excerpt" -->
    ```yaml
    ports:
      grafana: 18750
    ```

3. Run bootstrap again. Preflight now checks the new port; the play rejects an
   unknown name, a number outside 1-65535 and a port another lab listener
   uses. Platform services read the effective ports from
   `/etc/pinka/platform/endpoints.toml`, so they follow without a change.

Researchers' SSH port forwards use the new port too (for Grafana:
`ssh -L 18750:127.0.0.1:18750 ...`).

## Move the VM collector address

1. Pick an IPv4 address that no interface, route, Docker address pool or
   Incus network on the machine uses and that is outside the tailnet range
   `100.64.0.0/10`; addresses of the benchmarking range `198.18.0.0/15` are
   meant for this.

2. Set it in the machine's host_vars:

    <!-- qa:skip reason="example host_vars excerpt" -->
    ```yaml
    vm_collector_address: 198.19.0.1
    ```

3. Run bootstrap again. VM targets send their telemetry to the new address,
   which `endpoints.toml` reports as `vm_collector_grpc` and
   `vm_collector_http`.

## Verify the new addresses

Preflight's summary lists no `lab_port_in_use` or
`vm_collector_address_conflict` action, and `pinka doctor` passes.
