from collections.abc import Callable
from datetime import date

import pytest

from kyc import mrz

# The specimens printed in ICAO Doc 9303 (Part 4, Appendix B and Part 5, Appendix B), for the fictional state Utopia.
ICAO_TD3 = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
]
ICAO_TD1 = [
    "I<UTOD231458907<<<<<<<<<<<<<<<",
    "7408122F1204159UTO<<<<<<<<<<<6",
    "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
]


@pytest.mark.parametrize(
    ("data", "digit"),
    [
        ("L898902C3", "6"),  # document number in the TD3 specimen
        ("740812", "2"),  # birth date
        ("120415", "9"),  # expiry date
        ("ZE184226B<<<<<", "1"),  # personal number
        ("D23145890", "7"),  # document number in the TD1 specimen
        ("<<<<<<<<<", "0"),
    ],
)
def test_check_digit_matches_the_icao_specimens(data: str, digit: str) -> None:
    assert mrz.check_digit(data) == digit


def test_the_icao_passport_specimen_parses_with_every_check_digit_valid() -> None:
    parsed = mrz.parse(ICAO_TD3)
    assert parsed.format == "TD3"
    assert parsed.failed_check_digits == []
    assert (parsed.surname, parsed.given_names) == ("ERIKSSON", "ANNA MARIA")
    assert (parsed.document_number, parsed.birth_date_raw, parsed.expiry_date_raw) == ("L898902C3", "740812", "120415")


def test_the_icao_id_card_specimen_parses_with_every_check_digit_valid() -> None:
    parsed = mrz.parse(ICAO_TD1)
    assert parsed.format == "TD1"
    assert parsed.failed_check_digits == []
    assert parsed.document_number == "D23145890"
    assert parsed.sex == "F"


@pytest.mark.parametrize("position", range(9))
def test_changing_any_character_of_the_document_number_is_caught(position: int) -> None:
    line = ICAO_TD3[1]
    replacement = "7" if line[position] != "7" else "8"
    forged = line[:position] + replacement + line[position + 1 :]
    assert "document_number" in mrz.parse([ICAO_TD3[0], forged]).failed_check_digits


def test_a_wrong_composite_digit_alone_is_caught() -> None:
    forged = ICAO_TD1[1][:29] + "5"
    assert mrz.parse([ICAO_TD1[0], forged, ICAO_TD1[2]]).failed_check_digits == ["composite"]


@pytest.mark.parametrize("compose", [mrz.compose_td1, mrz.compose_td3])
def test_composed_mrz_parses_back_to_the_same_values(compose: Callable[..., list[str]]) -> None:
    lines = compose(
        state="D",
        number="C01X00T47",
        birth=date(1990, 3, 12),
        sex="F",
        expiry=date(2031, 8, 31),
        nationality="D",
        surname="MUELLER PROBE",
        given="JUERGEN",
    )
    parsed = mrz.parse(lines)
    assert parsed.failed_check_digits == []
    assert (parsed.issuing_state, parsed.nationality) == ("DEU", "DEU")  # ICAO "D" is Germany
    assert (parsed.surname, parsed.given_names) == ("MUELLER PROBE", "JUERGEN")
    assert (parsed.birth_date_raw, parsed.expiry_date_raw) == ("900312", "310831")


def test_lines_of_the_wrong_length_are_a_format_error_not_a_forgery() -> None:
    with pytest.raises(mrz.MrzFormatError):
        mrz.parse([ICAO_TD3[0], ICAO_TD3[1][:-1]])


def test_characters_outside_the_mrz_alphabet_are_a_format_error() -> None:
    with pytest.raises(mrz.MrzFormatError):
        mrz.parse([ICAO_TD3[0].replace("ANNA", "ANNÄ"), ICAO_TD3[1]])


def test_birth_dates_take_the_last_century_that_is_not_in_the_future() -> None:
    today = date(2026, 10, 1)
    assert mrz.yymmdd_to_date("740812", today=today, future=False) == date(1974, 8, 12)
    assert mrz.yymmdd_to_date("100101", today=today, future=False) == date(2010, 1, 1)
    assert mrz.yymmdd_to_date("310831", today=today, future=True) == date(2031, 8, 31)
    assert mrz.yymmdd_to_date("991399", today=today, future=False) is None
