# 0008. Offline replay keyed by document hash, and specimens that are byte-identical on every platform

Status: Accepted, 2026-10-06

## Context

There is no API key on the development machine and there must be none in CI. The tests, the eval gate and the API
demo still need something that answers like the model: same prompt, same schema, same retry loop.

## Decision

`ReplayClient` (`src/kyc/replay.py`) implements the same `ModelClient` protocol as the real client. It hashes the
document in the request with SHA-256, looks up the scripted replies the generator wrote for that document, and
returns the reply for the current attempt. Scripted replies are what a perfect reader would answer, plus
deliberate flaws in some cases: an invalid first answer, a misread digit, an unreadable photo, a refusal, and a
reader that obeys an injected instruction.

Because the key is a hash, a specimen generated on Windows must have the same bytes as one generated in the Linux
container. It did not at first: CPython on Windows links zlib-ng, Linux links zlib, and the same stream compresses
to different bytes. The generator now writes uncompressed PDFs (`pageCompression=0`, `invariant=1`) and PNGs with
`compress_level=0`. A test pins the digest of the whole set.

## Consequences

- `uv run kyc-eval --gate` evaluates the 56 cases in under a second with no network, and CI fails on any
  false approval or any changed decision.
- The offline numbers measure the pipeline: schema validation, retry, checks, routing. They say nothing about how
  well a real model reads a document. The README says this next to every offline number.
- Specimens are larger than they need to be: 7.3 MiB for 117 documents, all held in memory during the eval.
- Upgrading reportlab or Pillow can change the bytes. The pinned-digest test fails first, which is the intent.

## Alternatives

- **Record real model replies once and replay them.** The better test of reading, and the natural next step once a
  key is available: run `kyc-eval --live`, store the replies, replay them in CI. Lost for now because there is no
  key.
- **Key by case id instead of hash.** Lost: the API receives documents, not case ids, and keying by content means
  the API's offline mode works on any uploaded specimen.
