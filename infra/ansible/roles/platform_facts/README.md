# platform_facts

Runs first and derives every distribution-specific fact from the host profile
that preflight wrote (BUILD_PROMPT §19.1). It is the only role that interprets
the distribution: no other role branches on `ansible_distribution` or similar
facts (`make lint` enforces this).

## Facts it sets

| Fact | Content |
| --- | --- |
| `pokus_family` | `debian`, `redhat`, `suse` or `archlinux` |
| `pokus_distro`, `pokus_release` | the distribution and version used for sources (the parent's for derivatives) |
| `pokus_parent_release` | parent release identity from the host profile |
| `pokus_release_key` | key of `versions.lock.yml` pins, such as `ubuntu-24.04` or `el-9`; empty on rolling releases |
| `pokus_release_identity` | all of the above plus codename, major version and source group |
| `pokus_source_group` | group in `component-sources.yml`: `ubuntu`, `debian`, `fedora`, `rhel_like`, `opensuse`, `arch` |
| `pokus_release_model` | `fixed`, `fast` or `rolling` |
| `pokus_pkg_manager` | `apt`, `dnf`, `zypper` or `pacman` |
| `pokus_service_names` | `ssh` service name and the time sync candidates |
| `pokus_initramfs_tool` | `update-initramfs`, `dracut` or `mkinitcpio` |
| `pokus_version_hold_method` | how versions are held (§19.5) |
| `pokus_provider` | `wsl2`, `baremetal` or `vm` |
| `pokus_mac` | `selinux`, `apparmor` or `none` |
| `pokus_firewall_backend` | `nftables`, `firewalld` or `ufw` |
| `pokus_repositories`, `pokus_component_sources` | the component source matrix |
| `pokus_community_repos_allowed` | consent to community repositories |

## Variables

See `meta/argument_specs.yml`. Family data lives in `vars/<family>.yml`.

## Feature flags

None.

## Idempotency

Sets facts only; changes nothing on the host.
