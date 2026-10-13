from fnmatch import fnmatchcase


IGNORED_DIRECTORIES = frozenset({
    ".git", "node_modules", "dist", "build", "target", ".idea", ".vscode",
    "__pycache__", ".venv", "coverage", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".tools",
    "credentials", "secrets", ".credentials", ".secrets",
})
IGNORED_FILE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.min.js", "*.map",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", ".gitkeep",
    "credentials", "credentials.*", "secrets", "secrets.*", ".credentials", ".credentials.*",
    ".secrets", ".secrets.*",
)


def is_ignored_directory(name: str) -> bool:
    return name.casefold() in IGNORED_DIRECTORIES


def is_ignored_file(name: str) -> bool:
    return any(fnmatchcase(name.casefold(), pattern) for pattern in IGNORED_FILE_PATTERNS)
