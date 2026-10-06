import pytest

from kyc.names import NEAR_MATCH_THRESHOLD, compare, spellings


@pytest.mark.parametrize(
    ("visual", "other"),
    [
        ("Ayşe Örnekoğlu", "AYSE ORNEKOGLU"),  # Turkish ş, ğ, Ö folded the way an MRZ writes them
        ("İsmail Işık", "ISMAIL ISIK"),  # dotted capital İ and dotless ı
        ("ismail ışık", "İSMAİL IŞIK"),  # and the same in the other case
        ("Jürgen Müller-Probe", "JUERGEN MUELLER PROBE"),  # ICAO expands ü to UE
        ("Jürgen Müller-Probe", "Jurgen Muller Probe"),  # a person typing without accents drops it
        ("Søren Prøvesen", "SOEREN PROEVESEN"),  # ø is not decomposable; ICAO writes OE
        ("Anna Maria Specimen", "Specimen Anna Maria"),  # order does not matter
        ("Marie  Exemple", "marie exemple"),  # case and spacing do not matter
        ("Erika Mustermann", "Erika Mustermann"),
    ],
)
def test_the_same_name_written_differently_matches(visual: str, other: str) -> None:
    assert compare(visual, other).outcome == "match"


@pytest.mark.parametrize(
    ("a", "b", "similarity"),
    [
        ("Anna Maria Specimen", "Anna Specimen", 0.812),  # a dropped middle name
        ("Jan Voorbeeld", "Jen Voorbeeld", 0.923),  # one letter changed
        ("Erika Mustermann", "Erika Musterman", 0.968),  # one letter missing
        ("Kees Testpersoon", "Klaas Testpersoon", 0.848),  # a different person who looks like a typo
    ],
)
def test_close_names_are_near_and_never_a_match(a: str, b: str, similarity: float) -> None:
    result = compare(a, b)
    assert result.outcome == "near"
    assert result.similarity == similarity
    assert result.similarity >= NEAR_MATCH_THRESHOLD


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Jan Voorbeeld", "Pieter Anders"),
        ("Lukas Beispiel", "Erika Beispiel"),  # same surname, another first name: 0.786
        ("Ayşe Örnekoğlu", "Ayse Deneme"),
        ("Anna Specimen", ""),
        ("", ""),
    ],
)
def test_different_names_are_a_mismatch(a: str, b: str) -> None:
    assert compare(a, b).outcome == "mismatch"


def test_spellings_include_the_plain_and_the_icao_form() -> None:
    assert spellings("Jürgen Müller") == {"JURGEN MULLER", "JUERGEN MUELLER"}
