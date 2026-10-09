# inference_control

Split inference (BUILD_PROMPT §19.10, contracts.md#inference); tag
`inference_control`. Runs on `node_role: lab` and `node_role: inference`
only; a `full` node serves vLLM locally and needs none of it.

On both node roles the role creates the node's identity in
`/etc/pinka/inference/` (directory 0750 root:potka-orch): an ECDSA P-256 key
`self.key` (0640) and a self-signed certificate `self.crt` (0644) whose
subject alternative name is the tailnet name (`machine_name`), valid for
`inference_control_certificate_days`. `pinka pair show` (inference node)
and `pinka pair connect` (lab node) exchange the certificates over the
tailnet with a one-time code and write `peer.crt` and `peer.json`; each node
then trusts exactly the other's certificate. The role checks that
`inference_lab_node` or `inference_remote_node`, once set, names the node in
`peer.json`.

On a lab node the LLM gateway (`potka-gw`) gets read access to the client key
through POSIX ACL entries (`u:potka-gw:r-x` on the directory, `u:potka-gw:r--`
on `self.key`); the orchestrator reads it as a member of `potka-orch`.

On an inference node the role installs `pinka-inference-control.service`
(user `potka-orch`, hardened), which listens on the node's tailnet address
(`tailscale ip -4`, recorded in `control.json`) with mutual TLS 1.3 and
accepts only the paired lab node's certificate:

- port 18704 forwards the connection to vLLM on 127.0.0.1;
- port 18710 answers `GET /health`, `GET /model`, `GET /metrics`,
  `POST /activate` (through the vLLM deploy helper) and `POST /verify`
  (the serving verification); nothing else.

The unit starts only once `peer.crt` exists. The firewall opens both ports on
`tailscale0` only; the tailnet policy should allow them only from the lab
node (docs/node-roles.md).

## Variables

See `meta/argument_specs.yml`, and `inference_lab_node` and
`inference_remote_node` in `group_vars/all/main.yml`.

## Feature flags

An inference node needs `gpu_inference`.

## Idempotency

The key is created once and the certificate is reissued only when it no
longer matches the key or the tailnet name; either change means pairing
again. The endpoint restarts only when its program, settings or unit change.
