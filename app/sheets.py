"""Google Sheets client. Tabs, reads, idempotent upsert, run log.

Auth is a callable so tests never touch Google. The default provider loads the
service-account JSON and refreshes the token when it expires.
"""
from __future__ import annotations

import urllib.parse
from typing import Callable

from .config import LOG_HEADER, SYNC_HEADER
from .http import Transport
from .mapping import diff_rows

SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def service_account_token_provider(credentials_path: str) -> Callable[[], str]:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=SCOPES
    )

    def token() -> str:
        if not credentials.valid:
            credentials.refresh(Request())
        return credentials.token

    return token


def a1(tab: str, suffix: str = "") -> str:
    safe = "'" + tab.replace("'", "''") + "'"
    return f"{safe}!{suffix}" if suffix else safe


class Sheets:
    def __init__(
        self,
        sheet_id: str,
        token_provider: Callable[[], str],
        transport: Transport | None = None,
    ):
        self.sheet_id = sheet_id
        self.token_provider = token_provider
        self.transport = transport or Transport()

    # ------------------------------------------------------------- plumbing
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token_provider()}", "Content-Type": "application/json"}

    def _url(self, path: str = "") -> str:
        return f"{SHEETS_BASE}/{self.sheet_id}{path}"

    def _values_url(self, tab: str, suffix: str = "") -> str:
        return self._url(f"/values/{urllib.parse.quote(a1(tab, suffix), safe='')}")

    # ------------------------------------------------------------- read
    def tabs(self) -> list[str]:
        response = self.transport.request(
            "GET", self._url("?fields=sheets.properties.title"), headers=self._headers()
        )
        if not response.ok:
            raise SheetsError(f"tab list failed: {response.status}", response.status)
        return [sheet["properties"]["title"] for sheet in (response.body or {}).get("sheets", [])]

    def get_values(self, tab: str) -> list[list[str]]:
        response = self.transport.request("GET", self._values_url(tab), headers=self._headers())
        if response.status == 400:
            return []
        if not response.ok:
            raise SheetsError(f"read of {tab} failed: {response.status}", response.status)
        return (response.body or {}).get("values", [])

    # ------------------------------------------------------------- write
    def ensure_tab(self, tab: str, header: list[str]) -> bool:
        """Create the tab when missing. Returns True when this call created it."""
        existing = self.tabs()
        created = False
        if tab not in existing:
            payload = {"requests": [{"addSheet": {"properties": {"title": tab}}}]}
            response = self.transport.request(
                "POST", self._url(":batchUpdate"), headers=self._headers(), json=payload
            )
            if not response.ok:
                raise SheetsError(f"create of {tab} failed: {response.status}", response.status)
            created = True
        values = self.get_values(tab)
        if not values:
            self.update_range(tab, "A1", [header])
        return created

    def update_range(self, tab: str, start_cell: str, rows: list[list[str]]) -> int:
        end_row = _row_number(start_cell) + len(rows) - 1
        end_col = _column_letter(len(rows[0])) if rows else "A"
        target = f"{start_cell}:{end_col}{end_row}"
        response = self.transport.request(
            "PUT",
            self._values_url(tab, target) + "?valueInputOption=RAW",
            headers=self._headers(),
            json={"values": rows},
        )
        if not response.ok:
            raise SheetsError(f"write to {tab} failed: {response.status}", response.status)
        return int((response.body or {}).get("updatedCells", 0))

    def append_rows(self, tab: str, rows: list[list[str]]) -> int:
        response = self.transport.request(
            "POST",
            self._values_url(tab, "A1")
            + ":append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
            headers=self._headers(),
            json={"values": rows},
        )
        if not response.ok:
            raise SheetsError(f"append to {tab} failed: {response.status}", response.status)
        return int((response.body or {}).get("updates", {}).get("updatedRows", 0))

    # ------------------------------------------------------------- upsert
    def upsert_contacts(self, tab: str, rows: list[list[str]]) -> dict:
        """Write new contacts, update changed ones, leave the rest alone."""
        self.ensure_tab(tab, SYNC_HEADER)
        values = self.get_values(tab)
        header = values[0] if values else list(SYNC_HEADER)
        existing = values[1:] if len(values) > 1 else []
        diff = diff_rows(existing, rows)

        # status column is the last cell: mark what this run did
        appended = [row[:-1] + ["new"] for row in diff["append"]]
        updated = [row[:-1] + ["updated"] for row in diff["update"]]

        if appended:
            self.append_rows(tab, appended)
        if updated:
            known = {row[0]: index for index, row in enumerate(existing)}
            for row in updated:
                sheet_row = known[row[0]] + 2  # +1 header, +1 for 1-based rows
                self.update_range(tab, f"A{sheet_row}", [row])
        return {
            "header": header,
            "appended": len(appended),
            "updated": len(updated),
            "unchanged": diff["unchanged"],
        }

    def append_log(self, tab: str, row: list[str]) -> None:
        self.ensure_tab(tab, LOG_HEADER)
        self.append_rows(tab, [row])


def _row_number(cell: str) -> int:
    digits = "".join(ch for ch in cell if ch.isdigit())
    return int(digits or 1)


def _column_letter(count: int) -> str:
    letters = ""
    while count > 0:
        count, remainder = divmod(count - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters or "A"
