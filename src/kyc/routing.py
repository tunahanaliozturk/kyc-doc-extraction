"""Turn check results into a decision. Three rules, in this order:

1. Any failed check marked `rejects` rejects the case: the evidence cannot be a misreading (docs/adr/0003).
2. Every check passed: approve without a human.
3. Anything else, a failure or an unknown, goes to the review queue with those checks attached.

The model's own sense of confidence is not an input. It is not calibrated, it cannot be audited after the fact,
and a document can talk it into being confident. The checks can be re-run by anyone and give the same answer.
"""

from enum import StrEnum

from pydantic import BaseModel

from kyc.verification import Check


class Outcome(StrEnum):
    APPROVED = "approved"
    IN_REVIEW = "in_review"
    REJECTED = "rejected"


class Decision(BaseModel):
    outcome: Outcome
    reasons: list[str]  # the checks behind the outcome, empty for an approval


def decide(checks: list[Check]) -> Decision:
    if not checks:
        return Decision(outcome=Outcome.IN_REVIEW, reasons=["no checks ran"])
    rejecting = [c for c in checks if c.status == "fail" and c.rejects]
    if rejecting:
        return Decision(outcome=Outcome.REJECTED, reasons=[f"{c.name}: {c.reason}" for c in rejecting])
    open_checks = [c for c in checks if c.status != "pass"]
    if not open_checks:
        return Decision(outcome=Outcome.APPROVED, reasons=[])
    return Decision(outcome=Outcome.IN_REVIEW, reasons=[f"{c.name} {c.status}: {c.reason}" for c in open_checks])
