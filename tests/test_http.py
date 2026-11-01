"""Transport retry rules and HubSpot client behaviour, both offline."""
from __future__ import annotations

import requests

from app.http import RetryExhausted, Transport
from app.hubspot import HubSpot
from tests.conftest import FakeRawResponse, FakeSession, contact


def test_retries_a_429_then_succeeds():
    sleeps: list[float] = []
    session = FakeSession(
        [
            FakeRawResponse(429, {"error": "rate"}, headers={"Retry-After": "0"}),
            FakeRawResponse(200, {"ok": True}),
        ]
    )
    transport = Transport(session=session, sleep=sleeps.append, jitter=lambda: 0.0)
    response = transport.request("GET", "https://example.test/x")
    assert response.status == 200
    assert response.attempts == 2
    assert sleeps == [0.0]


def test_honours_retry_after_header():
    sleeps: list[float] = []
    session = FakeSession([FakeRawResponse(429, headers={"Retry-After": "2"}), FakeRawResponse(200, {})])
    transport = Transport(session=session, sleep=sleeps.append, jitter=lambda: 0.0)
    transport.request("GET", "https://example.test/x")
    assert sleeps == [2.0]


def test_gives_up_and_reports_the_status():
    session = FakeSession([FakeRawResponse(503, {"error": "down"}) for _ in range(5)])
    transport = Transport(session=session, max_attempts=3, sleep=lambda _: None, jitter=lambda: 0.0)
    try:
        transport.request("GET", "https://example.test/x")
    except RetryExhausted as exc:
        assert exc.status == 503
        assert len(session.calls) == 3
    else:
        raise AssertionError("expected RetryExhausted")


def test_reuses_the_idempotency_key_on_every_attempt():
    session = FakeSession([FakeRawResponse(500), FakeRawResponse(200, {})])
    transport = Transport(session=session, sleep=lambda _: None, jitter=lambda: 0.0)
    transport.request("POST", "https://example.test/x", json={"a": 1}, idempotency_key="key-1")
    assert [call["headers"]["Idempotency-Key"] for call in session.calls] == ["key-1", "key-1"]


def test_retries_a_connection_error():
    session = FakeSession([requests.ConnectionError("boom"), FakeRawResponse(200, {})])
    transport = Transport(session=session, sleep=lambda _: None, jitter=lambda: 0.0)
    assert transport.request("GET", "https://example.test/x").status == 200


def test_does_not_retry_a_400():
    session = FakeSession([FakeRawResponse(400, {"error": "bad"})])
    transport = Transport(session=session, sleep=lambda _: None, jitter=lambda: 0.0)
    assert transport.request("GET", "https://example.test/x").status == 400
    assert len(session.calls) == 1


# ---------------------------------------------------------------- HubSpot client
class StubTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json})
        return self.responses.pop(0)


def test_iter_contacts_follows_paging():
    from app.http import Response

    transport = StubTransport(
        [
            Response(status=200, body={"results": [contact("1", "a@example.com")], "paging": {"next": {"after": "abc"}}}),
            Response(status=200, body={"results": [contact("2", "b@example.com")]}),
        ]
    )
    contacts = list(HubSpot("token", transport).iter_contacts(page_size=1))
    assert [item["id"] for item in contacts] == ["1", "2"]
    assert transport.calls[1]["params"]["after"] == "abc"


def test_search_by_email_sends_an_eq_filter():
    from app.http import Response

    transport = StubTransport([Response(status=200, body={"results": [contact("1", "a@example.com")]})])
    found = HubSpot("token", transport).search_by_email("a@example.com")
    assert len(found) == 1
    sent = transport.calls[0]["json"]
    assert sent["filterGroups"][0]["filters"][0]["propertyName"] == "email"
    assert sent["filterGroups"][0]["filters"][0]["operator"] == "EQ"


def test_probe_reports_allowed_and_denied_scopes():
    from app.http import Response

    transport = StubTransport(
        [
            Response(status=200, body={}),
            Response(status=403, body={"message": "missing scope"}),
            Response(status=200, body={}),
            Response(status=403, body={"message": "missing scope"}),
            Response(status=200, body={}),
        ]
    )
    results = {row["scope"]: row["allowed"] for row in HubSpot("token", transport).probe()}
    assert results["crm.objects.contacts.read"] is True
    assert results["crm.objects.companies.read"] is False
