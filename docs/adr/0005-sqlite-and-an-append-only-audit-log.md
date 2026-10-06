# 0005. SQLite, hand-written migrations, and an audit log the database refuses to rewrite

Status: Accepted, 2026-10-06

## Context

The review queue needs durable cases, reviewer decisions that two people cannot both make, and an audit trail
that shows every state change. This is a portfolio project meant to run with `uv sync` and nothing else, on one
machine. The house default for a service is Postgres with asyncpg.

## Decision

We use SQLite through the standard library's `sqlite3`, one connection per unit of work, parameterised SQL only.
Migrations are numbered `.sql` files in `src/kyc/migrations/`, applied in order and tracked in
`PRAGMA user_version`; each runs in one transaction with its version bump. Append-only is enforced by triggers:
`UPDATE` or `DELETE` on `audit_events`, and `DELETE` on `cases`, abort with an error. Every state change writes its
audit event in the same transaction. Reviewer decisions use `BEGIN IMMEDIATE` plus an `UPDATE ... WHERE version = ?
AND status = 'in_review'`, and the API requires the version back in `If-Match`.

## Consequences

- No server to run. The quick start is three commands, and CI needs no container for the API tests.
- Two reviewers deciding at once: one wins, the other gets 409 or 412. Eight threads racing on one case give exactly
  one winner and one audit event (`test_two_reviewers_deciding_at_once_cannot_both_win`).
- A bug or a hand-typed `UPDATE` cannot rewrite history (`test_the_audit_log_cannot_be_rewritten_or_deleted`).
  Someone with file access can still drop the triggers; the protection is against mistakes, not an insider. A real
  deployment would ship audit events to write-once storage as well.
- SQLite allows one writer at a time. Fine for a review queue measured in cases per minute, not for a bank's
  intake volume. The path out is Postgres with the same SQL; the conditional `UPDATE` carries over unchanged.
- Hand-written migrations have no autogenerate and no downgrade. With one migration that is cheaper than Alembic.

## Alternatives

- **Postgres with asyncpg.** Would win as soon as there is more than one API instance or real volume. Lost here on
  setup cost for a reader who wants to run it in two minutes.
- **SQLAlchemy and Alembic.** Lost: two dependencies and a model layer for two tables.
- **Append-only enforced in application code.** Lost: any other code path, or a person with a SQL prompt, can
  bypass it. A trigger cannot be forgotten.
