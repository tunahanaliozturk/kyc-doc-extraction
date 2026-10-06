"""One case end to end: extract every document, verify, decide. No I/O beyond the model call."""

import hashlib
import io
import logging
from datetime import date

from pydantic import BaseModel
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from kyc.extraction import Document, ModelClient, extract
from kyc.routing import Decision, decide
from kyc.schemas import Application
from kyc.verification import Check, Evidence, verify

log = logging.getLogger(__name__)


class DocumentResult(BaseModel):
    kind: str
    media_type: str
    sha256: str
    has_text_layer: bool
    attempts: int
    failure: str | None
    errors: list[str]
    fields: dict[str, object] | None  # the extraction, values with their quotes


class CaseResult(BaseModel):
    decision: Decision
    checks: list[Check]
    documents: list[DocumentResult]


def text_layer(doc: Document) -> str | None:
    """The text a PDF carries itself. None for images, scanned PDFs with no text, and files pypdf cannot read."""
    if doc.media_type != "application/pdf":
        return None
    try:
        text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(doc.content)).pages)
    except (PyPdfError, ValueError, OSError) as exc:
        log.warning("no text layer: %s", type(exc).__name__)
        return None
    return text if text.strip() else None


def process(application: Application, documents: list[Document], client: ModelClient, today: date) -> CaseResult:
    evidence = [
        Evidence(index=i, document=doc, extraction=extract(client, doc), text_layer=text_layer(doc))
        for i, doc in enumerate(documents)
    ]
    checks = verify(application, evidence, today)
    return CaseResult(
        decision=decide(checks),
        checks=checks,
        documents=[
            DocumentResult(
                kind=e.document.kind.value,
                media_type=e.document.media_type,
                sha256=hashlib.sha256(e.document.content).hexdigest(),
                has_text_layer=e.text_layer is not None,
                attempts=e.extraction.attempts,
                failure=e.extraction.failure,
                errors=e.extraction.errors,
                fields=e.extraction.value.model_dump() if e.extraction.value else None,
            )
            for e in evidence
        ],
    )
