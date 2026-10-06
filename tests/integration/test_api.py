"""The HTTP contract, against a real SQLite file and the scripted reader. No network, no Docker."""

import base64
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kyc import api as api_module
from kyc.api import create_app, parse_tokens
from kyc.generator import REFERENCE_DATE, SpecimenCase, generate, replies_by_hash
from kyc.replay import ReplayClient
from kyc.routing import Outcome
from kyc.store import NotInReview, Store, VersionMismatch

ALICE = "alice-demo-token-0001"
BOB = "bob-demo-token-000002"
CASES = generate()


def by_scenario(name: str) -> SpecimenCase:
    return next(c for c in CASES if c.scenario == name)


def submission(case: SpecimenCase) -> dict[str, Any]:
    return {
        "application": case.application.model_dump(mode="json"),
        "documents": [
            {"kind": d.kind.value, "media_type": d.media_type, "content_base64": base64.b64encode(d.content).decode()}
            for d in case.documents
        ],
    }


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "kyc.sqlite3")
    s.migrate()
    return s


@pytest.fixture
def api(store: Store) -> Iterator[TestClient]:
    app = create_app(store, ReplayClient(replies_by_hash(CASES)), lambda: REFERENCE_DATE, {ALICE: "alice", BOB: "bob"})
    with TestClient(app) as client:
        yield client


def auth(token: str = ALICE) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def submit(api: TestClient, scenario: str) -> dict[str, Any]:
    response = api.post("/v1/cases", json=submission(by_scenario(scenario)), headers=auth())
    assert response.status_code == 202
    # TestClient runs the background task before returning, so the case is already decided when we read it.
    case: dict[str, Any] = api.get(response.headers["Location"], headers=auth()).json()
    return case


def test_a_clean_case_is_accepted_then_approved_without_a_human(api: TestClient) -> None:
    response = api.post("/v1/cases", json=submission(by_scenario("clean_id_card")), headers=auth())
    assert response.status_code == 202
    assert response.json()["status"] == "processing"
    assert response.headers["ETag"] == '"1"'
    case = api.get(response.headers["Location"], headers=auth())
    assert case.status_code == 200
    assert case.json()["status"] == "approved"
    assert case.headers["ETag"] == '"2"'
    assert case.json()["result"]["decision"]["reasons"] == []


def test_a_case_in_review_shows_the_failed_checks_and_their_quotes(api: TestClient) -> None:
    case = submit(api, "address_mismatch")
    assert case["status"] == "in_review"
    failed = [c for c in case["result"]["checks"] if c["status"] != "pass"]
    assert [c["name"] for c in failed] == ["address_matches_application"]
    assert "Andereweg 99" in failed[0]["quotes"]


def test_the_review_queue_lists_only_cases_in_review_oldest_first(api: TestClient) -> None:
    first = submit(api, "name_mismatch")
    submit(api, "clean_id_card")
    second = submit(api, "stale_proof_of_address")
    submit(api, "expired_id")
    page = api.get("/v1/review-queue", headers=auth()).json()
    assert [i["id"] for i in page["items"]] == [first["id"], second["id"]]
    assert page["items"][0]["open_checks"] == ["address_holder_matches_application"]
    assert page["next_cursor"] is None


def test_the_review_queue_pages_with_a_cursor(api: TestClient) -> None:
    ids = [submit(api, s)["id"] for s in ("name_mismatch", "address_mismatch", "stale_proof_of_address")]
    one = api.get("/v1/review-queue", params={"limit": 2}, headers=auth()).json()
    two = api.get("/v1/review-queue", params={"limit": 2, "cursor": one["next_cursor"]}, headers=auth()).json()
    assert [i["id"] for i in one["items"] + two["items"]] == ids
    assert two["next_cursor"] is None


def test_a_reviewer_approves_with_the_etag_and_the_audit_trail_records_every_step(api: TestClient) -> None:
    case = submit(api, "name_mismatch")
    response = api.post(
        f"/v1/cases/{case['id']}/decision",
        json={"outcome": "approve", "note": "Bill is in the name of a partner at the same address; checked."},
        headers=auth(BOB) | {"If-Match": f'"{case["version"]}"'},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["reviewed_by"] == "bob"
    trail = api.get(f"/v1/cases/{case['id']}/audit", headers=auth()).json()["events"]
    assert [(e["event"], e["actor"], e["to_status"]) for e in trail] == [
        ("case_submitted", "alice", "processing"),
        ("checks_completed", "system", "in_review"),
        ("review_decided", "bob", "approved"),
    ]


def test_deciding_a_case_twice_is_a_conflict(api: TestClient) -> None:
    case = submit(api, "name_mismatch")
    url, headers = f"/v1/cases/{case['id']}/decision", auth() | {"If-Match": f'"{case["version"]}"'}
    assert api.post(url, json={"outcome": "reject", "note": "not the same person"}, headers=headers).status_code == 200
    again = api.post(url, json={"outcome": "approve", "note": "second opinion"}, headers=headers)
    assert again.status_code == 409
    assert again.json()["code"] == "case_not_in_review"


def test_an_automatic_decision_cannot_be_overridden_through_the_review_endpoint(api: TestClient) -> None:
    case = submit(api, "expired_id")
    assert case["status"] == "rejected"
    response = api.post(
        f"/v1/cases/{case['id']}/decision",
        json={"outcome": "approve", "note": "please"},
        headers=auth() | {"If-Match": f'"{case["version"]}"'},
    )
    assert response.status_code == 409


def test_a_stale_etag_is_refused(api: TestClient) -> None:
    case = submit(api, "name_mismatch")
    response = api.post(
        f"/v1/cases/{case['id']}/decision",
        json={"outcome": "approve", "note": "ok"},
        headers=auth() | {"If-Match": '"1"'},
    )
    assert response.status_code == 412
    assert response.json()["code"] == "version_mismatch"
    assert response.headers["ETag"] == f'"{case["version"]}"'


def test_a_decision_without_if_match_is_refused(api: TestClient) -> None:
    case = submit(api, "name_mismatch")
    response = api.post(f"/v1/cases/{case['id']}/decision", json={"outcome": "approve", "note": "ok"}, headers=auth())
    assert response.status_code == 428
    assert response.json()["code"] == "if_match_required"


def test_unknown_cases_are_404_with_a_stable_code(api: TestClient) -> None:
    assert api.get("/v1/cases/nope", headers=auth()).json()["code"] == "case_not_found"
    assert api.get("/v1/cases/nope/audit", headers=auth()).status_code == 404
    response = api.post(
        "/v1/cases/nope/decision", json={"outcome": "approve", "note": "x"}, headers=auth() | {"If-Match": '"2"'}
    )
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


def test_every_validation_problem_comes_back_at_once_as_422(api: TestClient) -> None:
    body = submission(by_scenario("clean_id_card"))
    body["application"]["nationality"] = "XX"
    body["application"]["address"]["country"] = "UK"
    body["documents"][0]["kind"] = "selfie"
    response = api.post("/v1/cases", json=body, headers=auth())
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"
    fields = {e["field"] for e in response.json()["errors"]}
    assert {"body.application.nationality", "body.application.address.country", "body.documents.0.kind"} <= fields


def test_a_decision_needs_a_note(api: TestClient) -> None:
    case = submit(api, "name_mismatch")
    response = api.post(
        f"/v1/cases/{case['id']}/decision",
        json={"outcome": "approve", "note": ""},
        headers=auth() | {"If-Match": f'"{case["version"]}"'},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        ("not base64!", "application/pdf"),
        (base64.b64encode(b"just text").decode(), "application/pdf"),
        (base64.b64encode(b"%PDF-1.7").decode(), "image/png"),
    ],
)
def test_a_document_that_is_not_what_it_claims_is_422(api: TestClient, content: str, media_type: str) -> None:
    body = submission(by_scenario("clean_id_card"))
    body["documents"][0] |= {"content_base64": content, "media_type": media_type}
    response = api.post("/v1/cases", json=body, headers=auth())
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_document"


@pytest.mark.parametrize("chunked", [False, True])
def test_a_body_over_the_limit_is_refused_before_it_is_parsed(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, chunked: bool
) -> None:
    monkeypatch.setattr(api_module, "MAX_BODY_BYTES", 1000)
    body = b'{"documents": "' + b"A" * 5000 + b'"}'
    content: Any = iter([body[:2500], body[2500:]]) if chunked else body
    response = api.post("/v1/cases", content=content, headers={**auth(), "Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_a_malformed_cursor_is_422(api: TestClient) -> None:
    assert api.get("/v1/review-queue", params={"cursor": "garbage"}, headers=auth()).status_code == 422


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong-token-of-some-length"}, {"Authorization": ALICE}]
)
def test_calls_without_a_valid_token_are_401(api: TestClient, headers: dict[str, str]) -> None:
    response = api.get("/v1/review-queue", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_health_needs_no_token(api: TestClient) -> None:
    assert api.get("/health").json() == {"status": "ok"}


def test_tokens_must_be_long_enough() -> None:
    with pytest.raises(ValueError, match="16 characters"):
        parse_tokens("alice:short")
    assert parse_tokens("alice:aaaaaaaaaaaaaaaa, bob:bbbbbbbbbbbbbbbb") == {
        "aaaaaaaaaaaaaaaa": "alice",
        "bbbbbbbbbbbbbbbb": "bob",
    }


# --- the store underneath ---------------------------------------------------------------------------------------


def in_review_case(store: Store) -> str:
    case = by_scenario("name_mismatch")
    record = store.create_case(case.application, [], "test")
    store.recover_interrupted()  # the quickest way to put a case in review without running the pipeline
    return record.id


def test_two_reviewers_deciding_at_once_cannot_both_win(store: Store) -> None:
    case_id = in_review_case(store)
    version = store.get(case_id).version
    barrier = threading.Barrier(8)
    outcomes: list[str] = []
    lock = threading.Lock()

    def reviewer(i: int) -> None:
        barrier.wait()
        try:
            outcome = Outcome.APPROVED if i % 2 else Outcome.REJECTED
            store.decide(case_id, expected_version=version, reviewer=f"r{i}", outcome=outcome, note="race")
            result = "won"
        except NotInReview, VersionMismatch:
            result = "refused"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=reviewer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["refused"] * 7 + ["won"]
    decided = [e for e in store.audit(case_id) if e.event == "review_decided"]
    assert len(decided) == 1
    assert store.get(case_id).decided_by == decided[0].actor


def test_the_audit_log_cannot_be_rewritten_or_deleted(store: Store) -> None:
    case_id = in_review_case(store)
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE audit_events SET actor = 'someone else' WHERE case_id = ?", (case_id,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM audit_events WHERE case_id = ?", (case_id,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            # REPLACE deletes the old row without firing delete triggers, so it needs its own guard.
            conn.execute(
                "INSERT OR REPLACE INTO audit_events (seq, case_id, at, actor, event, detail)"
                " SELECT seq, case_id, at, 'someone else', event, detail FROM audit_events WHERE case_id = ?",
                (case_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
    conn.close()


def test_migrations_are_idempotent(store: Store) -> None:
    assert store.migrate() == 2
    assert store.migrate() == 2


def test_an_interrupted_case_moves_to_review_with_an_audit_event(store: Store) -> None:
    case = by_scenario("clean_id_card")
    record = store.create_case(case.application, [], "test")
    assert store.recover_interrupted() == 1
    assert store.get(record.id).status == "in_review"
    assert store.audit(record.id)[-1].event == "processing_interrupted"
    assert store.recover_interrupted() == 0
