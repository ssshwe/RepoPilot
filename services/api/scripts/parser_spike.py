"""Run the Java parser against one real, scanner-approved source; no indexing."""

import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parsing.base import CodeParsingError
from app.parsing.registry import create_parser_registry
from app.repository.scanner import create_repository_scanner
from app.core.exceptions import (
    RepositoryNotFoundError, RepositoryPathError, RepositoryScanError, RepositoryScanLimitError,
)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repository", default="demo-repository")
    cli.add_argument("--path", default="backend/service/RefundService.java")
    args = cli.parse_args()
    try:
        scan = create_repository_scanner().scan(args.repository)
        file = next((item for item in scan.files if item.path == args.path), None)
        if file is None:
            print("The selected source was not found among scanner-approved files.", file=sys.stderr)
            return 1
        result = create_parser_registry().parse(file)
        print(json.dumps({
            "packages": {name: version(name) for name in ("tree-sitter", "tree-sitter-java")},
            "decoded_source_sha256": hashlib.sha256(file.content.encode("utf-8")).hexdigest(),
            **asdict(result),
        }, ensure_ascii=False, indent=2))
        return 0
    except (CodeParsingError, RepositoryNotFoundError, RepositoryPathError,
            RepositoryScanError, RepositoryScanLimitError) as exc:
        print(f"Parser spike failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
