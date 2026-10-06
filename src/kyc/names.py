"""Name normalisation and matching across documents.

Two people are the same name when some spelling of each, after folding, has the same set of tokens. "Some
spelling" matters because one passport prints "Müller" in the visual zone and "MUELLER" in the MRZ, and the
application form says "Muller". All three are the same person; a one-letter difference ("Jan" and "Jen") is not.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

# Only an exact match after folding passes. The similarity never approves anything: it decides whether the reviewer
# reads "close, maybe a typo" (unknown) or "a different name" (fail). Measured in tests/unit/test_names.py: a dropped
# middle name scores 0.81, a one-letter change 0.92 to 0.97, another first name with the same surname 0.79. Kees and
# Klaas Testpersoon score 0.85, which is why a score alone may never pass a name. See docs/adr/0004.
NEAR_MATCH_THRESHOLD = 0.80

# Letters NFKD does not decompose, with their ICAO 9303 transliteration (Part 3, section 6).
_SPECIAL = {"Ø": "OE", "Æ": "AE", "Œ": "OE", "Đ": "D", "Ł": "L", "Þ": "TH", "ß": "SS", "Ŀ": "L", "Ħ": "H"}
# Letters with two accepted spellings: the ICAO one and the one a person types without the accent.
_DOUBLE = {"Ä": "AE", "Ö": "OE", "Ü": "UE", "Å": "AA"}


def fold(name: str, *, expand: bool) -> str:
    # Upper-casing first matters for Turkish: "ı".upper() is "I", and "İ" decomposes to I plus a combining dot.
    text = unicodedata.normalize("NFC", name.upper())
    text = "".join(_SPECIAL.get(ch, ch) for ch in text)
    if expand:
        text = "".join(_DOUBLE.get(ch, ch) for ch in text)
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z]+", " ", text).strip()


def spellings(name: str) -> set[str]:
    """Every folded spelling of a name: plain ("MULLER") and ICAO expanded ("MUELLER"), with tokens sorted so
    that word order does not count."""
    return {" ".join(sorted(fold(name, expand=e).split())) for e in (False, True)}


@dataclass(frozen=True)
class NameMatch:
    outcome: str  # "match", "near", "mismatch"
    similarity: float


def compare(a: str, b: str) -> NameMatch:
    left, right = spellings(a), spellings(b)
    if not all(left) or not all(right):
        return NameMatch("mismatch", 0.0)
    if left & right:
        return NameMatch("match", 1.0)
    best = max(SequenceMatcher(None, x, y).ratio() for x in left for y in right)
    return NameMatch("near" if best >= NEAR_MATCH_THRESHOLD else "mismatch", round(best, 3))
