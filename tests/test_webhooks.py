"""Webhook signature rules and the event store."""
from __future__ import annotations

import json
import time

from app.webhooks import EventStore, compute_signature, parse_events, verify_signature

SECRET = "test-secret-value"
URI = "/webhooks/hubspot"
BODY = json.dumps([{"eventId": 1, "objectId": 42, "subscriptionType": "contact.creation"}]).encode()


def test_a_correct_signature_passes():
    timestamp = str(int(time.time() * 1000))
    signature = compute_signature(SECRET, "POST", URI, BODY, timestamp)
    ok, reason = verify_signature(SECRET, "POST", URI, BODY, timestamp, signature)
    assert ok, reason


def test_a_wrong_secret_fails():
    timestamp = str(int(time.time() * 1000))
    signature = compute_signature("wrong-secret", "POST", URI, BODY, timestamp)
    ok, reason = verify_signature(SECRET, "POST", URI, BODY, timestamp, signature)
    assert not ok
    assert "mismatch" in reason


def test_a_tampered_body_fails():
    timestamp = str(int(time.time() * 1000))
    signature = compute_signature(SECRET, "POST", URI, BODY, timestamp)
    ok, _ = verify_signature(SECRET, "POST", URI, BODY + b" ", timestamp, signature)
    assert not ok


def test_a_stale_timestamp_fails():
    old = int(time.time() * 1000) - 400_000
    timestamp = str(old)
    signature = compute_signature(SECRET, "POST", URI, BODY, timestamp)
    ok, reason = verify_signature(SECRET, "POST", URI, BODY, timestamp, signature)
    assert not ok
    assert "tolerance" in reason


def test_a_missing_secret_fails_closed():
    ok, reason = verify_signature("", "POST", URI, BODY, "123", "sig")
    assert not ok
    assert "no webhook secret" in reason


def test_a_malformed_timestamp_fails():
    ok, reason = verify_signature(SECRET, "POST", URI, BODY, "not-a-number", "sig")
    assert not ok
    assert "timestamp" in reason


def test_parse_events_keeps_ids_and_skips_junk():
    payload = json.dumps(
        [
            {"eventId": 11, "objectId": 501, "subscriptionType": "contact.creation"},
            {"eventId": 12, "objectId": 502, "subscriptionType": "contact.propertyChange"},
            {"eventId": None, "objectId": 503},
            "not an object",
            {"objectId": 504},
        ]
    ).encode()
    events = parse_events(payload)
    assert [event["event_id"] for event in events] == ["11", "12"]
    assert events[0]["object_id"] == "501"


def test_parse_events_rejects_non_list_payloads():
    assert parse_events(b'{"eventId": 1}') == []
    assert parse_events(b"not json") == []


def test_event_store_remembers_ids(tmp_path):
    store = EventStore(tmp_path / "events.json")
    assert store.seen("abc") is False
    store.mark("abc")
    assert store.seen("abc") is True
    store.mark("abc")
    assert len(json.loads((tmp_path / "events.json").read_text())) == 1


def test_event_store_caps_its_size(tmp_path):
    store = EventStore(tmp_path / "events.json", max_size=3)
    for index in range(6):
        store.mark(f"event-{index}")
    assert json.loads((tmp_path / "events.json").read_text()) == ["event-3", "event-4", "event-5"]
