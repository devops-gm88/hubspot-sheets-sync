"""Webhook signature validation and event de-duplication.

HubSpot v3 signature: base64(HMAC-SHA256(app_client_secret,
                                       method + uri + raw_body + timestamp)).
The timestamp header is milliseconds and must be within the tolerance window.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

from .config import WEBHOOK_TOLERANCE_MS


def compute_signature(secret: str, method: str, uri: str, body: bytes, timestamp: str) -> str:
    message = method.encode() + uri.encode() + body + timestamp.encode()
    digest = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_signature(
    secret: str,
    method: str,
    uri: str,
    body: bytes,
    timestamp: str,
    signature: str,
    now_ms: int | None = None,
    tolerance_ms: int = WEBHOOK_TOLERANCE_MS,
) -> tuple[bool, str]:
    """Returns (ok, reason). The reason is what the caller logs and never the secret."""
    if not secret:
        return False, "no webhook secret configured"
    if not signature:
        return False, "missing X-HubSpot-Signature-v3"
    if not timestamp or not timestamp.isdigit():
        return False, "missing or malformed timestamp"
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    age = abs(now - int(timestamp))
    if age > tolerance_ms:
        return False, f"timestamp outside the {tolerance_ms // 1000}s tolerance"
    expected = compute_signature(secret, method, uri, body, timestamp)
    if not hmac.compare_digest(expected, signature):
        return False, "signature mismatch"
    return True, "ok"


def parse_events(payload: bytes) -> list[dict]:
    """HubSpot posts a JSON array of events. Unknown shapes are skipped, not guessed."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    if not isinstance(data, list):
        return []
    events = []
    for item in data:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("eventId") or item.get("event_id") or "").strip()
        object_id = str(item.get("objectId") or item.get("object_id") or "").strip()
        if not event_id or not object_id:
            continue
        events.append(
            {
                "event_id": event_id,
                "object_id": object_id,
                "subscription_type": item.get("subscriptionType") or "",
                "occurred_at": item.get("occurredAt") or "",
            }
        )
    return events


class EventStore:
    """A JSON file of event ids already handled. Small, atomic, testable."""

    def __init__(self, path: Path, max_size: int = 5000):
        self.path = path
        self.max_size = max_size

    def _load(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return []
        return [str(item) for item in data] if isinstance(data, list) else []

    def seen(self, event_id: str) -> bool:
        return event_id in self._load()

    def mark(self, event_id: str) -> None:
        events = self._load()
        if event_id in events:
            return
        events.append(event_id)
        events = events[-self.max_size :]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(events, indent=0), encoding="utf-8")
        tmp.replace(self.path)
