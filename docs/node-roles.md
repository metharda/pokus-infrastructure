---
title: Node roles and split inference
type: explanation
audience: [operator, researcher]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: What a full, lab and inference node runs, how a lab node and an inference node pair and talk over the tailnet, and what split inference means for measurements.
---

# Node roles and split inference

Most labs run on one host (`node_role: full`). A host without a suitable GPU
can still be a lab host if another Pokus host serves the model: the lab node
(`node_role: lab`) runs everything except vLLM, and the inference node
(`node_role: inference`) runs only the inference stack. There are no external
model APIs in either case: an inference node is a pinka-provisioned machine
you control.
[How to run inference on a separate GPU node](how-to/pair-inference-node.md)
has the steps.

## What runs where

| | full | lab | inference |
| --- | --- | --- | --- |
| Base, accounts, SSH, firewall, Tailscale, Docker | yes | yes | yes |
| Incus, golden images, benchmark targets | yes | yes | no |
| PostgreSQL, OPA, the platform and its content | yes | yes | no |
| Prometheus, Loki, Tempo, Grafana | yes | yes | no; forwards to the lab node |
| Collectors, node exporter, cAdvisor | yes | yes | yes |
| GPU exporter, models, vLLM | with `gpu_inference` | no | yes (needs `gpu_inference`) |
| Inference control endpoint | no | no | yes |
| Backups, doctor, health gate, session records | yes | yes | yes |

The platform's inference mode follows the role (contracts.md, "Inference"):
`local` on a full node with `gpu_inference`, `remote` on a lab node.

## Pairing

Every lab and inference node has its own identity: an ECDSA P-256 key and a
self-signed certificate in `/etc/pinka/inference/` whose subject alternative
name is the node's tailnet name. Pairing exchanges the two certificates once:

1. `pinka pair show` on the inference node prints a one-time code of 12
   characters (60 bits) and listens on its tailnet address.
2. `pinka pair connect` on the lab node connects with TLS and sends its
   certificate, node name, `lab_mode` and an HMAC keyed with the code over
   both certificates' SHA-256 fingerprints.
3. The inference node checks the HMAC, answers with its own certificate and
   HMAC, and the lab node checks that the certificate in the answer is the
   one the TLS server presented.

Someone between the nodes who swaps in another certificate changes a
fingerprint, so the HMAC no longer matches; guessing the code online is
limited to five attempts per pairing. Nodes with different `lab_mode`
never pair, so a staging node cannot feed a production lab. Afterwards each
node trusts exactly the other's certificate (`peer.crt`) and nothing else:
there is no certificate authority that could issue a third certificate.

## Traffic between the nodes

All traffic is mutual TLS 1.3 over the tailnet, on ports the firewall opens
on `tailscale0` only:

- **18704** on the inference node: the inference control endpoint accepts
  the lab node's certificate and forwards the connection byte for byte to
  vLLM, which itself listens only on 127.0.0.1. The platform's services use
  the OpenAI-compatible API unchanged.
- **18710** on the inference node: the control API, with exactly `GET
  /health` (including the node's fingerprint hash), `GET /model` (the served
  model and the `MANIFEST.json` whose hashes the models role verified on the
  inference node), `GET /metrics` (vLLM and GPU), `POST /activate` (a
  deployment by `<id>@<version>`, as a `serve` request to the node's GPU
  arbiter) and `POST /verify` (the serving verification). Everything else is
  404.
- **4317** on the lab node: the inference node's collector sends logs,
  metrics and traces with OTLP over gRPC; the lab node's collector accepts
  only the paired certificate and labels everything with the inference node's
  name (`pinka.machine`).

On the lab node only the inference forwarder `pinka-inference-forwarder`,
which runs as root, reads the client key in `/etc/pinka/inference/`; it
offers the API and the control API on the lab node's loopback, so platform
services handle no TLS and no keys. Only the accounts of profiles with the
inference API reach the forwarder, and only the controller its control port
([Integration contract](contracts.md#inference)).

## Measurement caveats

- Latency on a split lab includes the tailnet hop between the nodes and
  depends on the network between them. Reports never compare latency across
  different inference placements (full node, split nodes, other node pairs).
- Every run records both nodes' fingerprints (`inference_fingerprint_hash`
  for the inference node, from its control endpoint), so the GPU, driver,
  vLLM image and model of the inference node are part of the run's
  provenance.
- Model weights live on the inference node; the models role verifies their
  hashes there, and `GET /model` reports the verified manifest back.
- Both nodes need synchronized clocks: telemetry from the inference node
  carries its own timestamps. Doctor's `time.sync` check runs on both.
- An unpaired inference node sends its telemetry nowhere; pair the nodes
  before measuring.
