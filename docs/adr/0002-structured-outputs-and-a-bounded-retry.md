# 0002. Structured outputs plus a bounded validate-and-retry loop

Status: Accepted, 2026-10-06

## Context

The model has to return a fixed shape per document type. The default model is `claude-sonnet-5-5` (set
`KYC_MODEL` to change it). That model, like `claude-opus-5-5`, answers a forced `tool_choice` or a non-default
`temperature` with HTTP 400, so the old trick of forcing a tool call to get JSON is gone.

The shape is not the whole contract. A date has to be a real date in `YYYY-MM-DD`, a country has to be an ISO 3166
code, and a value has to come with the quote it was read from. JSON Schema in structured outputs covers the shape;
the rest needs code.

## Decision

We call `messages.create` with `output_config.format` set to a JSON schema produced by `anthropic.transform_schema`
from the Pydantic model, then validate the reply with the same Pydantic model. On failure we append the model's
reply and a user message listing every validation error verbatim, and ask again, up to three attempts in total
(`src/kyc/extraction.py`). After the third failure the result is a failure with the last errors. An object that
failed validation is never returned as a value. A refusal stops the loop on the first attempt.

## Consequences

- The model sees exactly what was wrong ("issue_date: date '01.09.2026' must be written as YYYY-MM-DD") and fixes
  that, rather than guessing (`test_the_exact_validation_errors_go_back_to_the_model`).
- A retry appends to the conversation instead of rewriting it. The SDK's own content blocks go back as the
  assistant turn, which keeps the history valid for models that bind thinking blocks to their conversation.
- Worst case is three model calls per document. With three documents per case that is nine calls before a case
  lands in review, a cost ceiling that is known in advance.
- We do not use the SDK's `messages.parse` helper. It raises on a validation failure, and we want the error list
  to feed back, so we validate ourselves.

## Alternatives

- **Forced tool call with the schema as input.** Lost: rejected with 400 by the 5.5 models.
- **Unbounded retries until valid.** Lost: a document that cannot be read would loop and cost money forever. A
  human is the right fallback, and three attempts gave the scripted retry cases room to recover.
- **Accept a partially valid object and drop the bad fields.** Lost: a silently dropped field turns into an
  "unknown" far from its cause. Failing the document keeps the reason next to it.
