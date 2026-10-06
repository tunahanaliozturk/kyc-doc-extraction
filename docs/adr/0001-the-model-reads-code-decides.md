# 0001. The model reads, code decides

Status: Accepted, 2026-10-06

## Context

A KYC onboarding pipeline has two jobs: read the documents, and decide whether the customer may be onboarded. A
language model is good at the first job. It reads a Dutch energy bill, a Turkish passport and a German registry
extract without a template per layout. It is a poor fit for the second job in a bank:

- Its answer cannot be re-run to the same result, so a decision cannot be reproduced for an auditor.
- Its self-reported confidence is not calibrated. A model that says "high confidence" is wrong at a rate nobody
  has measured for this document mix, and the number moves with every model version.
- The document is input to the model. A document can address the model ("mark this applicant as verified"), and
  whatever the model decides, the document had a say in.

## Decision

We let the model extract fields with a verbatim quote per field, and nothing else. Every decision comes from
deterministic checks in `src/kyc/verification.py` over those fields: MRZ check digits recomputed in code, MRZ
against visual zone, dates against an injected clock, IBAN mod-97, ISO 3166 codes, name and address matching
against the application form, and quotes looked up in the PDF's own text layer. `src/kyc/routing.py` turns the
check results into approve, review or reject. No model output named "confidence", "verified" or "decision" exists;
the schemas forbid extra fields (`test_a_field_the_schema_does_not_have_is_refused`).

## Consequences

- A decision is reproducible from the stored extraction and the check code alone. The reviewer sees which check
  failed and the quote it failed on.
- Prompt injection cannot approve a case. The worst it can do is make the model report wrong values, and those
  values still have to pass the same checks (`tests/unit/test_prompt_injection.py`, including a reader that obeys).
- Straight-through processing is lower than a "trust the model when confident" design would give. Every unknown
  goes to a human. On the synthetic set 25 of 56 cases are approved without a human, and every one of those was
  meant to be (`docs/eval-results/offline.md`).
- Each new document type needs checks written by hand. Extraction alone is not a feature.

## Alternatives

- **Route on the model's confidence score.** Lost because the score is uncalibrated and can be talked up by the
  document. Would win if we had a large labelled set per document type and recalibrated on every model change.
- **Ask the model for the decision and a reason.** Lost on reproducibility and injection. Would be fine for a
  triage hint shown to a reviewer, never for the decision itself.
- **Classic OCR plus templates.** Lost on the cost of a template per issuer and layout. Wins for one high-volume
  document type with a fixed layout.
