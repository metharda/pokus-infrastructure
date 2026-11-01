"""Screens 12-13: Platform and Platform review (contract v2).

The Platform screen takes the release's source and how it is trusted, then
inspects the release alone with the platform tool: only then does it know
which content packages need a source, which secrets the manifest names and
which services take settings. The Platform review screen runs the
deployment's own checks on every answer and shows what the platform will be
on this host (accounts and profiles, sockets dedicated to the agent, training
jobs with their images and caps, restricted content, state and backups,
required facilities, tools, relaxations); the operator approves it before
anything is written.
"""

from __future__ import annotations

import copy
import io
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Checkbox, Input, RadioButton, RadioSet, Static, TextArea

from pinka_installer import release, validators
from pinka_installer.answers import GENERATE, PROVIDED, Answers, Source
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.widgets import Field, NavBar, TypedConfirm
from pinka_platform import mirror

SAFE = YAML(typ="safe")
# Field ids of the release's source fields, by the platform tool's key.
SOURCE_FIELDS = {"repo": "repo", "ref": "ref", "path": "path", "tag_prefix": "tag-prefix"}


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _yaml_text(value: Mapping[str, Any]) -> str:
    if not value:
        return ""
    stream = io.StringIO()
    yaml = YAML(typ="safe", pure=True)
    yaml.default_flow_style = False
    yaml.dump(dict(value), stream)
    return stream.getvalue()


def _source_tuple(source: Source) -> tuple[str, ...]:
    return (source.repo, source.ref, source.path, source.tag_prefix)


def _field_of(problem: str) -> str:
    # "platform/ref: ..." -> "ref"; "content/cases/repo: ..." -> "content-cases-repo".
    where = problem.split(":", 1)[0]
    parts = where.split("/")
    if parts[0] == "platform" and len(parts) == 2:  # noqa: PLR2004
        return SOURCE_FIELDS.get(parts[1], "repo")
    if parts[0] == "content" and len(parts) == 3:  # noqa: PLR2004
        return f"content-{parts[1]}-{SOURCE_FIELDS.get(parts[2], 'repo')}"
    return "repo"


class PlatformScreen(StepScreen):
    """Screen 12: the platform release, its sources, secrets and settings."""

    STEP = "platform"
    HELP = (
        "A lab can run without a platform, or deploy one platform release from a Git "
        "repository: an SSH URL read with this host's read-only deploy key (Deploy key shows "
        "it; add it to the Git host), an anonymous https:// URL, or a root-owned file:/// "
        "repository. Inspect release fetches and checks the release and lists what it needs: "
        "a source for each content package, the secrets its services name (an empty field "
        "is generated) and the settings of services with a config schema. Nothing is "
        "deployed before the Review screen; the release's code never runs during the "
        "inspection."
    )

    def not_applicable(self) -> str | None:  # noqa: D102
        if self.answers.machine.node_role == "inference":
            return "an inference node runs no platform"
        return None

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(
            extra=(
                Button("Deploy key", id="key"),
                Button("Test access", id="test"),
                Button("Inspect release", id="inspect"),
            )
        )

    @property
    def found(self) -> dict[str, Any] | None:
        """The inspection of the current release source, if it passed."""
        inspected = self.wizard.release
        if inspected is None or not inspected.ok:
            return None
        if self.wizard.release_source != _source_tuple(self.answers.platform):
            return None
        return inspected.found

    def body(self) -> ComposeResult:  # noqa: D102
        answers = self.answers
        source, trust = answers.platform, answers.source_trust
        with RadioSet(id="deploy"):
            yield RadioButton(
                "No platform: a standalone lab", value=not answers.has_platform, id="deploy-none"
            )
            yield RadioButton(
                "Deploy a platform release", value=answers.has_platform, id="deploy-platform"
            )
        yield Field(
            "Repository (SSH URL, anonymous https:// URL or file:/// path)",
            Input(
                source.repo,
                id="repo",
                placeholder="git@github.com:example-org/example-platform.git",
            ),
        )
        with Horizontal(classes="inline"):
            yield Field(
                "Release (tag, branch or full commit)",
                Input(source.ref, id="ref", placeholder="example-v0.1.0"),
            )
            yield Field("Path in the repository", Input(source.path, id="path", placeholder="."))
            yield Field(
                "Tag prefix", Input(source.tag_prefix, id="tag-prefix", placeholder="example-v")
            )
        yield Field(
            "Host keys of an SSH host other than GitHub (known_hosts lines)",
            TextArea("\n".join(trust.known_hosts), id="known", classes="lines"),
        )
        yield Checkbox("Deploy only signed tags", trust.require_signed_tags, id="signed")
        yield Field(
            "Allowed signers (allowed_signers lines)",
            TextArea("\n".join(trust.allowed_signers), id="signers", classes="lines"),
        )
        yield Static("", id="key-text", classes="code", markup=False)
        yield from self._needs()

    def _needs(self) -> ComposeResult:
        inspected = self.wizard.release
        found = self.found
        if inspected is not None and not inspected.ok:
            reasons = inspected.problems or [inspected.error]
            yield Static(
                "The release cannot be used:\n" + "\n".join(f"  {r}" for r in reasons),
                id="needs",
                classes="banner bad",
                markup=False,
            )
            return
        if found is None:
            yield Static(
                "Inspect release to see what this release needs.", id="needs", classes="hint"
            )
            return
        platform, made = found["platform"], found["release"]
        yield Static(
            f"Release {platform['name']} {platform['version']} ({platform['runtime']}), "
            f"{made['name']} at {str(made['commit'])[:12]}",
            id="needs",
            classes="banner ok",
            markup=False,
        )
        yield from self._content_fields(found)
        for service, secret_names in sorted(found.get("secrets", {}).items()):
            for name in secret_names:
                key = f"{service}.{name}"
                yield Field(
                    f"Secret {key} (empty: generated; never written to a file but the vault)",
                    Input(
                        self.wizard.provided.platform.get(key, ""),
                        password=True,
                        id=f"secret-{service}-{name}",
                    ),
                )
        for service, schema in sorted(found.get("config", {}).items()):
            properties = (schema.get("properties") or {}) if isinstance(schema, Mapping) else {}
            keys = ", ".join(sorted(properties)) or "none"
            yield Field(
                f"Settings of {service} (YAML; keys: {keys})",
                TextArea(
                    _yaml_text(self.answers.platform_settings.get(service) or {}),
                    id=f"settings-{service}",
                    classes="lines",
                ),
                hint=str(schema.get("description", "")) if isinstance(schema, Mapping) else "",
            )

    def _content_fields(self, found: Mapping[str, Any]) -> ComposeResult:
        for package in found.get("content", []):
            name = str(package["name"])
            source = self.answers.content.get(name) or Source("", "", name, f"{name}-v")
            kind = "restricted" if package.get("restricted") else "not restricted"
            need = "required" if package.get("required") else "optional (no repository: skipped)"
            yield Static(
                f"Content package {name}: {kind}, {need}; often the release's repository",
                classes="info",
            )
            with Horizontal(classes="inline"):
                yield Field("Repository", Input(source.repo, id=f"content-{name}-repo"))
                yield Field("Release", Input(source.ref, id=f"content-{name}-ref"))
                yield Field("Path", Input(source.path, id=f"content-{name}-path"))
                yield Field("Tag prefix", Input(source.tag_prefix, id=f"content-{name}-tag-prefix"))

    # --- actions ---------------------------------------------------------------------

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        self.collect()
        if event.button.id == "key":
            self.deploy_key()
        elif event.button.id == "test":
            self.test_access(copy.deepcopy(self.answers))
        elif event.button.id == "inspect":
            problems = validators.platform_sources(
                {
                    "repo": self.answers.platform.repo,
                    "ref": self.answers.platform.ref,
                    "path": self.answers.platform.path,
                    "tag_prefix": self.answers.platform.tag_prefix,
                },
                {},
            )
            if not self.answers.has_platform:
                problems = ["platform/repo: choose Deploy a platform release and name it"]
            self.show_problems([(_field_of(p), p.split(": ", 1)[-1]) for p in problems])
            if not problems:
                self.query_one("#needs", Static).update("Inspecting the release …")
                self.inspect(copy.deepcopy(self.answers))

    @work(thread=True, exclusive=True)
    def deploy_key(self) -> None:
        """Create the read-only deploy key the platform role uses, and show it."""
        key = release.DEPLOY_KEY
        public = key.with_suffix(".pub")
        if not public.exists():
            key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            done = self.ctx.host.run(
                [
                    "ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                    "-C", f"pinka-deploy@{self.answers.machine.name}", "-f", str(key),
                ]
            )  # fmt: skip
            if done.returncode != 0:
                self.app.call_from_thread(
                    self.notify, f"ssh-keygen failed: {done.stderr}", severity="error"
                )
                return
        text = public.read_text(encoding="utf-8").strip()
        self.app.call_from_thread(
            self.query_one("#key-text", Static).update,
            f"Add this read-only deploy key to the Git host:\n{text}",
        )

    @work(thread=True, exclusive=True)
    def test_access(self, answers: Answers) -> None:
        """``git ls-remote`` the release's repository with the deployment's key and host keys.

        Args:
            answers: A copy of the answers.
        """
        source = answers.platform
        if problems := validators.platform_sources({"repo": source.repo, "ref": "x"}, {}):
            self.app.call_from_thread(self.notify, problems[0], severity="error")
            return
        with tempfile.TemporaryDirectory(prefix="pinka-installer-") as scratch:
            try:
                env = release.access_env(answers, Path(scratch), self.ctx.paths.versions_lock)
            except mirror.FetchError as exc:
                self.app.call_from_thread(self.notify, str(exc), severity="error")
                return
            done = self.ctx.host.run(["git", "ls-remote", "--tags", source.repo], env=env)
        if done.returncode == 0:
            tags = [line for line in done.stdout.splitlines() if "refs/tags/" in line]
            self.app.call_from_thread(self.notify, f"access works; {len(tags)} tags")
        else:
            self.app.call_from_thread(
                self.notify, f"no access: {done.stderr.strip()[-200:]}", severity="error"
            )

    @work(thread=True, exclusive=True)
    def inspect(self, answers: Answers) -> None:
        """Inspect the release alone (the platform tool's checks), then show what it needs.

        Args:
            answers: A copy of the answers.
        """
        result = self.ctx.host.inspect_platform(answers, full=False)
        self.app.call_from_thread(self._inspected, result, _source_tuple(answers.platform))

    def _inspected(self, result: release.Inspection, source: tuple[str, ...]) -> None:
        self.wizard.release = result
        self.wizard.release_source = source
        self.call_later(self.recompose)

    # --- saving ----------------------------------------------------------------------

    def collect(self) -> None:
        """Copy the fields into the answers without checking them."""
        answers = self.answers
        if not self.query_one("#deploy-platform", RadioButton).value:
            answers.platform = Source()
            answers.content, answers.platform_settings = {}, {}
            answers.secrets.platform = {}
            self.wizard.provided.platform = {}
            return
        answers.platform = Source(
            *(self.query_one(f"#{SOURCE_FIELDS[k]}", Input).value.strip() for k in SOURCE_FIELDS)
        )
        trust = answers.source_trust
        trust.known_hosts = _lines(self.query_one("#known", TextArea).text)
        trust.require_signed_tags = self.query_one("#signed", Checkbox).value
        trust.allowed_signers = _lines(self.query_one("#signers", TextArea).text)
        found = self.found
        if found is None:
            return
        content: dict[str, Source] = {}
        for package in found.get("content", []):
            name = str(package["name"])
            values = [
                self.query_one(f"#content-{name}-{SOURCE_FIELDS[k]}", Input).value.strip()
                for k in SOURCE_FIELDS
            ]
            if values[0]:
                content[name] = Source(*values)
        answers.content = content
        choices: dict[str, str] = {}
        provided: dict[str, str] = {}
        for service, secret_names in found.get("secrets", {}).items():
            for name in secret_names:
                key = f"{service}.{name}"
                value = self.query_one(f"#secret-{service}-{name}", Input).value
                choices[key] = PROVIDED if value else GENERATE
                if value:
                    provided[key] = value
        answers.secrets.platform = choices
        self.wizard.provided.platform = provided

    def _settings(self, found: Mapping[str, Any]) -> Problems:
        problems: Problems = []
        settings: dict[str, Any] = {}
        for service in sorted(found.get("config", {})):
            text = self.query_one(f"#settings-{service}", TextArea).text
            try:
                value = SAFE.load(text) if text.strip() else None
            except YAMLError as exc:
                problems.append((f"settings-{service}", f"not YAML: {exc}"))
                continue
            if value is None:
                continue
            if not isinstance(value, dict):
                problems.append((f"settings-{service}", "a mapping of settings, such as job: x"))
                continue
            settings[service] = value
        self.answers.platform_settings = settings
        return problems

    def save(self) -> Problems:  # noqa: D102
        self.collect()
        answers = self.answers
        if not answers.has_platform:
            self.wizard.release = None
            return []
        problems: Problems = []
        for problem in validators.platform_sources(
            {
                "repo": answers.platform.repo,
                "ref": answers.platform.ref,
                "path": answers.platform.path,
                "tag_prefix": answers.platform.tag_prefix,
            },
            {
                n: {"repo": s.repo, "ref": s.ref, "path": s.path, "tag_prefix": s.tag_prefix}
                for n, s in answers.content.items()
            },
        ):
            problems.append((_field_of(problem), problem.split(": ", 1)[-1]))
        if answers.source_trust.require_signed_tags and not answers.source_trust.allowed_signers:
            problems.append(("signers", "signed tags need at least one allowed signer"))
        found = self.found
        if found is None:
            problems.append(("repo", "press Inspect release: the installer reads it first"))
            return problems
        for package in found.get("content", []):
            name = str(package["name"])
            if package.get("required") and name not in answers.content:
                problems.append((f"content-{name}-repo", "a required package: name its source"))
        problems += self._settings(found)
        return problems


class PlatformReviewScreen(StepScreen):
    """Screen 13: what the platform will be on this host, approved by the operator."""

    STEP = "platform_review"
    HELP = (
        "The release, its content packages and its settings have passed the deployment's "
        "own checks (or the reasons they did not are shown). Read what the platform will be "
        "on this host: its accounts and privilege profiles, the sockets dedicated to the "
        "untrusted agent, its training jobs with their images and resources, its restricted "
        "content, which state is backed up, what it requires. Approve it to continue; any "
        "change on the Platform screen asks for a new review. A release of another platform "
        "than the active one is refused unless you replace the active platform: Replace "
        "asks you to type its name, exactly like pinka platform purge, and changes nothing "
        "without it."
    )

    def not_applicable(self) -> str | None:  # noqa: D102
        if self.answers.machine.node_role == "inference":
            return "an inference node runs no platform"
        if not self.answers.has_platform:
            return "no platform: a standalone lab"
        return None

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(extra=(Button("Check again", id="check"),))

    @property
    def current(self) -> release.Inspection | None:
        """The review of the current platform answers, if there is one."""
        if self.wizard.review_key != release.review_key(self.answers):
            return None
        return self.wizard.review

    def on_mount(self) -> None:  # noqa: D102
        if self.not_applicable() is None and self.current is None:
            self.check(copy.deepcopy(self.answers), self.wizard.replacing() or None)

    def body(self) -> ComposeResult:  # noqa: D102
        review = self.current
        replacing = self.wizard.replacing()
        if replacing:
            yield Static(
                f"Replacing the active platform {replacing} (you typed its name). Its "
                "services stop; its releases, content, state and database stay on disk until "
                f"you purge them with pinka platform purge {replacing}.",
                id="replacing",
                classes="banner warn",
                markup=False,
            )
        if review is None:
            yield Static(
                "Checking the release, its content packages and its settings …",
                id="review-text",
                classes="info",
            )
        elif not review.ok:
            reasons = review.problems or [review.error]
            yield Static(
                "The platform is refused; nothing on this host has changed:\n"
                + "\n".join(f"  {r}" for r in reasons),
                id="review-text",
                classes="banner bad",
                markup=False,
            )
            if review.active and not replacing:
                yield Static(
                    f"{review.active} is the active platform. Deploying this release replaces "
                    "it; you confirm that by typing its name.",
                    classes="info",
                    markup=False,
                )
                yield Button(f"Replace {review.active} …", id="replace", variant="error")
        else:
            yield from review_lines(review.found)
        yield Field(
            "Approval",
            Checkbox(
                "I approve deploying this platform with the accounts, privileges and jobs above",
                self.wizard.platform_approved and review is not None and review.ok,
                id="approve",
            ),
        )

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        if event.button.id == "check":
            self.wizard.review = None
            self.check(copy.deepcopy(self.answers), self.wizard.replacing() or None)
        elif event.button.id == "replace":
            review = self.current
            active = review.active if review is not None else ""
            if not active:
                return
            text = (
                f"The active platform {active} is replaced by this release: its services "
                "stop, the new platform's services start, and its releases, content, state, "
                "restricted area and database stay on disk until you purge them with "
                f"pinka platform purge {active}. Take a backup first."
            )
            self.app.push_screen(
                TypedConfirm(f"Replace the active platform {active}", text, active),
                lambda confirmed: self._replace(active, bool(confirmed)),
            )

    def _replace(self, active: str, confirmed: bool) -> None:
        """Replace the active platform only after its name was typed."""
        if not confirmed:
            self.notify("Nothing changed: the active platform's name was not typed")
            return
        self.wizard.replace_platform = active
        self.wizard.replace_key = release.review_key(self.answers)
        self.wizard.review = None
        self.call_later(self.recompose)
        self.check(copy.deepcopy(self.answers), active)

    @work(thread=True, exclusive=True)
    def check(self, answers: Answers, replace: str | None = None) -> None:
        """Run the deployment's checks on every platform answer.

        Args:
            answers: A copy of the answers.
            replace: The active platform the operator confirmed replacing, or
                None (bootstrap's own ``--replace-platform``).
        """
        result = self.ctx.host.inspect_platform(answers, full=True, replace=replace)
        self.app.call_from_thread(self._checked, result, release.review_key(answers))

    def _checked(self, result: release.Inspection, key: str) -> None:
        self.wizard.review, self.wizard.review_key = result, key
        self.wizard.platform_approved = False
        self.call_later(self.recompose)

    def save(self) -> Problems:  # noqa: D102
        review = self.current
        self.wizard.platform_approved = False
        if review is None:
            return [("approve", "the release is still being checked")]
        if not review.ok:
            return [("approve", "the platform is refused: correct it on the Platform screen")]
        if not self.query_one("#approve", Checkbox).value:
            return [("approve", "approve the platform to continue")]
        self.wizard.platform_approved = True
        return []


def review_lines(found: Mapping[str, Any]) -> Iterator[Static]:
    """The Platform review of a passed inspection, one block per topic.

    Args:
        found: The platform tool's report (``release.inspect(full=True)``).

    Yields:
        The blocks; what widens the platform's reach is highlighted.
    """
    summary, made = found["summary"], found["release"]
    signed = "signed tag" if made.get("signed_tag") else "tag not signed"
    yield Static(
        f"{summary['name']} {summary['version']} ({summary['runtime']}) · {made['name']} at "
        f"{str(made['commit'])[:12]} ({signed})\n"
        f"requires: {', '.join(summary['requires']) or 'nothing'} · tools: "
        f"{', '.join(summary['tools']) or 'none'} · CLI: {summary.get('cli') or 'none'}",
        id="review-text",
        classes="info",
        markup=False,
    )
    services = []
    for service in summary["services"]:
        extras = []
        if service["incus_projects"]:
            extras.append("Incus " + "/".join(service["incus_projects"]))
        if service["restricted_data"]:
            extras.append("reads restricted data")
        if service["relaxations"]:
            extras.append("relaxation " + ", ".join(service["relaxations"]))
        line = f"  {service['name']}: {service['profile']} as {service['account']}"
        services.append(line + (f" ({'; '.join(extras)})" if extras else ""))
        for state in service["state"]:
            kept = "backed up" if state["backed_up"] else "not backed up"
            services.append(f"      state {state['name']}: {kept}")
    database = "backed up" if summary.get("backup_database") else "not backed up"
    services.append(f"  database: {database}")
    yield Static("Services\n" + "\n".join(services), classes="card", markup=False)
    dedicated = [s for s in summary["sockets"] if s["agent_dedicated"]]
    if dedicated:
        yield Static(
            "Sockets dedicated to the untrusted agent:\n"
            + "\n".join(f"  {s['service']}/{s['name']}" for s in dedicated),
            classes="banner warn",
            markup=False,
        )
    others = [s for s in summary["sockets"] if not s["agent_dedicated"]]
    if others:
        yield Static(
            "Sockets\n"
            + "\n".join(f"  {s['service']}/{s['name']}: {', '.join(s['clients'])}" for s in others),
            classes="card",
            markup=False,
        )
    if summary["jobs"]:
        jobs = [
            f"  {j['name']}: {j['gpus']} GPU, {j['cpus']} CPUs, memory {j['memory_max']}, "
            f"{j['timeout_seconds']} s{', resumable' if j['resumable'] else ''}\n"
            f"      {j['image']}"
            for j in summary["jobs"]
        ]
        yield Static("Training jobs\n" + "\n".join(jobs), classes="card", markup=False)
    if found.get("warnings"):
        yield Static("\n".join(found["warnings"]), classes="banner warn", markup=False)
    if summary["content"]:
        content = [
            f"  {c['name']}: {'restricted' if c['restricted'] else 'not restricted'}"
            f"{'' if c['required'] else ', optional'}"
            for c in summary["content"]
        ]
        yield Static("Content packages\n" + "\n".join(content), classes="card", markup=False)
