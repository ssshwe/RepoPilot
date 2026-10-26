"""SQLite atomic publication plus OS-scoped writer leases for local snapshots."""
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.retrieval.snapshot_models import (
    MAX_SNAPSHOT_BYTES, LexicalSnapshot, SnapshotBusy, SnapshotError, SnapshotNotReady, canonical,
    valid_digest,
)
from app.retrieval.tokenizer import CodeTokenizer


@dataclass
class RebuildTicket:
    scope: object
    token: str
    expected_sources: str
    owner: object = field(repr=False)
    active: bool = field(default=True, repr=False)


@dataclass(frozen=True)
class SnapshotRead:
    snapshot: LexicalSnapshot
    ticket: str


class SnapshotStore:
    """One local database, short transactions, one writer per backend/collection/repo.

    SQLite commits the ready pointer and immutable generation together. Leases
    are OS locks, released after process death; rebuilding state remains durable.
    """
    def __init__(self, path: Path, *, read_only=False):
        self.path = Path(path).resolve()
        self.read_only = read_only
        self.locks = self.path.parent / (self.path.name + ".locks")
        if read_only:
            if not self.path.is_file():
                raise SnapshotNotReady("Snapshot database does not exist.")
            with self._db() as db:
                if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise SnapshotError("Unsupported snapshot database version.")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.locks.mkdir(exist_ok=True)
        with self._db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise SnapshotError("Unsupported snapshot database version.")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS generations (
                    revision TEXT PRIMARY KEY, scope_key TEXT NOT NULL, body BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS states (
                    scope_key TEXT PRIMARY KEY, scope_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('ready','rebuilding','incomplete')),
                    ticket TEXT NOT NULL, target_sources TEXT NOT NULL, active TEXT, reason TEXT, updated_at TEXT NOT NULL);
            """)
            if version == 0:
                db.execute("PRAGMA user_version=1")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1) if self.read_only else sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def status(self, scope):
        with self._db() as db:
            row = db.execute("SELECT state,ticket,target_sources,active,reason,updated_at FROM states WHERE scope_key=?", (scope.key,)).fetchone()
        return {"scope": scope.to_dict(), **dict(row)} if row else {
            "scope": scope.to_dict(), "state": "incomplete", "ticket": None, "target_sources": None, "active": None, "reason": "not_built", "updated_at": None}

    @contextmanager
    def rebuilding(self, scope, expected_sources):
        if self.read_only:
            raise SnapshotError("Cannot rebuild a read-only snapshot artifact.")
        valid_digest(expected_sources)
        # Scope keys are digests, never user paths. Lock files persist, but OS
        # lock ownership never survives process exit or a crash.
        handle = (self.locks / (scope.key + ".lock")).open("a+b")
        acquired, ticket = False, None
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise SnapshotBusy("This repository snapshot is already rebuilding.") from exc
            ticket = RebuildTicket(scope, uuid4().hex, expected_sources, self)
            with self._db() as db:
                db.execute("""INSERT INTO states VALUES (?,?, 'rebuilding', ?,?,NULL,NULL,?)
                    ON CONFLICT(scope_key) DO UPDATE SET state='rebuilding', ticket=excluded.ticket,
                    target_sources=excluded.target_sources, reason=NULL, updated_at=excluded.updated_at""",
                    (scope.key, canonical(scope.to_dict()).decode(), ticket.token, expected_sources, self._now()))
            yield ticket
        finally:
            try:
                if ticket is not None:
                    ticket.active = False
                    # Also covers cancellation and an accidental exit without publish.
                    with self._db() as db:
                        db.execute("UPDATE states SET state='incomplete',reason='rebuild_unconfirmed',updated_at=? WHERE scope_key=? AND ticket=? AND state='rebuilding'",
                                   (self._now(), scope.key, ticket.token))
            finally:
                if acquired:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()

    def publish(self, ticket, snapshot, tokenizer=None):
        if (ticket.owner is not self or not ticket.active or snapshot.index.scope != ticket.scope
                or snapshot.index.source_sha256 != ticket.expected_sources):
            raise SnapshotError("Invalid snapshot publication lease/scope.")
        tokenizer = tokenizer or CodeTokenizer()
        body = canonical(snapshot.to_dict())
        if len(body) > MAX_SNAPSHOT_BYTES:
            raise SnapshotError("Snapshot exceeds persistence size budget.")
        verified = LexicalSnapshot.from_dict(json.loads(body), tokenizer)
        revision = verified.revision
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT state,ticket FROM states WHERE scope_key=?", (ticket.scope.key,)).fetchone()
            if current is None or current["state"] != "rebuilding" or current["ticket"] != ticket.token:
                raise SnapshotError("Snapshot publication was superseded.")
            db.execute("INSERT OR IGNORE INTO generations VALUES (?,?,?)", (revision, ticket.scope.key, body))
            stored = db.execute("SELECT scope_key,body FROM generations WHERE revision=?", (revision,)).fetchone()
            if stored["scope_key"] != ticket.scope.key or bytes(stored["body"]) != body:
                raise SnapshotError("Immutable snapshot generation conflicts with saved data.")
            db.execute("UPDATE states SET state='ready',active=?,reason=NULL,updated_at=? WHERE scope_key=? AND ticket=?",
                       (revision, self._now(), ticket.scope.key, ticket.token))
        return revision

    def invalidate(self, scope, ticket, reason="dense_index_drift"):
        if self.read_only:
            return  # Rejected by load_ready; immutable proof artifacts stay unchanged.
        # A stale reader must never invalidate a newer published/rebuilding epoch.
        with self._db() as db:
            db.execute("UPDATE states SET state='incomplete',reason=?,updated_at=? WHERE scope_key=? AND ticket=? AND state='ready'",
                       (reason, self._now(), scope.key, ticket))

    def load_ready(self, scope, tokenizer=None):
        tokenizer = tokenizer or CodeTokenizer()
        with self._db() as db:
            row = db.execute("""SELECT s.state,s.ticket,s.active,g.body FROM states s
                LEFT JOIN generations g ON s.active=g.revision AND s.scope_key=g.scope_key
                WHERE s.scope_key=?""", (scope.key,)).fetchone()
        if row is None or row["state"] != "ready":
            raise SnapshotNotReady("Snapshot is not ready; explicitly rebuild it before using lexical retrieval.")
        try:
            body = bytes(row["body"])
            if len(body) > MAX_SNAPSHOT_BYTES:
                raise SnapshotError("Saved snapshot exceeds size budget.")
            snapshot = LexicalSnapshot.from_dict(json.loads(body), tokenizer)
            if snapshot.revision != row["active"] or snapshot.index.scope != scope:
                raise SnapshotError("Saved snapshot identity/scope differs from ready pointer.")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.invalidate(scope, row["ticket"], "snapshot_integrity")
            raise SnapshotNotReady("Saved snapshot is invalid; explicitly rebuild it.") from exc
        return SnapshotRead(snapshot, row["ticket"])

    def assert_current(self, read):
        scope = read.snapshot.index.scope
        status = self.status(scope)
        if status["state"] != "ready" or status["ticket"] != read.ticket or status["active"] != read.snapshot.revision:
            raise SnapshotNotReady("Snapshot epoch changed while reading; discard the result.")

    def backup(self, path):
        path = Path(path)
        # Refuse to replace an existing proof artifact.
        with path.open("xb"):
            pass
        with self._db() as source, closing(sqlite3.connect(path)) as destination:
            source.backup(destination)
