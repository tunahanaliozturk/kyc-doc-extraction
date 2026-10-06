# Changelog

## Unreleased

First version.

- Extraction of identity cards, passports, proofs of address and company registry extracts with Claude, using
  structured outputs and a validate-and-retry loop of at most three attempts.
- Deterministic verification: ICAO 9303 MRZ check digits for TD1 and TD3, MRZ against visual zone, expiry and age,
  name matching with Turkish, German and Danish transliteration, address and IBAN against the application, ISO 3166
  codes, quote grounding in the PDF text layer, a check that each value is what its quote says, and detection of
  text aimed at automated readers.
- Routing to approved, in review or rejected, with rejection only on evidence that cannot be a misreading.
- Review queue API with keyset paging, ETag-based optimistic concurrency and an append-only audit log enforced by
  SQLite triggers, and a request body cap enforced before the body is parsed.
- Seeded generator for 56 synthetic cases (117 documents), an offline eval gate with zero false approvals, and a
  live mode for a real model.
