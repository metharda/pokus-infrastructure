"""The schema validators read a pattern's "$" as JSON Schema does.

Python's ``$`` also matches before a final newline; each validator translates
the patterns (``end_anchored``) so that ``^...$`` matches the whole value only.
The copies of the translation (preflight and the platform tool are standard
library only and installed apart; the installer imports the platform tool's)
are kept equal here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pinka_installer import answers
from pinka_lockcheck import checks as lockcheck
from pinka_platform import schema as platform_schema
from pinka_preflight import schema_check
from pinka_repo_checks import docs_lint

ROOT = Path(__file__).resolve().parents[3]
COPIES = {
    "preflight": schema_check.end_anchored,
    "platform": platform_schema.end_anchored,
    "lockcheck": lockcheck.end_anchored,
    "docs_lint": docs_lint.end_anchored,
}  # the installer's answers check uses the platform tool's (tested through its Validator)
# (pattern, value, whether it matches: a class as Python's re reads it, "$" as JSON
# Schema does; no schema uses an empty class "[]" or "[^]", where ECMA 262 differs)
CASES = [
    ("^[a-z]+$", "ab", True),
    ("^[a-z]+$", "ab\n", False),
    ("^[a-z][a-z0-9-]{0,39}[a-z0-9]$", "base\n", False),
    ("^/", "/a\n", True),  # anchored at the start only: a prefix
    ("^a$|^b$", "b\n", False),
    ("^[$]$", "$", True),  # "$" in a class is a character
    ("^[$]$", "$\n", False),
    ("^a\\$$", "a$", True),  # an escaped "$" is a character
    ("^a\\$$", "a$\n", False),
    ("^[]$]$", "]", True),  # "]" first in a class is a character
    ("^[^]$]$", "x", True),
    ("^[^]$]$", "x\n", False),
    ("^x\\\\$", "x\\", True),  # an escaped backslash, then the end
    ("^x\\\\$", "x\\\n", False),
]


def schema_patterns() -> set[str]:
    """Every pattern and patternProperties key of the repository's JSON Schemas."""
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "pattern" and isinstance(value, str):
                    found.add(value)
                elif key == "patternProperties" and isinstance(value, dict):
                    found.update(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for path in ROOT.rglob("*.schema.json"):
        if not {".venv", "node_modules", "build", ".ansible-collections"} & set(path.parts):
            walk(json.loads(path.read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize("name", sorted(COPIES))
def test_every_copy_reads_dollar_at_the_end_of_the_value_only(name: str) -> None:
    translate = COPIES[name]
    for pattern, value, matches in CASES:
        assert bool(re.search(translate(pattern), value)) is matches, (pattern, value)


def test_the_copies_are_equal_on_every_schema_pattern() -> None:
    patterns = sorted(schema_patterns() | {p for p, _value, _m in CASES})
    assert len(patterns) > 50
    for pattern in patterns:
        translated = {name: copy(pattern) for name, copy in COPIES.items()}
        assert len(set(translated.values())) == 1, (pattern, translated)
        re.compile(translated["preflight"])


SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string", "pattern": "^[a-z]+$"}},
    "patternProperties": {"^x-[a-z]+$": {"type": "string"}},
    "additionalProperties": False,
}


def test_preflight_s_validator() -> None:
    schema = {k: v for k, v in SCHEMA.items() if k != "patternProperties"}
    assert schema_check.validate({"name": "ab"}, schema) == []
    assert schema_check.validate({"name": "ab\n"}, schema) == ["$.name: does not match '^[a-z]+$'"]


def test_the_platform_tool_s_validator() -> None:
    schema = {k: v for k, v in SCHEMA.items() if k != "patternProperties"}
    assert list(platform_schema.errors({"name": "ab"}, schema)) == []
    assert [m for _p, m in platform_schema.errors({"name": "ab\n"}, schema)] == [
        "'ab\\n' does not match ^[a-z]+$"
    ]


@pytest.mark.parametrize(
    ("validator", "name"),
    [
        (lockcheck.Validator, "lockcheck"),
        (docs_lint.Validator, "docs_lint"),
        (answers.Validator, "installer"),
    ],
)
def test_the_jsonschema_validators(validator: type, name: str) -> None:
    def messages(doc: object) -> list[str]:
        return sorted(e.message for e in validator(SCHEMA).iter_errors(doc))

    assert messages({"name": "ab", "x-y": "z"}) == []
    assert messages({"name": "ab\n"}) == ["'ab\\n' does not match '^[a-z]+$'"]
    # A key with a final newline matches no patternProperties key: an extra property.
    assert messages({"x-y\n": "z"}) == ["'x-y\\n' does not match any of the regexes: '^x-[a-z]+$'"]
    # Nor does its subschema apply to it: only the extra property is reported.
    assert messages({"x-y\n": 5}) == ["'x-y\\n' does not match any of the regexes: '^x-[a-z]+$'"]
    plain = {k: v for k, v in SCHEMA.items() if k != "patternProperties"}
    extra = [e.message for e in validator(plain).iter_errors({"x": 1, "y": 2})]
    assert extra == ["Additional properties are not allowed ('x', 'y' were unexpected)"]


def test_lockcheck_s_schema_findings(tmp_path: Path) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
    assert lockcheck.schema_findings({"name": "ab"}, schema) == []
    assert [f.message for f in lockcheck.schema_findings({"name": "ab\n"}, schema)] == [
        "'ab\\n' does not match '^[a-z]+$'"
    ]


def test_the_installer_s_answers_check() -> None:
    data = answers.Answers().to_dict()
    data["images"]["build"] = ["base\n"]
    problems = answers.schema_problems(data)
    assert any("'base\\n' does not match" in p for p in problems), problems


def test_the_docs_lint_checks_front_matter_with_its_validator() -> None:
    schema = docs_lint.load_schema()
    good = {"date": "2026-11-11", "rc": "rc1", "by": "researcher1"}
    for date, refused in (("2026-11-11", False), ("2026-11-11\n", True)):
        issues: list[docs_lint.Issue] = []
        front = {"title": "x", "last_verified": {**good, "date": date}}
        page = docs_lint.Page(Path("docs/x.md"), front, 0, [])
        docs_lint.validate_front_matter(page, schema, issues)
        found = [
            i.message for i in issues if i.code == "FM003" and "last_verified/date" in i.message
        ]
        assert bool(found) is refused, (date, [i.message for i in issues])


def test_no_schema_combines_pattern_properties_with_unevaluated_properties() -> None:
    # The validators read "$" as JSON Schema does in pattern, patternProperties and
    # additionalProperties; jsonschema's unevaluatedProperties matches patternProperties
    # keys with re.search itself, so a schema combining the two would disagree there.
    def walk(node: object, where: str) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            if "patternProperties" in node and "unevaluatedProperties" in node:
                found.append(where)
            for key, value in node.items():
                found += walk(value, f"{where}/{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                found += walk(item, f"{where}/{i}")
        return found

    combined = []
    for path in ROOT.rglob("*.schema.json"):
        if not {".venv", "node_modules", "build", ".ansible-collections"} & set(path.parts):
            combined += walk(json.loads(path.read_text(encoding="utf-8")), str(path))
    assert combined == []
