-- Cases and their append-only audit trail.

CREATE TABLE cases (
    id            TEXT PRIMARY KEY,
    status        TEXT NOT NULL CHECK (status IN ('processing', 'approved', 'in_review', 'rejected')),
    version       INTEGER NOT NULL,  -- bumped on every change; reviewers send it back in If-Match
    created_at    TEXT NOT NULL,     -- ISO 8601 UTC with microseconds, so text order is time order
    updated_at    TEXT NOT NULL,
    application   TEXT NOT NULL,     -- JSON, as submitted
    result        TEXT,              -- JSON CaseResult once processing finished
    decided_by    TEXT,              -- reviewer name for a human decision, NULL for an automatic one
    decision_note TEXT
) STRICT;

-- The review queue is read oldest first and paged by (created_at, id).
CREATE INDEX cases_review_queue ON cases (created_at, id) WHERE status = 'in_review';

CREATE TRIGGER cases_are_kept BEFORE DELETE ON cases
BEGIN
    SELECT RAISE(ABORT, 'cases are never deleted here; retention is a separate, audited process');
END;

CREATE TABLE audit_events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL REFERENCES cases (id),
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL,   -- "system" or the reviewer's name
    event       TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    detail      TEXT NOT NULL    -- JSON: outcomes and check names, never document content or credentials
) STRICT;

CREATE INDEX audit_events_by_case ON audit_events (case_id, seq);

-- Append-only is enforced by the database, so no code path, bug or hand-run UPDATE can rewrite history.
CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;

CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;
