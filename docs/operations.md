# Operations

How to run the API, what to watch, and what to do when it misbehaves. Written for someone who has never seen the
code.

## Configuration

| Variable | Default | What it does | Raising or changing it costs |
|---|---|---|---|
| `KYC_API_TOKENS` | empty | `name:token,name:token`. Tokens of at least 16 characters. Empty means every call except `/health` gets 401. | More tokens, more people who can decide cases. There are no roles. |
| `KYC_MODE` | `replay` | `replay` answers from the script for generated specimens. `live` calls Claude. | `live` costs money per document and needs a credential. |
| `KYC_MODEL` | `claude-sonnet-5-5` | Model for `live`. | A different model changes reading accuracy; re-run `kyc-eval --live` before trusting it. |
| `ANTHROPIC_API_KEY` | unset | Read by the SDK in `live` mode. `ant auth login` works too. | Never put it in the image or the repo. |
| `KYC_DB` | `data/kyc.sqlite3` (`/data/kyc.sqlite3` in the image) | SQLite file. Created and migrated on start. | Put it on a disk that is backed up. |
| `KYC_HOST`, `KYC_PORT` | `127.0.0.1`, `8000` | Where the API listens. The image overrides the host to `0.0.0.0` inside the container. | Binding anything but loopback on a host exposes demo auth to the network. |

Constants in code: `MAX_ATTEMPTS = 3` (extraction.py), `ADULT_AGE = 18`,
`PROOF_OF_ADDRESS_MAX_AGE_DAYS = 90`, `ADDRESS_LINE_MATCH = 0.90`, `ADDRESS_LINE_MISMATCH = 0.70`
(verification.py), `NEAR_MATCH_THRESHOLD = 0.80` (names.py), `MAX_DOCUMENTS = 6`, `MAX_DOCUMENT_BYTES = 10 MiB`,
`limit` capped at 100 (api.py).

## Running it

```bash
uv sync --locked
KYC_API_TOKENS=alice:alice-demo-token-0001 uv run kyc-api
```

In a container, publishing on the host's loopback only:

```bash
docker build -t kyc-doc-extraction .
docker run -p 127.0.0.1:8000:8000 -v kyc-data:/data -e KYC_API_TOKENS=alice:alice-demo-token-0001 kyc-doc-extraction
```

The image runs as uid 10001 and has a health check that calls `/health` with Python, because the slim base image
has no curl or wget.

## What to watch

| Signal | Where | Why it matters | Act when |
|---|---|---|---|
| Review rate | `SELECT status, count(*) FROM cases GROUP BY status` | A jump means a check started failing for everyone, or the model started reading worse. | Review share moves more than 10 points from last week's. |
| Automatic approvals of cases later found wrong | reviewer reports | The only number that must stay at zero. | Any single one: stop auto-approval (see runbook). |
| Extraction failures | `GET /v1/cases/{id}`, checks named `extraction` with status `unknown` | `model_unavailable` means the API is down or rate-limited; `refused` and `invalid_after_retries` mean the documents changed. | More than a handful per hour. |
| Cases stuck in `processing` | `SELECT count(*) FROM cases WHERE status = 'processing'` | Processing runs in the API process. | Any case older than five minutes. |
| `processing_interrupted` audit events | `audit_events` | Restarts during processing. | Every one: someone must review that case by hand. |

## Runbook

### Every case lands in review with `extraction unknown: model_unavailable`

Confirm: open one case and read the `extraction` check's reason; it names the SDK error class
(`APIConnectionError`, `RateLimitError`, `AuthenticationError`). Healthy cases show `valid on the first attempt`.

Act: for `AuthenticationError`, the credential is wrong or expired; fix `ANTHROPIC_API_KEY` and restart. For
`RateLimitError`, lower the submission rate; the SDK already retried twice. For connection errors, check outbound
network from the host.

Verify: submit a known specimen (`tools/submit_case.py data/specimens/case-001`) and see it approved.

Do not switch to `KYC_MODE=replay` to clear the backlog. Replay only knows the generated specimens, so real
documents would all land in review with no extraction at all.

### Cases stay in `processing`

Confirm: `SELECT id, created_at FROM cases WHERE status = 'processing'`. The API log shows `processing failed for
case ...` with a stack trace when the pipeline raised.

Act: restart the API. On start it moves every case still in `processing` to `in_review` and writes a
`processing_interrupted` audit event. The documents were only held in memory, so a reviewer must ask the customer
to upload them again.

Verify: the count above is zero and the cases appear in `GET /v1/review-queue`.

### Two reviewers say they both decided the same case

Confirm: `GET /v1/cases/{id}/audit`. There is exactly one `review_decided` event; the second reviewer got 409 or
412. If there are two, the triggers or the conditional update were removed: stop the API.

### Something approved a case it should not have

Stop the bleeding first: set `KYC_API_TOKENS` to empty and restart, which stops intake and decisions. Pull the case
with its checks from `GET /v1/cases/{id}`. Every approval has every check at `pass`, so the question is which check
passed wrongly. Add the case to the generator as a scenario, make the eval gate fail on it, then fix the check.

Do not edit the case or its audit events in the database; the triggers refuse, and the record is the evidence.

### The database file grows

Cases are never deleted (a trigger refuses). Retention is a separate, audited process that this repo does not
implement. Back up the file with `sqlite3 kyc.sqlite3 ".backup copy.sqlite3"` while the API runs; WAL mode makes
that safe.

## Known limitations

See the README's limitations section. The ones an operator feels first: processing runs inside the API process,
photos always go to review, and auth is a demo.
