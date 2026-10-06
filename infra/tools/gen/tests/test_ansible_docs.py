"""Tests for the documentation generated from the Ansible data."""

from __future__ import annotations

from pathlib import Path

import pytest

from pokus_infra_gen import ansible_docs, cli

ROOT = Path(__file__).resolve().parents[3]


def write_group_vars(root: Path, text: str) -> None:
    path = root / ansible_docs.GROUP_VARS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_group_var_docs_reads_comments(tmp_path: Path) -> None:
    write_group_vars(
        tmp_path,
        "# Managed header.\n"
        "\n"
        "# --- Section rule ---\n"
        "# Described above,\n"
        "# over two lines.\n"
        "first: 1\n"
        "second: two  # described at the end\n"
        "# Third.\n"
        "third:\n"
        "  - {a: 1}  # an item comment does not describe the next key\n"
        "# Fourth.\n"
        "fourth: {}\n",
    )
    assert ansible_docs.group_var_docs(tmp_path) == {
        "first": "Described above, over two lines.",
        "second": "described at the end",
        "third": "Third.",
        "fourth": "Fourth.",
    }


def test_group_var_docs_requires_descriptions(tmp_path: Path) -> None:
    write_group_vars(tmp_path, "# Comment separated by a blank line.\n\nlonely: 1\n")
    with pytest.raises(ValueError, match="undocumented keys: lonely"):
        ansible_docs.group_var_docs(tmp_path)


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        ("text", " `text`"),
        (True, " `true`"),
        (None, " `null`"),
        ("", " `''`"),
        ([], " `[]`"),
        ({"a": 1}, " `{a: 1}`"),
        (
            [{"path": f"p{i}", "mode": "0755"} for i in range(4)],
            "\n\n    ```yaml\n    - {path: p0, mode: '0755'}\n    - {path: p1, mode: '0755'}\n"
            "    - {path: p2, mode: '0755'}\n    - {path: p3, mode: '0755'}\n    ```",
        ),
        (
            "{{ [22]\n   + ([443] if serve else []) }}",
            "\n\n    ```jinja\n    {{ [22]\n       + ([443] if serve else []) }}\n    ```",
        ),
    ],
)
def test_default_rendering(value: object, rendered: str) -> None:
    assert ansible_docs._default(value) == rendered


def test_status_of_unverified_entries() -> None:
    assert ansible_docs._status({"source": "distro"}) == "verified"
    entry = {"verify": "TODO(verify): package names"}
    assert ansible_docs._status(entry) == "unverified: package names"


def test_architecture_page_needs_markers(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / ansible_docs.ARCHITECTURE).write_text("# Architecture\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        ansible_docs.architecture_page(tmp_path)


def test_architecture_region_is_replaced_and_stable() -> None:
    page = ansible_docs.architecture_page(ROOT)
    assert page == (ROOT / ansible_docs.ARCHITECTURE).read_text(encoding="utf-8")
    region = page.split(ansible_docs.LAYOUT_START, 1)[1].split(ansible_docs.LAYOUT_END, 1)[0]
    assert "| `/srv/pokus/catalog/` | root:pokus-gt | 0750 |" in region
    assert "| /srv/pokus/users/&lt;name&gt;/ | &lt;name&gt;:&lt;name&gt; | 0700 |" in region
    assert "| `/etc/pokus/secrets/` | root:pokus-svc | 0750 |" in region


def test_variables_page_lists_every_variable() -> None:
    page = (ROOT / "docs/reference/ansible-variables.md").read_text(encoding="utf-8")
    for _, _, needle in ansible_docs.ansible_variable_entries(ROOT):
        assert needle in page
    assert "### `firewall_tailnet_tcp_ports`" in page
    assert "| `host_profile` | bootstrap.sh |" in page


def test_coverage_includes_ansible_and_layout_entries() -> None:
    labels = [label for label, _, _ in cli.reference_entries(ROOT)]
    assert "group_vars key pokus_root" in labels
    assert "role firewall (main) variable firewall_tailnet_interface" in labels
    assert "layout path /srv/pokus/backups/" in labels


def test_component_sources_page_marks_unverified() -> None:
    page = ansible_docs.component_sources_page(ROOT)
    assert "| `tailscale-deb` | apt |" in page
    assert "unverified: suite and component" in page
    assert "TODO(verify)" not in page
