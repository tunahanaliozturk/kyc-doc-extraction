# 0003. Routing: approve only on all-pass, reject only on evidence that cannot be a misreading

Status: Accepted, 2026-10-06

## Context

Every check returns pass, fail or unknown. A bank cares about two errors with very different costs. A false
approval onboards someone who should not have been, which is a regulatory finding. A false rejection turns away a
real customer, which is a lost customer and a complaint. Sending a case to a human costs a few minutes of a
reviewer's time and is the safe default for both.

## Decision

`src/kyc/routing.py` applies three rules in order:

1. A failed check marked `rejects` rejects the case.
2. If every check passed, the case is approved without a human.
3. Anything else goes to the review queue, with the open checks and their quotes.

Only two checks can reject, and only when the evidence cannot be a reading error:

- `mrz_check_digits` rejects when a check digit is wrong in the PDF's own text layer. That is what the document
  says, not what the model read. The same failure read from a photo goes to review, because it could be a misread
  digit (`test_a_wrong_check_digit_read_from_a_photo_goes_to_review`).
- `document_not_expired` rejects only when the MRZ, read independently, agrees with the visual zone. A misread
  expiry in the visual zone fails the MRZ comparison and goes to review instead
  (`test_a_misread_expiry_in_the_visual_zone_goes_to_review_not_rejection`).

Other thresholds, each a named constant: adult at 18 (`ADULT_AGE`), proof of address at most 90 days old
(`PROOF_OF_ADDRESS_MAX_AGE_DAYS`), street similarity of at least 0.90 with equal postcode, city and country for an
address match and below 0.70 for a mismatch (`ADDRESS_LINE_MATCH`, `ADDRESS_LINE_MISMATCH`). Ninety days is the
common bank rule for proof of address. The street thresholds came from the abbreviation and typo cases in
`tests/unit/test_verification.py`; they never approve on their own, because the postcode must match exactly.

## Consequences

- Unknown is never a soft pass. One unreadable field sends the case to a human
  (`test_an_unknown_is_never_an_approval_however_many_checks_pass`).
- On the synthetic set: 0 false approvals in 31 cases that should not be approved, 0 wrong rejections
  (`docs/eval-results/offline.md`). Offline, those numbers measure the checks and the routing, not a model.
- The review rate is high on purpose. A sharp phone photo of a valid ID still goes to review (ADR 0007).
- A minor, a stale bill and a dissolved company go to review rather than being rejected. They are rule failures,
  not fraud, and a reviewer may know something the form does not.

## Alternatives

- **Weighted score with a threshold.** Lost: it lets four strong passes outvote one failure, which is exactly the
  case a bank wants a human on. It would win once a labelled set showed which failures are harmless.
- **Reject on any failure.** Lost on false rejections: a misread digit would turn away a real customer.
- **Never reject automatically.** Defensible, and simpler. Lost because a forged check digit in the document's own
  text is unambiguous, and reviewer time on it is waste.
