"""ICAO 9303 machine readable zone: check digits, parsing of TD1 (ID card) and TD3 (passport), and composing them.

Nothing here trusts the model. The pipeline hands over the MRZ lines the model read, this module recomputes every
check digit and returns the fields, and the verification step compares them with what the model read from the
visual zone. Composing lives here too, so the generator and the parser share one definition of the layout.
"""

import re
from dataclasses import dataclass
from datetime import date

from kyc.countries import icao_to_alpha3

TD1_LINE = 30
TD3_LINE = 44
_WEIGHTS = (7, 3, 1)
_ALLOWED = re.compile(r"^[A-Z0-9<]+$")


def char_value(char: str) -> int:
    if char.isdigit():
        return int(char)
    if "A" <= char <= "Z":
        return ord(char) - ord("A") + 10
    if char == "<":
        return 0
    raise ValueError(f"{char!r} is not an MRZ character")


def check_digit(data: str) -> str:
    """ICAO 9303 Part 3, 4.9: weights 7, 3, 1 repeating, sum modulo 10."""
    return str(sum(char_value(c) * _WEIGHTS[i % 3] for i, c in enumerate(data)) % 10)


@dataclass(frozen=True)
class CheckDigit:
    name: str
    data: str
    printed: str

    @property
    def expected(self) -> str:
        return check_digit(self.data)

    @property
    def valid(self) -> bool:
        # ICAO lets an all-filler optional field carry a filler as its check digit instead of 0.
        if self.printed == "<" and set(self.data) == {"<"}:
            return True
        return self.printed == self.expected


@dataclass(frozen=True)
class Mrz:
    format: str  # "TD1" or "TD3"
    document_code: str
    issuing_state: str  # ISO alpha-3 where the ICAO code maps to one
    document_number: str
    surname: str
    given_names: str
    nationality: str
    birth_date_raw: str  # YYMMDD as printed
    sex: str
    expiry_date_raw: str
    names_truncated: bool  # the name field is full, so the visual zone may hold more than the MRZ
    check_digits: tuple[CheckDigit, ...]

    @property
    def failed_check_digits(self) -> list[str]:
        return [c.name for c in self.check_digits if not c.valid]


class MrzFormatError(ValueError):
    """The lines are not a TD1 or TD3 MRZ at all. Different from a wrong check digit, which parses fine."""


def _names(field: str) -> tuple[str, str, bool]:
    surname, _, given = field.partition("<<")
    truncated = not field.endswith("<")
    return surname.replace("<", " ").strip(), given.replace("<", " ").strip(), truncated


def parse(lines: list[str]) -> Mrz:
    cleaned = [line.strip().upper().replace(" ", "") for line in lines if line.strip()]
    for line in cleaned:
        if not _ALLOWED.match(line):
            raise MrzFormatError(f"line {line!r} contains characters outside A-Z, 0-9 and <")
    if len(cleaned) == 3 and all(len(line) == TD1_LINE for line in cleaned):
        return _parse_td1(*cleaned)
    if len(cleaned) == 2 and all(len(line) == TD3_LINE for line in cleaned):
        return _parse_td3(*cleaned)
    shape = ", ".join(str(len(line)) for line in cleaned)
    raise MrzFormatError(f"expected 3 lines of 30 (TD1) or 2 lines of 44 (TD3), got {len(cleaned)} ({shape})")


def _parse_td1(l1: str, l2: str, l3: str) -> Mrz:
    surname, given, truncated = _names(l3)
    digits = (
        CheckDigit("document_number", l1[5:14], l1[14]),
        CheckDigit("birth_date", l2[0:6], l2[6]),
        CheckDigit("expiry_date", l2[8:14], l2[14]),
        CheckDigit("composite", l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29], l2[29]),
    )
    return Mrz(
        format="TD1",
        document_code=l1[0:2].replace("<", ""),
        issuing_state=icao_to_alpha3(l1[2:5]),
        document_number=l1[5:14].replace("<", ""),
        surname=surname,
        given_names=given,
        nationality=icao_to_alpha3(l2[15:18]),
        birth_date_raw=l2[0:6],
        sex=l2[7],
        expiry_date_raw=l2[8:14],
        names_truncated=truncated,
        check_digits=digits,
    )


def _parse_td3(l1: str, l2: str) -> Mrz:
    surname, given, truncated = _names(l1[5:44])
    digits = (
        CheckDigit("document_number", l2[0:9], l2[9]),
        CheckDigit("birth_date", l2[13:19], l2[19]),
        CheckDigit("expiry_date", l2[21:27], l2[27]),
        CheckDigit("personal_number", l2[28:42], l2[42]),
        CheckDigit("composite", l2[0:10] + l2[13:20] + l2[21:43], l2[43]),
    )
    return Mrz(
        format="TD3",
        document_code=l1[0:2].replace("<", ""),
        issuing_state=icao_to_alpha3(l1[2:5]),
        document_number=l2[0:9].replace("<", ""),
        surname=surname,
        given_names=given,
        nationality=icao_to_alpha3(l2[10:13]),
        birth_date_raw=l2[13:19],
        sex=l2[20],
        expiry_date_raw=l2[21:27],
        names_truncated=truncated,
        check_digits=digits,
    )


def yymmdd_to_date(raw: str, *, today: date, future: bool) -> date | None:
    """The MRZ drops the century. Expiry dates are taken as this century; birth dates as the latest century that
    does not put them in the future. Returns None for a field that is not a real date ("<<<<<<" or 991399)."""
    if not raw.isdigit():
        return None
    yy, mm, dd = int(raw[0:2]), int(raw[2:4]), int(raw[4:6])
    century = 2000 if future or 2000 + yy <= today.year else 1900
    try:
        return date(century + yy, mm, dd)
    except ValueError:
        return None


def _pad(value: str, length: int) -> str:
    return value[:length].ljust(length, "<")


def _field(value: str, length: int) -> str:
    return _pad(re.sub(r"[^A-Z0-9]+", "<", value.upper()), length)


def _name_field(surname: str, given_names: str, length: int) -> str:
    def part(name: str) -> str:
        return re.sub(r"[^A-Z]+", "<", name.upper()).strip("<")

    return _pad(part(surname) + "<<" + part(given_names), length)


def compose_td1(
    *, state: str, number: str, birth: date, sex: str, expiry: date, nationality: str, surname: str, given: str
) -> list[str]:
    """Compose a TD1 MRZ from MRZ-ready (already transliterated, upper case A-Z) values."""
    l1 = "I<" + _field(state, 3) + _field(number, 9)
    l1 += check_digit(_field(number, 9)) + "<" * 15
    l2 = birth.strftime("%y%m%d") + check_digit(birth.strftime("%y%m%d")) + sex
    l2 += expiry.strftime("%y%m%d") + check_digit(expiry.strftime("%y%m%d")) + _field(nationality, 3) + "<" * 11
    l2 += check_digit(l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29])
    l3 = _name_field(surname, given, TD1_LINE)
    return [l1, l2, l3]


def compose_td3(
    *, state: str, number: str, birth: date, sex: str, expiry: date, nationality: str, surname: str, given: str
) -> list[str]:
    l1 = "P<" + _field(state, 3) + _name_field(surname, given, 39)
    doc = _field(number, 9)
    personal = "<" * 14
    l2 = doc + check_digit(doc) + _field(nationality, 3)
    l2 += birth.strftime("%y%m%d") + check_digit(birth.strftime("%y%m%d")) + sex
    l2 += expiry.strftime("%y%m%d") + check_digit(expiry.strftime("%y%m%d"))
    l2 += personal + check_digit(personal)
    l2 += check_digit(l2[0:10] + l2[13:20] + l2[21:43])
    return [l1, l2]
