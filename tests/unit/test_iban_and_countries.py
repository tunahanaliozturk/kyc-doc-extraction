import pytest

from kyc import iban
from kyc.countries import ALPHA2_TO_ALPHA3, icao_to_alpha3, is_alpha2, is_alpha3


@pytest.mark.parametrize(
    "value",
    [
        "NL91ABNA0417164300",  # the examples in the SWIFT IBAN registry
        "DE89370400440532013000",
        "GB82WEST12345698765432",
        "TR330006100519786457841326",
        "BE68539007547034",
        "nl91 abna 0417 1643 00",  # as people type it
    ],
)
def test_valid_ibans_pass(value: str) -> None:
    assert iban.problem(value) is None


def test_one_changed_digit_fails_mod_97() -> None:
    assert iban.problem("NL91ABNA0417164301") == "mod-97 check failed, so at least one character is wrong"


def test_swapped_neighbouring_digits_fail_mod_97() -> None:
    assert iban.problem("NL91ABNA0471164300") is not None


def test_a_dutch_iban_of_the_wrong_length_fails_before_the_checksum() -> None:
    assert iban.problem("NL91ABNA04171643001") == "NL IBANs have 18 characters, this one has 19"


@pytest.mark.parametrize("value", ["", "NL", "1234ABNA0417164300", "NL9XABNA0417164300"])
def test_malformed_input_is_rejected(value: str) -> None:
    assert iban.problem(value) is not None


def test_with_check_digits_builds_a_valid_iban() -> None:
    assert iban.with_check_digits("NL", "ABNA0417164300") == "NL91ABNA0417164300"


def test_the_country_table_has_all_249_iso_3166_entries() -> None:
    assert len(ALPHA2_TO_ALPHA3) == 249
    assert len(set(ALPHA2_TO_ALPHA3.values())) == 249


def test_country_codes() -> None:
    assert is_alpha2("NL")
    assert is_alpha3("TUR")
    assert not is_alpha3("UTO")  # the ICAO specimen state is not a country
    assert not is_alpha2("UK")  # the United Kingdom is GB
    assert icao_to_alpha3("D<<") == "DEU"
    assert icao_to_alpha3("NLD") == "NLD"
