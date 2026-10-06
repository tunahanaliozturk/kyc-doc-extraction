"""The review queue API.

Submit a case, poll its status, list what waits for a human, and approve or reject with a note. Handlers are plain
`def`: sqlite3 and the model client block, so FastAPI runs them in its worker threads instead of stalling the loop.

Auth is a demo: static bearer tokens from KYC_API_TOKENS, compared in constant time, each mapped to a reviewer
name. Good enough to show that identity comes from the credential and never from the request body. Not good enough
for anything real: no expiry, no roles, no rotation. See SECURITY.md.
"""

import argparse
import base64
import binascii
import hmac
import logging
import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kyc import generator
from kyc.extraction import AnthropicModelClient, Document, ModelClient
from kyc.pipeline import CaseResult, process
from kyc.replay import ReplayClient
from kyc.routing import Outcome
from kyc.schemas import Application, DocumentKind, MediaType
from kyc.store import AuditEvent, CaseNotFound, CaseRecord, NotInReview, Store, VersionMismatch

log = logging.getLogger(__name__)

MAX_DOCUMENTS = 6
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_PAGE = 100
MAX_BODY_BYTES = MAX_DOCUMENTS * (MAX_DOCUMENT_BYTES * 4 // 3 + 4) + 1024 * 1024  # every document at its limit
_MAGIC = {"application/pdf": b"%PDF-", "image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail, self.headers = status, code, detail, headers


def _problem(status: int, code: str, detail: str, errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"type": "about:blank", "status": status, "code": code, "detail": detail}
    if errors is not None:
        body["errors"] = errors
    return body


# --- request and response bodies --------------------------------------------------------------------------------


class DocumentUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DocumentKind
    media_type: MediaType
    content_base64: str = Field(min_length=1, max_length=MAX_DOCUMENT_BYTES * 4 // 3 + 4)


class CaseSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application: Application
    documents: list[DocumentUpload] = Field(min_length=1, max_length=MAX_DOCUMENTS)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approve", "reject"]
    note: str = Field(min_length=1, max_length=2000, description="Why; kept in the audit trail")


class CaseView(BaseModel):
    id: str
    status: str  # processing, approved, in_review, rejected. Treat unknown values as "not final".
    version: int
    created_at: str
    updated_at: str
    application: Application
    result: CaseResult | None
    reviewed_by: str | None
    review_note: str | None


class CaseSummary(BaseModel):
    id: str
    created_at: str
    version: int
    full_name: str
    open_checks: list[str]


class ReviewQueuePage(BaseModel):
    items: list[CaseSummary]
    next_cursor: str | None


class AuditEventView(BaseModel):
    seq: int
    at: str
    actor: str
    event: str
    from_status: str | None
    to_status: str | None
    detail: dict[str, Any]


class AuditTrail(BaseModel):
    events: list[AuditEventView]


def _view(record: CaseRecord) -> CaseView:
    return CaseView(
        id=record.id,
        status=record.status,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
        application=record.application,
        result=record.result,
        reviewed_by=record.decided_by,
        review_note=record.decision_note,
    )


def _summary(record: CaseRecord) -> CaseSummary:
    open_checks = [c.name for c in record.result.checks if c.status != "pass"] if record.result else []
    return CaseSummary(
        id=record.id,
        created_at=record.created_at,
        version=record.version,
        full_name=record.application.full_name,
        open_checks=open_checks,
    )


def _cursor(record: CaseRecord) -> str:
    return base64.urlsafe_b64encode(f"{record.created_at}|{record.id}".encode()).decode()


def _parse_cursor(cursor: str) -> tuple[str, str]:
    try:
        created_at, _, case_id = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
    except binascii.Error, UnicodeDecodeError, ValueError:
        created_at = case_id = ""
    if not created_at or not case_id:
        raise ApiError(422, "invalid_cursor", "the cursor is not one this API returned")
    return created_at, case_id


def _decode(upload: DocumentUpload, index: int) -> Document:
    try:
        content = base64.b64decode(upload.content_base64, validate=True)
    except binascii.Error:
        raise ApiError(422, "invalid_document", f"documents[{index}].content_base64 is not valid base64") from None
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ApiError(422, "invalid_document", f"documents[{index}] is over {MAX_DOCUMENT_BYTES} bytes")
    if not content.startswith(_MAGIC[upload.media_type]):
        raise ApiError(422, "invalid_document", f"documents[{index}] is not a {upload.media_type} file")
    return Document(kind=upload.kind, media_type=upload.media_type, content=content)


def _etag(record: CaseRecord) -> str:
    return f'"{record.version}"'


def _parse_if_match(value: str | None) -> int:
    if value is None:
        raise ApiError(428, "if_match_required", "send the case's ETag in If-Match so a stale decision is refused")
    try:
        return int(value.strip().removeprefix("W/").strip('"'))
    except ValueError:
        raise ApiError(412, "version_mismatch", "If-Match does not hold a version of this case") from None


# --- the app ----------------------------------------------------------------------------------------------------


class BodyLimit:
    """Refuse a body over MAX_BODY_BYTES as it arrives. The field limits only run after FastAPI has read and parsed
    the whole body, which is too late for a 2 GB upload."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        declared = dict(scope.get("headers", [])).get(b"content-length", b"")
        received = 0

        async def limited() -> Message:
            nonlocal received
            if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
                raise HTTPException(413, f"the request body is over {MAX_BODY_BYTES} bytes")
            message = await receive()
            received += len(message.get("body", b""))
            if received > MAX_BODY_BYTES:  # no Content-Length, or one that lied
                raise HTTPException(413, f"the request body is over {MAX_BODY_BYTES} bytes")
            return message

        await self.app(scope, limited if scope["type"] == "http" else receive, send)


def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # noqa: S101  # registered for ApiError only
    body = _problem(exc.status, exc.code, exc.detail)
    return JSONResponse(body, exc.status, exc.headers, media_type="application/problem+json")


def _invalid_request(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101  # registered for RequestValidationError only
    errors = [{"field": ".".join(str(p) for p in e["loc"]), "issue": e["msg"]} for e in exc.errors()]
    body = _problem(422, "validation_failed", "the request body or parameters are invalid", errors)
    return JSONResponse(body, 422, media_type="application/problem+json")


def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101  # registered for HTTPException only
    code = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}.get(exc.status_code, "http_error")
    body = _problem(exc.status_code, code, str(exc.detail))
    return JSONResponse(body, exc.status_code, media_type="application/problem+json")


def current_reviewer(request: Request, authorization: Annotated[str | None, Header()] = None) -> str:
    """The caller's name, from the bearer token. Never from the body."""
    tokens: dict[str, str] = request.app.state.tokens
    scheme, _, presented = (authorization or "").partition(" ")
    presented = presented.strip() if scheme.lower() == "bearer" else ""
    name = None
    for token, who in tokens.items():
        # Compare against every token, so the time taken does not reveal which one matched.
        if hmac.compare_digest(presented.encode(), token.encode()):
            name = who
    if not presented or name is None:
        raise ApiError(401, "unauthenticated", "send a valid bearer token", {"WWW-Authenticate": "Bearer"})
    return name


Reviewer = Annotated[str, Depends(current_reviewer)]


def create_app(store: Store, client: ModelClient, today: Callable[[], date], tokens: dict[str, str]) -> FastAPI:
    """`tokens` maps bearer token to reviewer name. With no tokens every authenticated call is refused."""
    app = FastAPI(title="KYC document extraction", version="0.1.0")
    app.state.tokens = tokens
    app.add_middleware(BodyLimit)
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.add_exception_handler(StarletteHTTPException, _http_error)

    def run(case_id: str, application: Application, documents: list[Document]) -> None:
        try:
            result = process(application, documents, client, today())
        except Exception:
            # Leave the case in processing; recover_interrupted() moves it to review on the next start.
            log.exception("processing failed for case %s", case_id)
            raise
        store.record_result(case_id, result)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/cases", status_code=202)
    def submit_case(body: CaseSubmission, tasks: BackgroundTasks, actor: Reviewer, response: Response) -> CaseView:
        documents = [_decode(d, i) for i, d in enumerate(body.documents)]
        summary = [{"kind": d.kind.value, "media_type": d.media_type} for d in documents]
        record = store.create_case(body.application, summary, actor)
        tasks.add_task(run, record.id, body.application, documents)
        response.headers["Location"] = f"/v1/cases/{record.id}"
        response.headers["ETag"] = _etag(record)
        return _view(record)

    @app.get("/v1/cases/{case_id}")
    def get_case(case_id: str, _: Reviewer, response: Response) -> CaseView:
        record = _find(store, case_id)
        response.headers["ETag"] = _etag(record)
        return _view(record)

    @app.get("/v1/review-queue")
    def review_queue(
        _: Reviewer,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 20,
        cursor: Annotated[str | None, Query(max_length=200)] = None,
    ) -> ReviewQueuePage:
        records = store.review_queue(limit + 1, _parse_cursor(cursor) if cursor else None)
        page, more = records[:limit], len(records) > limit
        return ReviewQueuePage(items=[_summary(r) for r in page], next_cursor=_cursor(page[-1]) if more else None)

    @app.post("/v1/cases/{case_id}/decision")
    def decide(
        case_id: str,
        body: ReviewDecision,
        name: Reviewer,
        response: Response,
        if_match: Annotated[str | None, Header()] = None,
    ) -> CaseView:
        expected = _parse_if_match(if_match)
        outcome = Outcome.APPROVED if body.outcome == "approve" else Outcome.REJECTED
        try:
            record = store.decide(case_id, expected_version=expected, reviewer=name, outcome=outcome, note=body.note)
        except CaseNotFound:
            raise ApiError(404, "case_not_found", f"no case {case_id}") from None
        except NotInReview as exc:
            raise ApiError(
                409, "case_not_in_review", f"the case is {exc.status}; only cases in review can be decided"
            ) from None
        except VersionMismatch as exc:
            raise ApiError(
                412,
                "version_mismatch",
                f"the case changed since you read it; it is now at version {exc.current}",
                {"ETag": f'"{exc.current}"'},
            ) from None
        response.headers["ETag"] = _etag(record)
        return _view(record)

    @app.get("/v1/cases/{case_id}/audit")
    def audit(case_id: str, _: Reviewer) -> AuditTrail:
        try:
            events = store.audit(case_id)
        except CaseNotFound:
            raise ApiError(404, "case_not_found", f"no case {case_id}") from None
        return AuditTrail(events=[_event(e) for e in events])

    return app


def _find(store: Store, case_id: str) -> CaseRecord:
    try:
        return store.get(case_id)
    except CaseNotFound:
        raise ApiError(404, "case_not_found", f"no case {case_id}") from None


def _event(e: AuditEvent) -> AuditEventView:
    return AuditEventView(
        seq=e.seq,
        at=e.at,
        actor=e.actor,
        event=e.event,
        from_status=e.from_status,
        to_status=e.to_status,
        detail=e.detail,
    )


def parse_tokens(raw: str) -> dict[str, str]:
    """KYC_API_TOKENS="alice:long-random-token,bob:another-one" -> {token: name}."""
    tokens: dict[str, str] = {}
    for pair in filter(None, (p.strip() for p in raw.split(","))):
        name, _, token = pair.partition(":")
        if not name or len(token) < 16:
            raise ValueError("each KYC_API_TOKENS entry is name:token with a token of at least 16 characters")
        tokens[token] = name
    return tokens


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the KYC review queue API.")
    parser.add_argument("--host", default=os.environ.get("KYC_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KYC_PORT", "8000")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    store = Store(Path(os.environ.get("KYC_DB", "data/kyc.sqlite3")))
    store.migrate()
    recovered = store.recover_interrupted()
    if recovered:
        log.warning("moved %d interrupted cases to review", recovered)

    client: ModelClient
    today: Callable[[], date]
    if os.environ.get("KYC_MODE", "replay") == "live":
        client, today = AnthropicModelClient(), lambda: datetime.now(UTC).date()
    else:
        # Offline demo: answers come from the script for the generated specimens, and "today" is the date they
        # were generated for. Any other document gets no answer and lands in review.
        client = ReplayClient(generator.replies_by_hash(generator.generate()))
        today = lambda: generator.REFERENCE_DATE  # noqa: E731
    app = create_app(store, client, today, parse_tokens(os.environ.get("KYC_API_TOKENS", "")))
    uvicorn.run(app, host=args.host, port=args.port)
