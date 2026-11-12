"""Step 5: the capacity plan and its sub-steps (Storage device, NVIDIA driver, Serving).

The capacity plan decides nothing about capacity. After every change it
writes the plan of the choices so far (the same plan bootstrap writes from
host_vars), runs preflight with it through the host layer and shows what
preflight answered: a bar per resource, the items behind it, the growth and
preflight's reasons. Next waits where bootstrap's preflight stage would stop
(:func:`capacity.waits`); a degraded outcome needs the operator's consent.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Footer,
    Input,
    RadioButton,
    RadioSet,
    Select,
    Static,
)

from pinka_installer import hostvars, models, validators
from pinka_installer.answers import Answers
from pinka_installer.tui import capacity
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.host import Disk
from pinka_installer.tui.model import (
    DRIVER_MODES,
    ModelChoice,
    load_profiles,
    model_choices,
    suggest_cpusets,
    suggest_postgres,
    suggest_profile,
)
from pinka_installer.tui.widgets import Field, NavBar, StepHeader, TypedConfirm, dropdown

SAFE = YAML(typ="safe")
NODE_ROLES = [
    ("full: everything on this host", "full"),
    ("lab: lab services, inference on another node", "lab"),
    ("inference: vLLM only, paired with a lab node", "inference"),
]
NODE_ROLE_VALUES = [value for _label, value in NODE_ROLES]
INFERENCE = [
    ("auto: serve where the GPU can", "auto"),
    ("on: serve, or refuse the host", "on"),
    ("off: no vLLM", "off"),
]
TRAINING = [
    ("auto: jobs where the GPU can run them", "auto"),
    ("cpu: also the CPU-only variant", "cpu"),
    ("off: no training jobs", "off"),
]
DRIVER_LABELS = {
    "auto": "Install the pinned driver",
    "manual": "I install it myself",
    "skip": "No driver",
}
NVIDIA_DRIVER = [(DRIVER_LABELS[mode], mode) for mode in DRIVER_MODES]
MIN_LOOP_GIB = 20  # the answers schema's minimum
# Seconds after the last change before preflight runs again.
DEBOUNCE = 0.4


def _value(screen: StepScreen, selector: str) -> str:
    return screen.query_one(selector, Input).value.strip()


def _select(screen: StepScreen, selector: str) -> str:
    value = screen.query_one(selector, Select).value
    return "" if value is Select.NULL else str(value)


def cpu_bar(services: str, targets: str, threads: int) -> str:
    """A one-line picture of the CPU split (S: services, T: targets, ·: unused).

    Args:
        services: ``cpuset_services``.
        targets: ``cpuset_targets``.
        threads: CPU threads.

    Returns:
        The bar.
    """
    try:
        s = validators.merged_ranges(validators.cpuset_ranges(services))
        t = validators.merged_ranges(validators.cpuset_ranges(targets))
    except ValueError:
        return ""
    cells = []
    for cpu in range(threads):
        in_s, in_t = validators.in_cpuset(cpu, s), validators.in_cpuset(cpu, t)
        cells.append("X" if in_s and in_t else "S" if in_s else "T" if in_t else "·")
    return "".join(cells)


class Section(Vertical):
    """A part of a page built by a function, built again when what it shows changes."""

    def __init__(self, build: Callable[[], ComposeResult], **kwargs: Any) -> None:  # noqa: ANN401
        """Build the section.

        Args:
            build: Yields the section's widgets.
            **kwargs: For :class:`Vertical` (``id``, ...).
        """
        super().__init__(**kwargs)
        self.build = build

    def compose(self) -> ComposeResult:  # noqa: D102
        yield from self.build()


def disk_row(disk: Disk) -> tuple[str, ...]:
    """A row of the disk table.

    Args:
        disk: The disk.

    Returns:
        Name, by-id, model, size, serial, signatures, state ("-" where unknown).
    """
    size = (
        f"{disk.size_bytes / 2**40:.1f} TiB"
        if disk.size_bytes >= 2**40
        else f"{disk.size_bytes / 2**30:.0f} GiB"
    )
    return (
        disk.name,
        disk.by_id.rsplit("/", 1)[-1] if disk.by_id else "-",
        disk.model or "-",
        size,
        disk.serial or "-",
        ", ".join(disk.signatures) or "no signatures",
        f"✖ {disk.usable}" if disk.usable else "available",
    )


def driver_pin(versions_lock: Path, release_key: str) -> str:
    """The pinned NVIDIA driver packages for a release, or why there are none.

    Args:
        versions_lock: ``versions.lock.yml``.
        release_key: Such as ``ubuntu-24.04``.

    Returns:
        A line for the NVIDIA driver page.
    """
    lock = SAFE.load(versions_lock.read_text(encoding="utf-8"))
    pins = ((lock.get("packages") or {}).get("nvidia_driver") or {}).get(release_key)
    if isinstance(pins, dict) and pins:
        return "pinned: " + ", ".join(f"{k} {v}" for k, v in pins.items())
    return (
        "no verified driver pin for this release: choose I install it myself or No driver "
        "on the capacity plan"
    )


def serving_unavailable(screen: StepScreen) -> str | None:
    """Why the serving choices do not apply: a lab node, or no GPU.

    Args:
        screen: A page of step 5.

    Returns:
        The reason, or None.
    """
    if screen.answers.machine.node_role == "lab":
        return "a lab node serves no model; its inference node does"
    if not screen.wizard.has_gpu:
        return "no GPU detected; vLLM is not installed on this host"
    return None


def lock_text(screen: StepScreen) -> str:
    """``models.lock.yml`` with the models pinned in this session."""
    return screen.wizard.extra_files.get(
        "models.lock.yml"
    ) or screen.ctx.paths.models_lock.read_text(encoding="utf-8")


class CapacityScreen(StepScreen):
    """Step 5: what sizes the lab, and preflight's answer on whether it fits."""

    STEP = "capacity"
    HELP = (
        "Everything that sizes the lab is chosen here, and preflight judges it: after "
        "every change the installer writes the plan of your choices, as bootstrap will, "
        "and runs preflight with it. A green bar fits; a red one does not fit and Next "
        "waits until it does, also on a host forced on the Preflight screen. When "
        "preflight's outcome is degraded (a yellow bar, or a feature it turns off), going "
        "on needs your consent under the status line; it starts ticked only when you "
        "accepted each of its lines already (on the Preflight screen or here). Next also "
        "waits where preflight finds the host unsupported (unless forced on the Preflight "
        "screen) or asks for an action, as bootstrap would stop there. Press Enter on a "
        "bar to see its items: each number is estimated, configured or measured.\n\n"
        "A lab node runs the lab services and uses the GPU of an inference node, which "
        "it is paired with after both are installed (pinka pair).\n\n"
        "On bare metal and VMs with a GPU, the NVIDIA driver is chosen here, and preflight "
        "judges the GPU with it: 'Install the pinned driver', 'I install it myself' (refused "
        "with Secure Boot and no driver until you installed it) or 'No driver'.\n\n"
        "A model must be pinned before the plan can count it: until then the plan is "
        "refused. Pin model resolves the commit and the sha256 of every file from the "
        "Hugging Face Hub; gated models need a token, to pin them and to download them.\n\n"
        "The sandbox targets live in an Incus storage pool on btrfs, or on LVM thin when "
        "btrfs is missing. A loop file is the simple choice; a dedicated disk is faster "
        "and is chosen on the next page.\n\n"
        "Lab services and Incus targets never share CPU cores, so measurements of the "
        "targets are not disturbed by the lab itself. The suggestion gives about 30 % of "
        "the cores to the services and keeps both threads of a core (SMT siblings) in one "
        "set; a warning shows when your split puts them in different sets. On WSL the "
        "effective limits come from .wslconfig on the Windows side; enter the values you "
        "expect there so doctor can warn when they differ."
    )

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self.cores: tuple[frozenset[int], ...] = ()
        self.choices: list[ModelChoice] = []
        self.profiles: dict[str, dict[str, Any]] = {}
        # Every run gets the next number; only the latest run's result is shown.
        self.sequence = 0
        self.shown = 0
        self.pending: tuple[int, Answers, str, list[str]] | None = None
        # Next was pressed while preflight was still answering: go on with its answer.
        self.next_wanted = False
        self.timer: Timer | None = None
        self.answer = capacity.Answer()
        self.table: list[capacity.Row] = []
        self.computed_for = ""
        # A field held a value it does not accept when the run started: not counted.
        self.invalid = False
        # The operator left the page: a run that ends after that drops its answer
        # (Textual keeps is_mounted true on a screen that is gone).
        self.left = False

    # --- layout -------------------------------------------------------------------

    def compose(self) -> ComposeResult:  # noqa: D102
        yield StepHeader(self.title_text(), self.position())
        with VerticalScroll(id="budget"):
            # Plain text: a refusal names a value read from host_vars or a resumed state.
            yield Static(
                "Computing the plan with preflight…", id="cap-status", classes="info", markup=False
            )
            consent = Checkbox(
                "I accept what is degraded below (recorded as --accept-degraded)",
                False,
                id="cap-consent",
            )
            consent.display = False
            yield consent
            yield Vertical(id="cap-rows")
            yield Static("", id="cap-growth", classes="hint")
            yield Static("", id="cap-reasons", classes="banner bad", markup=False)
            yield Static(capacity.LEGEND, classes="hint")
        with VerticalScroll(id="body"):
            yield from self.body()
        yield self.nav()
        yield Footer()

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(extra=(Button("Pin model", id="pin"), Button("Check token", id="whoami")))

    def body(self) -> ComposeResult:  # noqa: D102
        yield from self._detected()
        yield from self._roles()
        yield Section(self._serving, id="serving")
        yield from self._storage()
        yield from self._resources()

    def _detected(self) -> ComposeResult:
        wizard = self.wizard
        gpu = wizard.gpu
        if gpu.get("present"):
            budgets = ((wizard.capacity or wizard.profile).get("requirements") or {}).get(
                "budgets"
            ) or {}
            mib = (budgets.get("vram") or {}).get("gpu_mib")
            memory = (
                f"{mib:g} MiB"
                if mib is not None
                else f"{gpu['vram_gib']} GiB"
                if gpu.get("vram_gib") is not None
                else "memory unknown"
            )
            card = (
                f"GPU {gpu.get('name') or gpu.get('vendor')}, {memory}, compute capability "
                f"{gpu.get('compute_capability') or '?'}, driver "
                f"{gpu.get('driver') or gpu.get('driver_state') or 'none'}"
            )
        else:
            card = "no GPU"
        yield Static(
            f"Detected: {wizard.cpu_threads or '?'} CPU threads, "
            f"{wizard.memory_gib or '?'} GiB memory, {card}",
            classes="info",
        )

    def _roles(self) -> ComposeResult:
        machine, features = self.answers.machine, self.answers.features
        yield Field("Node role", dropdown(NODE_ROLES, machine.node_role, "node_role"))
        yield Field(
            "Paired node (optional)",
            Input(
                self.answers.split_inference_peer,
                id="peer",
                placeholder="pair later with pinka pair",
            ),
            hint="lab and inference nodes only: the machine name of the other node",
        )
        with Horizontal(classes="inline"):
            yield Field("Inference", dropdown(INFERENCE, features.inference, "inference"))
            yield Field("Training", dropdown(TRAINING, features.training, "training"))

    def _serving(self) -> ComposeResult:
        gpu = self.answers.gpu
        if self.wizard.has_gpu and not self.wizard.is_wsl:
            # Chosen here, not on the NVIDIA driver page: preflight judges the GPU with it.
            # On every node role: the play installs the driver on a lab node too.
            yield Field(
                "NVIDIA driver",
                dropdown(NVIDIA_DRIVER, gpu.install_driver, "install_driver"),
                hint="the NVIDIA driver page shows the pinned packages, and with Secure Boot "
                "asks the one-time MOK password",
            )
        reason = serving_unavailable(self)
        if reason:
            yield Static(f"Serving: not applicable here: {reason}", classes="greyed")
            return
        self.profiles = load_profiles(self.ctx.paths.vllm_profiles)
        profile = (
            gpu.vllm_profile
            or suggest_profile(self.serving_answer())
            or next(iter(self.profiles), "")
        )
        # A profile that does not exist starts blank and is refused on Next.
        offered = isinstance(profile, str) and profile in self.profiles
        yield Field(
            "Serving profile",
            Select(
                [(f"{n}: {p.get('description', '')}", n) for n, p in self.profiles.items()],
                value=profile if offered else Select.NULL,
                id="profile",
            ),
        )
        table: DataTable[str] = DataTable(id="models", cursor_type="row")
        yield table
        # Plain text: a default model read from host_vars or a resumed state.
        yield Static(
            f"Default model: {gpu.default_model}", id="chosen", classes="info", markup=False
        )
        with Horizontal(classes="inline"):
            yield Input(placeholder="custom: <owner>/<name>", id="custom-repo")
            yield Input(placeholder="branch, tag or commit", id="custom-ref")
        yield Field(
            "Hugging Face token (gated models)",
            Input(self.wizard.provided.hf_token, password=True, id="hf"),
        )

    def _storage(self) -> ComposeResult:
        storage = self.answers.storage
        backend = self.wizard.storage_backend or "unknown"
        yield Static(f"Incus pool: {backend} (from preflight)", classes="info")
        with RadioSet(id="source"):
            yield RadioButton(
                "Loop file inside this system", value=storage.source == "loop", id="source-loop"
            )
            yield RadioButton(
                "Dedicated disk (chosen on the next page)",
                value=storage.source == "device",
                id="source-device",
            )
        yield Field(
            "Loop file size (GiB, or auto)",
            Input(str(storage.loop_size_gib), id="loop_size"),
            hint="auto: preflight sizes it from the targets, the golden images and the free space",
        )
        yield Field(
            "Concurrent targets",
            Input(str(self.answers.resources.concurrent_targets), id="targets"),
        )
        retention = self.answers.telemetry_retention
        yield Static("Telemetry retention", classes="field-label")
        with Horizontal(classes="inline"):
            yield Field("Prometheus", Input(retention.prometheus, id="ret-prometheus"))
            yield Field(
                "Prometheus size",
                Input(retention.prometheus_size, id="ret-size"),
                hint="0: by time only",
            )
        with Horizontal(classes="inline"):
            yield Field("Loki", Input(retention.loki, id="ret-loki"))
            yield Field("Tempo", Input(retention.tempo, id="ret-tempo"))

    def _resources(self) -> ComposeResult:
        threads, memory = self.wizard.cpu_threads, self.wizard.memory_gib
        resources = self.answers.resources
        self.cores = tuple(self.ctx.host.smt_cores())
        services, targets = suggest_cpusets(threads, self.cores)
        if self.wizard.is_wsl:
            yield Static(
                "WSL: the limits come from %UserProfile%\\.wslconfig on Windows "
                "(memory=, processors=). Enter the values you set there.",
                classes="banner warn",
            )
        with Horizontal(classes="inline"):
            yield Field(
                "Expected memory (GiB)",
                Input(
                    str(resources.expected_memory_gib or int(memory or 0) or ""),
                    id="memory",
                    type="integer",
                ),
            )
            yield Field(
                "Expected CPU threads",
                Input(
                    str(resources.expected_cpu_threads or threads or ""),
                    id="threads",
                    type="integer",
                ),
            )
        with Horizontal(classes="inline"):
            yield Field(
                "CPUs for lab services",
                Input(resources.cpuset_services or services, id="cpuset_services"),
            )
            yield Field(
                "CPUs for Incus targets",
                Input(resources.cpuset_targets or targets, id="cpuset_targets"),
            )
        yield Static("", id="cpu-bar", classes="bar")
        smt = Static("", id="smt-warning", classes="banner warn")
        smt.display = False
        yield smt
        yield Field(
            "Postgres shared_buffers",
            Input(resources.postgres_shared_buffers or suggest_postgres(memory), id="postgres"),
            hint="suggested: 64 MB per GiB of memory, 128 MB to 4 GB",
        )

    # --- preflight's answer ----------------------------------------------------------

    def serving_answer(self) -> dict[str, Any]:
        """Preflight's answer on which serving profiles and models fit the GPU."""
        profile = self.wizard.capacity or self.wizard.profile
        found: dict[str, Any] = (profile.get("requirements") or {}).get("serving") or {}
        return found

    def on_mount(self) -> None:  # noqa: D102
        self._update_bar()
        self.call_after_refresh(self._models_table)  # once the serving section is built
        self.recompute(now=True)

    def on_unmount(self) -> None:  # noqa: D102
        self.left = True

    def _models_table(self) -> None:
        if self.query("#models"):
            self._fill_models()

    async def _rebuild_serving(self) -> None:
        """Show the serving choices again for the node role now chosen."""
        await self.query_one("#serving", Section).recompose()
        self._models_table()

    def recompute(self, *, now: bool = False) -> None:
        """Run preflight again for the current choices (after a short pause while typing).

        Args:
            now: Run at once, not after the pause (a run already waiting starts too).
        """
        # The valid fields into the answers; Next shows the others' problems.
        invalid = any(field_id != "cap-consent" for field_id, _ in self.save())
        answers = copy.deepcopy(self.answers)
        lock = lock_text(self)
        args = self.wizard.plan_args()
        key = json.dumps([hostvars.host_vars(answers), lock, args], sort_keys=True, default=str)
        self.invalid = invalid
        if key == self.computed_for:
            if now:
                self._start_pending()
            self._show_status()
            return  # nothing preflight reads changed
        if self.timer is not None:
            self.timer.stop()
            self.timer = None
        self.computed_for = key
        self.next_wanted = False  # a Next pressed before this change waits for a new press
        self.sequence += 1
        self.pending = (self.sequence, answers, lock, args)
        self._status("Computing the plan with preflight…")
        if now:
            self._start_pending()
        else:
            self.timer = self.set_timer(DEBOUNCE, self._start_pending)

    def _start_pending(self) -> None:
        """Start the run that waits for the pause to end."""
        if self.timer is not None:
            self.timer.stop()
            self.timer = None
        if self.pending is not None:
            self.compute(self.app, *self.pending)
            self.pending = None

    @work(thread=True, group="capacity")
    def compute(
        self, app: App[Any], sequence: int, answers: Answers, lock: str, args: list[str]
    ) -> None:
        """Write the plan of the choices and run preflight with it.

        Args:
            app: The application, taken when the run starts: the answer reaches it
                even after the operator left the page, which then drops it.
            sequence: The change this run is for; a newer one drops its result.
            answers: A copy of the answers at that change.
            lock: ``models.lock.yml`` at that change.
            args: Bootstrap's driver options for these answers (``Wizard.plan_args``).
        """
        found = capacity.run(self.ctx.paths, self.ctx.host, answers, lock, args)
        app.call_from_thread(self.show_result, sequence, found)

    def show_result(self, sequence: int, answer: capacity.Answer) -> None:
        """Show preflight's answer, unless a newer change is being computed.

        Args:
            sequence: The change the answer is for.
            answer: Preflight's answer to the plan of that change.
        """
        if sequence != self.sequence or self.left:
            return  # an older run finished after a newer one started, or the page is gone
        self.shown = sequence
        self._show(answer)
        if self.next_wanted:
            self.next_wanted = False
            if self.app.screen is self:  # not under a dialog the operator opened since
                self.action_next()

    def _show(self, answer: capacity.Answer) -> None:
        self.answer = answer
        profile = answer.profile
        self.wizard.capacity = profile
        self.table = capacity.rows(profile)
        rows = self.query_one("#cap-rows", Vertical)
        rows.remove_children()
        for row, title in zip(self.table, capacity.titles(self.table), strict=True):
            body = Static("\n".join(row.items) or "no items", classes="hint")
            rows.mount(
                Collapsible(
                    body,
                    title=title,
                    collapsed=True,
                    classes=f"budget {row.status}",
                    name=row.key,
                )
            )
        self.query_one("#cap-growth", Static).update(capacity.growth(profile))
        found = capacity.reasons(profile)
        reasons = self.query_one("#cap-reasons", Static)
        reasons.update("\n".join(f"- {r}" for r in found))
        reasons.display = bool(found)
        consent = self.query_one("#cap-consent", Checkbox)
        consent.display = capacity.needs_consent(profile)
        # Ticked only when the operator accepted every line of it already (on the
        # Preflight screen or here); anything new is asked for again.
        accepted = set(capacity.degraded(profile)) <= self.wizard.degraded_accepted
        with consent.prevent(Checkbox.Changed):
            consent.value = consent.display and accepted
        self._save_consent(taken_back=False)
        self._show_status()
        if self.query("#models"):
            self._fill_models()

    def _show_status(self) -> None:
        if self.shown != self.sequence:
            return  # the answer to the current choices is still coming
        answer = self.answer
        wizard = self.wizard
        if waiting := capacity.waits(answer, wizard.consents, wizard.degraded_accepted):
            self._status(
                f"Next waits: {waiting}" + (" (see below)" if answer.profile else ""), bad=True
            )
        elif self.invalid:
            self._status(
                f"preflight: {answer.profile.get('outcome', '?')} without the fields that hold "
                "a value they do not accept (Next names them)"
            )
        else:
            self._status(f"preflight: {answer.profile.get('outcome', '?')} with these choices")

    def _status(self, text: str, *, bad: bool = False) -> None:
        status = self.query_one("#cap-status", Static)
        status.update(text)
        status.set_class(bad, "error")

    # --- events --------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:  # noqa: D102
        if event.input.id in ("cpuset_services", "cpuset_targets"):
            self._update_bar()
        if event.input.id not in ("hf", "custom-repo", "custom-ref"):
            self.recompute()

    async def on_select_changed(self, event: Select.Changed) -> None:  # noqa: D102
        if event.select.id == "profile" and self.query("#models"):
            self._fill_models()
        if event.select.id == "node_role":
            self.save()
            if self.answers.machine.node_role == "lab":
                self.answers.gpu.default_model = "none"  # a lab node serves no model
            await self._rebuild_serving()
        self.recompute()

    def on_radio_set_changed(self, _event: RadioSet.Changed) -> None:  # noqa: D102
        self.recompute()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:  # noqa: D102
        if event.checkbox.id == "cap-consent":
            self._save_consent(taken_back=not event.value)
            self._show_status()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:  # noqa: D102
        if event.data_table.id != "models":
            return
        self.answers.gpu.default_model = str(event.row_key.value)
        self.query_one("#chosen", Static).update(f"Default model: {self.answers.gpu.default_model}")
        self.recompute()

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        if event.button.id == "pin":
            self.pin()
        elif event.button.id == "whoami":
            self.check_token(_value(self, "#hf") if self.query("#hf") else "")

    def _update_bar(self) -> None:
        if not self.query("#cpu-bar"):
            return
        services, targets = _value(self, "#cpuset_services"), _value(self, "#cpuset_targets")
        # A warning only: the operator may accept shared cores.
        split = validators.smt_split(services, targets, self.cores)
        warning = self.query_one("#smt-warning", Static)
        warning.update(split or "")
        warning.display = bool(split)
        if not self.wizard.cpu_threads:
            return
        bar = cpu_bar(services, targets, self.wizard.cpu_threads)
        self.query_one("#cpu-bar", Static).update(f"[{bar}]  S services · T targets · X overlap")

    # --- models --------------------------------------------------------------------

    def _lock(self) -> dict[str, Any]:
        loaded: dict[str, Any] = SAFE.load(lock_text(self)) or {}
        return loaded

    def _fill_models(self) -> None:
        known = SAFE.load(self.ctx.paths.models_catalog.read_text(encoding="utf-8")) or {}
        profile = _select(self, "#profile")
        lock = {str(m.get("id")): m for m in self._lock().get("models") or []}
        serving = self.serving_answer()
        self.choices = model_choices(known, lock, profile, serving)
        fits = serving.get("models") or {}
        table = self.query_one("#models", DataTable)
        if not table.columns:
            table.add_columns("model", "repository", "license", "gated", "VRAM", "files", "pinned")
        table.clear()
        table.add_row("none", "-", "-", "-", "-", "-", "-", key="none")
        chosen = self.answers.gpu.default_model
        for choice in self.choices:
            if choice.fits and choice.id != chosen:
                continue  # only the models that fit, and the chosen one
            fit = (fits.get(choice.id) or {}).get(profile) or {}
            vram = (
                f"{capacity.up(float(fit['expected_vram_gib'])):.1f} GiB (estimated)"
                if fit.get("expected_vram_gib") is not None
                else "?"
            )
            entry = lock.get(choice.id)
            files = (
                capacity.gib(sum(int(f.get("size", 0)) for f in entry.get("files") or []) / 2**30)
                if entry
                else "pin to know"
            )
            table.add_row(
                choice.id,
                choice.hf_repo,
                choice.license,
                "yes" if choice.gated else "no",
                vram if not choice.fits else f"does not fit: {choice.fits}",
                files,
                "yes" if choice.pinned else "no",
                key=choice.id,
            )

    @work(thread=True, exclusive=True)
    def check_token(self, token: str) -> None:
        """Validate the Hugging Face token with the Hub's whoami.

        Args:
            token: The token.
        """
        try:
            name = models.whoami(token)
        except models.HubError as exc:
            self.app.call_from_thread(self.notify, f"token not accepted: {exc}", severity="error")
            return
        self.app.call_from_thread(self.notify, f"token of {name} accepted")

    @work(thread=True, exclusive=True)
    def pin(self) -> None:
        """Resolve the chosen (or custom) model and add it to models.lock.yml."""
        token = _value(self, "#hf") if self.query("#hf") else ""
        repo = _value(self, "#custom-repo") if self.query("#custom-repo") else ""
        ref = (_value(self, "#custom-ref") if self.query("#custom-ref") else "") or "main"
        chosen = next((c for c in self.choices if c.id == self.answers.gpu.default_model), None)
        meta: dict[str, Any]
        if repo:
            if error := validators.hf_repo(repo):
                self.app.call_from_thread(self.notify, error, severity="error")
                return
            model_id = repo.rsplit("/", 1)[-1].lower()
            meta = {"license": "", "gated": False, "notes": "custom model chosen in the installer"}
        elif chosen is not None:
            repo, model_id, meta = chosen.hf_repo, chosen.id, dict(chosen.entry)
        else:
            self.app.call_from_thread(
                self.notify, "choose a listed model or enter a repository", severity="error"
            )
            return
        try:
            revision = models.resolve_revision(repo, ref, token)
            files = models.pin_files(repo, revision, token)
        except models.HubError as exc:
            self.app.call_from_thread(self.notify, f"cannot pin {repo}: {exc}", severity="error")
            return
        text = lock_text(self)
        entry = models.lock_entry(model_id, repo, revision, files, meta)
        self.wizard.extra_files["models.lock.yml"] = models.add_to_lock(text, entry)
        self.answers.gpu.default_model = model_id
        self.app.call_from_thread(self._pinned, model_id, revision, len(files))

    def _pinned(self, model_id: str, revision: str, count: int) -> None:
        self.notify(f"pinned {model_id} at {revision[:12]} ({count} files)")
        self.query_one("#chosen", Static).update(f"Default model: {model_id} (pinned now)")
        self.recompute(now=True)

    # --- save ----------------------------------------------------------------------

    def save(self) -> Problems:  # noqa: D102
        problems: Problems = []
        problems += self._save_roles()
        problems += self._save_driver()
        problems += self._save_serving()
        problems += self._save_storage()
        problems += self._save_resources()
        if self.wizard.is_wsl:
            # The Windows driver provides the GPU on WSL; the NVIDIA driver page is greyed.
            self.answers.gpu.install_driver = "skip"
        return problems + self._save_consent(taken_back=False)

    def _save_consent(self, *, taken_back: bool) -> Problems:
        """The consent to what preflight finds degraded in the plan shown here.

        ``accept_degraded`` follows this tick alone: the answer here is the
        plan's, which bootstrap's preflight stage judges. A tick accepts the
        lines below it; the operator taking it back takes them back (an answer
        with new lines unticks it without taking back the old ones); without
        anything degraded the flag is not needed and goes.

        Args:
            taken_back: The operator just unticked the box.
        """
        found = self.query("#cap-consent")
        if not found:
            return []
        consent = found.first(Checkbox)
        wizard = self.wizard
        lines = set(capacity.degraded(self.answer.profile))
        if consent.display and consent.value:
            wizard.consents.add("accept_degraded")
            wizard.degraded_accepted |= lines
        else:
            wizard.consents.discard("accept_degraded")
            if taken_back:
                wizard.degraded_accepted -= lines
        if consent.display and not consent.value:
            return [("cap-consent", "what is degraded needs your consent: tick it above")]
        return []

    def action_next(self) -> None:  # noqa: D102
        # The consent is judged on the answer to these choices, once it is here.
        problems = [p for p in self.save() if p[0] != "cap-consent"]
        self.show_problems(problems)
        if problems:
            return
        self.recompute(now=True)  # nothing runs when the shown answer is for these choices
        if self.shown != self.sequence:
            self.next_wanted = True  # goes on when the answer to these choices arrives
            self._status("Next goes on once preflight has answered these choices…")
            return
        if consent := self._save_consent(taken_back=False):
            self.show_problems(consent)
            return
        wizard = self.wizard
        if waiting := capacity.waits(self.answer, wizard.consents, wizard.degraded_accepted):
            self.show_problems([("capacity", f"Next waits: {waiting}")])
            return
        self.proceed()

    def _save_roles(self) -> Problems:
        machine, features = self.answers.machine, self.answers.features
        problems: Problems = []
        machine.node_role = self.chosen(
            "node_role",
            machine.node_role,
            validators.offered(machine.node_role, NODE_ROLE_VALUES),
            problems,
        )
        peer = _value(self, "#peer")
        self.answers.split_inference_peer = peer if machine.node_role != "full" else ""
        if peer and machine.node_role != "full":
            if error := validators.hostname(peer):
                problems.append(("peer", error))
            elif peer == machine.name:
                problems.append(("peer", "the other node, not this machine"))
        features.inference = self.chosen(
            "inference",
            features.inference,
            validators.offered(features.inference, [v for _label, v in INFERENCE]),
            problems,
        )
        features.training = self.chosen(
            "training",
            features.training,
            validators.offered(features.training, [v for _label, v in TRAINING]),
            problems,
        )
        return problems

    def _save_driver(self) -> Problems:
        if not self.query("#install_driver"):
            return []
        gpu = self.answers.gpu
        problems: Problems = []
        gpu.install_driver = self.chosen(
            "install_driver",
            gpu.install_driver,
            validators.offered(gpu.install_driver, DRIVER_MODES),
            problems,
        )
        # A tuple compares, never hashes: a value of any type from a resumed state is safe.
        if self.wizard.mok_enrollment_needed and gpu.install_driver in DRIVER_MODES:
            # The Preflight screen judges with it too, and offers the MOK consent
            # unless it is "No driver" (as after "Continue without the driver").
            self.wizard.nvidia_driver = gpu.install_driver
        if self.wizard.mok_enrollment_needed and gpu.install_driver == "manual":
            # It would stop the installation at bootstrap's preflight (exit 30).
            problems.append(
                (
                    "install_driver",
                    "Secure Boot without a driver: install the driver yourself first and "
                    "Re-check on the Preflight screen, or choose Install the pinned driver "
                    "or No driver",
                )
            )
        return problems

    def _save_serving(self) -> Problems:
        if not self.query("#profile"):
            return []
        gpu = self.answers.gpu
        problems: Problems = []
        if gpu.vllm_profile and str(gpu.vllm_profile) not in self.profiles:
            gpu.vllm_profile = self.chosen(
                "profile",
                gpu.vllm_profile,
                validators.unknown_profile(gpu.vllm_profile, self.profiles),
                problems,
            )
        else:
            gpu.vllm_profile = _select(self, "#profile")
        token = _value(self, "#hf")
        self.wizard.provided.hf_token = token
        self.answers.secrets.hf_token = "provided" if token else "none"
        chosen = next((c for c in self.choices if c.id == gpu.default_model), None)
        lock_ids = {str(m.get("id")) for m in self._lock().get("models") or []}
        if gpu.default_model != "none" and gpu.default_model not in lock_ids:
            problems.append(("models", f"{gpu.default_model} is not pinned yet: press Pin model"))
        if chosen is not None and chosen.gated and not token:
            # The install downloads a gated model with the token, pinned or not.
            problems.append(("hf", "this model is gated: enter a Hugging Face token"))
        return problems

    def _save_storage(self) -> Problems:
        storage = self.answers.storage
        problems: Problems = []
        storage.source = "device" if self.query_one("#source-device", RadioButton).value else "loop"
        text = _value(self, "#loop_size")
        if storage.source == "loop":
            if text == "auto":
                storage.loop_size_gib = "auto"
            elif (size := validators.whole(text)) is None or size < MIN_LOOP_GIB:
                problems.append(
                    ("loop_size", f"auto, or at least {MIN_LOOP_GIB} GiB (at most nine digits)")
                )
            else:
                storage.loop_size_gib = size
        count = _value(self, "#targets")
        if error := validators.concurrent_targets_text(count):
            problems.append(("targets", error))
        else:
            self.answers.resources.concurrent_targets = int(count)
        retention = self.answers.telemetry_retention
        for field_id, name, check in (
            ("ret-prometheus", "prometheus", validators.retention_period),
            ("ret-size", "prometheus_size", validators.prometheus_size),
            ("ret-loki", "loki", validators.retention_period),
            ("ret-tempo", "tempo", lambda v: validators.retention_period(v, "h")),
        ):
            value = _value(self, f"#{field_id}")
            if error := check(value):
                problems.append((field_id, error))
            else:
                setattr(retention, name, value)
        return problems

    def _save_resources(self) -> Problems:
        resources = self.answers.resources
        problems: Problems = []
        for field_id, attr in (
            ("memory", "expected_memory_gib"),
            ("threads", "expected_cpu_threads"),
        ):
            text = _value(self, f"#{field_id}")
            number = validators.whole(text)
            setattr(resources, attr, number or None)
            if text and not number:
                problems.append((field_id, "a positive whole number (at most nine digits)"))
        resources.cpuset_services = _value(self, "#cpuset_services")
        resources.cpuset_targets = _value(self, "#cpuset_targets")
        for key in ("cpuset_services", "cpuset_targets"):
            value = getattr(resources, key)
            if value and (error := validators.cpuset(value, self.wizard.cpu_threads)):
                problems.append((key, error))
        if not problems and (
            error := validators.cpusets_disjoint(
                resources.cpuset_services, resources.cpuset_targets
            )
        ):
            problems.append(("cpuset_targets", error))
        resources.postgres_shared_buffers = _value(self, "#postgres")
        value = resources.postgres_shared_buffers
        if value and not (value[:-2].isdigit() and value[-2:] in ("kB", "MB", "GB")):
            problems.append(("postgres", "a size in kB, MB or GB, such as 2048MB"))
        return problems


class DeviceScreen(StepScreen):
    """Step 5, Storage device: the dedicated disk of the Incus pool."""

    STEP = "capacity_device"
    HELP = (
        "A dedicated disk is formatted only if you type its full /dev/disk/by-id path, and "
        "a disk that already carries signatures only after you also type WIPE. Mounted and "
        "system disks cannot be chosen."
    )

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self.disks: list[Disk] = []

    def not_applicable(self) -> str | None:  # noqa: D102
        if self.answers.storage.source != "device":
            return "the Incus pool is a loop file (Capacity plan): no disk to choose"
        return None

    def body(self) -> ComposeResult:  # noqa: D102
        table: DataTable[str] = DataTable(id="disks", cursor_type="row")
        yield table
        storage = self.answers.storage
        yield Static(
            self._device_text(), id="device", classes="banner bad" if storage.device else "hint"
        )

    def _device_text(self) -> str:
        storage = self.answers.storage
        if not storage.device:
            return "Select a disk and press Enter to choose it."
        wipe = " and wiped (signatures found)" if storage.force_wipe else ""
        return f"⚠ All data on {storage.device} will be destroyed{wipe}."

    def on_mount(self) -> None:  # noqa: D102
        if not self.query("#disks"):
            return
        self.disks = self.ctx.host.disks()
        table = self.query_one("#disks", DataTable)
        table.add_columns("disk", "by-id", "model", "size", "serial", "signatures", "state")
        for disk in self.disks:
            table.add_row(*disk_row(disk), key=disk.name)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:  # noqa: D102
        disk = next((d for d in self.disks if d.name == event.row_key.value), None)
        if disk is None:
            return
        if disk.usable:
            self.notify(f"{disk.name} cannot be the pool: {disk.usable}", severity="error")
            return
        text = (
            f"All data on {disk.name} ({disk.model}, {disk_row(disk)[3]}) will be destroyed when "
            "the Incus pool is created on it."
        )
        self.app.push_screen(
            TypedConfirm("Format a disk", text, disk.by_id), lambda ok: self._chosen(disk, ok)
        )

    def _chosen(self, disk: Disk, confirmed: bool | None) -> None:
        if not confirmed:
            return
        storage = self.answers.storage
        storage.device = storage.device_confirm = disk.by_id
        storage.force_wipe = False
        if disk.signatures:
            text = f"{disk.name} carries {', '.join(disk.signatures)}. Wipe them?"
            self.app.push_screen(
                TypedConfirm("Wipe existing signatures", text, "WIPE"), self._wiped
            )
        self.query_one("#device", Static).update(self._device_text())

    def _wiped(self, confirmed: bool | None) -> None:
        storage = self.answers.storage
        storage.force_wipe = bool(confirmed)
        if not confirmed:
            storage.device = storage.device_confirm = ""
        self.query_one("#device", Static).update(self._device_text())

    def save(self) -> Problems:  # noqa: D102
        if not self.answers.storage.device:
            return [("device", "choose a disk in the table (Enter) and confirm it")]
        return []


class DriverScreen(StepScreen):
    """Step 5, NVIDIA driver: the pinned packages, a GPU it cannot serve, the MOK password.

    The driver mode itself is chosen on the capacity plan's main page, whose
    preflight runs judge the GPU with it.
    """

    STEP = "capacity_driver"
    HELP = (
        "The driver is chosen on the capacity plan. 'Install the pinned driver' installs the "
        "pinned NVIDIA driver branch on bare metal and VMs (never on WSL, where the Windows "
        "driver provides the GPU). With Secure Boot and no driver it needs your consent on "
        "the Preflight screen: the driver's module is signed by a Machine Owner Key, which "
        "the installation imports with a one-time password you enter here; you enroll the "
        "key in MOK Manager at the next boot. 'I install it myself' stops the installation "
        "until you installed the driver yourself."
    )

    def not_applicable(self) -> str | None:  # noqa: D102
        if self.wizard.is_wsl:
            return "WSL: the Windows driver provides the GPU; no Linux driver is installed"
        if not self.wizard.has_gpu:
            return "no GPU detected; no NVIDIA driver is installed"
        return None  # also on a lab node: the play installs the driver on every node role

    def body(self) -> ComposeResult:  # noqa: D102
        gpu = self.answers.gpu
        info = self.wizard.gpu
        release = (self.wizard.profile.get("os") or {}).get("release") or ""
        if self.wizard.gpu_unsupported:
            pci = info.get("pci_device") or "?"
            reason = (
                f"the pinned driver supports this GPU (PCI {pci}) only with its proprietary "
                "kernel module (Maxwell, Pascal, Volta: below compute capability 7.5), and a "
                "driver is installed only for a GPU its open kernel module supports (Turing or "
                "later)"
                if info.get("proprietary_only_drivers")
                else f"the pinned driver does not support this GPU (PCI {pci}): only an older "
                "legacy branch does, or NVIDIA's list does not name it"
            )
            yield Static(
                f"Not usable: {reason}. No driver is installed for it automatically and the lab "
                "installs without the GPU features.",
                classes="banner warn",
                id="gpu-unsupported",
            )
        yield Static(
            f"NVIDIA driver: {DRIVER_LABELS.get(str(gpu.install_driver), gpu.install_driver)} "
            "(chosen on the capacity plan)",
            id="driver",
            classes="info",
            markup=False,  # a value read from host_vars or a resumed state
        )
        yield Static(driver_pin(self.ctx.paths.versions_lock, str(release)), classes="hint")
        if self.wizard.mok_enrollment_needed and gpu.install_driver == "auto":
            yield Field("MOK password (one time)", Input(password=True, id="mok"))
            yield Field("MOK password again", Input(password=True, id="mok2"))

    def save(self) -> Problems:  # noqa: D102
        # Secure Boot without a driver: auto needs the consent given on the Preflight
        # screen and the one-time MOK password (manual is refused on the capacity plan).
        gpu = self.answers.gpu
        wizard = self.wizard
        if not wizard.mok_enrollment_needed:
            return []
        wizard.nvidia_driver = gpu.install_driver
        if gpu.install_driver == "skip":
            wizard.consents.discard("accept_mok_enrollment")
            gpu.accept_mok_enrollment = False
            wizard.mok_password = ""
            return []
        problems: Problems = []
        if "accept_mok_enrollment" not in wizard.consents:
            problems.append(
                ("driver", "Secure Boot: consent to the MOK enrollment on the Preflight screen")
            )
        first, second = _value(self, "#mok"), _value(self, "#mok2")
        if not first or first != second:
            problems.append(("mok", "enter the same one-time password twice"))
        wizard.mok_password = first if first == second else ""
        gpu.accept_mok_enrollment = "accept_mok_enrollment" in wizard.consents
        return problems


class ServingScreen(StepScreen):
    """Step 5, Serving: starting the default model, the GPU arbiter and the training caps."""

    STEP = "capacity_serving"
    HELP = (
        "vLLM and training jobs share the GPU through the GPU arbiter. After boot it serves "
        "the default deployment or stays idle; a training run stops serving and, if you "
        "choose, serving resumes when the run ends. The caps bound every run of a training "
        "job; the arbiter refuses a run over them."
    )

    def not_applicable(self) -> str | None:  # noqa: D102
        return serving_unavailable(self)

    def body(self) -> ComposeResult:  # noqa: D102
        gpu = self.answers.gpu
        yield Checkbox("Start serving after the install", gpu.start_serving, id="serve")
        arbiter, caps = self.answers.gpu_arbiter, self.answers.training_caps
        yield Static("GPU arbiter: vLLM and training jobs share the GPU", classes="info")
        with RadioSet(id="after-boot"):
            yield RadioButton(
                "After boot: serve the default deployment",
                value=arbiter.after_boot == "serve",
                id="boot-serve",
            )
            yield RadioButton(
                "After boot: stay idle until asked",
                value=arbiter.after_boot == "idle",
                id="boot-idle",
            )
        yield Checkbox(
            "Serve the deployment again when a training run ends",
            arbiter.restore_after_training,
            id="restore-serving",
        )
        if self.answers.machine.node_role != "full":
            yield Static(
                f"Training is unavailable here ({self.answers.machine.node_role} node): "
                "only a full node runs training jobs.",
                classes="hint",
            )
            return
        yield Static(
            "Training caps: the GPU arbiter refuses every run of a job over them",
            classes="info",
        )
        with Horizontal(classes="inline"):
            yield Field("Run time (s)", Input(str(caps.timeout_seconds), id="cap-timeout"))
            yield Field("CPUs", Input(str(caps.cpus), id="cap-cpus"))
            yield Field("Memory", Input(caps.memory_max, id="cap-memory"))
        with Horizontal(classes="inline"):
            yield Field("Dataset", Input(caps.dataset_max, id="cap-dataset"))
            yield Field("Output and checkpoints", Input(caps.run_storage_max, id="cap-storage"))
            yield Field("Job tmpfs", Input(caps.tmp_max, id="cap-tmp"))

    def save(self) -> Problems:  # noqa: D102
        self.answers.gpu.start_serving = self.query_one("#serve", Checkbox).value
        arbiter, caps = self.answers.gpu_arbiter, self.answers.training_caps
        arbiter.after_boot = "idle" if self.query_one("#boot-idle", RadioButton).value else "serve"
        arbiter.restore_after_training = self.query_one("#restore-serving", Checkbox).value
        if self.answers.machine.node_role != "full":
            return []
        problems: Problems = []
        for field_id, name, minimum in (
            ("cap-timeout", "timeout_seconds", 60),
            ("cap-cpus", "cpus", 1),
        ):
            text = _value(self, f"#{field_id}")
            if (number := validators.whole(text)) is None or number < minimum:
                problems.append(
                    (field_id, f"a whole number of at least {minimum} (at most nine digits)")
                )
            else:
                setattr(caps, name, number)
        for field_id, name, check in (
            ("cap-memory", "memory_max", validators.memory_max),
            ("cap-dataset", "dataset_max", validators.cap_size),
            ("cap-storage", "run_storage_max", validators.cap_size),
            ("cap-tmp", "tmp_max", validators.cap_size),
        ):
            value = _value(self, f"#{field_id}")
            if error := check(value):
                problems.append((field_id, error))
            else:
                setattr(caps, name, value)
        return problems
