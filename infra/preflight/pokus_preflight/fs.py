"""Read-only, root-relative filesystem access.

Detection code never touches the filesystem directly. It asks a
:class:`FileSystem` for paths as they appear on the inspected host
(``/etc/os-release``, ``/proc/1/comm`` ...). With the default root ``/`` this
is the real host. With ``--root <fixture>`` every path is resolved inside the
fixture directory, and properties that a plain directory cannot carry
(character devices, symlinks, read-write openability) come from the fixture's
``fixture.json``::

    {
      "char_devices": ["/dev/kvm", "/dev/net/tun"],
      "rw_openable": ["/dev/kvm"],
      "symlinks": {"/var/run/docker.sock": "/mnt/wsl/docker-desktop/docker.sock"}
    }

Paths are normalized before use, so ``..`` can never leave the root.
"""

from __future__ import annotations

import fnmatch
import json
import os
import posixpath
import stat

FIXTURE_META = "fixture.json"
KEEP_FILE = ".fixture-keep"
DEFAULT_MAX_BYTES = 1024 * 1024


class FileSystem:
    """Root-relative, read-only view of a host filesystem.

    Attributes:
        root: Absolute path of the inspected root (``/`` for the real host).
        is_fixture: True when ``root`` is not ``/``.
    """

    def __init__(self, root: str = "/") -> None:
        """Create a view rooted at ``root``.

        Args:
            root: Directory that plays the role of ``/``.
        """
        self.root = os.path.abspath(root)
        self.is_fixture = self.root != "/"
        self._meta: dict[str, object] = self._load_meta() if self.is_fixture else {}

    def _load_meta(self) -> dict[str, object]:
        path = os.path.join(self.root, FIXTURE_META)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: fixture metadata must be a JSON object")
        return data

    def _meta_list(self, key: str) -> list[str]:
        value = self._meta.get(key, [])
        return [str(v) for v in value] if isinstance(value, list) else []

    def _meta_symlinks(self) -> dict[str, str]:
        value = self._meta.get("symlinks", {})
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}

    @staticmethod
    def normalize(path: str) -> str:
        """Normalize an absolute target path.

        Args:
            path: Absolute path on the inspected host.

        Returns:
            The normalized path; ``..`` components cannot climb above ``/``.

        Raises:
            ValueError: If the path is not absolute.
        """
        if not path.startswith("/"):
            raise ValueError(f"path must be absolute: {path!r}")
        norm = posixpath.normpath(path)
        return "/" + norm.lstrip("/")

    def host_path(self, path: str) -> str:
        """Map a target path to the path that is actually opened.

        Args:
            path: Absolute path on the inspected host.

        Returns:
            The real filesystem path below ``root``.
        """
        norm = self.normalize(path)
        if not self.is_fixture:
            return norm
        return os.path.join(self.root, norm.lstrip("/"))

    def is_symlink(self, path: str) -> bool:
        """Tell whether a path is a symbolic link.

        Args:
            path: Absolute target path.

        Returns:
            True for symbolic links.
        """
        if self.is_fixture:
            return self.normalize(path) in self._meta_symlinks()
        return os.path.islink(self.host_path(path))

    def readlink(self, path: str) -> str | None:
        """Return the target of a symbolic link.

        Args:
            path: Absolute target path.

        Returns:
            The link target, or None if the path is not a link.
        """
        if self.is_fixture:
            return self._meta_symlinks().get(self.normalize(path))
        try:
            return os.readlink(self.host_path(path))
        except OSError:
            return None

    def exists(self, path: str) -> bool:
        """Tell whether a path exists (links count even when dangling).

        Args:
            path: Absolute target path.

        Returns:
            True if the path exists.
        """
        return self.is_symlink(path) or os.path.lexists(self.host_path(path))

    def is_file(self, path: str) -> bool:
        """Tell whether a path is a regular file.

        Args:
            path: Absolute target path.

        Returns:
            True for regular files.
        """
        return os.path.isfile(self.host_path(path))

    def is_dir(self, path: str) -> bool:
        """Tell whether a path is a directory.

        Args:
            path: Absolute target path.

        Returns:
            True for directories.
        """
        return os.path.isdir(self.host_path(path))

    def is_char_device(self, path: str) -> bool:
        """Tell whether a path is a character device.

        Args:
            path: Absolute target path.

        Returns:
            True for character devices.
        """
        if self.is_fixture:
            return self.normalize(path) in self._meta_list("char_devices") and self.exists(path)
        try:
            return stat.S_ISCHR(os.stat(self.host_path(path)).st_mode)
        except OSError:
            return False

    def can_open_rw(self, path: str) -> bool:
        """Tell whether a device can be opened read-write by this process.

        Args:
            path: Absolute target path.

        Returns:
            True if ``open(O_RDWR)`` succeeds.
        """
        if self.is_fixture:
            return self.normalize(path) in self._meta_list("rw_openable") and self.exists(path)
        try:
            fd = os.open(self.host_path(path), os.O_RDWR | os.O_CLOEXEC)
        except OSError:
            return False
        os.close(fd)
        return True

    def read_bytes(self, path: str, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes | None:
        """Read up to ``max_bytes`` of a file.

        Args:
            path: Absolute target path.
            max_bytes: Upper bound on the bytes read.

        Returns:
            The content, or None if the file cannot be read.
        """
        try:
            with open(self.host_path(path), "rb") as handle:
                return handle.read(max_bytes)
        except OSError:
            return None

    def read_text(self, path: str, max_bytes: int = DEFAULT_MAX_BYTES) -> str | None:
        """Read a file as UTF-8 text (invalid bytes are replaced).

        Args:
            path: Absolute target path.
            max_bytes: Upper bound on the bytes read.

        Returns:
            The content, or None if the file cannot be read.
        """
        data = self.read_bytes(path, max_bytes)
        return None if data is None else data.decode("utf-8", errors="replace")

    def read_first_line(self, path: str) -> str | None:
        """Read the first line of a file, stripped.

        Args:
            path: Absolute target path.

        Returns:
            The first line, or None if the file cannot be read.
        """
        text = self.read_text(path, 64 * 1024)
        if text is None:
            return None
        lines = text.splitlines()
        return lines[0].strip() if lines else ""

    def listdir(self, path: str) -> list[str]:
        """List a directory.

        Args:
            path: Absolute target path.

        Returns:
            Sorted entry names (empty if the directory cannot be read).
        """
        try:
            names = os.listdir(self.host_path(path))
        except OSError:
            names = []
        if self.is_fixture:
            names = [n for n in names if n != KEEP_FILE]
            prefix = self.normalize(path).rstrip("/") + "/"
            names.extend(
                link[len(prefix) :]
                for link in self._meta_symlinks()
                if link.startswith(prefix) and "/" not in link[len(prefix) :]
            )
        return sorted(set(names))

    def glob(self, pattern: str) -> list[str]:
        """Expand a shell-style pattern (``*``, ``?``, ``[...]``) per path component.

        Args:
            pattern: Absolute pattern such as ``/sys/bus/pci/devices/*/vendor``.

        Returns:
            Sorted matching target paths.
        """
        parts = self.normalize(pattern).strip("/").split("/")
        current = ["/"]
        for part in parts:
            matched: list[str] = []
            for base in current:
                if any(ch in part for ch in "*?["):
                    for name in self.listdir(base):
                        if fnmatch.fnmatchcase(name, part):
                            matched.append(posixpath.join(base, name))
                else:
                    candidate = posixpath.join(base, part)
                    if self.exists(candidate):
                        matched.append(candidate)
            current = matched
        return sorted(current)
