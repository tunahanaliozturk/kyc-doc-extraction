# Security

## Reporting a problem

Please report a vulnerability privately through GitHub's "Report a vulnerability" on this repository, not in a
public issue. I aim to reply within a week. This is a portfolio project with no production deployment, so there is
no bounty.

## What this repository does and does not protect

Designed in:

- **Decisions do not come from the model.** Text inside a document cannot approve a case; at worst it makes the
  model report false values, which the checks and the text-layer grounding catch (ADR 0001, ADR 0007).
- **Audit trail is append-only in the database.** Triggers abort any `UPDATE`, `DELETE` or `INSERT OR REPLACE` on
  an existing row of `audit_events`.
- **Identity comes from the credential.** The reviewer name is taken from the bearer token, never from the body.
  Tokens are compared with `hmac.compare_digest` against every configured token.
- **Uploads are checked** for valid base64, a 10 MiB cap per document, at most six documents, and the file's magic
  bytes matching the declared type. The whole request body is capped (413) before FastAPI reads and parses it.
- **Parameterised SQL everywhere.** No string-built queries.
- **Loopback by default.** The API binds 127.0.0.1 unless told otherwise.
- **No real personal data.** Every document is a generated SPECIMEN with an invented name.

Not done, and needed before anything real:

- **Auth is a demo.** Static tokens, no expiry, no rotation, no roles. A real system needs SSO with separate
  submitter and reviewer roles, and four-eyes approval for high-risk cases.
- **Documents are processed in memory and not stored.** A deployment needs encrypted storage with retention rules
  and access logging for the originals.
- **The database file is not encrypted.** It holds names, birth dates and addresses.
- **No rate limiting** on the API.
- **Text layer versus rendered page** is not compared, so a crafted PDF can show one thing and say another.
- **Someone with file access can drop the triggers.** The append-only guarantee protects against bugs and mistakes,
  not against an administrator. Ship audit events to write-once storage for that.

## Secrets

The repository holds none. The demo token in the README is a placeholder for local use. `ANTHROPIC_API_KEY` is
read by the SDK from the environment and is never logged or written to the database. `.env` files are ignored.
