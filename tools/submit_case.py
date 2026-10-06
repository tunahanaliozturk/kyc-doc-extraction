"""Submit a generated specimen case to a running API and print where it ended up.

    uv run kyc-generate
    uv run python tools/submit_case.py data/specimens/case-001

Reads the token from KYC_TOKEN. Uses only the standard library so it runs anywhere the repo is checked out.
"""

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    folder = Path(sys.argv[1])
    base = os.environ.get("KYC_URL", "http://127.0.0.1:8000")
    token = os.environ["KYC_TOKEN"]
    if not base.startswith(("http://", "https://")):
        raise SystemExit("KYC_URL must be an http or https URL")
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    body = {
        "application": json.loads((folder / "application.json").read_text(encoding="utf-8")),
        "documents": [
            {
                "kind": d["kind"],
                "media_type": d["media_type"],
                "content_base64": base64.b64encode((folder / d["file"]).read_bytes()).decode(),
            }
            for d in expected["documents"]
        ],
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{base}/v1/cases"
    request = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")  # noqa: S310  # scheme checked above
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310  # scheme checked above
        location = response.headers["Location"]
    print(f"submitted {folder.name} ({expected['scenario']}), expected {expected['expected']}: {base}{location}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
