# 0007. Every quote must be found on the document, so photos go to review

Status: Accepted, 2026-10-06

## Context

Checks compare extracted values with the application. If the model reports a value that is not on the document,
every downstream check is comparing against fiction. That happens through misreading, and it happens on purpose
when a document carries an injected instruction and the model follows it. The prompt-injection specimen shows the
dangerous case: the bill shows another address, the model reports the application's address, and the address
check passes.

## Decision

Every extracted value carries a verbatim quote, and the `quotes_grounded` check looks each quote up in the PDF's
own text layer (pypdf), ignoring case and whitespace. A quote that is not there fails the check. A document with no
text layer, such as a phone photo, gets `unknown`: the reviewer compares the highlighted quotes with the image.

The quote also has to say what the value says, or the model could cite real text for an invented value. Text fields
must appear, token for token, inside their own quote; dates must appear in it as day, month, year or year, month,
day, never with day and month swapped. Country codes and sex are exempt, because the model writes them as codes and
each is also compared with the MRZ or the application.

## Consequences

- The injected bill goes to review even when the model obeys the injection, because the invented address is not
  on the page (`test_a_reader_that_obeys_and_invents_the_wanted_address_still_cannot_approve`).
  Citing the real address as the quote for the wanted one fails too
  (`test_a_reader_that_cites_the_real_text_for_an_invented_value_still_cannot_approve`).
- A misread birth date fails twice: against the MRZ and against the text layer.
- Photos and scanned PDFs are never approved without a human. On the synthetic set that is 5 of the 25 review
  cases. This is the largest cost of the design.
- A PDF whose text layer says something different from what it renders would pass grounding. Detecting that needs
  rendering the page and comparing, which is out of scope.

## Alternatives

- **Trust MRZ consistency for photos of identity documents.** A model is unlikely to invent an MRZ whose check
  digits are valid and which agrees with the visual zone. This would let sharp ID photos through and is the first
  thing to add if the review rate on photos hurts. It does not help a photographed bill, which has no MRZ.
- **OCR the photo locally and ground against that.** Would work, at the cost of an OCR dependency and its own error
  rate. Tesseract is Apache-2.0, so licensing is not the blocker.
- **Skip grounding.** Lost: the injection test fails the moment the model obeys.
