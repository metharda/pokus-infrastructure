"""The Incus pool's count: everything that may be in its thin pool, at its worst case.

The uncommitted space is the thin pool's data size minus the count. It never
reads higher than it is, because nothing here reads what a volume has
written: every logical volume of the thin pool counts at a size it can never
exceed.

- An image volume counts at its full size (one Incus lists but has not
  created yet at the pool's ``volume.size``, a VM image's state volume with it).
- A project counts as disk-limited only by ``limits.disk``. Incus 6.0.7 checks
  ``limits.disk.pool.<pool>`` when an instance is created, but a custom
  volume's creation and update skip every disk check when ``limits.disk`` is
  unset (``internal/server/project/permissions.go:345-348`` and
  ``1089-1092``), so the per-pool key only lowers ``limits.disk``. A limited
  project counts its volumes, each at the larger of its real size and the size
  Incus counts for it (a volume Incus lists but has not created yet at its
  counted size in whole extents), plus what its limit still leaves beyond the
  sizes Incus counts. ``limits.disk`` bounds only what Incus counts: what it
  does not (a backup import's snapshots, a disk restored larger than its
  ``size``) counts from the moment it exists, through the larger-of rule and
  the full size of every snapshot.
- An instance's volume whose instance the instance listing lacks counts outside
  every project's limit, at the larger of its real and counted size: Incus
  counts only the instances it holds.
- Every other volume of the thin pool (snapshots, temporary volumes, buckets,
  volumes of a project without a limit, anything no name matches) counts at its
  full size; a volume of a project without a limit that Incus lists but has not
  created yet, at its configured size in whole extents.

What the count does not bound: storage Incus does not count (a project without
a limit, a bucket, a backup import) can be added later; it counts from the
moment it exists. pinka's projects all have a limit, and doctor fails while any
bucket exists on the pool.

Volumes are recognised by generating the logical volume names Incus 6.0.7
gives what it lists (``lvmFullVolumeName`` in
``internal/server/storage/drivers/driver_lvm_utils.go``): never by parsing a
name. The design note is ``docs/explanation/incus-pool-accounting.md``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

GIB = 1024**3
# Incus's default size of a VM's state volume (size.state) and of a block
# volume (volume.size unset): internal/server/device/config/consts.go and
# internal/server/storage/drivers/volume.go.
DEFAULT_VM_STATE = 500 * 1024**2
DEFAULT_VOLUME_SIZE = 10 * GIB
DEFAULT_THINPOOL = "IncusThinPool"
# The lvs fields the count reads, in this order.
LVS_FIELDS = (
    "lv_name",
    "lv_size",
    "pool_lv",
    "data_percent",
    "metadata_percent",
    "lv_metadata_size",
)
# Incus's volume types as its LVM driver names their logical volumes.
LV_PREFIX = {
    "container": "containers",
    "virtual-machine": "virtual-machines",
    "custom": "custom",
    "image": "images",
}
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([KMGTPE]i?B|B)?", re.IGNORECASE)
POWERS = {"K": 1, "M": 2, "G": 3, "T": 4, "P": 5, "E": 6}


class PoolError(RuntimeError):
    """The pool's state cannot be read completely."""


@dataclass(frozen=True)
class LogicalVolume:
    """One row of ``lvs``.

    Attributes:
        name: The logical volume's name.
        size: Its size in bytes.
        pool_lv: The thin pool it belongs to, empty for none.
        data_percent: Data usage, None when lvs gives none (inactive).
        metadata_percent: Metadata usage (thin pools only), None when none.
        metadata_size: The metadata volume's size in bytes (thin pools only).
    """

    name: str
    size: int
    pool_lv: str
    data_percent: float | None
    metadata_percent: float | None
    metadata_size: int | None


@dataclass
class Count:
    """The count of a thin pool, by part.

    Attributes:
        data_size: The thin pool's data size in bytes.
        images: The image volumes' full sizes.
        projects: Per project with a disk limit, its volumes and its room left.
        rest: Every other logical volume of the thin pool, at its full size.
        unmatched: The names of the logical volumes no generated name matched.
    """

    data_size: int
    images: int = 0
    projects: dict[str, int] = field(default_factory=dict)
    rest: int = 0
    unmatched: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Everything that may be in the thin pool, in bytes."""
        return self.images + sum(self.projects.values()) + self.rest

    @property
    def uncommitted(self) -> int:
        """The data size minus the count, in bytes (negative when overcommitted)."""
        return self.data_size - self.total

    def describe(self) -> str:
        """The count by part, for a message."""
        parts = [f"images {gib(self.images)}"]
        parts += [f"project {name} {gib(size)}" for name, size in sorted(self.projects.items())]
        parts.append(f"other volumes {gib(self.rest)}")
        return f"data size {gib(self.data_size)}; counted: " + ", ".join(parts)


def gib(value: int | float) -> str:
    """Bytes as GiB for a message."""
    return f"{value / GIB:.1f} GiB"


def size_bytes(text: str) -> int:
    """An Incus or LVM size in bytes (``3GiB``, ``500MiB``, ``10GB``, ``1073741824``).

    Args:
        text: The size.

    Returns:
        Bytes.

    Raises:
        PoolError: If the text is no size.
    """
    match = SIZE_RE.fullmatch(text.strip())
    if match is None:
        raise PoolError(f"not a size: {text!r}")
    number, unit = float(match.group(1)), (match.group(2) or "B").upper()
    if unit == "B":
        return int(number)
    base = 1024 if unit.endswith("IB") else 1000
    return int(number * base ** POWERS[unit[0]])


def _number(text: str) -> float | None:
    text = text.strip()
    return float(text) if text else None


def parse_lvs(text: str) -> list[LogicalVolume]:
    """Parse ``lvs --noheadings --units b --nosuffix --separator , -o <LVS_FIELDS>``.

    Args:
        text: lvs's output.

    Returns:
        One entry per row.

    Raises:
        PoolError: If a row has another number of fields or no size.
    """
    found = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != len(LVS_FIELDS) or not cells[1].isdigit():
            raise PoolError(f"unexpected lvs row: {line.strip()!r}")
        meta = cells[5]
        found.append(
            LogicalVolume(
                name=cells[0],
                size=int(cells[1]),
                pool_lv=cells[2],
                data_percent=_number(cells[3]),
                metadata_percent=_number(cells[4]),
                metadata_size=int(meta) if meta.isdigit() else None,
            )
        )
    return found


def lv_name(volume_type: str, content_type: str, storage_name: str) -> str:
    """The logical volume name Incus 6.0.7's LVM driver gives a volume.

    ``lvmFullVolumeName``: the volume type's prefix, the name with ``-``
    doubled and the snapshot delimiter ``/`` as ``-``, and ``.block`` or
    ``.iso`` for those content types.

    Args:
        volume_type: ``container``, ``virtual-machine``, ``custom`` or ``image``.
        content_type: ``filesystem``, ``block`` or ``iso``.
        storage_name: The volume's storage name (project-prefixed where Incus prefixes it).

    Returns:
        The logical volume's name.
    """
    escaped = storage_name.replace("-", "--").replace("/", "-")
    suffix = {"block": ".block", "iso": ".iso"}.get(content_type, "")
    return f"{LV_PREFIX[volume_type]}_{escaped}{suffix}"


def project_limit(config: Mapping[str, Any], pool: str) -> int | None:
    """A project's disk limit on the pool: ``limits.disk``, lowered by the per-pool key.

    ``limits.disk.pool.<pool>`` alone limits nothing Incus counts (a custom
    volume skips every disk check without ``limits.disk``), so it only lowers
    ``limits.disk``. ``limits.disk`` bounds what Incus counts; what it does not
    count is counted from the moment it exists.

    Args:
        config: The project's configuration.
        pool: The pool's name.

    Returns:
        Bytes, or None when the project has no ``limits.disk``.
    """
    disk = str(config.get("limits.disk") or "")
    if not disk:
        return None
    per_pool = str(config.get(f"limits.disk.pool.{pool}") or "")
    values = [size_bytes(disk)] + ([size_bytes(per_pool)] if per_pool else [])
    return min(values)


def _root(instance: Mapping[str, Any]) -> Mapping[str, Any]:
    """An instance's root disk device (expanded), or an empty mapping."""
    devices = instance.get("expanded_devices") or {}
    for device in devices.values():
        if (
            isinstance(device, Mapping)
            and device.get("type") == "disk"
            and device.get("path") == "/"
        ):
            return device
    return {}


def expected_volumes(
    volumes: Sequence[Mapping[str, Any]],
    instances: Sequence[Mapping[str, Any]],
    volume_size: int = DEFAULT_VOLUME_SIZE,
) -> dict[str, tuple[str, str, int]]:
    """The logical volumes Incus's listing says the pool holds.

    Args:
        volumes: ``/1.0/storage-pools/<pool>/volumes?recursion=1&all-projects=true``.
        instances: ``/1.0/instances?recursion=1&all-projects=true``.
        volume_size: The pool's ``volume.size`` (an image volume's size, and a
            volume's without its own ``size``).

    Returns:
        Logical volume name to ``(kind, project, counted)``: kind ``image`` or
        ``volume``; counted, the size the volume takes once created (an image's
        ``volume.size``, a VM's state volume; a volume's configured size, the
        one Incus counts against the project's limit, else ``volume.size``).
        An instance volume whose instance is not in the instance listing (an
        instance deleted, renamed or moved between the two listings, or a
        record an interrupted delete left) has project ``""``: Incus counts
        only the instances it holds against a limit, so the volume counts
        outside every project's limit, at the larger of its real and counted
        size.
    """
    roots = {(str(i.get("project") or "default"), str(i.get("name"))): _root(i) for i in instances}
    expected: dict[str, tuple[str, str, int]] = {}
    for volume in volumes:
        kind = str(volume.get("type"))
        name = str(volume.get("name"))
        project = str(volume.get("project") or "default")
        content = str(volume.get("content_type") or "filesystem")
        if kind not in LV_PREFIX or "/" in name:
            continue  # a snapshot or an unknown type: its LV counts at full size
        if kind == "image":
            if content == "block":  # a VM image: its block volume and its state volume
                expected[lv_name(kind, "filesystem", name)] = ("image", project, DEFAULT_VM_STATE)
                expected[lv_name(kind, "block", name)] = ("image", project, volume_size)
            else:
                expected[lv_name(kind, "filesystem", name)] = ("image", project, volume_size)
            continue
        if kind == "custom":
            size = str((volume.get("config") or {}).get("size") or "")
            counted = size_bytes(size) if size else volume_size
            expected[lv_name(kind, content, f"{project}_{name}")] = ("volume", project, counted)
            continue
        storage = name if project == "default" else f"{project}_{name}"
        root = roots.get((project, name))
        if root is None:
            root, project = {}, ""  # no instance listed: outside every project's limit
        size = str(root.get("size") or "")
        counted = size_bytes(size) if size else volume_size
        if kind == "container":
            expected[lv_name(kind, "filesystem", storage)] = ("volume", project, counted)
            continue
        state = str(root.get("size.state") or "")
        expected[lv_name(kind, "filesystem", storage)] = (
            "volume",
            project,
            size_bytes(state) if state else DEFAULT_VM_STATE,
        )
        expected[lv_name(kind, "block", storage)] = ("volume", project, counted)
    return expected


def count(
    thin_pool: LogicalVolume,
    lvs: Sequence[LogicalVolume],
    expected: Mapping[str, tuple[str, str, int]],
    limits: Mapping[str, int | None],
    extent: int = 1,
) -> Count:
    """Count everything that may be in the thin pool.

    Args:
        thin_pool: The thin pool's own row.
        lvs: Every row of the volume group.
        expected: :func:`expected_volumes`.
        limits: Every project's disk limit (:func:`project_limit`), None for none.
        extent: The volume group's extent size: a volume not created yet will
            take its size rounded up to whole extents (LVM rounds every
            logical volume so; Incus counts the size itself).

    Returns:
        The count.
    """
    result = Count(data_size=thin_pool.size)
    taken: dict[str, int] = {p: 0 for p, limit in limits.items() if limit is not None}
    counted: dict[str, int] = dict.fromkeys(taken, 0)
    thin = {lv.name: lv for lv in lvs if lv.pool_lv == thin_pool.name}
    for name, lv in sorted(thin.items()):
        kind, project, size = expected.get(name, ("", "", 0))
        if kind == "image":
            result.images += max(lv.size, size)
        elif kind == "volume" and project in taken:
            taken[project] += max(lv.size, size)
            counted[project] += size
        elif kind == "volume":
            result.rest += max(lv.size, size)
        else:
            result.rest += lv.size
            result.unmatched.append(name)
    for name, (kind, project, size) in expected.items():
        if name in thin:
            continue
        # Listed, not created yet: it will take its size in whole extents.
        rounded = -(-size // max(1, extent)) * max(1, extent)
        if kind == "image":
            result.images += rounded
        elif project in taken:
            taken[project] += rounded
            counted[project] += size
        else:
            result.rest += rounded
    for project, limit in limits.items():
        if limit is not None:
            result.projects[project] = taken[project] + max(0, limit - counted[project])
    return result
