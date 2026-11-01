"""Sheets client against the in-memory fake: tabs, upsert, idempotency, log."""
from __future__ import annotations

from app.config import LOG_HEADER, SYNC_HEADER
from app.mapping import rows_from_contacts
from app.sheets import a1
from tests.conftest import contact


def _rows(emails: list[tuple[str, str]]):
    contacts = [contact(str(index), email, name) for index, (email, name) in enumerate(emails, start=1)]
    rows, _ = rows_from_contacts(contacts, synced_at="2026-10-31T00:00:00Z")
    return rows


def test_a1_quotes_the_tab_name():
    assert a1("my tab", "A1") == "'my tab'!A1"


def test_ensure_tab_creates_the_tab_and_the_header(sheets, sheets_transport):
    created = sheets.ensure_tab("contacts", SYNC_HEADER)
    assert created is True
    assert sheets_transport.tabs["contacts"][0] == SYNC_HEADER


def test_ensure_tab_is_idempotent(sheets, sheets_transport):
    sheets.ensure_tab("contacts", SYNC_HEADER)
    created = sheets.ensure_tab("contacts", SYNC_HEADER)
    assert created is False
    assert sheets_transport.tabs["contacts"].count(SYNC_HEADER) == 1


def test_upsert_appends_new_rows(sheets, sheets_transport):
    applied = sheets.upsert_contacts("contacts", _rows([("a@example.com", "Ann"), ("b@example.com", "Bob")]))
    assert applied["appended"] == 2
    assert len(sheets_transport.tabs["contacts"]) == 3  # header + 2


def test_second_upsert_is_idempotent(sheets, sheets_transport):
    rows = _rows([("a@example.com", "Ann"), ("b@example.com", "Bob")])
    sheets.upsert_contacts("contacts", rows)
    applied = sheets.upsert_contacts("contacts", rows)
    assert applied == {"header": SYNC_HEADER, "appended": 0, "updated": 0, "unchanged": 2}
    assert len(sheets_transport.tabs["contacts"]) == 3


def test_changed_contact_updates_in_place(sheets, sheets_transport):
    sheets.upsert_contacts("contacts", _rows([("a@example.com", "Ann")]))
    sheets.upsert_contacts("contacts", _rows([("a@example.com", "Anne")]))
    values = sheets_transport.tabs["contacts"]
    assert len(values) == 2
    assert values[1][1] == "Anne"
    assert values[1][-1] == "updated"


def test_log_tab_gets_the_header_and_one_row(sheets, sheets_transport):
    sheets.append_log("log", ["2026-10-31", "run1", "full", "2", "2", "0", "0", "0", "0"])
    values = sheets_transport.tabs["log"]
    assert values[0] == LOG_HEADER
    assert values[1][2] == "full"


def test_upsert_creates_the_tab_when_missing(sheets, sheets_transport):
    sheets.upsert_contacts("fresh", _rows([("a@example.com", "Ann")]))
    assert list(sheets_transport.tabs) == ["fresh"]
    assert sheets_transport.tabs["fresh"][0] == SYNC_HEADER


def test_a_new_timestamp_alone_does_not_rewrite_the_row(sheets, sheets_transport):
    contacts = [contact("1", "a@example.com", "Ann")]
    from app.mapping import rows_from_contacts
    first, _ = rows_from_contacts(contacts, synced_at="2026-10-31T00:00:00Z")
    second, _ = rows_from_contacts(contacts, synced_at="2026-11-01T00:00:00Z")
    sheets.upsert_contacts("contacts", first)
    applied = sheets.upsert_contacts("contacts", second)
    assert applied["appended"] == 0 and applied["updated"] == 0 and applied["unchanged"] == 1
    assert sheets_transport.tabs["contacts"][1][8] == "2026-10-31T00:00:00Z"
