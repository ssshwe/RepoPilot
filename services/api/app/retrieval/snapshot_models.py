"""Immutable lexical snapshots derived from existing indexed Points, never live files."""
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from uuid import UUID

from app.embedding.validation import MAX_EMBEDDING_DIMENSION, validate_vector
from app.retrieval.candidates import SourceMetadata
from app.retrieval.tokenizer import CodeTokenizer
from app.schemas.retrieval import RetrievalSearchRequest

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


class SnapshotError(ValueError):
    """Safe snapshot integrity/state error; messages never include indexed code."""


class SnapshotNotReady(SnapshotError):
    pass


class SnapshotBusy(SnapshotError):
    pass


def plain(value):
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def canonical(value) -> bytes:
    return json.dumps(plain(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def valid_digest(value):
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise SnapshotError("Invalid snapshot digest.")


def tokenizer_implementation() -> str:
    from app.retrieval import tokenizer
    return "sha256:" + hashlib.sha256(Path(tokenizer.__file__).read_bytes()).hexdigest()


@dataclass(frozen=True)
class SnapshotScope:
    backend_id: str
    collection: str
    repository: str

    def __post_init__(self):
        valid_digest(self.backend_id)
        if not isinstance(self.collection, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.collection) is None:
            raise SnapshotError("Invalid snapshot collection.")
        RetrievalSearchRequest(repository=self.repository, query="validation")

    def to_dict(self):
        return {"backend_id": self.backend_id, "collection": self.collection, "repository": self.repository}

    @property
    def key(self):
        return digest(self.to_dict())[7:]


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    source: SourceMetadata
    payload_sha256: str
    vector_sha256: str

    def __post_init__(self):
        if not isinstance(self.chunk_id, str) or str(UUID(self.chunk_id)) != self.chunk_id or not isinstance(self.source, SourceMetadata):
            raise SnapshotError("Invalid indexed Chunk identity.")
        valid_digest(self.payload_sha256)
        valid_digest(self.vector_sha256)

    def to_dict(self):
        return {"chunk_id": self.chunk_id, "source": self.source.to_dict(),
                "payload_sha256": self.payload_sha256, "vector_sha256": self.vector_sha256}


@dataclass(frozen=True)
class IndexedRepository:
    scope: SnapshotScope
    configuration: Mapping
    chunks: tuple[IndexedChunk, ...]

    def __post_init__(self):
        config = json.loads(canonical(self.configuration))
        if set(config) != {"dimension", "distance", "embedding", "chunking"}:
            raise SnapshotError("Invalid indexed configuration fields.")
        if type(config["dimension"]) is not int or not 1 <= config["dimension"] <= MAX_EMBEDDING_DIMENSION or config["distance"] != "Cosine":
            raise SnapshotError("Indexed vectors must use one unnamed Cosine dimension.")
        embedding = config["embedding"]
        if (not isinstance(embedding, dict) or set(embedding) != {"schema_version", "embedding_provider", "embedding_model", "endpoint_sha256"}
                or embedding["schema_version"] != "code-points-v1"
                or any(not isinstance(embedding[key], str) or not 1 <= len(embedding[key]) <= 256 for key in embedding)
                or re.fullmatch(r"[0-9a-f]{64}", embedding["endpoint_sha256"]) is None):
            raise SnapshotError("Invalid indexed embedding identity.")
        chunks = tuple(self.chunks)
        if (len(chunks) > 10000 or any(not isinstance(c, IndexedChunk) for c in chunks)
                or len({c.chunk_id for c in chunks}) != len(chunks)
                or [c.chunk_id for c in chunks] != sorted(c.chunk_id for c in chunks)
                or sum(len(c.source.content) for c in chunks) > 2_000_000):
            raise SnapshotError("Indexed Chunk identity/order/budget is invalid.")
        profile = config["chunking"]
        if profile is not None:
            if (not isinstance(profile, dict) or set(profile) != {"chunking_version", "fallback_chunking_version", "chunk_lines", "overlap_lines", "max_chunk_characters"}
                    or profile["chunking_version"] not in {"ast-v1", "line-window-v1"}
                    or profile["fallback_chunking_version"] != ("ast-fallback-line-v1" if profile["chunking_version"] == "ast-v1" else None)
                    or type(profile["chunk_lines"]) is not int or not 1 <= profile["chunk_lines"] <= 200
                    or type(profile["overlap_lines"]) is not int or not 0 <= profile["overlap_lines"] < profile["chunk_lines"]
                    or type(profile["max_chunk_characters"]) is not int or not 1 <= profile["max_chunk_characters"] <= 65536):
                raise SnapshotError("Invalid repository chunking profile.")
        allowed = {"ast", "fallback"} if profile and profile["chunking_version"] == "ast-v1" else {"simple"}
        if any(c.source.chunking_strategy not in allowed for c in chunks):
            raise SnapshotError("Indexed Chunks disagree with repository chunking metadata.")
        object.__setattr__(self, "configuration", freeze(config))
        object.__setattr__(self, "chunks", chunks)

    def to_dict(self):
        return {"scope": self.scope.to_dict(), "configuration": plain(self.configuration), "chunks": [c.to_dict() for c in self.chunks]}

    @property
    def index_revision(self):
        return digest(self.to_dict())

    @property
    def source_sha256(self):
        return digest([{"chunk_id": c.chunk_id, "source": c.source.to_dict()} for c in self.chunks])

    @classmethod
    def from_records(cls, scope, configuration, records):
        try:
            if len(records) * configuration["dimension"] > 2_000_000:
                raise SnapshotError("Indexed vector coordinate budget exceeded.")
            chunks = []
            for point in records:
                payload = point["payload"]
                identity = str(UUID(str(point["id"])))
                vector = validate_vector(point["vector"])
                if (payload["chunk_id"] != identity or payload["repository"] != scope.repository
                        or payload["embedding_model"] != configuration["embedding"]["embedding_model"]
                        or payload["schema_version"] != configuration["embedding"]["schema_version"]
                        or len(vector) != configuration["dimension"]):
                    raise SnapshotError("Invalid indexed Point identity/scope/dimension.")
                chunks.append(IndexedChunk(identity, SourceMetadata.from_payload(payload), digest(payload), digest(vector)))
            return cls(scope, configuration, tuple(sorted(chunks, key=lambda c: c.chunk_id)))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise SnapshotError("Indexed Points are invalid; no snapshot can be published.") from exc

    @classmethod
    def from_dict(cls, value):
        return cls(SnapshotScope(**value["scope"]), value["configuration"], tuple(
            IndexedChunk(c["chunk_id"], SourceMetadata.from_payload(c["source"]), c["payload_sha256"], c["vector_sha256"])
            for c in value["chunks"]))


@dataclass(frozen=True)
class LexicalSnapshot:
    index: IndexedRepository
    tokenizer_spec: Mapping
    tokenizer_sha256: str
    tokens: tuple[tuple[str, ...], ...]

    def __post_init__(self):
        valid_digest(self.tokenizer_sha256)
        tokens = tuple(tuple(row) for row in self.tokens)
        if (not isinstance(self.index, IndexedRepository) or len(tokens) != len(self.index.chunks)
                or any(not isinstance(token, str) or not token for row in tokens for token in row)):
            raise SnapshotError("Invalid snapshot document tokens.")
        object.__setattr__(self, "tokens", tokens)
        object.__setattr__(self, "tokenizer_spec", freeze(json.loads(canonical(self.tokenizer_spec))))

    @classmethod
    def build(cls, index, tokenizer):
        return cls(index, freeze(tokenizer.specification()), tokenizer_implementation(),
                   tuple(tuple(tokenizer.tokenize_source(c.source)) for c in index.chunks))

    def to_dict(self):
        return {"schema_version": 1, "index": self.index.to_dict(), "index_revision": self.index.index_revision,
                "source_sha256": self.index.source_sha256, "tokenizer": plain(self.tokenizer_spec),
                "tokenizer_implementation_sha256": self.tokenizer_sha256,
                "documents": [{"chunk_id": chunk.chunk_id, "tokens": list(tokens), "term_frequencies": dict(sorted(Counter(tokens).items()))}
                              for chunk, tokens in zip(self.index.chunks, self.tokens, strict=True)]}

    @property
    def revision(self):
        return digest(self.to_dict())

    @classmethod
    def from_dict(cls, value, tokenizer):
        try:
            if (value["tokenizer"] != tokenizer.specification()
                    or value["tokenizer_implementation_sha256"] != tokenizer_implementation()):
                raise SnapshotError("Tokenizer implementation/specification changed; rebuild the snapshot.")
            rebuilt = cls.build(IndexedRepository.from_dict(value["index"]), tokenizer)
            if canonical(value) != canonical(rebuilt.to_dict()):
                raise SnapshotError("Snapshot content/tokens/fingerprints disagree.")
            return rebuilt
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise SnapshotError("Snapshot cannot be validated; rebuild it.") from exc
