"""Eval harness: run every synthetic case through the pipeline and measure what a bank cares about.

    uv run kyc-eval                 offline, scripted replies; measures wiring and verification, not reading
    uv run kyc-eval --live          the real model on the same documents; needs an API key and costs money

The number that matters most is false approvals: cases approved that a correct pipeline would not approve. It must
be zero, and with --gate the command exits non-zero when it is not. Offline, every decision must also match the
expected one, because the replies are scripted and any difference is a regression in the code.
"""

import argparse
import json
import platform
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kyc.extraction import AnthropicModelClient, Document, ModelClient
from kyc.generator import DEFAULT_SEED, REFERENCE_DATE, SpecimenCase, generate, replies_by_hash
from kyc.pipeline import process
from kyc.replay import ReplayClient
from kyc.routing import Outcome


def normalise(value: Any) -> str:
    """Forgiving comparison: case, accents, spaces and punctuation do not count. "NL91 ABNA" equals "nl91abna"."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "|".join(sorted(normalise(v) for v in value))
    text = unicodedata.normalize("NFKD", str(value).casefold())
    return re.sub(r"[\W_]+", "", "".join(c for c in text if not unicodedata.combining(c)))


def _exact(value: Any) -> Any:
    return sorted(value) if isinstance(value, list) else value


def _value(raw: object) -> Any:
    """The value out of a dumped Extracted ({"value", "quote"}), or the list of values for a list field."""
    if isinstance(raw, list):
        return [_value(item) for item in raw]
    return raw.get("value") if isinstance(raw, dict) else None


@dataclass
class FieldScore:
    total: int = 0
    exact: int = 0
    normalised: int = 0


@dataclass
class Report:
    mode: str
    cases: int = 0
    documents: int = 0
    seconds: float = 0.0
    model_calls: int = 0
    outcomes: Counter[str] = field(default_factory=Counter)
    confusion: Counter[tuple[str, str]] = field(default_factory=Counter)
    false_approvals: list[str] = field(default_factory=list)
    false_rejections: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    hard_cases: int = 0
    hard_false_approvals: int = 0
    retried_documents: int = 0
    fields: dict[str, FieldScore] = field(default_factory=lambda: defaultdict(FieldScore))

    def rate(self, n: int) -> str:
        return f"{n / self.cases:.1%}" if self.cases else "n/a"


def evaluate(cases: list[SpecimenCase], client: ModelClient, mode: str) -> Report:
    report = Report(mode=mode)
    started = time.perf_counter()
    for case in cases:
        docs = [Document(d.kind, d.media_type, d.content) for d in case.documents]
        result = process(case.application, docs, client, REFERENCE_DATE)
        got, want = result.decision.outcome, case.expected
        label = f"{case.case_id} ({case.scenario})"
        report.cases += 1
        report.documents += len(docs)
        report.outcomes[got.value] += 1
        report.confusion[(want.value, got.value)] += 1
        report.hard_cases += case.hard
        if got != want:
            report.mismatches.append(f"{label}: expected {want.value}, got {got.value}")
        if got is Outcome.APPROVED and want is not Outcome.APPROVED:
            report.false_approvals.append(label)
            report.hard_false_approvals += case.hard
        if got is Outcome.REJECTED and want is not Outcome.REJECTED:
            report.false_rejections.append(label)
        for spec, doc_result in zip(case.documents, result.documents, strict=True):
            report.retried_documents += doc_result.attempts > 1
            group = "identity" if spec.kind.value in {"identity_card", "passport"} else spec.kind.value
            extracted = doc_result.fields or {}
            for name, truth in spec.truth.items():
                value = _value(extracted.get(name))
                score = report.fields[f"{group}.{name}"]
                score.total += 1
                score.exact += _exact(value) == _exact(truth)
                score.normalised += normalise(value) == normalise(truth)
    report.seconds = time.perf_counter() - started
    report.model_calls = getattr(client, "calls", 0)
    return report


def to_markdown(report: Report) -> str:
    approved = report.outcomes[Outcome.APPROVED.value]
    review = report.outcomes[Outcome.IN_REVIEW.value]
    rejected = report.outcomes[Outcome.REJECTED.value]
    not_approvable = sum(n for (want, _), n in report.confusion.items() if want != "approved")
    lines = [
        f"## Eval run: {report.mode}",
        "",
        f"{report.cases} cases, {report.documents} documents, seed {DEFAULT_SEED}, reference date "
        f"{REFERENCE_DATE.isoformat()}. Took {report.seconds:.1f} s on Python {platform.python_version()}, "
        f"{platform.system()} {platform.release()} {platform.machine()}.",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| False approvals (must be 0) | {len(report.false_approvals)} of {not_approvable} cases that should not "
        f"be approved |",
        f"| False approvals on the {report.hard_cases} hard cases | {report.hard_false_approvals} |",
        f"| Decisions matching the expected outcome | {report.cases - len(report.mismatches)} of {report.cases} |",
        f"| Straight-through (auto-approved) | {approved} ({report.rate(approved)}) |",
        f"| Sent to human review | {review} ({report.rate(review)}) |",
        f"| Rejected automatically | {rejected} ({report.rate(rejected)}) |",
        f"| Wrongly rejected | {len(report.false_rejections)} |",
        f"| Documents that needed a retry | {report.retried_documents} |",
        "",
        "Expected outcome down, actual across:",
        "",
        "| expected \\ actual | approved | in_review | rejected |",
        "|---|---|---|---|",
    ]
    for want in ("approved", "in_review", "rejected"):
        row = " | ".join(str(report.confusion[(want, got)]) for got in ("approved", "in_review", "rejected"))
        lines.append(f"| {want} | {row} |")
    lines += ["", "Per-field accuracy (exact string, then after folding case, accents, spaces and punctuation):", ""]
    lines += ["| Field | Documents | Exact | Normalised |", "|---|---|---|---|"]
    for name in sorted(report.fields):
        s = report.fields[name]
        lines.append(f"| {name} | {s.total} | {s.exact / s.total:.1%} | {s.normalised / s.total:.1%} |")
    if report.mismatches:
        lines += ["", "Decisions that differ from the expected outcome:", ""]
        lines += [f"- {m}" for m in report.mismatches]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the pipeline on the synthetic case set.")
    parser.add_argument("--live", action="store_true", help="use the real model (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--gate", action="store_true", help="exit 1 on any false approval (offline: any mismatch)")
    parser.add_argument("--markdown", type=Path, help="also write the report to this file")
    parser.add_argument("--json", type=Path, help="write the raw numbers to this file")
    args = parser.parse_args()

    cases = generate(args.seed)
    client: ModelClient
    if args.live:
        live = AnthropicModelClient()
        client, mode = live, f"live ({live.model})"
    else:
        client, mode = ReplayClient(replies_by_hash(cases)), "offline replay (scripted replies, not a model)"
    report = evaluate(cases, client, mode)
    text = to_markdown(report)
    sys.stdout.write(text)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(text, encoding="utf-8")
    if args.json:
        numbers = {
            "mode": report.mode,
            "cases": report.cases,
            "false_approvals": report.false_approvals,
            "mismatches": report.mismatches,
            "outcomes": dict(report.outcomes),
            "fields": {k: vars(v) for k, v in sorted(report.fields.items())},
        }
        args.json.write_text(json.dumps(numbers, indent=2), encoding="utf-8")
    if args.gate:
        failed = bool(report.false_approvals) or (not args.live and bool(report.mismatches))
        raise SystemExit(1 if failed else 0)
