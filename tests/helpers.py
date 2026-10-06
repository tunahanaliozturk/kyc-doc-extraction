import hashlib
from datetime import date

from kyc.countries import ALPHA2_TO_ALPHA3
from kyc.extraction import Document
from kyc.generator import PEOPLE, REFERENCE_DATE, Identity, SpecimenDocument, proof_of_address
from kyc.pipeline import CaseResult, process
from kyc.replay import ReplayClient
from kyc.schemas import Address, Application, DocumentKind
from kyc.verification import Check


def run(application: Application, docs: list[SpecimenDocument], today: date = REFERENCE_DATE) -> CaseResult:
    client = ReplayClient({hashlib.sha256(d.content).hexdigest(): d.replies for d in docs})
    return process(application, [Document(d.kind, d.media_type, d.content) for d in docs], client, today)


def status(result: CaseResult, name: str) -> str:
    return next(c.status for c in result.checks if c.name == name)


def find(result: CaseResult, name: str, document: int | None = None) -> Check:
    return next(c for c in result.checks if c.name == name and document in (None, c.document))


def person_case(
    index: int = 0,
    kind: DocumentKind = DocumentKind.IDENTITY_CARD,
    *,
    expiry: date = date(2030, 5, 31),
    birth: date = date(1988, 4, 17),
) -> tuple[Application, Identity, SpecimenDocument]:
    """A consistent applicant: the application and a proof of address that agree with the identity document."""
    p = PEOPLE[index]
    ident = Identity(p, kind, "X4K9P2L7Q", birth, expiry, "F", (p.given, p.surname))
    app = Application(
        full_name=f"{p.given} {p.surname}",
        date_of_birth=birth,
        nationality=ALPHA2_TO_ALPHA3[p.country],
        address=Address(line=p.street, postcode=p.postcode, city=p.city, country=p.country),
    )
    poa = proof_of_address(app.full_name, app.address, date(2026, 9, 10))
    return app, ident, poa
