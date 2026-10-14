"""Contract v2 (BUILD_PROMPT §12): reserved names, the schema's lists and reference pages.

``contract/reserved-names.yml`` is generated from the reserved lists of §12.1
and from infra's own data (groups, unit templates, compose projects and
containers, Incus objects, PostgreSQL roles, the layout), so a name infra
starts using is reserved automatically. The manifest schema carries the same
lists in its ``$defs``; everything else in the schema is written by hand.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

from pinka_gen.bootstrap import _IndentedDumper
from pinka_gen.pages import _option_rows, front_matter, schema_rows, table, usage
from pinka_gen.sources import load_yaml
from pinka_platform import cli as platform_cli
from pinka_platform import contract as platform_contract

RESERVED = "contract/reserved-names.yml"
SCHEMA = "contract/platform-manifest.schema.json"
PROFILES = "contract/profiles.yml"
MANIFEST_PAGE = "docs/reference/platform-manifest.md"
PROFILES_PAGE = "docs/reference/platform-profiles.md"
CLI_PAGE = "docs/reference/cli/pinka-platform.md"
RECORDS = "contract/records"
RECORDS_PAGE = "docs/reference/platform-records.md"
# The records in the order a deployment writes them.
RECORD_FILES = (
    "deployed-from",
    "active-platform",
    "retired-platform",
    "session-record",
    "dataset-provenance",
    "adapter-manifest",
    "adapter-lineage",
)
GROUP_VARS = "ansible/group_vars/all/main.yml"

# The reserved lists of BUILD_PROMPT §12.1, including names infra creates in
# later contract v2 steps. Infra's data adds to them (_collected).
SPEC_NAMES: dict[str, list[str]] = {
    "accounts_groups": [
        "root",
        "pinka",
        "pinka-admin",
        "pinka-restricted",
        "pinka-build",
        "pinka-datasets",
        "pinka-query",
        "pinka-lab",
        "pinka-monitor",
    ],
    "units": [
        "pinka-lab.target",
        "pinka-platform.target",
        "pinka-services.slice",
        "pinka-core",
        "pinka-observability",
        "pinka-inference",
        "pinka-health-gate",
        "pinka-session-start",
        "pinka-sleep-inhibit",
        "pinka-incus-events",
        "pinka-inference-control",
        "pinka-gpu-arbiter",
        "pinka-gpu-job@",
        "pinka-hook@",
        "pinka-otlp-agent",
        "pinka-backup",
        "pinka-backup-prune",
        "pinka-pgdump",
        "pinka-security-patch",
        "pinka-nftables",
        "pinka-docker-incus-compat",
        "pinka-vm-otlp-address",
        "pinka-docs",
        "pinka-window-expiry",
        "pinka-run-network",
        "pinka-inference-forwarder",
    ],
    "containers": [
        "pinka-postgres",
        "pinka-postgres-exporter",
        "pinka-prometheus",
        "pinka-loki",
        "pinka-tempo",
        "pinka-grafana",
        "pinka-opa",
        "pinka-otel-gateway",
        "pinka-otel-journald",
        "pinka-node-exporter",
        "pinka-cadvisor",
        "pinka-gpu-exporter",
        "pinka-vllm",
        "pinka-query-proxy",
    ],
    "incus": ["pinka-build0", "pinka-otlp0", "pinka-run-default", "bench", "build"],
    "postgresql_roles": ["postgres", "pinka_monitor"],
    "postgresql_databases": ["postgres", "template0", "template1"],
    "commands": ["pinka", "pinka-preflight"],
    "platform_names": [
        "infra",
        "lab",
        "root",
        "system",
        "platform",
        "restricted",
        "training",
        "pinka",
        "pokus",
    ],
}
SPEC_PATTERNS: dict[str, list[str]] = {
    "containers": ["pinka-job-*"],
    "incus": ["r-*"],
    "postgresql_roles": ["pg_*"],
}
SPEC_PATHS: dict[str, list[str]] = {
    "/srv/pinka": [
        "models",
        "datasets",
        "images",
        "shared",
        "users",
        "services",
        "state",
        "backups",
        "platforms",
        "restricted",
        "training",
    ],
    "/etc/pinka": [
        "secrets",
        "incus",
        "inference",
        "platform",
        "qa",
        "signing",
        "vllm",
        "gpu",
        "postgres-reader",
    ],
    "/var/lib/pinka": [
        "git",
        "staging",
        "sessions",
        "fingerprint",
        "gpu",
        "platforms",
        "run-networks",
    ],
    "/run/pinka": [
        "postgres",
        "postgres-reader",
        "platform",
        "otlp",
        "gpu.sock",
        "query.sock",
        "run-network.sock",
    ],
}
DESCRIPTIONS = {
    "accounts_groups": "Accounts and groups; also every account or group already on the host.",
    "units": "systemd units, targets and slices (names without .service).",
    "containers": "Container names.",
    "incus": "Incus networks, network ACLs, interfaces, projects and storage pools.",
    "postgresql_roles": "PostgreSQL roles.",
    "postgresql_databases": "PostgreSQL databases.",
    "commands": "Commands; also every command already on the PATH.",
    "platform_names": "Platform names.",
}
_UNIT_RE = re.compile(r"^(pinka-[a-z0-9@-]+?)\.(service|timer|socket|target|slice)\.j2$")


def _collected(root: Path) -> dict[str, set[str]]:
    """Names infra's own data creates today."""
    group_vars = load_yaml(root / GROUP_VARS)
    found: dict[str, set[str]] = {k: set() for k in SPEC_NAMES}
    found["accounts_groups"] |= {
        g["name"] for g in group_vars["pinka_groups"] if g["name"].startswith("pinka")
    }
    found["accounts_groups"] |= {a["name"] for a in group_vars["infra_accounts"]}
    for template in (root / "ansible/roles").glob("*/templates/*.j2"):
        match = _UNIT_RE.match(template.name)
        if match:
            name, suffix = match.groups()
            found["units"].add(
                name if suffix in ("service", "socket", "timer") else template.name[:-3]
            )
    for compose in sorted((root / "compose").glob("*/compose.yml")):
        found["units"].add(f"pinka-{compose.parent.name}")
        data = load_yaml(compose)
        found["containers"] |= {
            s["container_name"] for s in data.get("services", {}).values() if "container_name" in s
        }
    backup = load_yaml(root / "ansible/roles/backup/vars/main.yml")
    found["units"] |= {job["name"] for job in backup["backup_jobs"]}
    incus = load_yaml(root / "ansible/roles/incus/defaults/main.yml")
    found["incus"] |= {
        incus["incus_pool_name"],
        incus["incus_build_network"],
        incus["incus_run_acl"],
        group_vars["pinka_vm_otlp"]["interface"],
    }
    postgres = load_yaml(root / "ansible/roles/postgres/defaults/main.yml")
    found["postgresql_roles"].add(postgres["postgres_monitor_role"])
    return found


def _collected_paths(root: Path) -> dict[str, set[str]]:
    group_vars = load_yaml(root / GROUP_VARS)
    srv = {e["path"].split("/")[0] for e in group_vars["pinka_layout"] if e["path"]}
    return {"/srv/pinka": srv}


def reserved(root: Path) -> dict[str, Any]:
    """Build the reserved-name lists.

    Args:
        root: Component root.

    Returns:
        The content of ``contract/reserved-names.yml``.
    """
    collected = _collected(root)
    namespaces = {}
    for name, listed in SPEC_NAMES.items():
        entry: dict[str, Any] = {
            "description": DESCRIPTIONS[name],
            "names": sorted(set(listed) | collected[name]),
        }
        if name in SPEC_PATTERNS:
            entry["patterns"] = SPEC_PATTERNS[name]
        namespaces[name] = entry
    extra_paths = _collected_paths(root)
    paths = {
        base: sorted(set(segments) | extra_paths.get(base, set()))
        for base, segments in SPEC_PATHS.items()
    }
    return {"contract_version": 2, "namespaces": namespaces, "path_segments": paths}


def reserved_yml(root: Path) -> str:
    """Render ``contract/reserved-names.yml``.

    Args:
        root: Component root.

    Returns:
        File text.
    """
    header = (
        f"# Managed by pinka ({RESERVED}). Local changes will be overwritten.\n"
        "# Generated by tools/gen (make generate) from BUILD_PROMPT §12.1 and infra's own\n"
        "# data; do not edit. The manifest validator refuses every derived name listed\n"
        "# here or matching a pattern (shell-style).\n"
    )
    body = yaml.dump(
        reserved(root),
        Dumper=_IndentedDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    return header + body


def _reserved_defs(data: dict[str, Any]) -> dict[str, Any]:
    defs: dict[str, Any] = {}
    for name, entry in data["namespaces"].items():
        node: dict[str, Any] = {"description": entry["description"], "enum": entry["names"]}
        if entry.get("patterns"):
            regexes = [
                "^" + re.escape(p.rstrip("*")) + (".*$" if p.endswith("*") else "$")
                for p in entry["patterns"]
            ]
            node = {
                "description": entry["description"] + " Patterns: " + ", ".join(entry["patterns"]),
                "anyOf": [{"enum": entry["names"]}, *({"pattern": r} for r in regexes)],
            }
        defs[f"reserved_{name}"] = node
    for base, segments in data["path_segments"].items():
        key = "reserved_paths_" + base.strip("/").replace("/", "_")
        defs[key] = {"description": f"Path segments directly below {base}.", "enum": segments}
    return defs


def schema_json(root: Path) -> str:
    """Render the manifest schema with the reserved lists in ``$defs``.

    Args:
        root: Component root.

    Returns:
        File text: the hand-written schema with ``$defs.reserved_*`` replaced.
    """
    schema = json.loads((root / SCHEMA).read_text(encoding="utf-8"))
    defs = {k: v for k, v in schema["$defs"].items() if not k.startswith("reserved_")}
    defs.update(_reserved_defs(reserved(root)))
    schema["$defs"] = defs
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def _profiles(root: Path) -> dict[str, Any]:
    data: dict[str, Any] = load_yaml(root / PROFILES)
    return data


def profiles_page(root: Path) -> str:
    """Render the profiles reference page from ``contract/profiles.yml``.

    Args:
        root: Component root.

    Returns:
        Page text.
    """
    data = _profiles(root)
    columns = data["columns"]
    profiles = data["profiles"]
    out = [
        front_matter(
            "Platform privilege profiles",
            ["developer", "operator"],
            "The eight privilege profiles of contract v2, who may use whose sockets, "
            "and infra's caps.",
        ),
        "# Platform privilege profiles\n\n",
        "Each service or job of a platform names exactly one profile, and its privileges are "
        "exactly that profile's grants (contract v2). A manifest adds only its own state "
        f"directories, secrets, limits within the caps below and the one fixed relaxation. "
        f"Source: `{PROFILES}`.\n\n",
        table(
            ["Profile", *columns.values()],
            [[f"`{name}`", *(p["table"][c] for c in columns)] for name, p in profiles.items()],
        ),
        "\n## Who may use a socket\n\n",
        "Root can always connect. A socket the agent uses is dedicated to it "
        "(`untrusted_clients: true`) and lists `untrusted-agent` as its only client.\n\n",
    ]
    rows = []
    for name, p in profiles.items():
        rules = p["socket_clients"]
        if not rules:
            rows.append([f"`{name}`", "serves no sockets", ""])
            continue
        users = [f"`{c}`" for c in rules["profiles"]] + list(rules["humans"])
        if rules["root"]:
            users.append("root")
        agent = {
            "dedicated": "`untrusted-agent` only on a dedicated socket",
            "never": "never `untrusted-agent`",
        }[rules["agent"]]
        rows.append([f"`{name}`", ", ".join(users), agent])
    out.append(table(["Served by", "May be used by", "The agent"], rows))
    own = [name for name, p in profiles.items() if p.get("own_package")]
    out.append("\n## The agent's own package\n\n")
    out.append(
        "A service of "
        + ", ".join(f"`{name}`" for name in own)
        + " names its own package in the manifest (`package`, required) and sees only that "
        "package and its own environment of the release, never the rest of it: no other "
        "package, no migrations, policy bundle, docs or dashboards (approved deviation V6, which "
        "narrows the release in the table above). For `python-uv` the package is a workspace "
        "member; the build installs it with only its locked dependencies into "
        "`.agent-venvs/<service>/`, and a start command below `.venv/` runs from there. Every "
        "dependency must come from an `https://` package index: a manifest whose agent package "
        "depends, directly or through others, on another package of the release, or on a path, "
        "Git or URL source, is refused. For `static` the package is the directory the agent "
        "runs from.\n"
    )
    out.append("\n## Facilities\n\n")
    out.append(
        "A manifest declares in `requires` every infra facility it uses; the health gate "
        "checks exactly these. It is refused when a service or job of one of these profiles, "
        "or one of these fields, needs a facility it does not declare. A field counts when it "
        "is present and not false or empty.\n\n"
    )
    out.append(
        table(
            ["Facility", "What it is", "Needed by profiles", "Needed by fields"],
            [
                [
                    f"`{name}`",
                    f["description"],
                    ", ".join(f"`{p}`" for p in f["profiles"]) or "—",
                    ", ".join(f"`{x}`" for x in f["fields"]) or "—",
                ]
                for name, f in data["facilities"].items()
            ],
        )
    )
    out.append("\n## Caps\n\n")
    out.append(table(["Cap", "Value"], [[f"`{k}`", f"`{v}`"] for k, v in data["caps"].items()]))
    out.append("\n## Relaxations\n\n")
    out.append(
        table(
            ["Relaxation", "Effect", "Change"],
            [
                [f"`{k}`", v["description"], f"`{v['unit_change']}`"]
                for k, v in data["relaxations"].items()
            ],
        )
    )
    return "".join(out).rstrip("\n") + "\n"


def _field_rows(node: dict[str, Any], path: str, defs: dict[str, Any]) -> list[list[Any]]:
    rows = list(schema_rows(node, path))
    for name, child in node.get("properties", {}).items():
        items = child.get("items")
        if isinstance(items, dict) and "$ref" in items and items["$ref"].startswith("#/$defs/"):
            target = defs.get(items["$ref"].rsplit("/", 1)[-1], {})
            if "properties" in target:
                rows += list(schema_rows(target, f"{path}.{name}[]" if path else f"{name}[]"))
    return rows


def manifest_page(root: Path) -> str:
    """Render the manifest reference page from the schema and the reserved lists.

    Args:
        root: Component root.

    Returns:
        Page text.
    """
    schema = json.loads(schema_json(root))
    defs = schema["$defs"]
    data = reserved(root)
    out = [
        front_matter(
            "Platform manifest",
            ["developer", "operator"],
            "Every field of platform-manifest.yaml and the reserved names (contract v2).",
        ),
        "# Platform manifest\n\n",
        f"{schema['description']} Schema file: `{SCHEMA}` (JSON Schema draft 2020-12). "
        "Check a release with [pinka-platform](cli/pinka-platform.md); the profiles are in "
        "[Platform privilege profiles](platform-profiles.md).\n\n",
        table(["Field", "Type", "Required", "Description"], _field_rows(schema, "", defs)),
        "\n## Definitions\n\n",
    ]
    for name in ("relative_path", "command", "size", "hook"):
        node = defs[name]
        rows = list(_field_rows(node, name, defs))
        out.append(f"### {name}\n\n")
        if node.get("description"):
            out.append(f"{node['description']}\n\n")
        if rows:
            out.append(table(["Field", "Type", "Required", "Description"], rows) + "\n")
    out.append("## Reserved names\n\n")
    out.append(
        "The validator derives every name the manifest would create (accounts, groups, units, "
        "database roles, the database, the CLI) and refuses one that is listed here, matches a "
        f"pattern, or already exists on the host. Generated into `{RESERVED}`.\n\n"
    )
    rows = []
    for name, entry in data["namespaces"].items():
        listed = ", ".join(f"`{n}`" for n in entry["names"])
        patterns = ", ".join(f"`{p}`" for p in entry.get("patterns", []))
        rows.append([f"`{name}`", entry["description"], listed, patterns])
    out.append(table(["Namespace", "Meaning", "Names", "Patterns"], rows))
    out.append("\n")
    out.append(
        table(
            ["Below", "Reserved path segments"],
            [
                [f"`{b}`", ", ".join(f"`{s}`" for s in segs)]
                for b, segs in data["path_segments"].items()
            ],
        )
    )
    return "".join(out).rstrip("\n") + "\n"


def records_page(root: Path) -> str:
    """Render the reference page of the deployment records from ``contract/records``.

    Args:
        root: Component root.

    Returns:
        Page text.
    """
    out = [
        front_matter(
            "Platform records",
            ["operator", "developer"],
            "The records a platform deployment writes (DEPLOYED_FROM.json, active.json and "
            "retired.json), the session records infra writes at every boot, and the training "
            "records of the GPU arbiter (PROVENANCE.json, MANIFEST.json, LINEAGE.json), in "
            "contract v2.",
        ),
        "# Platform records\n\n",
        "A platform deployment records where every deployed directory came from, which "
        "platform is active, and which platform it replaced; infra records every lab session, "
        "which the platform's controller reads; the GPU arbiter records where every published "
        "dataset and every registered adapter came from ([GPU arbiter requests](gpu-arbiter.md)). "
        "The records are part of contract v2; their "
        f"JSON Schemas (draft 2020-12) are in `{RECORDS}/`, next to the schema of the "
        "environment fingerprint (see [Fingerprint](fingerprint.md)).\n\n",
    ]
    for name in RECORD_FILES:
        schema = json.loads((root / RECORDS / f"{name}.schema.json").read_text(encoding="utf-8"))
        out.append(f"## {schema['title']}\n\n")
        out.append(f"{schema['description']} Schema file: `{RECORDS}/{name}.schema.json`.\n\n")
        out.append(
            table(["Field", "Type", "Required", "Description"], list(schema_rows(schema, "")))
        )
        out.append("\n")
    return "".join(out).rstrip("\n") + "\n"


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def cli_page() -> str:
    """Render the pinka-platform reference page.

    Returns:
        Page text.
    """
    parser = platform_cli.build_parser()
    out = [
        front_matter(
            "pinka-platform command",
            ["developer", "operator"],
            "Every command and option of pinka-platform, the contract v2 validator and "
            "deployment tool.",
        ),
        "# pinka-platform command\n\n",
        "`pinka-platform` checks a platform release against contract v2 before anything on the "
        "host changes, and deploys it. Platform developers run `validate` on their releases "
        "(see [Platform manifest](../platform-manifest.md)); the platform role runs the `deploy` "
        "steps on the lab host, and `pinka platform purge` runs `purge` (see "
        "[Platform records](../platform-records.md)).\n\n",
        f"```text\n{usage(parser)}\n```\n\n",
        f"{parser.description}\n\n",
    ]
    for name, child in _subcommands(parser).items():
        out.append(f"## pinka-platform {name}\n\n```text\n{usage(child)}\n```\n\n")
        out.append(f"{child.description}\n\n")
        rows = list(_option_rows(child))
        positional = [
            [f"`{a.dest}`", "", "", a.help or ""]
            for a in child._actions
            if not a.option_strings and a.dest != "help"
        ]
        out.append(table(["Option", "Value", "Default", "Description"], positional + rows))
        out.append("\n")
    out.append("## Exit codes\n\n")
    out.append(
        table(
            ["Code", "Name", "Meaning"],
            [
                [str(code), f"`{n}`", text]
                for code, (n, text) in sorted(platform_cli.EXIT_CODES.items())
            ],
        )
    )
    return "".join(out).rstrip("\n") + "\n"


def reference_entries(root: Path) -> list[tuple[str, str, str]]:
    """Entries the contract reference pages must contain.

    Args:
        root: Component root.

    Returns:
        ``(label, page, needle)`` triples.
    """
    schema = json.loads(schema_json(root))
    entries = [
        (f"manifest field {row[0]}", MANIFEST_PAGE, str(row[0])) for row in schema_rows(schema, "")
    ]
    contract = platform_contract.load(root / "contract")
    entries += [(f"profile {p}", PROFILES_PAGE, f"`{p}`") for p in contract.profiles]
    for name, child in _subcommands(platform_cli.build_parser()).items():
        entries.append((f"pinka-platform command {name}", CLI_PAGE, f"## pinka-platform {name}"))
        entries += [
            (f"pinka-platform {name} option {opt}", CLI_PAGE, f"`{opt}`")
            for a in child._actions
            for opt in a.option_strings
        ]
    entries += [
        (f"pinka-platform exit code {code}", CLI_PAGE, f"| {code} |")
        for code in platform_cli.EXIT_CODES
    ]
    for name in RECORD_FILES:
        record = json.loads((root / RECORDS / f"{name}.schema.json").read_text(encoding="utf-8"))
        entries += [
            (f"record {record['title']} field {row[0]}", RECORDS_PAGE, str(row[0]))
            for row in schema_rows(record, "")
        ]
    return entries
