"""Sync engine and the HTTP surface, end to end against the fakes."""
from __future__ import annotations

import json
import time

from app.config import LOG_TAB, SYNC_TAB
from app.http import Response
from app.hubspot import HubSpot
from app.server import WebhookApp
from app.sync import run_contact_sync, run_sync
from app.webhooks import EventStore, compute_signature
from tests.conftest import contact

SECRET = "test-secret-value"


class ContactTransport:
    """Serves a list of contacts for the paged pull and single-contact reads."""

    def __init__(self, contacts: list[dict]):
        self.contacts = contacts
        self.calls: list[dict] = []

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        self.calls.append({"method": method, "url": url})
        if "/crm/v3/objects/contacts/" in url:
            contact_id = url.rsplit("/", 1)[-1]
            for item in self.contacts:
                if item["id"] == contact_id:
                    return Response(status=200, body=item)
            return Response(status=404, body={"message": "not found"})
        return Response(status=200, body={"results": self.contacts})


def _hubspot(contacts):
    return HubSpot("token", ContactTransport(contacts))


# --------------------------------------------------------------------- sync
def test_full_sync_writes_every_unique_contact(hubspot_contacts, sheets, sheets_transport):
    result = run_sync(_hubspot(hubspot_contacts), sheets)
    assert result.seen == 5
    assert result.collapsed == 2  # one duplicate email, one missing email
    assert result.written == 3
    assert result.errors == 0
    emails = [row[0] for row in sheets_transport.tabs[SYNC_TAB][1:]]
    assert sorted(emails) == ["brian@example.com", "dupe@example.com", "maria@example.com"]


def test_full_sync_is_idempotent(hubspot_contacts, sheets, sheets_transport):
    run_sync(_hubspot(hubspot_contacts), sheets)
    second = run_sync(_hubspot(hubspot_contacts), sheets)
    assert second.written == 0
    assert second.updated == 0
    assert second.unchanged == 3
    assert len(sheets_transport.tabs[SYNC_TAB]) == 4  # header + 3


def test_sync_appends_one_log_row(hubspot_contacts, sheets, sheets_transport):
    run_sync(_hubspot(hubspot_contacts), sheets)
    log = sheets_transport.tabs[LOG_TAB]
    assert log[0][2] == "mode"
    assert log[1][2] == "full"
    assert log[1][3] == "5"


def test_one_contact_sync_updates_only_that_row(hubspot_contacts, sheets, sheets_transport):
    run_sync(_hubspot(hubspot_contacts), sheets)
    brian = contact("1002", "brian@example.com", "Brian", "Chen", "Northline Renamed", "2026-11-01T00:00:00Z")
    result = run_contact_sync(_hubspot(contacts := [brian]), sheets, "1002")
    assert result.updated == 1
    row = [entry for entry in sheets_transport.tabs[SYNC_TAB] if entry and entry[0] == "brian@example.com"][0]
    assert row[3] == "Northline Renamed"


def test_a_missing_contact_is_reported_not_raised(sheets):
    result = run_contact_sync(_hubspot([]), sheets, "999")
    assert result.errors == 1
    assert "not found" in result.error_detail


# ------------------------------------------------------------------- server
def _app(contacts, sheets, tmp_path):
    return WebhookApp(
        secret=SECRET,
        hubspot=_hubspot(contacts),
        sheets=sheets,
        events=EventStore(tmp_path / "events.json"),
    )


def test_health_reports_configuration(hubspot_contacts, sheets, tmp_path):
    status, payload = _app(hubspot_contacts, sheets, tmp_path).health()
    assert status == 200
    assert payload["webhook_secret_configured"] is True


def test_webhook_rejects_an_unsigned_request(hubspot_contacts, sheets, tmp_path):
    status, payload = _app(hubspot_contacts, sheets, tmp_path).webhook(
        "POST", "/webhooks/hubspot", {}, b"[]"
    )
    assert status == 401
    assert payload["error"] == "invalid signature"


def test_webhook_syncs_a_signed_event(hubspot_contacts, sheets, sheets_transport, tmp_path):
    body = json.dumps([{"eventId": "e-1", "objectId": "1001", "subscriptionType": "contact.creation"}]).encode()
    timestamp = str(int(time.time() * 1000))
    signature = compute_signature(SECRET, "POST", "/webhooks/hubspot", body, timestamp)
    headers = {
        "X-HubSpot-Signature-v3": signature,
        "X-HubSpot-Request-Timestamp": timestamp,
    }
    status, payload = _app(hubspot_contacts, sheets, tmp_path).webhook(
        "POST", "/webhooks/hubspot", headers, body
    )
    assert status == 200
    assert payload["handled"] == 1
    assert payload["results"][0]["status"] == "synced"
    assert any(row and row[0] == "maria@example.com" for row in sheets_transport.tabs[SYNC_TAB])


def test_the_same_event_twice_is_a_duplicate(hubspot_contacts, sheets, sheets_transport, tmp_path):
    app = _app(hubspot_contacts, sheets, tmp_path)
    body = json.dumps([{"eventId": "e-2", "objectId": "1002"}]).encode()
    timestamp = str(int(time.time() * 1000))
    headers = {
        "X-HubSpot-Signature-v3": compute_signature(SECRET, "POST", "/webhooks/hubspot", body, timestamp),
        "X-HubSpot-Request-Timestamp": timestamp,
    }
    app.webhook("POST", "/webhooks/hubspot", headers, body)
    status, payload = app.webhook("POST", "/webhooks/hubspot", headers, body)
    assert status == 200
    assert payload["results"][0]["status"] == "duplicate"
    rows = [row for row in sheets_transport.tabs[SYNC_TAB] if row and row[0] == "brian@example.com"]
    assert len(rows) == 1


def test_manual_sync_endpoint_returns_the_summary(hubspot_contacts, sheets, tmp_path):
    status, payload = _app(hubspot_contacts, sheets, tmp_path).manual_sync()
    assert status == 200
    assert payload["written"] == 3
