# 0006. Python 3.14 and a dependency list that is permissive at every depth

Status: Accepted, 2026-10-06

## Context

The repository may be read by a bank's engineers, and a bank's legal team reads licences. The rule is: no GPL,
LGPL or AGPL anywhere in the installed tree, and no commercial terms. Several obvious libraries for this job break
it.

## Decision

- **Python 3.14** (3.14.6 locally). Every dependency publishes 3.14 wheels, so there was no reason to stay older.
- **PDF writing: reportlab 5.0.1 (BSD).** fpdf2 is LGPL-3.0, so it lost.
- **PDF reading: pypdf 6.19.0 (BSD-3-Clause)** for the text layer. PyMuPDF is AGPL, so it lost despite better
  extraction.
- **Images: Pillow 12.3.0 (MIT-CMU)** for the photo specimens, blur and rotation.
- **Country codes: a literal ISO 3166-1 table in `src/kyc/countries.py`.** pycountry is LGPL-2.1. The table was
  checked once against pycountry's data, all 249 entries equal, and its size is asserted in a test.
- **Model: anthropic 1.11.0 (MIT)**, the official SDK. **API: FastAPI 0.142.2 (MIT), uvicorn 0.54.0 (BSD).**
- **Fonts: Bitstream Vera**, which ships inside the reportlab wheel under a permissive licence and has the Turkish
  and Danish letters the specimens need.

`tools/license_audit.py` reads the metadata of every installed distribution and fails CI on anything outside the
allow list. At the time of writing it passes 40 distributions.

## Consequences

- The audit checks distribution metadata, not every file inside a wheel. It cannot see that the reportlab wheel
  also contains the DarkGarden font under GPL with a font exception. This project never loads that file. If that
  is not acceptable to a reader's legal team, the fix is to delete the file from the installed package or vendor a
  reportlab build without it; the code does not change.
- pypdf's text extraction is weaker than PyMuPDF's on complex layouts. The grounding check compares quotes with all
  whitespace removed, which absorbs pypdf's spacing quirks on these specimens.

## Alternatives

- **Python 3.13.** Would have been chosen with an ADR if a dependency lacked 3.14 wheels. None did.
- **pycountry for country codes.** Lost on LGPL. Would win if a permissive package with the same data existed.
