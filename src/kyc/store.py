"""SQLite persistence: cases, the review queue, reviewer decisions and the audit trail.

Plain sqlite3 and parameterised SQL, one connection per unit of work. Every state change and its audit event are
written in the same transaction, so there is never a decision without its audit record or the other way round.
Migrations are numbered .sql files applied in order and tracked in PRAGMA user_version (docs/adr/0005).
"""

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from kyc.pipeline import CaseResult
from kyc.routing import Outcome
from kyc.schemas import Application

PROCESSING = "processing"


class CaseNotFound(LookupError):
    pass


class NotInReview(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(f"case is {status}, only cases in review can be decided")
        self.status = status


class VersionMismatch(Exception):
    def __init__(self, current: int) -> None:
        super().__init__(f"case is at version {current}")
        self.current = current


@dataclass(frozen=True)
class CaseRecord:
    id: str
    status: str
    version: int
    created_at: str
    updated_at: str
    application: Application
    result: CaseResult | None
    decided_by: str | None
    decision_note: str | None


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    at: str
    actor: str
    event: str
    from_status: str | None
    to_status: str | None
    detail: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, path: Path, now: Callable[[], datetime] = _utc_now) -> None:
        self.path = path
        self._now = now

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            # IMMEDIATE takes the write lock up front, so two deciders queue instead of failing half way.
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="microseconds")

    def migrate(self) -> int:
        """Apply every migration newer than the database. Returns the schema version afterwards."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        scripts = sorted(
            (int(f.name.split("_", 1)[0]), f.read_text(encoding="utf-8"))
            for f in files("kyc").joinpath("migrations").iterdir()
            if f.name.endswith(".sql")
        )
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            current: int = conn.execute("PRAGMA user_version").fetchone()[0]
            for number, sql in scripts:
                if number > current:
                    # The script and the version bump commit together or not at all.
                    conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
                    current = number
        finally:
            conn.close()
        return current

    def _audit(
        self,
        conn: sqlite3.Connection,
        case_id: str,
        *,
        actor: str,
        event: str,
        from_status: str | None,
        to_status: str | None,
        detail: dict[str, Any],
    ) -> None:
        conn.execute(
            "INSERT INTO audit_events (case_id, at, actor, event, from_status, to_status, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, self._stamp(), actor, event, from_status, to_status, json.dumps(detail)),
        )

    def create_case(self, application: Application, documents: list[dict[str, str]], actor: str) -> CaseRecord:
        case_id = uuid.uuid4().hex
        stamp = self._stamp()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO cases (id, status, version, created_at, updated_at, application)"
                " VALUES (?, ?, 1, ?, ?, ?)",
                (case_id, PROCESSING, stamp, stamp, application.model_dump_json()),
            )
            self._audit(
                conn,
                case_id,
                actor=actor,
                event="case_submitted",
                from_status=None,
                to_status=PROCESSING,
                detail={"documents": documents},
            )
        return self.get(case_id)

    def record_result(self, case_id: str, result: CaseResult) -> None:
        to_status = result.decision.outcome.value
        open_checks = [c.name for c in result.checks if c.status != "pass"]
        with self._transaction() as conn:
            updated = conn.execute(
                "UPDATE cases SET status = ?, version = version + 1, updated_at = ?, result = ?"
                " WHERE id = ? AND status = ?",
                (to_status, self._stamp(), result.model_dump_json(), case_id, PROCESSING),
            ).rowcount
            if updated != 1:
                raise CaseNotFound(case_id)
            self._audit(
                conn,
                case_id,
                actor="system",
                event="checks_completed",
                from_status=PROCESSING,
                to_status=to_status,
                detail={"outcome": to_status, "open_checks": open_checks},
            )

    def recover_interrupted(self) -> int:
        """Cases left in processing by a restart go to review: the documents were only ever held in memory."""
        with self._transaction() as conn:
            stuck = [r["id"] for r in conn.execute("SELECT id FROM cases WHERE status = ?", (PROCESSING,))]
            for case_id in stuck:
                conn.execute(
                    "UPDATE cases SET status = ?, version = version + 1, updated_at = ? WHERE id = ?",
                    (Outcome.IN_REVIEW.value, self._stamp(), case_id),
                )
                self._audit(
                    conn,
                    case_id,
                    actor="system",
                    event="processing_interrupted",
                    from_status=PROCESSING,
                    to_status=Outcome.IN_REVIEW.value,
                    detail={"reason": "the service restarted before the checks finished"},
                )
        return len(stuck)

    def get(self, case_id: str) -> CaseRecord:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise CaseNotFound(case_id)
        return _record(row)

    def review_queue(self, limit: int, after: tuple[str, str] | None) -> list[CaseRecord]:
        """Oldest first. Keyset paging on (created_at, id) so a case decided mid-scan never shifts a page."""
        conn = self._connect()
        try:
            if after is None:
                rows = conn.execute(
                    "SELECT * FROM cases WHERE status = 'in_review' ORDER BY created_at, id LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM cases WHERE status = 'in_review' AND (created_at, id) > (?, ?)"
                    " ORDER BY created_at, id LIMIT ?",
                    (*after, limit),
                ).fetchall()
        finally:
            conn.close()
        return [_record(r) for r in rows]

    def decide(self, case_id: str, *, expected_version: int, reviewer: str, outcome: Outcome, note: str) -> CaseRecord:
        """A reviewer's approve or reject. BEGIN IMMEDIATE queues concurrent deciders behind one write lock, and the
        UPDATE only matches the version the reviewer read. Of two reviewers who read the same version, the first
        wins and the second finds the case already decided (NotInReview) or at another version (VersionMismatch)."""
        with self._transaction() as conn:
            row = conn.execute("SELECT status, version FROM cases WHERE id = ?", (case_id,)).fetchone()
            if row is None:
                raise CaseNotFound(case_id)
            if row["status"] != Outcome.IN_REVIEW.value:
                raise NotInReview(row["status"])
            updated = conn.execute(
                "UPDATE cases SET status = ?, version = version + 1, updated_at = ?, decided_by = ?, decision_note = ?"
                " WHERE id = ? AND version = ? AND status = 'in_review'",
                (outcome.value, self._stamp(), reviewer, note, case_id, expected_version),
            ).rowcount
            if updated != 1:
                raise VersionMismatch(row["version"])
            self._audit(
                conn,
                case_id,
                actor=reviewer,
                event="review_decided",
                from_status=Outcome.IN_REVIEW.value,
                to_status=outcome.value,
                detail={"outcome": outcome.value, "note": note},
            )
        return self.get(case_id)

    def audit(self, case_id: str) -> list[AuditEvent]:
        self.get(case_id)  # 404 for an unknown case rather than an empty list
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM audit_events WHERE case_id = ? ORDER BY seq", (case_id,)).fetchall()
        finally:
            conn.close()
        return [
            AuditEvent(
                r["seq"], r["at"], r["actor"], r["event"], r["from_status"], r["to_status"], json.loads(r["detail"])
            )
            for r in rows
        ]


def _record(row: sqlite3.Row) -> CaseRecord:
    return CaseRecord(
        id=row["id"],
        status=row["status"],
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        application=Application.model_validate_json(row["application"]),
        result=CaseResult.model_validate_json(row["result"]) if row["result"] else None,
        decided_by=row["decided_by"],
        decision_note=row["decision_note"],
    )
