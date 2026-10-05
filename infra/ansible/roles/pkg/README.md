# pkg

Package-manager abstraction used by every role that installs host packages
(BUILD_PROMPT §19.3, §19.4). Roles never call apt, dnf, zypper or pacman
directly; they include this role with one of three entry points:

| Entry point | Does |
| --- | --- |
| `main` | installs `pkg_packages`, pinned to `pkg_versions` (`name=version` for apt and zypper, `name-version` for dnf; pacman installs unpinned on the rolling Arch Linux) |
| `repo` | adds the vendor repository `pkg_repo_name` from `ansible/component-sources.yml`: downloads its key, checks the primary key fingerprint against `versions.lock.yml`, then installs it with a `signed-by` keyring (apt) or `rpm --import` with `gpgcheck=1` (dnf, zypper) |
| `hold` | holds `pkg_hold_packages` with the family's method: apt preferences pins in `/etc/apt/preferences.d/pokus-pins`, `dnf versionlock`, `zypper addlock`, or `IgnorePkg` in `/etc/pacman.conf` |

```yaml
- name: Install Docker Engine
  ansible.builtin.include_role:
    name: pkg
  vars:
    pkg_packages: "{{ docker_source.packages }}"
    pkg_versions: "{{ pokus_versions.packages | pokus_lock_packages('docker', pokus_release_key) }}"
```

The apt and dnf modules of ansible-core run under the pinned CPython of
`/opt/pokus/ansible-venv` and respawn under the system interpreter that has
the distribution's bindings; zypper and pacman are driven through their
command-line tools by the community.general modules.

## Variables

See `meta/argument_specs.yml` (one section per entry point).

## Idempotency

Every entry point checks the current state first: installed versions,
existing keys and repository files, and existing locks.
