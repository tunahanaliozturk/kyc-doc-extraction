-- INSERT OR REPLACE deletes the clashing row without firing delete triggers (recursive_triggers is off), so the
-- no-delete trigger alone let a hand-run statement overwrite an audit event. Refuse an insert onto an existing seq.

CREATE TRIGGER audit_events_no_replace BEFORE INSERT ON audit_events
WHEN EXISTS (SELECT 1 FROM audit_events WHERE seq = NEW.seq)
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only');
END;
