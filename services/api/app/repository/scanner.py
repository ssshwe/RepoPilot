import logging
import os
from pathlib import Path
import stat

from app.core.config import REPOSITORY_ROOT, get_settings
from app.core.exceptions import (
    RepositoryNotFoundError, RepositoryPathError, RepositoryScanError, RepositoryScanLimitError,
)
from app.repository.ignore import is_ignored_directory, is_ignored_file
from app.repository.language import detect_language
from app.repository.models import RepositoryFile, RepositoryScanResult, SkippedPath
from app.repository.paths import (
    UnsafeRepositoryEntry, check_contained_path, is_link_or_reparse,
    read_bounded_file, validate_repository_name,
)


logger = logging.getLogger(__name__)
_BINARY_CONTROLS = frozenset(range(32)) - {9, 10, 12, 13}


class RepositoryScanner:
    def __init__(
        self, workspaces_root: Path, *, max_file_bytes: int = 1_048_576,
        max_entries: int = 10_000, max_total_bytes: int = 20_971_520,
    ) -> None:
        if min(max_file_bytes, max_entries, max_total_bytes) < 1:
            raise ValueError("Scanner limits must be positive.")
        self.workspaces_root = Path(os.path.abspath(workspaces_root))
        self.max_file_bytes = max_file_bytes
        self.max_entries = max_entries
        self.max_total_bytes = max_total_bytes

    def _repository_root(self, repository: str) -> Path:
        validate_repository_name(repository)
        root = self.workspaces_root / repository
        try:
            check_contained_path(root, self.workspaces_root)
            if not stat.S_ISDIR(root.lstat().st_mode):
                raise RepositoryNotFoundError("Repository directory was not found.")
            if root.resolve(strict=True) != root:
                raise UnsafeRepositoryEntry("outside_repository")
        except FileNotFoundError:
            raise RepositoryNotFoundError("Repository directory was not found.") from None
        except (UnsafeRepositoryEntry, RuntimeError):
            raise RepositoryPathError("Repository directories must not be links or reparse points.") from None
        except OSError:
            raise RepositoryScanError("Repository directory could not be accessed.") from None
        return root

    def scan(self, repository: str) -> RepositoryScanResult:
        root = self._repository_root(repository)
        files: list[RepositoryFile] = []
        skipped: list[SkippedPath] = []
        pruned: list[SkippedPath] = []
        pending = [root]
        visited_entries = 0
        total_bytes = 0

        while pending:
            directory = pending.pop()
            relative_directory = directory.relative_to(root).as_posix()
            try:
                check_contained_path(directory, root)
                with os.scandir(directory) as iterator:
                    entries = []
                    for entry in iterator:
                        visited_entries += 1
                        if visited_entries > self.max_entries:
                            raise RepositoryScanLimitError("Repository exceeds the scan entry limit.")
                        entries.append(Path(entry.path))
            except UnsafeRepositoryEntry as exc:
                if directory == root:
                    raise RepositoryPathError("Repository directories must not be links or reparse points.") from None
                pruned.append(SkippedPath(relative_directory, exc.reason))
                continue
            except OSError:
                if directory == root:
                    raise RepositoryScanError("Repository directory could not be accessed.") from None
                pruned.append(SkippedPath(relative_directory, "unreadable"))
                continue

            for path in sorted(entries, key=lambda item: (item.name.casefold(), item.name)):
                relative = path.relative_to(root).as_posix()
                try:
                    info = path.lstat()
                except OSError:
                    skipped.append(SkippedPath(relative, "unreadable"))
                    continue
                # Never descend into any reparse point, including Windows junctions.
                if is_link_or_reparse(info):
                    skipped.append(SkippedPath(relative, "unsafe_link"))
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if is_ignored_directory(path.name):
                        pruned.append(SkippedPath(relative, "ignored_directory"))
                    else:
                        pending.append(path)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    skipped.append(SkippedPath(relative, "not_regular_file"))
                    continue
                if is_ignored_file(path.name):
                    skipped.append(SkippedPath(relative, "ignored_file"))
                    continue
                language = detect_language(path)
                if language is None:
                    skipped.append(SkippedPath(relative, "unsupported_extension"))
                    continue
                try:
                    data = read_bounded_file(path, root, self.max_file_bytes)
                except UnsafeRepositoryEntry as exc:
                    skipped.append(SkippedPath(relative, exc.reason))
                    continue
                except OSError:
                    skipped.append(SkippedPath(relative, "unreadable"))
                    continue
                if any(value in _BINARY_CONTROLS or value == 127 for value in data):
                    skipped.append(SkippedPath(relative, "binary"))
                    continue
                try:
                    content = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    skipped.append(SkippedPath(relative, "invalid_encoding"))
                    continue
                total_bytes += len(data)
                if total_bytes > self.max_total_bytes:
                    raise RepositoryScanLimitError("Repository exceeds the accepted text byte limit.")
                files.append(RepositoryFile(
                    repository=repository, path=relative, language=language,
                    size_bytes=len(data), line_count=len(content.splitlines()), content=content,
                ))

        files.sort(key=lambda file: file.path)
        skipped.sort(key=lambda entry: entry.path)
        pruned.sort(key=lambda entry: entry.path)
        result = RepositoryScanResult(repository, files, skipped, pruned)
        logger.info(
            "Repository scan repository=%s files_scanned=%s supported=%s skipped=%s pruned=%s bytes=%s",
            repository, result.files_scanned, len(files), len(skipped), len(pruned), result.total_bytes,
        )
        return result


def create_repository_scanner() -> RepositoryScanner:
    settings = get_settings()
    return RepositoryScanner(
        REPOSITORY_ROOT / "workspaces", max_file_bytes=settings.repository_max_file_bytes,
        max_entries=settings.repository_max_entries,
        max_total_bytes=settings.repository_max_total_bytes,
    )
