"""Screens 4-6: Machine, Resources, Storage."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import DataTable, Input, RadioButton, RadioSet, Select, Static

from pinka_installer import validators
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.host import Disk
from pinka_installer.tui.model import (
    LOOP_RESERVE_GIB,
    loop_size_limit,
    suggest_cpusets,
    suggest_postgres,
)
from pinka_installer.tui.widgets import Field, TypedConfirm

LAB_MODES = [("production", "production"), ("staging (QA: tag:pinka-qa, own vault)", "staging")]
NODE_ROLES = [
    ("full: everything on this host", "full"),
    ("lab: lab services, inference on another node", "lab"),
    ("inference: vLLM only, paired with a lab node", "inference"),
]
POWER_POLICIES = [
    ("keep awake while a lab session runs", "lab_session_inhibit"),
    ("never sleep", "never_sleep"),
    ("the OS default", "os_default"),
]
MIN_LOOP_GIB = 20  # the answers schema's minimum
PROVIDERS = [
    ("auto (from preflight)", "auto"),
    ("bare metal", "baremetal"),
    ("VM", "vm"),
    ("WSL2", "wsl2"),
]


def _value(screen: StepScreen, selector: str) -> str:
    return screen.query_one(selector, Input).value.strip()


def _select(screen: StepScreen, selector: str) -> str:
    value = screen.query_one(selector, Select).value
    return "" if value is Select.BLANK else str(value)


class MachineScreen(StepScreen):
    """Screen 4: name, lab mode, node role, provider, power policy, paths."""

    STEP = "machine"
    HELP = (
        "The machine name names the host_vars file and the tailnet host; it must be unique "
        "in your configuration. Staging hosts are QA labs with their own vault, research "
        "space and Tailscale identity. A lab node runs the lab services and uses the GPU of "
        "an inference node, which it is paired with after both are installed (pinka "
        "pair). The provider comes from preflight; change it only when preflight's "
        "confidence is low. /srv/pinka needs a file system with room for the research space."
    )

    def body(self) -> ComposeResult:  # noqa: D102
        machine = self.answers.machine
        yield Field("Machine name", Input(machine.name or self.ctx.machine, id="name"))
        yield Field(
            "Lab mode", Select(LAB_MODES, value=machine.lab_mode, allow_blank=False, id="lab_mode")
        )
        yield Field(
            "Node role",
            Select(NODE_ROLES, value=machine.node_role, allow_blank=False, id="node_role"),
        )
        yield Field(
            "Paired node (optional)",
            Input(
                self.answers.split_inference_peer,
                id="peer",
                placeholder="pair later with pinka pair",
            ),
            hint="lab and inference nodes only: the machine name of the other node",
        )
        confidence = (self.wizard.profile.get("environment") or {}).get("confidence")
        detected = self.wizard.provider or "unknown"
        if confidence == "low":
            yield Field(
                f"Provider (detected: {detected}, low confidence)",
                Select(PROVIDERS, value=machine.provider, allow_blank=False, id="provider"),
            )
        else:
            yield Static(f"Provider: {detected} (from preflight)", classes="info")
        power = Select(
            POWER_POLICIES, value=machine.power_policy, allow_blank=False, id="power_policy"
        )
        power.disabled = self.wizard.is_wsl
        yield Field(
            "Power policy",
            power,
            hint="not on WSL: Windows decides when the host sleeps" if self.wizard.is_wsl else "",
        )
        yield Field("Research space (/srv/pinka)", Input(machine.srv_path, id="srv_path"))
        yield Static("", id="srv-check", classes="hint")
        yield Field("Docker data root", Input(machine.docker_data_root, id="docker_data_root"))

    def on_mount(self) -> None:  # noqa: D102
        self._check_srv()

    def on_input_changed(self, event: Input.Changed) -> None:  # noqa: D102
        if event.input.id == "srv_path":
            self._check_srv()

    def _check_srv(self) -> None:
        found = self.query("#srv-check")
        if not found:
            return
        path = _value(self, "#srv_path")
        if validators.absolute_path(path):
            return
        try:
            free, fstype = self.ctx.host.free_gib(path)
        except OSError:
            return
        self.query_one("#srv-check", Static).update(f"{free:.0f} GiB free on {fstype or 'unknown'}")

    def save(self) -> Problems:  # noqa: D102
        machine = self.answers.machine
        problems: Problems = []
        machine.name = _value(self, "#name")
        if error := validators.hostname(machine.name):
            problems.append(("name", error))
        elif self.answers.mode != "modify" and self.ctx.paths.host_vars(machine.name).exists():
            problems.append(
                ("name", "host_vars exist for this name: choose Modify, or another name")
            )
        machine.lab_mode = _select(self, "#lab_mode")
        machine.node_role = _select(self, "#node_role")
        peer = _value(self, "#peer")
        self.answers.split_inference_peer = peer if machine.node_role != "full" else ""
        if peer and machine.node_role != "full":
            if error := validators.hostname(peer):
                problems.append(("peer", error))
            elif peer == machine.name:
                problems.append(("peer", "the other node, not this machine"))
        if self.query("#provider"):
            machine.provider = _select(self, "#provider")
        machine.power_policy = (
            "os_default" if self.wizard.is_wsl else _select(self, "#power_policy")
        )
        for key in ("srv_path", "docker_data_root"):
            setattr(machine, key, _value(self, f"#{key}"))
            if error := validators.absolute_path(getattr(machine, key)):
                problems.append((key, error))
        return problems


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
        s = validators.parse_cpuset(services)
        t = validators.parse_cpuset(targets)
    except ValueError:
        return ""
    cells = []
    for cpu in range(threads):
        cells.append(
            "X" if cpu in s and cpu in t else "S" if cpu in s else "T" if cpu in t else "·"
        )
    return "".join(cells)


class ResourcesScreen(StepScreen):
    """Screen 5: detected CPU and memory, expected values, the CPU split, Postgres memory."""

    STEP = "resources"
    # The host's cores with more than one thread, read when the screen mounts.
    cores: tuple[frozenset[int], ...] = ()
    HELP = (
        "Lab services and Incus targets never share CPU cores, so measurements of the "
        "targets are not disturbed by the lab itself. The suggestion gives about 30 % of "
        "the cores to the services and keeps both threads of a core (SMT siblings) in one "
        "set; a warning shows when your split puts them in different sets. On WSL the "
        "effective limits come from .wslconfig on the Windows side; enter the values you "
        "expect there so doctor can warn when they differ."
    )

    def body(self) -> ComposeResult:  # noqa: D102
        threads, memory = self.wizard.cpu_threads, self.wizard.memory_gib
        resources = self.answers.resources
        self.cores = tuple(self.ctx.host.smt_cores())
        services, targets = suggest_cpusets(threads, self.cores)
        yield Static(
            f"Detected: {threads or '?'} CPU threads, {memory or '?'} GiB memory", classes="info"
        )
        if self.wizard.is_wsl:
            yield Static(
                "WSL: the limits come from %UserProfile%\\.wslconfig on Windows "
                "(memory=, processors=). Enter the values you set there.",
                classes="banner warn",
            )
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
                str(resources.expected_cpu_threads or threads or ""), id="threads", type="integer"
            ),
        )
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

    def on_mount(self) -> None:  # noqa: D102
        self._update_bar()

    def on_input_changed(self, event: Input.Changed) -> None:  # noqa: D102
        if event.input.id in ("cpuset_services", "cpuset_targets"):
            self._update_bar()

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

    def save(self) -> Problems:  # noqa: D102
        resources = self.answers.resources
        problems: Problems = []
        for field_id, attr in (
            ("memory", "expected_memory_gib"),
            ("threads", "expected_cpu_threads"),
        ):
            text = _value(self, f"#{field_id}")
            setattr(resources, attr, int(text) if text.isdigit() and int(text) > 0 else None)
            if text and not (text.isdigit() and int(text) > 0):
                problems.append((field_id, "a positive whole number"))
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


def disk_row(disk: Disk) -> tuple[str, ...]:
    """A row of the disk table.

    Args:
        disk: The disk.

    Returns:
        Name, by-id, model, size, serial, signatures, state.
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


class StorageScreen(StepScreen):
    """Screen 6: the Incus pool on a loop file or a dedicated disk."""

    STEP = "storage"
    HELP = (
        "The sandbox targets live in an Incus storage pool on btrfs, or on LVM thin when "
        "btrfs is missing. A loop file is the simple choice; a dedicated disk is faster. A "
        "dedicated disk is formatted only if you type its full /dev/disk/by-id path, and a "
        "disk that already carries signatures only after you also type WIPE. Mounted and "
        "system disks cannot be chosen."
    )

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self.disks: list[Disk] = []
        self.free_gib: float | None = None

    def body(self) -> ComposeResult:  # noqa: D102
        storage = self.answers.storage
        backend = self.wizard.storage_backend or "unknown"
        yield Static(f"Pool backend: {backend} (from preflight)", classes="info")
        with RadioSet(id="source"):
            yield RadioButton(
                "Loop file inside this system", value=storage.source == "loop", id="source-loop"
            )
            yield RadioButton(
                "Dedicated disk", value=storage.source == "device", id="source-device"
            )
        yield Field(
            "Loop file size (GiB, or auto)",
            Input(str(storage.loop_size_gib), id="loop_size"),
        )
        yield Static("", id="loop-check", classes="hint")
        table: DataTable[str] = DataTable(id="disks", cursor_type="row")
        yield table
        yield Static(
            self._device_text(), id="device", classes="banner bad" if storage.device else "hint"
        )

    def _device_text(self) -> str:
        storage = self.answers.storage
        if storage.source != "device" or not storage.device:
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
        try:
            free, _ = self.ctx.host.free_gib("/var/lib")
        except OSError:
            return
        self.free_gib = free
        self.query_one("#loop-check", Static).update(
            f"{free:.0f} GiB free in /var/lib; at most {loop_size_limit(free)} GiB "
            f"keeps {LOOP_RESERVE_GIB} GiB in reserve; auto: preflight sizes it from the "
            "targets, the golden images and the free space"
        )

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
        storage.source = "device"
        storage.device = storage.device_confirm = disk.by_id
        storage.force_wipe = False
        self.query_one("#source-device", RadioButton).value = True
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
        storage = self.answers.storage
        problems: Problems = []
        storage.source = "device" if self.query_one("#source-device", RadioButton).value else "loop"
        text = _value(self, "#loop_size")
        if storage.source == "loop":
            if text == "auto":
                storage.loop_size_gib = "auto"
            elif not text.isdigit() or int(text) < MIN_LOOP_GIB:
                problems.append(("loop_size", f"auto, or at least {MIN_LOOP_GIB} GiB"))
            else:
                storage.loop_size_gib = int(text)
                free = self.free_gib
                if free is not None and storage.loop_size_gib > loop_size_limit(free):
                    problems.append(("loop_size", f"at most {loop_size_limit(free)} GiB fits here"))
        elif not storage.device:
            problems.append(("device", "choose a disk in the table (Enter) and confirm it"))
        return problems
