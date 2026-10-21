# Security policy

## Supported versions

pokus-infrastructure is pre-release: no version has been released yet, and
fixes go to the default branch only.

## Reporting a vulnerability

Report it privately with GitHub's private vulnerability reporting: on the
repository's **Security** tab, choose **Report a vulnerability**. Do not open
a public issue, pull request or discussion for it.

Include what is affected (the component, role or file), how to reproduce it
and what an attacker gains. Leave out data from a real lab: hostnames,
addresses, account names, keys and logs that name them. The maintainer
answers in the private advisory, and the fix is published with a GitHub
security advisory.

## Scope

In scope: everything in this repository, among it bootstrap, preflight, the
Ansible roles, the `pinka` tool, the installer, the platform deployment
tools and the platform contract. The threat model and the security
boundaries are in [docs/security.md](docs/security.md).

Out of scope: a vulnerability in an upstream component that pinka installs
or pins (Incus, Docker, Tailscale, vLLM, the NVIDIA driver and others):
report it to that project.
