# 0004. Name matching: fold, transliterate, then require the same tokens

Status: Accepted, 2026-10-06

## Context

One person's name arrives in three spellings. The visual zone of a passport prints "Jürgen Müller-Probe". The MRZ
writes "MUELLER<PROBE<<JUERGEN", because ICAO 9303 transliterates ü as UE. The application form, typed on an
English keyboard, says "Jurgen Muller Probe". Turkish adds letters that Unicode decomposition does not handle the
way people expect: the dotless ı has no decomposition, and the capital İ decomposes to I plus a combining dot.

## Decision

`src/kyc/names.py` upper-cases first (so ı becomes I), maps the letters NFKD leaves alone (ø, æ, ł, ß and a few
more) to their ICAO form, strips combining marks, and produces two spellings per name: plain (ü to U) and ICAO
expanded (ü to UE). Tokens are sorted, so word order does not count. Two names match when any spelling of one
equals any spelling of the other. Nothing else passes.

A `difflib` similarity on the folded strings decides only the label of a non-match: at or above 0.80 the check is
"unknown, close but not the same", below it "fail, a different name". Both go to review.

## Consequences

- Turkish, German, Danish and French names match across the visual zone, the MRZ and a form typed without accents
  (`test_the_same_name_written_differently_matches`).
- A dropped middle name (0.81), a one-letter typo (0.92 to 0.97) and a different person with a similar first name
  (Kees and Klaas Testpersoon, 0.85) all land in review. The score cannot tell a typo from a different person,
  which is why it is not allowed to pass anything.
- Names in non-Latin scripts are out of scope. Cyrillic or Greek would need a transliteration table per script.

## Alternatives

- **Pass above a similarity threshold, say 0.92.** Lost: "Jan" and "Jen" Voorbeeld score 0.92 and are not the
  same name. Every threshold that admits real typos also admits some different people.
- **Phonetic matching (Soundex, Metaphone).** Lost: built for English, poor on Turkish and Dutch, and it produces
  matches nobody can explain to an auditor.
- **A name-matching service.** Would win in production with a sanctions-screening vendor already in place.
