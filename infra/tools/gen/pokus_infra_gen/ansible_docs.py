"""Documentation generated from the Ansible data.

* ``docs/reference/ansible-variables.md``: every lab-wide variable of
  ``group_vars/all/main.yml`` (described by its comment) and every role
  variable (from ``meta/argument_specs.yml``, DOCS_SPEC §A.5).
* ``docs/reference/component-sources.md``: the component source matrix
  (BUILD_PROMPT §19.4) with the repositories and what is still unverified.
* ``docs/reference/accounts.md``: groups and service accounts (BUILD_PROMPT §6.1).
* The layout table in ``docs/architecture.md`` between the
  ``generated:layout`` markers, from ``pokus_layout`` (BUILD_PROMPT §6.2).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from pokus_infra_gen import pages
from pokus_infra_gen.sources import load_yaml

GROUP_VARS = "ansible/group_vars/all/main.yml"
ROLES = "ansible/roles"
SOURCES = "ansible/component-sources.yml"
ARCHITECTURE = "docs/architecture.md"
LAYOUT_START = "<!-- generated:layout:start -->"
LAYOUT_END = "<!-- generated:layout:end -->"
KEY_RE = re.compile(r"^(?P<key>[a-z_][a-z0-9_]*):(?P<rest>.*)$")
TRAILING_COMMENT_RE = re.compile(r"\s#\s?(?P<text>.*)$")
# Variables passed by bootstrap.sh as extra variables, not defined in any vars file.
BOOTSTRAP_VARS = {"host_profile", "pokus_bootstrap", "machine"}
INLINE_DEFAULT_MAX = 80
ROLE_SHORT_DESCRIPTIONS = "short_description"


def group_var_docs(root: Path) -> dict[str, str]:
    """Describe every top-level key of group_vars/all/main.yml from its comment.

    The description is the comment at the end of the key's line, else the
    comment block directly above it (section rules ``# ---`` excluded).

    Args:
        root: Component root.

    Returns:
        Key to description, in file order.

    Raises:
        ValueError: If a key has no description.
    """
    docs: dict[str, str] = {}
    pending: list[str] = []
    for line in (root / GROUP_VARS).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            pending = []
            continue
        if line.startswith("#"):
            text = line[1:].strip()
            pending = [] if text.startswith("---") else [*pending, text]
            continue
        match = KEY_RE.match(line)
        if match:
            trailing = TRAILING_COMMENT_RE.search(match.group("rest"))
            docs[match.group("key")] = trailing.group("text") if trailing else " ".join(pending)
        pending = []
    missing = sorted(key for key, text in docs.items() if not text)
    if missing:
        raise ValueError(f"{GROUP_VARS}: undocumented keys: {', '.join(missing)}")
    return docs


def _text(text: str) -> str:
    """Escape angle brackets of placeholders such as ``<family>`` in prose."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _default(value: Any) -> str:  # noqa: ANN401 - YAML value
    """Render a default value: inline code when short, a YAML block otherwise."""
    if isinstance(value, (dict, list)) and value:
        text = yaml.safe_dump(value, default_flow_style=None, sort_keys=False, width=88)
        if "\n" in text.rstrip("\n") or len(text) > INLINE_DEFAULT_MAX:
            block = ["```yaml", *text.rstrip("\n").splitlines(), "```"]
            # Indented so that the block stays inside the list item.
            return "\n\n" + "\n".join(f"    {line}" for line in block)
        return f" `{text.strip()}`"
    if isinstance(value, bool):
        return f" `{str(value).lower()}`"
    if value is None:
        return " `null`"
    if value in ("", [], {}):
        return f" `{yaml.safe_dump(value, default_flow_style=True).strip()}`"
    return f" `{value}`"


def role_names(root: Path) -> list[str]:
    """List the roles.

    Args:
        root: Component root.

    Returns:
        Role names, sorted.
    """
    return sorted(p.name for p in (root / ROLES).iterdir() if p.is_dir())


def argument_specs(root: Path, role: str) -> dict[str, Any]:
    """Load a role's argument specs.

    Args:
        root: Component root.
        role: Role name.

    Returns:
        Entry point name to spec.
    """
    specs: dict[str, Any] = load_yaml(root / ROLES / role / "meta" / "argument_specs.yml")[
        "argument_specs"
    ]
    return specs


def _inputs_table(inputs: dict[str, dict[str, Any]], lab_wide: dict[str, str]) -> str:
    rows = []
    for name, option in sorted(inputs.items()):
        if name in lab_wide:
            where = f"[lab-wide](#{name})"
        elif name in BOOTSTRAP_VARS:
            where = "bootstrap.sh"
        else:
            where = "host_vars"
        rows.append([f"`{name}`", where, option["description"]])
    return pages.table(["Variable", "Set by", "Description"], rows)


def _variable_entry(
    name: str, option: dict[str, Any], defaults: dict[str, Any], entry_points: list[str] | None
) -> str:
    out = [f"### `{name}`\n\n{_text(option['description'])}\n\n- Type: `{option['type']}`"]
    if option.get("elements"):
        out.append(f" of `{option['elements']}`")
    out.append("\n")
    if option.get("choices"):
        out.append("- Choices: " + ", ".join(f"`{c}`" for c in option["choices"]) + "\n")
    if name in defaults:
        out.append(f"- Default:{_default(defaults[name])}\n")
    elif option.get("required"):
        out.append("- Required, no default\n")
    if entry_points:
        out.append("- Entry points: " + ", ".join(f"`{e}`" for e in entry_points) + "\n")
    return "".join(out) + "\n"


def _role_section(root: Path, role: str, lab_wide: dict[str, str]) -> list[str]:
    specs = argument_specs(root, role)
    defaults = load_yaml(root / ROLES / role / "defaults" / "main.yml") or {}
    owned: dict[str, tuple[dict[str, Any], list[str]]] = {}
    inputs: dict[str, dict[str, Any]] = {}
    for entry_point, entry in specs.items():
        for name, option in (entry.get("options") or {}).items():
            if name.startswith(f"{role}_"):
                owned.setdefault(name, (option, []))[1].append(entry_point)
            else:
                inputs.setdefault(name, option)
    summaries = ", ".join(f"`{e}` ({specs[e][ROLE_SHORT_DESCRIPTIONS].rstrip('.')})" for e in specs)
    out = [
        f"## {role}\n\n",
        f"{_text(specs['main'][ROLE_SHORT_DESCRIPTIONS])}\n\n",
        f"Entry points: {summaries}. Role README: `ansible/roles/{role}/README.md`.\n\n",
    ]
    if inputs:
        out += [
            "Variables the role reads from elsewhere:\n\n",
            _inputs_table(inputs, lab_wide),
            "\n",
        ]
    for name, (option, entry_points) in owned.items():
        out.append(
            _variable_entry(name, option, defaults, entry_points if len(specs) > 1 else None)
        )
    return out


def ansible_variables_page(root: Path) -> str:
    """Render ``docs/reference/ansible-variables.md``.

    Args:
        root: Component root.

    Returns:
        Page text.
    """
    lab_wide = group_var_docs(root)
    values = load_yaml(root / GROUP_VARS)
    out = [
        pages.front_matter(
            "Ansible variables",
            ["operator", "developer"],
            "Every lab-wide and role variable of the provisioning play, with defaults.",
        ),
        "# Ansible variables\n\n",
        "Set per machine in `ansible/inventory/host_vars/<machine>.yml` (start from the "
        "commented `example-machine.yml`); secrets go into the Ansible Vault file "
        "`ansible/group_vars/all/vault.yml`. Lab-wide defaults live in "
        f"`{GROUP_VARS}`; role variables are defined by each role's "
        "`meta/argument_specs.yml`, which Ansible also uses to validate them. Host facts "
        "such as `pokus_family` are not variables: the `platform_facts` role derives them "
        "from the host profile.\n\n",
        "## Lab-wide variables\n\n",
    ]
    for name, text in lab_wide.items():
        out.append(f"### `{name}`\n\n{_text(text)}\n\n- Default:{_default(values[name])}\n\n")
    for role in role_names(root):
        out += _role_section(root, role, lab_wide)
    return "".join(out).rstrip("\n") + "\n"


def ansible_variable_entries(root: Path) -> list[tuple[str, str, str]]:
    """List the coverage entries for the Ansible variables page.

    Args:
        root: Component root.

    Returns:
        ``(label, page, needle)`` triples.
    """
    page = "docs/reference/ansible-variables.md"
    entries = [(f"group_vars key {k}", page, f"### `{k}`") for k in group_var_docs(root)]
    for role in role_names(root):
        for entry_point, entry in argument_specs(root, role).items():
            entries += [
                (f"role {role} ({entry_point}) variable {name}", page, f"`{name}`")
                for name in entry.get("options") or {}
            ]
    return entries


def component_sources_page(root: Path) -> str:
    """Render ``docs/reference/component-sources.md``.

    Args:
        root: Component root.

    Returns:
        Page text.
    """
    data = load_yaml(root / SOURCES)
    out = [
        pages.front_matter(
            "Component sources",
            ["operator", "developer"],
            "Where each host component comes from on each distribution, and what is unverified.",
        ),
        "# Component sources\n\n",
        f"Generated from `{SOURCES}` (BUILD_PROMPT §19.4). A source group is the "
        "distribution itself, or for derivatives their parent: `rhel_like` covers RHEL, "
        "AlmaLinux, Rocky Linux, CentOS Stream and Oracle Linux, `opensuse` covers Leap, "
        "Tumbleweed and SLES. `default` applies to every group without its own row. Package "
        "versions are pinned per release in [Pinned versions](versions.md); repository keys "
        "are fetched and checked against the fingerprints listed there. Entries marked "
        "*unverified* are refused by the play until someone verifies them.\n\n",
    ]
    for component, groups in data["components"].items():
        out.append(f"## {component}\n\n")
        rows = []
        for group, entry in groups.items():
            details = {k: v for k, v in entry.items() if k in ("channel", "method", "tier")}
            rows.append(
                [
                    f"`{group}`",
                    entry["source"],
                    f"`{entry['repo']}`" if entry.get("repo") else None,
                    entry["packages"],
                    details or None,
                    _status(entry),
                ]
            )
        headers = ["Group", "Source", "Repository", "Packages", "Details", "Status"]
        out += [pages.table(headers, rows), "\n"]
    out.append("## Repositories\n\n")
    rows = []
    for name, repo in data["repositories"].items():
        if repo["type"] == "apt":
            where = f"`{repo['uri']}` suite `{repo['suite']}`"
            if repo["components"]:
                where += " components " + ", ".join(f"`{c}`" for c in repo["components"])
        else:
            where = f"`{repo['baseurl']}`"
        rows.append([f"`{name}`", repo["type"], where, f"`{repo['key']}`", _status(repo)])
    out.append(pages.table(["Repository", "Type", "Location", "Key", "Status"], rows))
    out.append(
        "\nPlaceholders: `{distro}` is the distribution ID (the parent's for derivatives), "
        "`{codename}` the parent release codename, `{major}` the major release, "
        "`{deb_arch}` `amd64` or `arm64`; `$basearch` is expanded by dnf and zypper.\n"
    )
    return "".join(out)


def _status(entry: dict[str, Any]) -> str:
    verify = entry.get("verify")
    if not verify:
        return "verified"
    return "unverified: " + str(verify).split(":", 1)[1].strip()


def accounts_page(root: Path) -> str:
    """Render ``docs/reference/accounts.md``.

    Args:
        root: Component root.

    Returns:
        Page text.
    """
    data = load_yaml(root / GROUP_VARS)
    groups = [[f"`{g['name']}`", g["system"], g["description"]] for g in data["pokus_groups"]]
    accounts = [
        [f"`{a['name']}`", a["description"], ", ".join(f"`{g}`" for g in a["groups"])]
        for a in data["service_accounts"]
    ]
    return "".join(
        [
            pages.front_matter(
                "Accounts and groups",
                ["operator", "developer"],
                "The groups and accounts the users_groups role creates.",
            ),
            "# Accounts and groups\n\n",
            f"Generated from `{GROUP_VARS}` (BUILD_PROMPT §6.1); the directories they own "
            "are in the [layout table](../architecture.md#directory-layout). How to change "
            "the researcher list: "
            "[How to manage researchers](../how-to/manage-researchers.md).\n\n",
            "## Groups\n\n",
            pages.table(["Group", "System group", "Purpose"], groups),
            "\n## Researchers\n\n",
            "Every researcher in `researchers` is a member of `pokus` (and, once Incus is "
            "installed, of Incus's restricted per-user group); admins are also in "
            "`pokus-admin`, which may use sudo (`/etc/sudoers.d/90-pokus-admin`). "
            "No researcher is added to `docker` or `incus-admin`; an admin listed in "
            "`break_glass_docker` is the only exception. Researchers in "
            "`researchers_removed` are locked, never deleted.\n\n",
            "## Service accounts\n\n",
            f"System users with the shell `/usr/sbin/nologin`, home "
            f"`{data['pokus_service_home']}/<account>`, no password and no SSH keys. "
            "Each account's primary group is a system group of the same name, which "
            "the contract uses for socket and password-file ownership.\n\n",
            pages.table(["Account", "Runs", "Groups"], accounts),
        ]
    )


def layout_table(root: Path) -> str:
    """Render the directory layout table (system paths, then the research space).

    Args:
        root: Component root.

    Returns:
        Table text.
    """
    data = load_yaml(root / GROUP_VARS)
    base = data["pokus_root"]
    rows = []
    for entry in data["pokus_system_layout"]:
        rows.append(
            [
                f"`{entry['path']}/`",
                f"{entry['owner']}:{entry['group']}",
                entry["mode"],
                None,
                entry["notes"],
            ]
        )
    for entry in data["pokus_layout"]:
        path = f"{base}/{entry['path']}/" if entry["path"] else f"{base}/"
        acl = entry.get("default_acl")
        rows.append(
            [f"`{path}`", f"{entry['owner']}:{entry['group']}", entry["mode"], acl, entry["notes"]]
        )
        if entry["path"] == "users":
            user = [f"{base}/users/<name>/", "<name>:<name>", data["pokus_layout_user_mode"]]
            rows.append([*user, None, "one per researcher"])
    return pages.table(["Path", "Owner:group", "Mode", "Default ACL", "Notes"], rows)


def architecture_page(root: Path) -> str:
    """Return ``docs/architecture.md`` with a freshly generated layout table.

    Args:
        root: Component root.

    Returns:
        Page text.

    Raises:
        ValueError: If the page lacks the layout markers.
    """
    text = (root / ARCHITECTURE).read_text(encoding="utf-8")
    head, sep, rest = text.partition(LAYOUT_START + "\n")
    _, sep2, tail = rest.partition(LAYOUT_END)
    if not sep or not sep2:
        raise ValueError(f"{ARCHITECTURE}: missing {LAYOUT_START} / {LAYOUT_END}")
    return f"{head}{LAYOUT_START}\n{pages.GENERATED_NOTE}\n\n{layout_table(root)}{LAYOUT_END}{tail}"
