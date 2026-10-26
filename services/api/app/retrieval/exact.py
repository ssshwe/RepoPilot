"""Case-sensitive exact locators with bounded, source-grounded diagnostics."""
from dataclasses import dataclass, field
from pathlib import PurePosixPath
import re
from time import perf_counter
from types import MappingProxyType

from app.retrieval.candidates import RetrievalCandidate
from app.retrieval.scope import ScopedCandidateBatch, ScopedSearchRequest, relative_path, scoped_request

KINDS = {"symbol", "short_symbol", "path", "filename", "text", "literal"}
FULL_SCORES = {"symbol": 100., "short_symbol": 90., "path": 80., "filename": 70., "text": 60., "literal": 60.}
PARTIAL_SCORES = {"symbol": 40., "short_symbol": 35., "path": 30., "filename": 25., "text": 20., "literal": 20.}
_NUMBER = r"(?<![\w.])[-+]?(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|[0-9][0-9_]*(?:\.[0-9_]+)?(?:[eE][-+]?[0-9_]+)?)[fFdDlL]?(?![\w.])"
_KEYWORD = r"(?<!\w)(?:true|false|null|True|False|None|NULL)(?!\w)"
_QUOTED = r'''"""(?:\\.|(?!""")[\s\S])*"""|\x27\x27\x27(?:\\.|(?!\x27\x27\x27)[\s\S])*\x27\x27\x27|"(?:\\.|[^"\\])*"|'(?:\\.|''|[^'\\])*'|`(?:\\.|[^`\\])*`'''


@dataclass(frozen=True)
class ExactMatch:
    kind: str
    value: str
    mode: str = "full"

    def __post_init__(self):
        if self.kind not in KINDS or self.mode not in {"full", "partial"}:
            raise ValueError("Unsupported exact matching kind/mode.")
        if not isinstance(self.value, str) or not 1 <= len(self.value) <= 8192 or not self.value.strip() or "\x00" in self.value:
            raise ValueError("Exact target must be a nonblank string of at most 8192 characters.")
        if self.kind in {"symbol", "short_symbol", "filename", "path"}:
            if self.value.strip() != self.value or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in self.value):
                raise ValueError("Metadata targets cannot contain whitespace padding/control characters.")
        if self.kind == "path": relative_path(self.value, trailing_slash=self.mode == "partial")
        if self.kind == "filename" and any(c in self.value for c in "/\\:"):
            raise ValueError("Filename target must not contain directories or drive syntax.")

    def to_dict(self): return {"kind": self.kind, "value": self.value, "mode": self.mode}

    @classmethod
    def infer(cls, query):
        value = query.strip()
        directive = re.fullmatch(r"(symbol|short_symbol|path|filename|text|literal):([\s\S]+)", value)
        if directive: return cls(directive[1], directive[2].lstrip())
        if not value: return None
        code = re.findall(r"`([^`\r\n]+)`", value)
        literal_cue = re.search(r"错误|字面量|字符串|事件|\berror\b|\bliteral\b|\bstring\b", value, re.IGNORECASE)
        if len(code) == 1:
            if literal_cue and code[0][:1] not in {"'", '"'}: return cls("text", code[0])
            return cls.infer(code[0])
        if len(code) > 1: return None
        if value[:1] in {"'", '"', "`"} or re.fullmatch(_NUMBER + "|" + _KEYWORD, value): return cls("literal", value)
        if not any(c.isalnum() for c in value): return None
        if ("/" in value or "\\" in value) and not any(c.isspace() for c in value): return cls("path", value)
        quoted = list(re.finditer(_QUOTED, value))
        if len(quoted) == 1 and literal_cue:
            raw = quoted[0].group()
            delimiter = 3 if raw.startswith(('"""', "'''")) else 1
            return cls("text", raw[delimiter:-delimiter])
        paths = set(re.findall(r"(?<![A-Za-z0-9_./])[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", value))
        if len(paths) == 1: return cls("path", paths.pop())
        if len(paths) > 1: return None
        names = set(re.findall(r"(?<![A-Za-z0-9_$./])[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+", value))
        if len(names) > 1: return None
        if len(names) == 1:
            name = names.pop()
            if PurePosixPath(name).suffix.lower() in {".java", ".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".json", ".sql", ".md", ".yml", ".yaml", ".toml", ".txt"}: return cls("filename", name)
            return cls("symbol", name)
        if re.fullmatch(r"[^\W\d]\w*(?:\.[^\W\d]\w*)+", value): return cls("symbol", value)
        if re.fullmatch(r"[^\W\d]\w*", value): return cls("short_symbol", value)
        return cls("text", value)


@dataclass(frozen=True)
class ExactSearchRequest(ScopedSearchRequest):
    candidate_limit: int = 10
    match: ExactMatch = field(kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.match, ExactMatch): raise TypeError("Exact request needs an ExactMatch selector.")


@dataclass(frozen=True)
class MatchReason:
    kind: str
    strength: str
    location: str
    matched_value: str
    start_line: int
    end_line: int
    char_start: int | None
    char_end_exclusive: int | None
    occurrences: int

    def __post_init__(self):
        if (self.kind not in KINDS or self.strength not in {"full", "partial"}
                or self.location not in {"metadata", "lexical_literal", "quoted_text", "source_line", "source_text"}
                or not isinstance(self.matched_value, str) or not self.matched_value
                or type(self.start_line) is not int or type(self.end_line) is not int or not 1 <= self.start_line <= self.end_line
                or type(self.occurrences) is not int or self.occurrences < 1):
            raise ValueError("Invalid exact match reason.")
        if self.location == "metadata":
            if self.char_start is not None or self.char_end_exclusive is not None: raise ValueError("Metadata matches have no source character offsets.")
        elif type(self.char_start) is not int or type(self.char_end_exclusive) is not int or not 0 <= self.char_start < self.char_end_exclusive:
            raise ValueError("Invalid source character offsets.")

    def to_dict(self): return dict(self.__dict__)


def literals(source):
    language = source.language
    if language == "python": comment = r"\#[^\r\n]*"
    elif language == "sql": comment = r"--[^\r\n]*|/\*[\s\S]*?\*/"
    elif language in {"java", "javascript", "typescript", "vue"}: comment = r"//[^\r\n]*|/\*[\s\S]*?\*/"
    else: comment = r"(?!x)x"
    pattern = re.compile(rf"(?P<comment>{comment})|(?P<quoted>{_QUOTED})|(?P<number>{_NUMBER})|(?P<keyword>{_KEYWORD})")
    return [(m.start(), m.end(), m.group(), m.lastgroup) for m in pattern.finditer(source.content) if m.lastgroup != "comment"]


def line_range(source, start, end):
    # Use the same splitlines convention as SourceMetadata, including CR-only
    # files; exclusive end offsets never invent a line after a final newline.
    offset, first, last = 0, None, None
    for number, line in enumerate(source.content.splitlines(keepends=True), source.start_line):
        if offset <= start < offset + len(line): first = number
        if offset <= end - 1 < offset + len(line): last = number
        offset += len(line)
    if first is None or last is None: raise ValueError("Exact match offsets exceed the indexed Chunk.")
    return first, last


def match_source(source, selector):
    """Return one best reason and an occurrence count; never rewrite source text."""
    kind, target, mode = selector.kind, selector.value, selector.mode
    if kind in {"symbol", "short_symbol", "path", "filename"}:
        value = {"symbol": source.symbol, "short_symbol": source.symbol.rsplit(".", 1)[-1] if source.symbol else None,
                 "path": source.path, "filename": PurePosixPath(source.path).name}[kind]
        if value is None or (value != target and (mode != "partial" or target not in value)): return None
        return MatchReason(kind, "full" if value == target else "partial", "metadata", value,
                           source.start_line, source.end_line, None, None, 1)
    tokens = literals(source)
    full, partial = [], []
    if kind == "literal":
        for start, end, raw, _ in tokens:
            if raw == target: full.append((start, end, "lexical_literal"))
            elif mode == "partial":
                found = raw.find(target)
                if found >= 0: partial.append((start + found, start + found + len(target), "lexical_literal"))
    else:
        for start, end, raw, token_kind in tokens:
            if token_kind != "quoted": continue
            delimiter = 3 if raw.startswith(('"""', "'''")) else 1
            if raw[delimiter:-delimiter] == target: full.append((start + delimiter, end - delimiter, "quoted_text"))
        offset = 0
        for line in source.content.splitlines(keepends=True):
            text = line.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
            if text == target: full.append((offset, offset + len(text), "source_line"))
            offset += len(line)
        if mode == "partial" and not full:
            start = source.content.find(target)
            while start >= 0:
                partial.append((start, start + len(target), "source_text"))
                start = source.content.find(target, start + 1)
    matches = sorted(set(full if full else partial))
    if not matches: return None
    start, end, location = matches[0]
    first, last = line_range(source, start, end)
    return MatchReason(kind, "full" if full else "partial", location, source.content[start:end], first, last,
                       start, end, len(matches))


@dataclass(frozen=True)
class ExactCandidateBatch(ScopedCandidateBatch):
    match: ExactMatch | None = field(default=None, kw_only=True)
    reasons: object = field(default_factory=dict, kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        if self.channel != "exact" or (self.match is not None and not isinstance(self.match, ExactMatch)):
            raise ValueError("Invalid exact matching channel/selector.")
        reasons = dict(self.reasons)
        if set(reasons) != {c.chunk_id for c in self.candidates}: raise ValueError("Exact reasons must cover precisely the returned Chunks.")
        for candidate in self.candidates:
            if self.match is None or reasons[candidate.chunk_id] != match_source(candidate.source_metadata, self.match):
                raise ValueError("Exact matching reason is not supported by its indexed source.")
            expected = (FULL_SCORES if reasons[candidate.chunk_id].strength == "full" else PARTIAL_SCORES)[self.match.kind]
            if candidate.channel_scores != {"exact": expected}: raise ValueError("Exact score disagrees with its match reason.")
        object.__setattr__(self, "reasons", MappingProxyType(reasons))

    def to_dict(self):
        return super().to_dict() | {"match": self.match.to_dict() if self.match else None,
                                    "reasons": {key: reason.to_dict() for key, reason in self.reasons.items()}}

    @classmethod
    def from_dict(cls, value):
        fields = dict(value)
        match = ExactMatch(**fields.pop("match")) if fields.get("match") is not None else fields.pop("match")
        reasons = {key: MatchReason(**reason) for key, reason in fields.pop("reasons").items()}
        base = ScopedCandidateBatch.from_dict(fields)
        return cls(base.repository, base.query, base.channel, base.index_revision, base.candidate_limit, base.candidates,
                   base.retrieval_latency_ms, filters=base.filters, match=match, reasons=reasons)


class ExactCandidateChannel:
    name = "exact"
    version = "exact-locators-v1"

    def __init__(self, coordinator): self.coordinator = coordinator

    async def recall(self, request):
        selector = request.match if isinstance(request, ExactSearchRequest) else ExactMatch.infer(request.query)
        request = scoped_request(request)
        started = perf_counter()
        async def retrieve(snapshot):
            ranked = []
            if selector is not None:
                for chunk in snapshot.index.chunks:
                    if not request.filters.matches(chunk.source): continue
                    reason = match_source(chunk.source, selector)
                    if reason is None: continue
                    score = (FULL_SCORES if reason.strength == "full" else PARTIAL_SCORES)[selector.kind]
                    ranked.append((score, chunk, reason))
            ranked.sort(key=lambda item: (-item[0], item[1].chunk_id))
            selected = ranked[:request.candidate_limit]
            candidates = tuple(RetrievalCandidate(chunk.chunk_id, request.repository, snapshot.index.index_revision,
                               chunk.source, {self.name: score}, {self.name: rank}, (request.query,))
                               for rank, (score, chunk, _) in enumerate(selected, 1))
            return snapshot.index.index_revision, candidates, {chunk.chunk_id: reason for _, chunk, reason in selected}
        revision, candidates, reasons = await self.coordinator.use(request.repository, retrieve)
        return ExactCandidateBatch(request.repository, request.query, self.name, revision, request.candidate_limit, candidates,
                                   round((perf_counter() - started) * 1000, 3), filters=request.filters, match=selector, reasons=reasons)
