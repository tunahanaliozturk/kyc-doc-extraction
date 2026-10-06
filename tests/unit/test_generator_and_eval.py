"""The synthetic set and the offline eval gate that CI runs."""

import hashlib
import io
from collections import Counter

import pytest
from PIL import Image
from pypdf import PdfReader

from kyc.evaluation import Report, evaluate, normalise
from kyc.generator import PEOPLE, generate, replies_by_hash
from kyc.replay import ReplayClient
from kyc.routing import Outcome

CASES = generate()


def test_the_set_is_deterministic_for_a_seed() -> None:
    again = generate()
    assert [d.content for c in again for d in c.documents] == [d.content for c in CASES for d in c.documents]


def test_the_set_has_the_same_bytes_on_every_platform() -> None:
    # The replay client and the API's offline mode find scripted answers by document hash, so a specimen generated
    # on Windows must hash the same as one generated in the Linux container. Compression is off for that reason.
    # This digest changes when reportlab or Pillow change their output; regenerate it then, on any platform.
    digest = hashlib.sha256(b"".join(d.content for c in CASES for d in c.documents)).hexdigest()
    assert digest[:16] == "ea950e4c05d860f2"


def test_the_set_has_the_promised_size_and_mix() -> None:
    assert 40 <= len(CASES) <= 60
    expected = Counter(c.expected for c in CASES)
    assert expected[Outcome.APPROVED] >= 20
    assert expected[Outcome.REJECTED] >= 6
    scenarios = {c.scenario for c in CASES}
    for hard in (
        "name_mismatch",
        "expired_id",
        "forged_mrz_check_digit",
        "blurred_rotated_photo",
        "address_mismatch",
        "prompt_injection",
    ):
        assert hard in scenarios


def test_every_pdf_says_specimen_in_its_own_text() -> None:
    for case in CASES:
        for doc in case.documents:
            if doc.media_type == "application/pdf":
                text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(doc.content)).pages)
                assert "SPECIMEN" in text, case.case_id


def test_photos_are_real_pngs() -> None:
    photos = [d for c in CASES for d in c.documents if d.media_type == "image/png"]
    assert len(photos) >= 5
    for doc in photos:
        assert Image.open(io.BytesIO(doc.content)).format == "PNG"


def test_names_are_the_obviously_fake_ones() -> None:
    fake = {f"{p.given} {p.surname}" for p in PEOPLE}
    assert {c.application.full_name for c in CASES} <= fake


@pytest.fixture(scope="module")
def offline_report() -> Report:
    return evaluate(CASES, ReplayClient(replies_by_hash(CASES)), "offline")


def test_the_offline_eval_has_no_false_approvals(offline_report: Report) -> None:
    assert offline_report.false_approvals == []
    assert offline_report.hard_false_approvals == 0


def test_the_offline_eval_matches_every_expected_decision(offline_report: Report) -> None:
    assert offline_report.mismatches == []


def test_the_retry_cases_needed_exactly_one_retry(offline_report: Report) -> None:
    assert offline_report.retried_documents == 4 + 1  # four scripted retries and the always-invalid document


def test_normalised_comparison() -> None:
    assert normalise("NL91 ABNA 0417 1643 00") == normalise("NL91ABNA0417164300")
    assert normalise("Örnekoğlu") == normalise("ORNEKOGLU")
    assert normalise(["b", "A"]) == normalise(["a", "B"])
    assert normalise(None) == ""
