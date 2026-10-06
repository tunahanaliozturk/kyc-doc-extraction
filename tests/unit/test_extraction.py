"""The validate-and-retry loop, against a scripted client that records what it was sent."""

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
from anthropic.types import MessageParam

from kyc.extraction import MAX_ATTEMPTS, NO_JSON, AnthropicModelClient, Document, ModelTurn, extract
from kyc.schemas import DocumentKind, ProofOfAddressExtraction

PDF = Document(DocumentKind.PROOF_OF_ADDRESS, "application/pdf", b"%PDF-1.4 specimen")


def field(value: str | None, quote: str | None = None) -> dict[str, str | None]:
    return {"value": value, "quote": quote if quote is not None else value}


GOOD = {
    "issuer": field("Specimen Energy Ltd."),
    "holder_name": field("Jan Voorbeeld"),
    "address_line": field("Proefweg 3"),
    "postcode": field("3511 AA"),
    "city": field("Utrecht"),
    "country": field("NL", "Netherlands"),
    "issue_date": field("2026-09-01", "01.09.2026"),
    "iban": field(None),
}


def reply(**changes: Any) -> str:
    return json.dumps(GOOD | changes)


class Scripted:
    def __init__(self, *replies: str | None, refuse: bool = False) -> None:
        self.replies = list(replies)
        self.refuse = refuse
        self.sent: list[list[MessageParam]] = []

    def complete(self, system: str, messages: list[MessageParam], schema: dict[str, Any]) -> ModelTurn:
        self.sent.append(list(messages))
        if self.refuse:
            return ModelTurn(text=None, stop_reason="refusal", content=[])
        text = self.replies[min(len(self.sent) - 1, len(self.replies) - 1)]
        return ModelTurn(text=text, stop_reason="end_turn", content=[{"type": "text", "text": text or ""}])


def test_a_valid_first_answer_is_returned_after_one_call() -> None:
    client = Scripted(reply())
    result = extract(client, PDF)
    assert result.ok
    assert result.attempts == 1
    assert isinstance(result.value, ProofOfAddressExtraction)
    assert result.value.city.value == "Utrecht"


def test_the_exact_validation_errors_go_back_to_the_model() -> None:
    client = Scripted(reply(issue_date=field("01.09.2026")), reply())
    result = extract(client, PDF)
    assert result.ok
    assert result.attempts == 2
    feedback = client.sent[1][-1]
    assert feedback["role"] == "user"
    assert "issue_date: Value error, date '01.09.2026' must be written as YYYY-MM-DD" in str(feedback["content"])
    # The first answer stays in the history as the assistant turn: the retry appends, it never edits.
    assert client.sent[1][1] == {"role": "assistant", "content": [{"type": "text", "text": client.replies[0]}]}


def test_every_problem_is_reported_in_one_round() -> None:
    client = Scripted(
        reply(issue_date=field("2026-02-30"), country=field("XX"), city={"value": "Utrecht", "quote": None})
    )
    result = extract(client, PDF, max_attempts=1)
    assert sorted(e.split(":")[0] for e in result.errors) == ["city", "country", "issue_date"]


def test_an_object_that_never_validates_is_never_returned() -> None:
    client = Scripted(reply(country=field("Netherlands")))
    result = extract(client, PDF)
    assert result.value is None
    assert not result.ok
    assert result.failure == "invalid_after_retries"
    assert result.attempts == MAX_ATTEMPTS
    assert len(client.sent) == MAX_ATTEMPTS
    assert any("country" in e for e in result.errors)


def test_a_field_the_schema_does_not_have_is_refused() -> None:
    client = Scripted(json.dumps(GOOD | {"verified": True}))
    result = extract(client, PDF, max_attempts=1)
    assert not result.ok
    assert result.errors == ["verified: Extra inputs are not permitted"]


def test_text_that_is_not_json_is_reported_as_such() -> None:
    client = Scripted("Sure! Here is the data you asked for.", reply())
    result = extract(client, PDF)
    assert result.attempts == 2
    assert NO_JSON in str(client.sent[1][-1]["content"])


def test_a_refusal_stops_at_once() -> None:
    client = Scripted(refuse=True)
    result = extract(client, PDF)
    assert result.failure == "refused"
    assert len(client.sent) == 1


class Unavailable:
    def complete(self, system: str, messages: list[MessageParam], schema: dict[str, Any]) -> ModelTurn:
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.APIConnectionError(request=request)


def test_an_api_outage_is_a_failure_not_an_exception() -> None:
    result = extract(Unavailable(), PDF)
    assert result.failure == "model_unavailable"
    assert result.errors == ["APIConnectionError"]


class FakeMessages:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        block = SimpleNamespace(type="text", text=reply())
        return SimpleNamespace(content=[block], stop_reason="end_turn")


def test_the_real_client_uses_structured_outputs_and_no_rejected_parameters() -> None:
    messages = FakeMessages()
    sdk = SimpleNamespace(messages=messages)
    client = AnthropicModelClient(model="claude-sonnet-5-5", client=sdk)  # type: ignore[arg-type]  # a stand-in for the SDK client
    result = extract(client, PDF)
    assert result.ok
    sent = messages.kwargs
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["output_config"]["format"]["schema"]["additionalProperties"] is False
    # Sonnet 5.5 and Opus 5.5 answer 400 to a forced tool_choice or a non-default temperature.
    assert "tool_choice" not in sent
    assert "temperature" not in sent
    document = sent["messages"][0]["content"][0]
    assert document["type"] == "document"
    assert document["source"]["media_type"] == "application/pdf"


def test_images_are_sent_as_image_blocks() -> None:
    messages = FakeMessages()
    client = AnthropicModelClient(client=SimpleNamespace(messages=messages))  # type: ignore[arg-type]  # SDK stand-in
    extract(client, Document(DocumentKind.PROOF_OF_ADDRESS, "image/png", b"\x89PNG\r\n\x1a\n"))
    assert messages.kwargs["messages"][0]["content"][0]["type"] == "image"
