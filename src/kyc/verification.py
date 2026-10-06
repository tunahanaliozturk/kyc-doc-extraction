"""Deterministic checks. The model reads; this module decides what the reading is worth.

Each check returns pass, fail or unknown with a reason a reviewer can act on. Unknown is not a soft pass: it means
the evidence is missing or unreadable, and it routes to a human exactly like a failure does. A few checks also
reject outright, but only when the evidence cannot be a misreading (see `rejects` on each check and docs/adr/0003).
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel

from kyc import iban, mrz, names
from kyc.extraction import Document, ExtractionResult
from kyc.schemas import (
    IDENTITY_KINDS,
    Application,
    CompanyExtraction,
    DocumentKind,
    Extracted,
    IdentityExtraction,
    ProofOfAddressExtraction,
)

Status = Literal["pass", "fail", "unknown"]

ADULT_AGE = 18
PROOF_OF_ADDRESS_MAX_AGE_DAYS = 90
ADDRESS_LINE_MATCH = 0.90  # at or above: same street. Below ADDRESS_LINE_MISMATCH: a different street.
ADDRESS_LINE_MISMATCH = 0.70

# Phrases that only make sense as an attempt to steer a model. Finding one never approves or rejects anything; it
# sends the case to a human, who should know someone tried. The decision itself never depended on the model.
_INJECTION = re.compile(
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|rules)"
    r"|disregard (the |all )?(previous|prior|above) "
    r"|mark (this |the )?(case |document |applicant |customer )?as (verified|approved|genuine)"
    r"|you are now|system prompt|new instructions",
    re.IGNORECASE,
)


class Check(BaseModel):
    name: str
    status: Status
    reason: str
    document: int | None = None  # index into the case's documents
    rejects: bool = False  # a failure here rejects the case instead of sending it to review
    quotes: list[str] = []  # the source text a reviewer should look at


@dataclass(frozen=True)
class Evidence:
    """One submitted document with what the model read and the document's own text layer, if it has one."""

    index: int
    document: Document
    extraction: ExtractionResult
    text_layer: str | None


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text)).casefold()


def grounded(quote: str | None, text_layer: str | None) -> bool | None:
    """Is the quote really on the document? None when there is no text layer to look in (a photo or scan)."""
    if text_layer is None or not quote:
        return None
    return _squash(quote) in _squash(text_layer)


def _quotes(*fields: Extracted) -> list[str]:
    return [f.quote for f in fields if f.quote]


def _date(field: Extracted) -> date | None:
    return date.fromisoformat(field.value) if field.value else None


def age_on(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


# --- checks that apply to every document ------------------------------------------------------------------------


def check_documents_complete(application: Application, evidence: list[Evidence]) -> Check:
    kinds = {e.document.kind for e in evidence}
    missing = []
    if not kinds & IDENTITY_KINDS:
        missing.append("an identity card or passport")
    if DocumentKind.PROOF_OF_ADDRESS not in kinds:
        missing.append("a proof of address")
    if application.company and DocumentKind.COMPANY_EXTRACT not in kinds:
        missing.append("a company registry extract")
    if missing:
        return Check(name="documents_complete", status="fail", reason="missing " + " and ".join(missing))
    return Check(name="documents_complete", status="pass", reason="every required document type is present")


def check_extraction(e: Evidence) -> Check:
    r = e.extraction
    if r.ok:
        tries = "first attempt" if r.attempts == 1 else f"attempt {r.attempts}"
        return Check(name="extraction", status="pass", reason=f"valid on the {tries}", document=e.index)
    detail = "; ".join(r.errors[:5])
    return Check(name="extraction", status="unknown", reason=f"{r.failure}: {detail}", document=e.index)


def _all_fields(value: BaseModel) -> list[tuple[str, Extracted]]:
    found: list[tuple[str, Extracted]] = []
    for name, item in value:
        if isinstance(item, Extracted):
            found.append((name, item))
        elif isinstance(item, list):
            found.extend((f"{name}[{i}]", x) for i, x in enumerate(item) if isinstance(x, Extracted))
    return found


def check_grounding(e: Evidence) -> Check:
    if e.extraction.value is None:
        return Check(name="quotes_grounded", status="unknown", reason="nothing was extracted", document=e.index)
    if e.text_layer is None:
        return Check(
            name="quotes_grounded",
            status="unknown",
            reason="the document has no text layer, so a reviewer compares the quotes with the image",
            document=e.index,
        )
    missing = [
        (name, f.quote)
        for name, f in _all_fields(e.extraction.value)
        if f.quote and not grounded(f.quote, e.text_layer)
    ]
    if missing:
        return Check(
            name="quotes_grounded",
            status="fail",
            reason="quotes not found on the document: " + ", ".join(n for n, _ in missing),
            document=e.index,
            quotes=[q for _, q in missing if q],
        )
    return Check(
        name="quotes_grounded", status="pass", reason="every quote appears in the text layer", document=e.index
    )


def check_embedded_instructions(e: Evidence) -> Check:
    texts = [e.text_layer or ""]
    if e.extraction.value is not None:
        texts += [f"{f.value} {f.quote}" for _, f in _all_fields(e.extraction.value)]
    hits = sorted({m.group(0) for t in texts for m in _INJECTION.finditer(t)})
    if hits:
        return Check(
            name="no_embedded_instructions",
            status="fail",
            reason="the document contains text addressed to an automated reader; a human should look at it",
            document=e.index,
            quotes=hits,
        )
    if e.text_layer is None:
        return Check(
            name="no_embedded_instructions",
            status="pass",
            reason="no instruction-like text in the extracted fields (no text layer to scan)",
            document=e.index,
        )
    return Check(name="no_embedded_instructions", status="pass", reason="no instruction-like text", document=e.index)


# --- identity document ------------------------------------------------------------------------------------------


def check_mrz(e: Evidence, doc: IdentityExtraction, today: date) -> list[Check]:
    if not doc.mrz.value:
        unknown = "the MRZ was not readable"
        return [
            Check(name="mrz_check_digits", status="unknown", reason=unknown, document=e.index),
            Check(name="mrz_matches_visual_zone", status="unknown", reason=unknown, document=e.index),
        ]
    lines = doc.mrz.value.splitlines()
    try:
        parsed = mrz.parse(lines)
    except mrz.MrzFormatError as exc:
        reason = f"the MRZ does not parse: {exc}"
        return [
            Check(name="mrz_check_digits", status="unknown", reason=reason, document=e.index, quotes=[doc.mrz.value]),
            Check(name="mrz_matches_visual_zone", status="unknown", reason=reason, document=e.index),
        ]
    return [_mrz_digits(e, doc, parsed, lines), _mrz_vs_visual(e, doc, parsed, today)]


def _mrz_digits(e: Evidence, doc: IdentityExtraction, parsed: mrz.Mrz, lines: list[str]) -> Check:
    failed = parsed.failed_check_digits
    if not failed:
        return Check(name="mrz_check_digits", status="pass", reason="all check digits are correct", document=e.index)
    # A wrong digit in the PDF's own text layer is what the document says, not a misreading: that is a forged or
    # corrupted MRZ and the case is rejected. Read from a photo, the same failure may be the model's mistake.
    on_document = all(grounded(line, e.text_layer) for line in lines if line.strip())
    if on_document:
        reason = f"check digits wrong for {', '.join(failed)} in the document's own text: forged or corrupted MRZ"
    else:
        reason = f"check digits wrong for {', '.join(failed)}; could be a misread, compare with the image"
    return Check(
        name="mrz_check_digits",
        status="fail",
        reason=reason,
        document=e.index,
        rejects=on_document,
        quotes=_quotes(doc.mrz),
    )


def _mrz_vs_visual(e: Evidence, doc: IdentityExtraction, parsed: mrz.Mrz, today: date) -> Check:
    differences: list[str] = []
    unreadable: list[str] = []

    def same(label: str, visual: str | None, machine: str | None) -> None:
        if visual is None:
            unreadable.append(label)
        elif machine is None or visual != machine:
            differences.append(f"{label} (visual {visual}, MRZ {machine})")

    same("document_number", _alnum(doc.document_number.value), _alnum(parsed.document_number))
    same("nationality", doc.nationality.value, parsed.nationality)
    same("issuing_country", doc.issuing_country.value, parsed.issuing_state)
    same("sex", (doc.sex.value or "").upper()[:1] or None, parsed.sex.replace("<", "X"))
    birth = mrz.yymmdd_to_date(parsed.birth_date_raw, today=today, future=False)
    expiry = mrz.yymmdd_to_date(parsed.expiry_date_raw, today=today, future=True)
    same("date_of_birth", doc.date_of_birth.value, birth.isoformat() if birth else None)
    same("date_of_expiry", doc.date_of_expiry.value, expiry.isoformat() if expiry else None)

    if doc.surname.value is None or doc.given_names.value is None:
        unreadable.append("name")
    elif not _mrz_name_matches(doc.surname.value, doc.given_names.value, parsed):
        differences.append(
            f"name (visual {doc.surname.value}, {doc.given_names.value}; MRZ {parsed.surname}, {parsed.given_names})"
        )

    quotes = _quotes(doc.mrz, doc.document_number, doc.date_of_birth, doc.date_of_expiry)
    if differences:
        reason = "visual zone and MRZ disagree on " + "; ".join(differences)
        return Check(name="mrz_matches_visual_zone", status="fail", reason=reason, document=e.index, quotes=quotes)
    if unreadable:
        reason = "could not compare " + ", ".join(unreadable)
        return Check(name="mrz_matches_visual_zone", status="unknown", reason=reason, document=e.index)
    return Check(name="mrz_matches_visual_zone", status="pass", reason="every field agrees", document=e.index)


def _alnum(value: str | None) -> str | None:
    return re.sub(r"[^A-Z0-9]", "", value.upper()) if value else None


def _mrz_name_matches(surname: str, given: str, parsed: mrz.Mrz) -> bool:
    if names.compare(f"{given} {surname}", f"{parsed.given_names} {parsed.surname}").outcome == "match":
        return True
    if not parsed.names_truncated:
        return False
    # A full name field means the MRZ may have cut the name short, so the MRZ only has to be a prefix of it.
    machine = names.fold(f"{parsed.surname} {parsed.given_names}", expand=False)
    return any(names.fold(f"{surname} {given}", expand=e).startswith(machine) for e in (False, True))


def check_identity(e: Evidence, doc: IdentityExtraction, application: Application, today: date) -> list[Check]:
    checks = check_mrz(e, doc, today)
    expiry, birth = _date(doc.date_of_expiry), _date(doc.date_of_birth)

    if expiry is None:
        checks.append(
            Check(name="document_not_expired", status="unknown", reason="expiry date not readable", document=e.index)
        )
    elif expiry < today:
        # Reject only when the MRZ, read independently, gives the same expired date. One misread digit in the
        # visual zone should not reject a customer; it fails the MRZ comparison and goes to review instead.
        mrz_agrees = any(c.name == "mrz_matches_visual_zone" and c.status == "pass" for c in checks)
        checks.append(
            Check(
                name="document_not_expired",
                status="fail",
                reason=f"expired on {expiry.isoformat()}",
                document=e.index,
                rejects=mrz_agrees,
                quotes=_quotes(doc.date_of_expiry),
            )
        )
    else:
        checks.append(
            Check(
                name="document_not_expired", status="pass", reason=f"valid until {expiry.isoformat()}", document=e.index
            )
        )

    if birth is None:
        checks.append(
            Check(name="holder_is_adult", status="unknown", reason="birth date not readable", document=e.index)
        )
    else:
        age = age_on(birth, today)
        status: Status = "pass" if age >= ADULT_AGE else "fail"
        checks.append(
            Check(
                name="holder_is_adult",
                status=status,
                reason=f"{age} years old",
                document=e.index,
                quotes=_quotes(doc.date_of_birth),
            )
        )

    visual_name = f"{doc.given_names.value or ''} {doc.surname.value or ''}".strip()
    checks.append(
        _name_check(
            "identity_name_matches_application",
            visual_name or None,
            application.full_name,
            e.index,
            _quotes(doc.given_names, doc.surname),
        )
    )
    checks.append(
        _equal_check(
            "identity_birth_date_matches_application",
            doc.date_of_birth.value,
            application.date_of_birth.isoformat(),
            e.index,
            _quotes(doc.date_of_birth),
        )
    )
    checks.append(
        _equal_check(
            "identity_nationality_matches_application",
            doc.nationality.value,
            application.nationality,
            e.index,
            _quotes(doc.nationality),
        )
    )
    return checks


def _name_check(name: str, found: str | None, expected: str, index: int, quotes: list[str]) -> Check:
    if not found:
        return Check(name=name, status="unknown", reason="name not readable", document=index)
    result = names.compare(found, expected)
    if result.outcome == "match":
        return Check(name=name, status="pass", reason=f"{found!r} matches {expected!r}", document=index)
    if result.outcome == "near":
        reason = f"{found!r} is close to {expected!r} (similarity {result.similarity}) but not the same name"
        return Check(name=name, status="unknown", reason=reason, document=index, quotes=quotes)
    reason = f"{found!r} does not match {expected!r} (similarity {result.similarity})"
    return Check(name=name, status="fail", reason=reason, document=index, quotes=quotes)


def _equal_check(name: str, found: str | None, expected: str, index: int, quotes: list[str]) -> Check:
    if found is None:
        return Check(name=name, status="unknown", reason="value not readable", document=index)
    if found == expected:
        return Check(name=name, status="pass", reason=f"{found} as on the application", document=index)
    return Check(
        name=name,
        status="fail",
        reason=f"document says {found}, application says {expected}",
        document=index,
        quotes=quotes,
    )


# --- proof of address -------------------------------------------------------------------------------------------

_ABBREVIATIONS = {
    "st": "street", "rd": "road", "ave": "avenue", "str": "strasse", "sok": "sokak", "cad": "caddesi",
    "mah": "mahallesi", "bd": "boulevard", "blvd": "boulevard",
}  # fmt: skip


def normalise_address(text: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    folded = folded.replace("ı", "i")
    tokens = re.findall(r"[a-z]+|\d+", folded)
    # German writes the abbreviation glued on: "Musterstr." is "Musterstrasse".
    return " ".join(_ABBREVIATIONS.get(t, re.sub(r"(?<=.)str$", "strasse", t)) for t in tokens)


def _postcode(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def check_address(e: Evidence, doc: ProofOfAddressExtraction, application: Application, today: date) -> list[Check]:
    want = application.address
    quotes = _quotes(doc.address_line, doc.postcode, doc.city)
    if None in (doc.address_line.value, doc.postcode.value, doc.city.value, doc.country.value):
        address = Check(
            name="address_matches_application",
            status="unknown",
            reason="address not fully readable",
            document=e.index,
            quotes=quotes,
        )
    else:
        line = SequenceMatcher(
            None, normalise_address(doc.address_line.value or ""), normalise_address(want.line)
        ).ratio()
        same_postcode = _postcode(doc.postcode.value or "") == _postcode(want.postcode)
        same_city = normalise_address(doc.city.value or "") == normalise_address(want.city)
        same_country = doc.country.value == want.country
        found = f"{doc.address_line.value}, {doc.postcode.value} {doc.city.value}, {doc.country.value}"
        expected = f"{want.line}, {want.postcode} {want.city}, {want.country}"
        if same_postcode and same_city and same_country and line >= ADDRESS_LINE_MATCH:
            address = Check(
                name="address_matches_application",
                status="pass",
                reason=f"{found} as on the application",
                document=e.index,
            )
        elif not (same_postcode and same_country) or line < ADDRESS_LINE_MISMATCH:
            address = Check(
                name="address_matches_application",
                status="fail",
                reason=f"document says {found}, application says {expected}",
                document=e.index,
                quotes=quotes,
            )
        else:
            address = Check(
                name="address_matches_application",
                status="unknown",
                reason=f"close but not the same: {found} against {expected} (street {line:.2f})",
                document=e.index,
                quotes=quotes,
            )

    issued = _date(doc.issue_date)
    if issued is None:
        recent = Check(
            name="address_document_recent", status="unknown", reason="issue date not readable", document=e.index
        )
    else:
        days = (today - issued).days
        ok = 0 <= days <= PROOF_OF_ADDRESS_MAX_AGE_DAYS
        reason = f"issued {days} days ago" if days >= 0 else f"dated {-days} days in the future"
        if not ok:
            reason += f"; accepted window is 0 to {PROOF_OF_ADDRESS_MAX_AGE_DAYS} days"
        recent = Check(
            name="address_document_recent",
            status="pass" if ok else "fail",
            reason=reason,
            document=e.index,
            quotes=[] if ok else _quotes(doc.issue_date),
        )

    holder = _name_check(
        "address_holder_matches_application",
        doc.holder_name.value,
        application.full_name,
        e.index,
        _quotes(doc.holder_name),
    )
    checks = [address, recent, holder]
    if application.iban:
        checks.append(check_iban(e, doc, application.iban))
    return checks


def check_iban(e: Evidence, doc: ProofOfAddressExtraction, applied: str) -> Check:
    problem = iban.problem(applied)
    if problem:
        return Check(name="iban_valid", status="fail", reason=f"application IBAN: {problem}", document=e.index)
    if doc.iban.value is None:
        return Check(name="iban_valid", status="pass", reason="application IBAN passes mod-97", document=e.index)
    if iban.compact(doc.iban.value) != iban.compact(applied):
        return Check(
            name="iban_valid",
            status="fail",
            reason="the statement's IBAN differs from the application",
            document=e.index,
            quotes=_quotes(doc.iban),
        )
    return Check(name="iban_valid", status="pass", reason="valid and matches the bank statement", document=e.index)


# --- company registry extract -----------------------------------------------------------------------------------


def _company_key(value: str) -> str:
    return normalise_address(value)


def check_company(e: Evidence, doc: CompanyExtraction, application: Application) -> list[Check]:
    company = application.company
    if company is None:
        return []
    found = (doc.company_name.value, doc.registration_number.value, doc.country.value)
    if None in found:
        same = Check(
            name="company_matches_application",
            status="unknown",
            reason="registry fields not readable",
            document=e.index,
        )
    elif (
        _company_key(doc.company_name.value or "") == _company_key(company.name)
        and _alnum(doc.registration_number.value) == _alnum(company.registration_number)
        and doc.country.value == company.country
    ):
        same = Check(
            name="company_matches_application", status="pass", reason="name, number and country match", document=e.index
        )
    else:
        same = Check(
            name="company_matches_application",
            status="fail",
            reason=f"registry says {found[0]} ({found[1]}, {found[2]}), application says {company.name} "
            f"({company.registration_number}, {company.country})",
            document=e.index,
            quotes=_quotes(doc.company_name, doc.registration_number),
        )

    status_value = (doc.status.value or "").strip().lower()
    if not status_value:
        active = Check(name="company_active", status="unknown", reason="status not readable", document=e.index)
    elif status_value == "active":
        active = Check(name="company_active", status="pass", reason="registered as active", document=e.index)
    else:
        active = Check(
            name="company_active",
            status="fail",
            reason=f"registry status is {status_value!r}",
            document=e.index,
            quotes=_quotes(doc.status),
        )

    outcomes = [names.compare(d.value, application.full_name) for d in doc.directors if d.value]
    if any(o.outcome == "match" for o in outcomes):
        director = Check(name="applicant_is_director", status="pass", reason="listed as director", document=e.index)
    elif any(o.outcome == "near" for o in outcomes):
        director = Check(
            name="applicant_is_director",
            status="unknown",
            reason="a director's name is close but not the same",
            document=e.index,
            quotes=_quotes(*doc.directors),
        )
    else:
        director = Check(
            name="applicant_is_director",
            status="fail",
            reason="the applicant is not a listed director",
            document=e.index,
            quotes=_quotes(*doc.directors),
        )
    return [same, active, director]


# --- all together -----------------------------------------------------------------------------------------------


def verify(application: Application, evidence: list[Evidence], today: date) -> list[Check]:
    checks = [check_documents_complete(application, evidence)]
    for e in evidence:
        checks += [check_extraction(e), check_grounding(e), check_embedded_instructions(e)]
        value = e.extraction.value
        if isinstance(value, IdentityExtraction):
            checks += check_identity(e, value, application, today)
        elif isinstance(value, ProofOfAddressExtraction):
            checks += check_address(e, value, application, today)
        elif isinstance(value, CompanyExtraction):
            checks += check_company(e, value, application)
    return checks
