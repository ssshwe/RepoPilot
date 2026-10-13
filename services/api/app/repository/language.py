from pathlib import PurePath


EXTENSION_LANGUAGES = {
    ".py": "python", ".java": "java", ".js": "javascript",
    ".ts": "typescript", ".vue": "vue", ".json": "json",
    ".yml": "yaml", ".yaml": "yaml", ".md": "markdown", ".sql": "sql",
}


def detect_language(path: str | PurePath) -> str | None:
    """Use the supported extension; content parsing belongs to later stages."""
    return EXTENSION_LANGUAGES.get(PurePath(path).suffix.lower())
