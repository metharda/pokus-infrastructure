"""Tests for the distribution-fact check (BUILD_PROMPT §19.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pinka_repo_checks import cli, portability


@pytest.mark.parametrize(
    ("text", "fact"),
    [
        ("when: ansible_distribution == 'Ubuntu'", "distribution"),
        ("when: ansible_distribution_major_version | int >= 9", "distribution_major_version"),
        ("when: ansible_os_family == 'RedHat'", "os_family"),
        ("{{ ansible_pkg_mgr }}", "pkg_mgr"),
        ("{{ ansible_lsb.codename }}", "lsb"),
        ("when: ansible_facts.distribution == 'Fedora'", "distribution"),
        ("when: ansible_facts['os_family'] == 'Suse'", "os_family"),
        ('when: ansible_facts[ "pkg_mgr" ] == "apt"', "pkg_mgr"),
        ("{{ hostvars[inventory_hostname]['ansible_facts']['distribution'] }}", "distribution"),
        ("# when: ansible_distribution == 'Debian'", "distribution"),
    ],
)
def test_flags_distribution_facts(text: str, fact: str) -> None:
    findings = portability.scan_text(Path("roles/x/tasks/main.yml"), text)
    assert [f.fact for f in findings] == [fact]
    assert findings[0].line == 1


@pytest.mark.parametrize(
    "text",
    [
        "when: pinka_family == 'debian'",
        "{{ ansible_facts['architecture'] }}",
        "{{ ansible_facts.getent_group['docker'] }}",
        "{{ ansible_facts['kernel'] }}",
        "my_ansible_distribution_note: x",
        "pinka_distro: '{{ platform_facts_identity.distro }}'",
    ],
)
def test_allows_other_facts(text: str) -> None:
    assert portability.scan_text(Path("a.yml"), text) == []


def make_tree(root: Path) -> None:
    for relative, text in {
        "site.yml": "- hosts: all\n",
        "roles/base/tasks/main.yml": "- name: x\n  when: ansible_os_family == 'Debian'\n",
        "roles/base/templates/a.conf.j2": "{{ ansible_facts.distribution }}\n",
        "roles/base/README.md": "Never use ansible_distribution.\n",
        "roles/platform_facts/tasks/main.yml": "x: '{{ ansible_distribution }}'\n",
        "filter_plugins/pinka.py": "# ansible_os_family is mapped here\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_scan_skips_allowed_places_and_other_file_types(tmp_path: Path) -> None:
    make_tree(tmp_path)
    findings = portability.scan(tmp_path)
    assert [(f.path.relative_to(tmp_path).as_posix(), f.line) for f in findings] == [
        ("roles/base/tasks/main.yml", 2),
        ("roles/base/templates/a.conf.j2", 1),
    ]


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_tree(tmp_path)
    assert cli.main(["portability", "--ansible-dir", str(tmp_path)]) == 1
    out = capsys.readouterr()
    assert "use the pinka_* facts of platform_facts instead" in out.out
    assert "portability: 2 finding(s)" in out.err
    (tmp_path / "roles/base/tasks/main.yml").write_text("- name: x\n", encoding="utf-8")
    (tmp_path / "roles/base/templates/a.conf.j2").write_text("{{ pinka_distro }}\n", "utf-8")
    assert cli.main(["portability", "--ansible-dir", str(tmp_path)]) == 0
    assert cli.main(["portability", "--ansible-dir", str(tmp_path / "missing")]) == 2


def test_repository_ansible_tree_is_clean() -> None:
    root = Path(__file__).resolve().parents[3] / "ansible"
    assert portability.scan(root) == []
