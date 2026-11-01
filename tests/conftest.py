"""Offline fakes: an HTTP transport queue and a stateful Google Sheets transport."""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field

import pytest

from app.http import Response
from app.sheets import Sheets


# --------------------------------------------------------------------- helpers
@dataclass
class FakeRawResponse:
    status_code: int
    payload: dict | None = None
    headers: dict = field(default_factory=dict)
    text: str = ""

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


class FakeSession:
    """Stands in for requests.Session. Returns queued responses in order."""

    def __init__(self, outcomes: list):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "json": json})
        outcome = self.outcomes.pop(0) if self.outcomes else FakeRawResponse(200, {})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def contact(contact_id: str, email: str, firstname: str = "", lastname: str = "",
            company: str = "", modified: str = "2026-10-01T10:00:00Z") -> dict:
    return {
        "id": contact_id,
        "properties": {
            "email": email,
            "firstname": firstname,
            "lastname": lastname,
            "company": company,
            "phone": "",
            "lifecyclestage": "lead",
            "lastmodifieddate": modified,
        },
    }


# ------------------------------------------------------------ sheets fake state
CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _parse_range(tab_and_range: str) -> tuple[str, str, str, int, int]:
    decoded = urllib.parse.unquote(tab_and_range)
    tab, _, cells = decoded.partition("!")
    tab = tab.strip("'")
    if not cells:
        cells = "A1"
    start, _, end = cells.partition(":")
    return tab, start, end or start, 0, 0


def _split_cell(cell: str) -> tuple[int, int]:
    match = CELL_RE.match(cell.upper())
    if not match:
        return 1, 1
    letters, digits = match.groups()
    column = 0
    for char in letters:
        column = column * 26 + (ord(char) - 64)
    return int(digits), column


class FakeSheetsTransport:
    """A small in-memory model of the Sheets v4 endpoints this project uses."""

    def __init__(self, existing: dict[str, list[list[str]]] | None = None):
        self.tabs: dict[str, list[list[str]]] = existing or {}
        self.calls: list[dict] = []

    # ---- transport interface
    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json})
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        if path.endswith(":batchUpdate"):
            for request in (json or {}).get("requests", []):
                title = request["addSheet"]["properties"]["title"]
                self.tabs.setdefault(title, [])
            return Response(status=200, body={"replies": [{}]})

        if "/values/" in path:
            raw_range = path.split("/values/", 1)[1]
            tab, start, end, _, _ = _parse_range(raw_range)
            if method == "GET":
                if tab not in self.tabs:
                    return Response(status=400, body={"error": "no tab"})
                return Response(status=200, body={"values": self.tabs[tab]})
            if method == "PUT":
                rows = (json or {}).get("values", [])
                self._write(tab, start, rows)
                return Response(status=200, body={"updatedCells": sum(len(r) for r in rows)})
            if method == "POST":
                rows = (json or {}).get("values", [])
                self.tabs.setdefault(tab, []).extend(rows)
                return Response(status=200, body={"updates": {"updatedRows": len(rows)}})

        if "fields=sheets.properties.title" in url:
            return Response(
                status=200,
                body={"sheets": [{"properties": {"title": title}} for title in self.tabs]},
            )
        return Response(status=404, body={"error": "unexpected call"})

    # ---- range writing
    def _write(self, tab: str, start_cell: str, rows: list[list[str]]) -> None:
        first_row, first_column = _split_cell(start_cell)
        sheet = self.tabs.setdefault(tab, [])
        while len(sheet) < first_row + len(rows) - 1:
            sheet.append([])
        for offset, row in enumerate(rows):
            target = sheet[first_row - 1 + offset]
            while len(target) < first_column - 1 + len(row):
                target.append("")
            for column, value in enumerate(row):
                target[first_column - 1 + column] = value


@pytest.fixture
def sheets_transport() -> FakeSheetsTransport:
    return FakeSheetsTransport()


@pytest.fixture
def sheets(sheets_transport) -> Sheets:
    return Sheets("test-sheet-id", token_provider=lambda: "fake-token", transport=sheets_transport)


@pytest.fixture
def hubspot_contacts() -> list[dict]:
    return [
        contact("1001", "maria@example.com", "Maria", "Lopez", "Acme Fleet", "2026-10-01T09:00:00Z"),
        contact("1002", "brian@example.com", "Brian", "Chen", "Northline", "2026-10-02T09:00:00Z"),
        contact("1003", "dupe@example.com", "Old", "Duplicate", "Acme", "2026-09-01T09:00:00Z"),
        contact("1004", "dupe@example.com", "New", "Duplicate", "Acme", "2026-10-03T09:00:00Z"),
        contact("1005", "", "No", "Email", "Acme", "2026-10-04T09:00:00Z"),
    ]


def dump(payload) -> str:
    return json.dumps(payload)
