"""Configuration. Everything comes from the environment or a local .env file."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path | None = None) -> None:
    """Read KEY=VALUE lines into os.environ without overwriting real env vars."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
HUBSPOT_BASE = os.environ.get("HUBSPOT_BASE", "https://api.hubapi.com")
WEBHOOK_SECRET = os.environ.get("HUBSPOT_WEBHOOK_SECRET", "")

SHEET_ID = os.environ.get("SHEET_ID", "")
GOOGLE_SA_JSON = os.environ.get("GOOGLE_SA_JSON", str(ROOT.parent.parent / ".secrets" / "sheets-sa.json"))
SYNC_TAB = os.environ.get("SYNC_TAB", "hubspot_contacts")
LOG_TAB = os.environ.get("LOG_TAB", "sync_log")
EVENT_STORE = Path(os.environ.get("EVENT_STORE", ROOT / "data" / "seen_events.json"))

CONTACT_PROPERTIES = [
    "email",
    "firstname",
    "lastname",
    "company",
    "phone",
    "lifecyclestage",
    "lastmodifieddate",
]

SYNC_HEADER = [
    "email",
    "firstname",
    "lastname",
    "company",
    "phone",
    "lifecyclestage",
    "hubspot_id",
    "hubspot_lastmodified",
    "synced_at",
    "status",
]

LOG_HEADER = ["run_at", "run_id", "mode", "seen", "written", "updated", "unchanged", "collapsed", "errors"]

MAX_ATTEMPTS = 4
BASE_DELAY = 0.5
WEBHOOK_TOLERANCE_MS = 300_000
