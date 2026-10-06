## Eval run: offline replay (scripted replies, not a model)

56 cases, 117 documents, seed 7, reference date 2026-10-01. Took 0.7 s on Python 3.14.6, Windows 11 AMD64.

| Measure | Value |
|---|---|
| False approvals (must be 0) | 0 of 31 cases that should not be approved |
| False approvals on the 31 hard cases | 0 |
| Decisions matching the expected outcome | 56 of 56 |
| Straight-through (auto-approved) | 25 (44.6%) |
| Sent to human review | 25 (44.6%) |
| Rejected automatically | 6 (10.7%) |
| Wrongly rejected | 0 |
| Documents that needed a retry | 5 |

Expected outcome down, actual across:

| expected \ actual | approved | in_review | rejected |
|---|---|---|---|
| approved | 25 | 0 | 0 |
| in_review | 0 | 25 | 0 |
| rejected | 0 | 0 | 6 |

Per-field accuracy (exact string, then after folding case, accents, spaces and punctuation):

| Field | Documents | Exact | Normalised |
|---|---|---|---|
| company_extract.company_name | 6 | 100.0% | 100.0% |
| company_extract.country | 6 | 100.0% | 100.0% |
| company_extract.directors | 6 | 100.0% | 100.0% |
| company_extract.registration_number | 6 | 100.0% | 100.0% |
| company_extract.status | 6 | 100.0% | 100.0% |
| identity.date_of_birth | 56 | 92.9% | 92.9% |
| identity.date_of_expiry | 56 | 91.1% | 91.1% |
| identity.document_number | 56 | 91.1% | 91.1% |
| identity.given_names | 56 | 96.4% | 96.4% |
| identity.issuing_country | 56 | 96.4% | 96.4% |
| identity.mrz | 56 | 91.1% | 91.1% |
| identity.nationality | 56 | 96.4% | 96.4% |
| identity.sex | 56 | 96.4% | 96.4% |
| identity.surname | 56 | 96.4% | 96.4% |
| proof_of_address.address_line | 55 | 94.5% | 94.5% |
| proof_of_address.city | 55 | 94.5% | 94.5% |
| proof_of_address.country | 55 | 100.0% | 100.0% |
| proof_of_address.holder_name | 55 | 100.0% | 100.0% |
| proof_of_address.iban | 55 | 83.6% | 100.0% |
| proof_of_address.issue_date | 55 | 100.0% | 100.0% |
| proof_of_address.issuer | 55 | 100.0% | 100.0% |
| proof_of_address.postcode | 55 | 94.5% | 94.5% |
