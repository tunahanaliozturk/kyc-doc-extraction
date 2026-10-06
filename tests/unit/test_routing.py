"""Every combination of check results routes the way docs/adr/0003 says."""

import itertools

import pytest

from kyc.routing import Outcome, decide
from kyc.verification import Check, Status


def check(name: str, status: Status, *, rejects: bool = False) -> Check:
    return Check(name=name, status=status, reason="test", rejects=rejects)


# (status of an ordinary check, status of a check that rejects when it fails) -> outcome
TABLE = {
    ("pass", "pass"): Outcome.APPROVED,
    ("unknown", "pass"): Outcome.IN_REVIEW,
    ("fail", "pass"): Outcome.IN_REVIEW,
    ("pass", "unknown"): Outcome.IN_REVIEW,
    ("unknown", "unknown"): Outcome.IN_REVIEW,
    ("fail", "unknown"): Outcome.IN_REVIEW,
    ("pass", "fail"): Outcome.REJECTED,
    ("unknown", "fail"): Outcome.REJECTED,
    ("fail", "fail"): Outcome.REJECTED,
}


@pytest.mark.parametrize(("statuses", "expected"), TABLE.items())
def test_routing_table(statuses: tuple[Status, Status], expected: Outcome) -> None:
    ordinary, hard = statuses
    checks = [check("address_matches_application", ordinary), check("mrz_check_digits", hard, rejects=True)]
    assert decide(checks).outcome is expected


def test_the_table_covers_every_combination() -> None:
    assert set(TABLE) == set(itertools.product(["pass", "unknown", "fail"], repeat=2))


def test_an_unknown_is_never_an_approval_however_many_checks_pass() -> None:
    checks = [check(f"c{i}", "pass") for i in range(30)] + [check("holder_is_adult", "unknown")]
    assert decide(checks).outcome is Outcome.IN_REVIEW


def test_a_check_marked_rejects_only_rejects_when_it_fails() -> None:
    assert decide([check("document_not_expired", "unknown", rejects=True)]).outcome is Outcome.IN_REVIEW


def test_review_reasons_name_every_open_check() -> None:
    decision = decide([check("a", "pass"), check("b", "fail"), check("c", "unknown")])
    assert [r.split(" ")[0] for r in decision.reasons] == ["b", "c"]


def test_no_checks_is_not_an_approval() -> None:
    assert decide([]).outcome is Outcome.IN_REVIEW
