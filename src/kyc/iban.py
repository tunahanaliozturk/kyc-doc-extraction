"""IBAN validation with the ISO 13616 mod-97 check, plus the per-country length for the countries we see most."""

import re

# Lengths from the SWIFT IBAN registry. A country missing here still gets the mod-97 check and the 15-34 bound.
LENGTHS = {
    "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18,
    "FR": 27, "GB": 22, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27, "LT": 20, "LU": 20, "LV": 21,
    "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "RO": 24, "SE": 24, "SI": 19, "SK": 24, "TR": 26,
}  # fmt: skip

_SHAPE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")


def compact(iban: str) -> str:
    return re.sub(r"\s+", "", iban).upper()


def _as_number(text: str) -> int:
    return int("".join(str(int(ch, 36)) for ch in text))


def problem(iban: str) -> str | None:
    """None when the IBAN is valid, otherwise the reason it is not."""
    value = compact(iban)
    if not _SHAPE.match(value):
        return "not shaped like an IBAN: two letters, two digits, then 11 to 30 letters or digits"
    expected = LENGTHS.get(value[:2])
    if expected is not None and len(value) != expected:
        return f"{value[:2]} IBANs have {expected} characters, this one has {len(value)}"
    if _as_number(value[4:] + value[:4]) % 97 != 1:
        return "mod-97 check failed, so at least one character is wrong"
    return None


def with_check_digits(country: str, bban: str) -> str:
    """Build a valid IBAN from a country code and a basic bank account number."""
    digits = 98 - _as_number(bban + country + "00") % 97
    return f"{country}{digits:02d}{bban}"
