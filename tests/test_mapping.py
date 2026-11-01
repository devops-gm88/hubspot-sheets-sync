"""Mapping, dedupe and the row diff."""
from __future__ import annotations

from app.mapping import (
    contact_to_row,
    dedupe_contacts,
    diff_rows,
    normalise_email,
    rows_from_contacts,
)
from tests.conftest import contact


def test_normalise_email_trims_and_lowercases():
    assert normalise_email("  Maria@Example.COM ") == "maria@example.com"
    assert normalise_email(None) == ""


def test_contact_to_row_keeps_the_declared_column_order():
    row = contact_to_row(contact("42", "A@Example.com", "Ann", "Lee", "Acme"), synced_at="2026-10-31T00:00:00Z")
    assert row == ["a@example.com", "Ann", "Lee", "Acme", "", "lead", "42", "2026-10-01T10:00:00Z", "2026-10-31T00:00:00Z", "new"]


def test_dedupe_keeps_the_newest_contact_for_an_email():
    contacts = [
        contact("1", "dupe@example.com", "Old", modified="2026-09-01T00:00:00Z"),
        contact("2", "dupe@example.com", "New", modified="2026-10-01T00:00:00Z"),
    ]
    unique, collapsed = dedupe_contacts(contacts)
    assert collapsed == 1
    assert len(unique) == 1
    assert unique[0]["id"] == "2"


def test_dedupe_drops_a_contact_without_an_email_and_counts_it():
    unique, collapsed = dedupe_contacts([contact("1", ""), contact("2", "a@example.com")])
    assert collapsed == 1
    assert [item["id"] for item in unique] == ["2"]


def test_rows_are_sorted_by_email():
    rows, _ = rows_from_contacts(
        [contact("1", "z@example.com"), contact("2", "a@example.com")], synced_at="2026-10-31T00:00:00Z"
    )
    assert [row[0] for row in rows] == ["a@example.com", "z@example.com"]


def test_diff_detects_appends():
    new = [["a@example.com", "Ann"], ["b@example.com", "Bob"]]
    diff = diff_rows([], new)
    assert len(diff["append"]) == 2
    assert diff["update"] == []
    assert diff["unchanged"] == 0


def test_diff_detects_unchanged_rows():
    existing = [["a@example.com", "Ann", "", "", "", "", "1", "t", "s", "new"]]
    new = [["a@example.com", "Ann", "", "", "", "", "1", "t", "s", "new"]]
    diff = diff_rows(existing, new)
    assert diff["unchanged"] == 1
    assert diff["append"] == []
    assert diff["update"] == []


def test_diff_detects_a_changed_name_as_an_update_not_a_second_row():
    existing = [["a@example.com", "Ann", "", "", "", "", "1", "t", "s", "new"]]
    new = [["a@example.com", "Anne", "", "", "", "", "1", "t", "s2", "new"]]
    diff = diff_rows(existing, new)
    assert diff["update"] == new
    assert diff["append"] == []
