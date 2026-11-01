"""The sync engine: pull contacts, map, dedupe, upsert, log."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import LOG_TAB, SYNC_TAB
from .hubspot import HubSpot
from .mapping import rows_from_contacts
from .sheets import Sheets


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SyncResult:
    mode: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    run_at: str = field(default_factory=_now)
    seen: int = 0
    written: int = 0
    updated: int = 0
    unchanged: int = 0
    collapsed: int = 0
    errors: int = 0
    error_detail: str = ""
    probe: list = field(default_factory=list)
    duration_ms: int = 0

    def log_row(self) -> list[str]:
        return [
            self.run_at,
            self.run_id,
            self.mode,
            str(self.seen),
            str(self.written),
            str(self.updated),
            str(self.unchanged),
            str(self.collapsed),
            str(self.errors),
        ]

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "run_at": self.run_at,
            "mode": self.mode,
            "seen": self.seen,
            "written": self.written,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "collapsed": self.collapsed,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
            "error_detail": self.error_detail,
        }


def run_sync(hubspot: HubSpot, sheets: Sheets, mode: str = "full") -> SyncResult:
    started = time.perf_counter()
    result = SyncResult(mode=mode)
    try:
        contacts = list(hubspot.iter_contacts())
        result.seen = len(contacts)
        rows, collapsed = rows_from_contacts(contacts)
        result.collapsed = collapsed
        applied = sheets.upsert_contacts(SYNC_TAB, rows)
        result.written = applied["appended"]
        result.updated = applied["updated"]
        result.unchanged = applied["unchanged"]
    except Exception as exc:  # noqa: BLE001 - the failure is the result
        result.errors = 1
        result.error_detail = f"{type(exc).__name__}: {exc}"[:300]
    result.duration_ms = int((time.perf_counter() - started) * 1000)
    try:
        sheets.append_log(LOG_TAB, result.log_row())
    except Exception as exc:  # noqa: BLE001 - never lose the sync over the log
        result.errors = max(result.errors, 1)
        result.error_detail = (result.error_detail + f" | log: {type(exc).__name__}")[:300]
    return result


def run_contact_sync(hubspot: HubSpot, sheets: Sheets, contact_id: str, mode: str = "webhook") -> SyncResult:
    """Sync one contact. Used by the webhook handler; idempotent by construction."""
    started = time.perf_counter()
    result = SyncResult(mode=mode)
    try:
        contact = hubspot.get_contact(contact_id)
        result.seen = 1
        rows, collapsed = rows_from_contacts([contact])
        result.collapsed = collapsed
        applied = sheets.upsert_contacts(SYNC_TAB, rows)
        result.written = applied["appended"]
        result.updated = applied["updated"]
        result.unchanged = applied["unchanged"]
    except Exception as exc:  # noqa: BLE001 - the failure is the result
        result.errors = 1
        result.error_detail = f"{type(exc).__name__}: {exc}"[:300]
    result.duration_ms = int((time.perf_counter() - started) * 1000)
    try:
        sheets.append_log(LOG_TAB, result.log_row())
    except Exception:  # noqa: BLE001
        pass
    return result
