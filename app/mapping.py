"""Contact to spreadsheet row mapping, dedupe on email, and the row diff."""
from __future__ import annotations

from datetime import datetime, timezone

from .config import SYNC_HEADER


def normalise_email(value: str | None) -> str:
    return (value or "").strip().lower()


def last_modified(contact: dict) -> str:
    return str((contact.get("properties") or {}).get("lastmodifieddate") or "")


def contact_to_row(contact: dict, synced_at: str | None = None) -> list[str]:
    props = contact.get("properties") or {}
    synced_at = synced_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        normalise_email(props.get("email")),
        props.get("firstname") or "",
        props.get("lastname") or "",
        props.get("company") or "",
        props.get("phone") or "",
        props.get("lifecyclestage") or "",
        str(contact.get("id") or ""),
        last_modified(contact),
        synced_at,
        "new",
    ]


def dedupe_contacts(contacts: list[dict]) -> tuple[list[dict], int]:
    """Collapse contacts that share an email. The newest modified record wins.

    Contacts without an email cannot be matched to a row key, so they are dropped
    and counted in the collapsed total.
    """
    best: dict[str, dict] = {}
    collapsed = 0
    for contact in contacts:
        key = normalise_email((contact.get("properties") or {}).get("email"))
        if not key:
            collapsed += 1
            continue
        if key not in best:
            best[key] = contact
            continue
        collapsed += 1
        if last_modified(contact) > last_modified(best[key]):
            best[key] = contact
    return list(best.values()), collapsed


def rows_from_contacts(contacts: list[dict], synced_at: str | None = None) -> tuple[list[list[str]], int]:
    unique, collapsed = dedupe_contacts(contacts)
    rows = [contact_to_row(contact, synced_at) for contact in unique]
    rows.sort(key=lambda row: row[0])
    return rows, collapsed


def diff_rows(existing: list[list[str]], new_rows: list[list[str]], key_index: int = 0) -> dict:
    """Compare by key. Return appends, updates and the unchanged count.

    `existing` excludes the header row. Rows are compared cell by cell, so a changed
    contact name or company is an update, not a second row.
    """
    known = {row[key_index]: row for row in existing if row and row[key_index]}
    to_append: list[list[str]] = []
    to_update: list[list[str]] = []
    unchanged = 0
    for row in new_rows:
        key = row[key_index]
        if key not in known:
            to_append.append(row)
            continue
        current = known[key]
        comparable_new = row[:-1]
        comparable_old = (current + [""] * len(row))[: len(comparable_new)]
        if comparable_new == comparable_old:
            unchanged += 1
        else:
            to_update.append(row)
    return {"append": to_append, "update": to_update, "unchanged": unchanged}


def header() -> list[str]:
    return list(SYNC_HEADER)
