# platform_facts

Runs first and derives every distribution-specific fact from the host profile
that preflight wrote (BUILD_PROMPT §19.1). It is the only role that interprets
the distribution: no other role branches on `ansible_distribution` or similar
facts (`make lint` enforces this).

## Facts it sets

| Fact | Content |
| --- | --- |
| `pinka_family` | `debian`, `redhat`, `suse` or `archlinux` |
| `pinka_distro`, `pinka_release` | the distribution and version used for sources (the parent's for derivatives) |
| `pinka_parent_release` | parent release identity from the host profile |
| `pinka_release_key` | key of `versions.lock.yml` pins, such as `ubuntu-24.04` or `el-9`; empty on rolling releases |
| `pinka_release_identity` | all of the above plus codename, major version and source group |
| `pinka_source_group` | group in `component-sources.yml`: `ubuntu`, `debian`, `fedora`, `rhel_like`, `opensuse`, `arch` |
| `pinka_release_model` | `fixed`, `fast` or `rolling` |
| `pinka_pkg_manager` | `apt`, `dnf`, `zypper` or `pacman` |
| `pinka_service_names` | `ssh` service name and the time sync candidates |
| `pinka_initramfs_tool` | `update-initramfs`, `dracut` or `mkinitcpio` |
| `pinka_version_hold_method` | how versions are held (§19.5) |
| `pinka_provider` | `wsl2`, `baremetal` or `vm` |
| `pinka_mac` | `selinux`, `apparmor` or `none` |
| `pinka_firewall_backend` | `nftables`, `firewalld` or `ufw` |
| `pinka_repositories`, `pinka_component_sources` | the component source matrix |
| `pinka_community_repos_allowed` | consent to community repositories |
| `pinka_present` | check mode only: the systemd units (`units`), accounts (`users`) and component commands (`commands`, from `platform_facts_component_commands`) that exist when the play starts; empty lists otherwise |

## Variables

See `meta/argument_specs.yml`. Family data lives in `vars/<family>.yml`.

## Feature flags

None.

## Idempotency

Sets facts only; changes nothing on the host. In check mode it lists the unit
files, reads the account database and looks up the component commands for
`pinka_present`.
