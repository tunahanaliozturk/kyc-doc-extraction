"""Checks run on real generated specimens, with the scripted reader standing in for the model."""

import json
from datetime import date

import pytest

from kyc.generator import identity_document, misread, proof_of_address
from kyc.routing import Outcome
from kyc.schemas import Address, DocumentKind, Extracted
from kyc.verification import grounded, normalise_address, supported
from tests.helpers import find, person_case, run, status


def test_a_consistent_case_is_approved_with_every_check_passing() -> None:
    app, ident, poa = person_case()
    result = run(app, [identity_document(ident), poa])
    assert result.decision.outcome is Outcome.APPROVED
    assert {c.status for c in result.checks} == {"pass"}


def test_turkish_letters_survive_the_trip_through_pdf_mrz_and_name_matching() -> None:
    app, ident, poa = person_case(index=2)  # Ayşe Örnekoğlu, İstanbul
    result = run(app, [identity_document(ident), poa])
    assert result.decision.outcome is Outcome.APPROVED
    assert status(result, "mrz_matches_visual_zone") == "pass"
    assert status(result, "quotes_grounded") == "pass"


def test_german_umlauts_match_the_icao_spelling_in_the_mrz() -> None:
    app, ident, poa = person_case(index=8, kind=DocumentKind.PASSPORT)  # Jürgen Müller-Probe, MRZ MUELLER<PROBE
    assert run(app, [identity_document(ident), poa]).decision.outcome is Outcome.APPROVED


def test_an_expired_document_is_rejected_when_the_mrz_agrees() -> None:
    app, ident, poa = person_case(expiry=date(2026, 3, 1))
    result = run(app, [identity_document(ident), poa])
    assert result.decision.outcome is Outcome.REJECTED
    assert find(result, "document_not_expired").rejects


def test_a_misread_expiry_in_the_visual_zone_goes_to_review_not_rejection() -> None:
    app, ident, poa = person_case(expiry=date(2030, 5, 31))
    doc = identity_document(ident)
    misread(doc, "date_of_expiry", "2020-05-31", "31.05.2020")
    result = run(app, [doc, poa])
    assert status(result, "document_not_expired") == "fail"
    assert not find(result, "document_not_expired").rejects
    assert status(result, "mrz_matches_visual_zone") == "fail"
    assert result.decision.outcome is Outcome.IN_REVIEW


def test_a_wrong_check_digit_in_the_pdf_text_rejects() -> None:
    app, ident, poa = person_case()
    result = run(app, [identity_document(ident, forge_check_digit=True), poa])
    check = find(result, "mrz_check_digits")
    assert check.rejects
    assert "document_number" in check.reason
    assert result.decision.outcome is Outcome.REJECTED


def test_a_wrong_check_digit_read_from_a_photo_goes_to_review() -> None:
    app, ident, poa = person_case()
    result = run(app, [identity_document(ident, media="image/png", forge_check_digit=True), poa])
    assert status(result, "mrz_check_digits") == "fail"
    assert not find(result, "mrz_check_digits").rejects
    assert result.decision.outcome is Outcome.IN_REVIEW


def test_a_photo_cannot_be_approved_because_its_quotes_cannot_be_grounded() -> None:
    app, ident, poa = person_case()
    result = run(app, [identity_document(ident, media="image/png"), poa])
    assert status(result, "quotes_grounded") == "unknown"
    assert result.decision.outcome is Outcome.IN_REVIEW


def test_a_misread_birth_date_is_caught_by_the_mrz_and_the_text_layer() -> None:
    app, ident, poa = person_case(birth=date(1988, 4, 17))
    doc = identity_document(ident)
    misread(doc, "date_of_birth", "1988-04-11", "11.04.1988")
    result = run(app, [doc, poa])
    assert status(result, "mrz_matches_visual_zone") == "fail"
    assert status(result, "quotes_grounded") == "fail"
    assert result.decision.outcome is Outcome.IN_REVIEW


def test_a_seventeen_year_old_is_not_auto_approved() -> None:
    app, ident, poa = person_case(birth=date(2009, 6, 1))
    result = run(app, [identity_document(ident), poa])
    assert status(result, "holder_is_adult") == "fail"
    assert result.decision.outcome is Outcome.IN_REVIEW


def test_a_proof_of_address_dated_in_the_future_fails() -> None:
    app, ident, _ = person_case()
    poa = proof_of_address(app.full_name, app.address, date(2026, 12, 1))
    result = run(app, [identity_document(ident), poa])
    assert "in the future" in find(result, "address_document_recent").reason


def test_an_address_written_with_an_abbreviation_still_matches() -> None:
    app, ident, _ = person_case(index=1)  # Musterstraße 7, 10115 Berlin
    written = Address(line="Musterstr. 7", postcode="10115", city="Berlin", country="DE")
    result = run(app, [identity_document(ident), proof_of_address(app.full_name, written, date(2026, 9, 10))])
    assert status(result, "address_matches_application") == "pass"


def test_another_postcode_is_a_mismatch_even_on_the_same_street() -> None:
    app, ident, _ = person_case(index=3)
    moved = app.address.model_copy(update={"postcode": "3512 BB"})
    result = run(app, [identity_document(ident), proof_of_address(app.full_name, moved, date(2026, 9, 10))])
    assert status(result, "address_matches_application") == "fail"


def test_a_value_the_reader_made_up_is_not_grounded() -> None:
    app, ident, poa = person_case()
    fields = json.loads(poa.replies[-1])
    fields["city"] = {"value": "Rotterdam", "quote": "Rotterdam"}
    poa.replies[-1] = json.dumps(fields)
    result = run(app, [identity_document(ident), poa])
    assert "city" in find(result, "quotes_grounded", document=1).reason
    assert result.decision.outcome is Outcome.IN_REVIEW


def test_grounding_ignores_spacing_and_case_but_not_content() -> None:
    page = "Customer:   JAN VOORBEELD\nProefweg 3"
    assert grounded("Jan Voorbeeld", page) is True
    assert grounded("Jan Vorbeeld", page) is False
    assert grounded("Jan Voorbeeld", None) is None


@pytest.mark.parametrize(
    ("name", "value", "quote", "expected"),
    [
        ("holder_name", "Ayşe Örnekoğlu", "Customer: AYŞE ÖRNEKOĞLU", True),
        ("holder_name", "Erika Mustermann", "Pieter Anders", False),
        ("postcode", "1012 AB", "1012 AB Amsterdam", True),
        ("address_line", "Proefweg 3", "Proefweg 31", False),
        ("issue_date", "2026-09-10", "10.09.2026", True),
        ("date_of_birth", "1988-04-17", "17 APR/AVR 88", True),
        ("issue_date", "2026-09-05", "09.05.2026", False),  # a May bill read as a September one
        ("issue_date", "2026-09-10", "10.09.2025", False),
        ("country", "NL", "Netherlands", True),
    ],
)
def test_a_value_must_be_what_its_quote_says(name: str, value: str, quote: str, expected: bool) -> None:
    assert supported(name, Extracted(value=value, quote=quote)) is expected


def test_address_normalisation() -> None:
    assert normalise_address("Musterstraße 7") == normalise_address("Musterstr. 7")
    assert normalise_address("Örnek Sok. No: 5") == "ornek sokak no 5"
    assert normalise_address("İstanbul") == normalise_address("ISTANBUL")
