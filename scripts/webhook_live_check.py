"""Live check of the webhook receiver: forged, tampered, valid, duplicate.

    python scripts/webhook_live_check.py http://127.0.0.1:8090

Prints a JSON report and exits non-zero when any expectation fails.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import WEBHOOK_SECRET
from app.webhooks import compute_signature

URI = "/webhooks/hubspot"


def post(base: str, body: bytes, headers: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{base}{URI}", data=body, headers={"Content-Type": "application/json", **headers}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090"
    events = [
        {"eventId": 900001, "objectId": 286977368001, "subscriptionType": "contact.creation", "occurredAt": 1761900000000},
        {"eventId": 900002, "objectId": 286977400780, "subscriptionType": "contact.propertyChange", "occurredAt": 1761900000000},
    ]
    body = json.dumps(events).encode()
    timestamp = str(int(time.time() * 1000))
    good = compute_signature(WEBHOOK_SECRET, "POST", URI, body, timestamp)

    checks = []

    status, payload = post(base, body, {})
    checks.append({"case": "unsigned request", "expected": 401, "got": status, "reason": payload.get("reason", "")})

    tampered = body + b" "
    status, payload = post(
        base,
        tampered,
        {"X-HubSpot-Signature-v3": good, "X-HubSpot-Request-Timestamp": timestamp},
    )
    checks.append({"case": "tampered body", "expected": 401, "got": status, "reason": payload.get("reason", "")})

    stale = str(int(time.time() * 1000) - 600_000)
    stale_signature = compute_signature(WEBHOOK_SECRET, "POST", URI, body, stale)
    status, payload = post(
        base,
        body,
        {"X-HubSpot-Signature-v3": stale_signature, "X-HubSpot-Request-Timestamp": stale},
    )
    checks.append({"case": "stale timestamp", "expected": 401, "got": status, "reason": payload.get("reason", "")})

    status, payload = post(
        base, body, {"X-HubSpot-Signature-v3": good, "X-HubSpot-Request-Timestamp": timestamp}
    )
    checks.append(
        {
            "case": "valid signature",
            "expected": 200,
            "got": status,
            "handled": payload.get("handled"),
            "results": [item.get("status") for item in payload.get("results", [])],
        }
    )

    status, payload = post(
        base, body, {"X-HubSpot-Signature-v3": good, "X-HubSpot-Request-Timestamp": timestamp}
    )
    checks.append(
        {
            "case": "same events a second time",
            "expected": 200,
            "got": status,
            "results": [item.get("status") for item in payload.get("results", [])],
        }
    )

    failed = [check for check in checks if check["got"] != check["expected"]]
    print(json.dumps({"checks": checks, "failed": len(failed)}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
