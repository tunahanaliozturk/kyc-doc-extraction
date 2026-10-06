"""What crosses a boundary: the application form a customer fills in, and what the model may return per document.

Every extracted field carries the value and the verbatim text the model read it from. The quote is what a reviewer
sees highlighted, and what the grounding check looks for in the document's own text layer. A value without a quote
fails validation, so the model cannot hand back a number it did not read.
"""

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from kyc.countries import is_alpha2, is_alpha3


class DocumentKind(StrEnum):
    IDENTITY_CARD = "identity_card"
    PASSPORT = "passport"
    PROOF_OF_ADDRESS = "proof_of_address"
    COMPANY_EXTRACT = "company_extract"


IDENTITY_KINDS = frozenset({DocumentKind.IDENTITY_CARD, DocumentKind.PASSPORT})


def _alpha2(code: str) -> str:
    if not is_alpha2(code):
        raise ValueError(f"{code!r} is not an ISO 3166-1 alpha-2 country code")
    return code


def _alpha3(code: str) -> str:
    if not is_alpha3(code):
        raise ValueError(f"{code!r} is not an ISO 3166-1 alpha-3 country code")
    return code


Alpha2 = Annotated[str, AfterValidator(_alpha2)]
Alpha3 = Annotated[str, AfterValidator(_alpha3)]


# --- The application form ---------------------------------------------------------------------------------------


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: str = Field(min_length=1, max_length=200, description="Street and house number")
    postcode: str = Field(min_length=1, max_length=20)
    city: str = Field(min_length=1, max_length=100)
    country: Alpha2


class Company(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    registration_number: str = Field(min_length=1, max_length=50)
    country: Alpha2


class Application(BaseModel):
    """What the applicant typed. The documents have to back up every one of these values."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=200)
    date_of_birth: date
    nationality: Alpha3
    address: Address
    iban: str | None = Field(default=None, max_length=42)
    company: Company | None = None


# --- What the model returns ------------------------------------------------------------------------------------


class Extracted(BaseModel):
    """One field as read from the document. Both null means the field is absent or unreadable."""

    model_config = ConfigDict(extra="forbid")

    value: str | None = Field(description="The value, normalised as the field description says, or null")
    quote: str | None = Field(description="The exact text on the document the value was read from, or null")

    @model_validator(mode="after")
    def quote_backs_value(self) -> Self:
        if self.value is not None and not (self.quote and self.quote.strip()):
            raise ValueError("a value needs the quote it was read from")
        return self


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date_value(field: Extracted) -> Extracted:
    if field.value is not None:
        if not _ISO_DATE.match(field.value):
            raise ValueError(f"date {field.value!r} must be written as YYYY-MM-DD")
        date.fromisoformat(field.value)  # raises for 2026-02-30
    return field


def _alpha3_value(field: Extracted) -> Extracted:
    if field.value is not None:
        _alpha3(field.value)
    return field


def _alpha2_value(field: Extracted) -> Extracted:
    if field.value is not None:
        _alpha2(field.value)
    return field


DateField = Annotated[Extracted, AfterValidator(_date_value)]
Alpha3Field = Annotated[Extracted, AfterValidator(_alpha3_value)]
Alpha2Field = Annotated[Extracted, AfterValidator(_alpha2_value)]


class IdentityExtraction(BaseModel):
    """An identity card or passport."""

    model_config = ConfigDict(extra="forbid")

    document_number: Extracted
    surname: Extracted = Field(description="As printed in the visual zone, with accents")
    given_names: Extracted = Field(description="As printed in the visual zone, with accents")
    nationality: Alpha3Field = Field(description="ISO 3166-1 alpha-3, e.g. NLD")
    date_of_birth: DateField = Field(description="YYYY-MM-DD")
    sex: Extracted = Field(description="F, M or X")
    date_of_expiry: DateField = Field(description="YYYY-MM-DD")
    issuing_country: Alpha3Field = Field(description="ISO 3166-1 alpha-3")
    mrz: Extracted = Field(description="All MRZ lines exactly as printed, joined with a newline")


class ProofOfAddressExtraction(BaseModel):
    """A utility bill or bank statement."""

    model_config = ConfigDict(extra="forbid")

    issuer: Extracted = Field(description="The company that issued the document")
    holder_name: Extracted = Field(description="The person the document is addressed to")
    address_line: Extracted = Field(description="Street and house number")
    postcode: Extracted
    city: Extracted
    country: Alpha2Field = Field(description="ISO 3166-1 alpha-2, e.g. NL")
    issue_date: DateField = Field(description="YYYY-MM-DD")
    iban: Extracted = Field(description="The account IBAN on a bank statement, null on a utility bill")


class CompanyExtraction(BaseModel):
    """An extract from a company registry."""

    model_config = ConfigDict(extra="forbid")

    company_name: Extracted
    registration_number: Extracted
    country: Alpha2Field = Field(description="ISO 3166-1 alpha-2 of the registry")
    status: Extracted = Field(description="Registry status in lower case, e.g. active, dissolved, in liquidation")
    directors: list[Extracted] = Field(description="Every person listed as director or board member")


type Extraction = IdentityExtraction | ProofOfAddressExtraction | CompanyExtraction

SCHEMA_FOR: dict[DocumentKind, type[IdentityExtraction | ProofOfAddressExtraction | CompanyExtraction]] = {
    DocumentKind.IDENTITY_CARD: IdentityExtraction,
    DocumentKind.PASSPORT: IdentityExtraction,
    DocumentKind.PROOF_OF_ADDRESS: ProofOfAddressExtraction,
    DocumentKind.COMPANY_EXTRACT: CompanyExtraction,
}

MediaType = Literal["application/pdf", "image/png", "image/jpeg"]
