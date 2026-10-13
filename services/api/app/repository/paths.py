import os
from pathlib import Path
import re
import stat

from app.core.exceptions import RepositoryPathError
from app.repository.models import SkipReason


REPOSITORY_NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
_WINDOWS_DEVICE_NAMES = frozenset({
    "con", "prn", "aux", "nul", "conin$", "conout$",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})


def validate_repository_name(name: str) -> str:
    """Accept one portable directory name, never a user-supplied filesystem path."""
    if (
        not isinstance(name, str) or re.fullmatch(REPOSITORY_NAME_PATTERN, name) is None
        or name.endswith(".") or name.split(".", 1)[0].casefold() in _WINDOWS_DEVICE_NAMES
    ):
        raise RepositoryPathError("Repository must be a valid single directory name.")
    return name


class UnsafeRepositoryEntry(Exception):
    def __init__(self, reason: SkipReason):
        self.reason = reason
        super().__init__(reason)


def is_link_or_reparse(info: os.stat_result) -> bool:
    # Junctions are reparse points on Windows but need not be symbolic links.
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def check_contained_path(path: Path, root: Path) -> None:
    """Reject links in every component, then check real containment, not a prefix."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise UnsafeRepositoryEntry("outside_repository") from None
    current = root
    if is_link_or_reparse(current.lstat()):
        raise UnsafeRepositoryEntry("unsafe_link")
    for part in relative.parts:
        current = current / part
        if is_link_or_reparse(current.lstat()):
            raise UnsafeRepositoryEntry("unsafe_link")
    try:
        contained = path.resolve(strict=True).is_relative_to(root.resolve(strict=True))
    except RuntimeError:
        raise UnsafeRepositoryEntry("unsafe_link") from None
    if not contained:
        raise UnsafeRepositoryEntry("outside_repository")


def _windows_handle_path(fd: int) -> Path:
    """Verify the file actually opened before reading its contents (TOCTOU guard)."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    get_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    get_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_path.restype = wintypes.DWORD
    length = 512
    while True:
        buffer = ctypes.create_unicode_buffer(length)
        used = get_path(msvcrt.get_osfhandle(fd), buffer, length, 0)
        if used == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if used < length:
            value = buffer.value
            if value.startswith("\\\\?\\UNC\\"):
                value = "\\\\" + value[8:]
            elif value.startswith("\\\\?\\"):
                value = value[4:]
            return Path(value)
        length = used + 1


def _open_posix_file(path: Path, root: Path) -> int:
    """Open parent components by descriptor, never following replacement links."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(root.anchor, directory_flags)
    try:
        relative = path.relative_to(root)
        # Include the workspace ancestors, not just the final repository component.
        for part in (*root.parts[1:], *relative.parts[:-1]):
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(
            relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)


def read_bounded_file(path: Path, root: Path, max_bytes: int) -> bytes:
    check_contained_path(path, root)
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode):
        raise UnsafeRepositoryEntry("not_regular_file")
    if initial.st_size > max_bytes:
        raise UnsafeRepositoryEntry("too_large")

    if os.name == "nt":
        fd = os.open(path, os.O_RDONLY | os.O_BINARY)
    else:
        fd = _open_posix_file(path, root)
    # fdopen owns and closes the descriptor even if subsequent validation fails.
    with os.fdopen(fd, "rb") as file:
        opened = os.fstat(file.fileno())
        if os.name == "nt" and not _windows_handle_path(file.fileno()).is_relative_to(root):
            raise UnsafeRepositoryEntry("outside_repository")
        if not stat.S_ISREG(opened.st_mode):
            raise UnsafeRepositoryEntry("not_regular_file")
        if not os.path.samestat(initial, opened):
            raise UnsafeRepositoryEntry("changed_during_scan")
        if opened.st_size > max_bytes:
            raise UnsafeRepositoryEntry("too_large")
        data = file.read(max_bytes + 1)
        after = os.fstat(file.fileno())
    if len(data) > max_bytes:
        raise UnsafeRepositoryEntry("too_large")
    if (
        len(data) != opened.st_size or opened.st_size != after.st_size
        or opened.st_mtime_ns != after.st_mtime_ns
        or initial.st_mtime_ns != opened.st_mtime_ns
    ):
        raise UnsafeRepositoryEntry("changed_during_scan")
    return data
