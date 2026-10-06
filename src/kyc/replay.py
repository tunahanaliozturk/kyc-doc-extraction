"""A stand-in for the model that answers from a script, keyed by the SHA-256 of the document it is shown.

Used by the tests, the offline eval and the API's offline mode. It runs the same extraction loop as the real client
(same prompt, same schema, same retry with errors), so what it exercises is the wiring and the verification. What
it cannot tell you is how well a real model reads a document. That needs `kyc-eval --live` and an API key.
"""

import base64
import hashlib
from typing import Any

from anthropic.types import MessageParam

from kyc.extraction import ModelTurn
from kyc.generator import REFUSAL


class ReplayClient:
    def __init__(self, replies: dict[str, list[str]]) -> None:
        self.replies = replies
        self.calls = 0

    def complete(self, system: str, messages: list[MessageParam], schema: dict[str, Any]) -> ModelTurn:  # noqa: ARG002  # same signature as the real client
        self.calls += 1
        attempt = sum(1 for m in messages if m["role"] == "assistant")
        script = self.replies.get(_document_hash(messages), [])
        if not script:
            text = "no recorded answer for this document"  # not JSON, so it fails validation like garbage would
            return ModelTurn(text=text, stop_reason="end_turn", content=[{"type": "text", "text": text}])
        reply = script[min(attempt, len(script) - 1)]
        if reply == REFUSAL:
            return ModelTurn(text=None, stop_reason="refusal", content=[])
        return ModelTurn(text=reply, stop_reason="end_turn", content=[{"type": "text", "text": reply}])


def _document_hash(messages: list[MessageParam]) -> str:
    content = messages[0]["content"]
    for block in content if isinstance(content, list) else []:
        source = block.get("source") if isinstance(block, dict) else None
        if isinstance(source, dict) and source.get("type") == "base64":
            return hashlib.sha256(base64.standard_b64decode(str(source["data"]))).hexdigest()
    return ""
