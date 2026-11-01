"""HTTP surface: webhook receiver, manual sync trigger, health.

Standard library only. The route logic lives in WebhookApp so tests can call it
without opening a socket.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import HUBSPOT_TOKEN, SHEET_ID, WEBHOOK_SECRET
from .http import Transport
from .hubspot import HubSpot
from .sheets import Sheets, service_account_token_provider
from .sync import run_contact_sync, run_sync
from .webhooks import EventStore, parse_events, verify_signature


class WebhookApp:
    def __init__(
        self,
        secret: str,
        hubspot: HubSpot,
        sheets: Sheets,
        events: EventStore,
        max_contacts_per_event: int = 10,
    ):
        self.secret = secret
        self.hubspot = hubspot
        self.sheets = sheets
        self.events = events
        self.max_contacts_per_event = max_contacts_per_event

    def health(self) -> tuple[int, dict]:
        return 200, {
            "status": "ok",
            "hubspot_configured": bool(self.hubspot.token),
            "sheet_configured": bool(self.sheets.sheet_id),
            "webhook_secret_configured": bool(self.secret),
        }

    def webhook(self, method: str, uri: str, headers: dict, body: bytes) -> tuple[int, dict]:
        headers_lower = {key.lower(): value for key, value in headers.items()}
        ok, reason = verify_signature(
            self.secret,
            method,
            uri,
            body,
            headers_lower.get("x-hubspot-request-timestamp", ""),
            headers_lower.get("x-hubspot-signature-v3", ""),
        )
        if not ok:
            return 401, {"error": "invalid signature", "reason": reason}

        events = parse_events(body)
        if not events:
            return 204, {}

        results = []
        handled = 0
        for event in events:
            if self.events.seen(event["event_id"]):
                results.append({"event_id": event["event_id"], "status": "duplicate"})
                continue
            if handled >= self.max_contacts_per_event:
                results.append({"event_id": event["event_id"], "status": "deferred"})
                continue
            sync = run_contact_sync(self.hubspot, self.sheets, event["object_id"])
            self.events.mark(event["event_id"])
            handled += 1
            results.append(
                {
                    "event_id": event["event_id"],
                    "status": "synced" if sync.errors == 0 else "error",
                    "written": sync.written,
                    "updated": sync.updated,
                }
            )
        return 200, {"events": len(events), "handled": handled, "results": results}

    def manual_sync(self) -> tuple[int, dict]:
        result = run_sync(self.hubspot, self.sheets)
        return (200 if result.errors == 0 else 500), result.summary()


def build_app() -> WebhookApp:
    from .config import EVENT_STORE, GOOGLE_SA_JSON

    return WebhookApp(
        secret=WEBHOOK_SECRET,
        hubspot=HubSpot(HUBSPOT_TOKEN, transport=Transport()),
        sheets=Sheets(SHEET_ID, service_account_token_provider(GOOGLE_SA_JSON)),
        events=EventStore(EVENT_STORE),
    )


def make_handler(app: WebhookApp):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:  # keep the console clean
            return

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            if self.path.startswith("/health"):
                status, payload = app.health()
                self._send(status, payload)
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib naming
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            if self.path.startswith("/webhooks/hubspot"):
                status, payload = app.webhook("POST", self.path, dict(self.headers), body)
                self._send(status, payload)
            elif self.path.startswith("/sync"):
                status, payload = app.manual_sync()
                self._send(status, payload)
            else:
                self._send(404, {"error": "not found"})

    return Handler


def serve(port: int, app: WebhookApp) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
