# hubspot-sheets-sync

A HubSpot CRM → Google Sheets sync with three things most syncs skip: **idempotent upserts**,
**signed webhooks**, and a **run log** you can audit. Built against a real HubSpot developer
portal and a real Google Sheet, not mocks.

**Verified live on 2026-10-31:** 5 contacts pulled from HubSpot and written to a Google Sheet,
a second run changed **0 rows** (5 unchanged), and the webhook receiver accepted a correctly
signed event while rejecting unsigned, tampered and stale ones.

## What was run and what was not

**Run on this machine, live against HubSpot and Google:**

- `make probe` → the token's real scope set: contacts read **200**, companies read **200**,
  contact schemas read **200**, deals read **403**, owners read **403**. That is the
  least-privilege claim with a status code behind each line.
- `make sync` four times against the live portal and the live sheet:
  - run 1: 5 contacts seen, **5 written**, 0 unchanged, 1843 ms
  - run 2: 0 written, **5 updated** — a real idempotency bug, see below
  - run 3 after the fix: 0 written, 0 updated, **5 unchanged**, 1119 ms
  - run 4: 0 written, 0 updated, **5 unchanged**, 1000 ms
- Webhook receiver, live on `127.0.0.1:8090`, five checks, all as expected:
  unsigned → **401**, tampered body → **401**, stale timestamp → **401**, valid signature →
  **200** with 2 events `synced`, the same events replayed → **200** with 2 `duplicate`.
- `pytest`: **47 tests, all pass** in 0.09 s, fully offline.

**Not run:**

- **No live HubSpot webhook delivery.** HubSpot needs a public HTTPS URL to deliver to.
  The signature rules are proven against signatures computed with the documented algorithm
  and through the running receiver, but no request originated at HubSpot.
- The webhook secret is a **local random string**, because a Private App has no app client
  secret. A public app would use its client secret instead; the verification code is the same.
- No deletions: the sync upserts and never deletes a row. A contact deleted in HubSpot stays
  in the sheet, marked by its last data change. Deletion is a listed next step.

## The bug the live run caught

The first version compared whole rows, including the `synced_at` timestamp. Every run therefore
saw a "change" and rewrote all 5 rows — a sync that looks idempotent in a mocked test and is not
in production. The fix compares only the source-data columns (`email` … `hubspot_lastmodified`);
`synced_at` and `status` are bookkeeping. Two regression tests now cover it. The run log shows the
before and after, because the log is never rewritten.

## How it works

```
HubSpot CRM API ──(paged read, 100/page)──► map to rows ──► dedupe on email
                                                             │
                                                     diff against the sheet
                                                             │
                                       append new ── update changed ── leave the rest
                                                             │
                                                     run log row
```

Webhook path: `POST /webhooks/hubspot` → verify signature and timestamp → parse events →
skip event ids already handled → sync that one contact → log.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/webhooks/hubspot` | HubSpot event receiver. 401 on a bad signature, 200 on a good one. |
| `POST` | `/sync` | Manual full sync, returns the run summary. |
| `GET` | `/health` | Configuration present: token, sheet, webhook secret. |

### The sheet

Two tabs, created on first run:

- `hubspot_contacts` — `email, firstname, lastname, company, phone, lifecyclestage,
  hubspot_id, hubspot_lastmodified, synced_at, status`. Keyed on email.
- `sync_log` — one row per run: `run_at, run_id, mode, seen, written, updated, unchanged,
  collapsed, errors`.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env      # fill HUBSPOT_TOKEN, HUBSPOT_WEBHOOK_SECRET, SHEET_ID, GOOGLE_SA_JSON
```

1. **HubSpot.** Create a Private App in the developer portal, and give it only
   `crm.objects.contacts.read` (plus `crm.objects.companies.read` if you map companies). Copy
   the access token into `HUBSPOT_TOKEN`.
2. **Google.** Create a service account, download the JSON key, and **share the target sheet
   with the service account email as Editor**. A service account cannot create a spreadsheet —
   Google gives it no Drive quota — so the sheet must exist and be shared first.
3. **Secret.** Any long random string in `HUBSPOT_WEBHOOK_SECRET`.

## Commands

| Command | What it does |
|---|---|
| `make probe` | test which HubSpot scopes the token really has |
| `make sync` | full sync: pull every contact, upsert the sheet, write a log row |
| `make serve` | webhook receiver on port 8090 |
| `make test` | 47 offline tests |
| `make sign BODY='[...]' TS=1761900000000` | print a valid signature for a test payload |

Live check of the receiver, once `make serve` is running:

```bash
python scripts/webhook_live_check.py http://127.0.0.1:8090
```

It prints a JSON report: forged, tampered, stale, valid, replayed. Exit code 0 means all five
behaved as designed.

## Layout

```
app/
  config.py     environment, column plan, comparison width
  http.py       retries with backoff, Retry-After, idempotency keys
  hubspot.py    paged contact reads, email search, scope probe
  mapping.py    row mapping, dedupe on email, row diff
  sheets.py     tabs, read, idempotent upsert, run log
  sync.py       the sync engine, single-contact path for webhooks
  webhooks.py   signature verification, event parsing, event store
  server.py     stdlib HTTP surface
  cli.py        probe / sync / serve / sign
scripts/
  webhook_live_check.py
tests/          47 tests, offline, no credentials needed
```

## Design notes

- **Idempotency is the product.** A sync you cannot run twice safely is a liability. The key is
  the email; the diff compares data columns only; a re-run writes nothing.
- **Retries are bounded and logged.** 429, 5xx and connection errors retry with exponential
  backoff, `Retry-After` is honoured, and a non-idempotent POST reuses one `Idempotency-Key`.
- **Webhooks fail closed.** No secret, no signature, a stale timestamp or a mismatched digest
  all return 401 with a reason that never contains the secret.
- **The log is the audit trail.** Every run, including webhook-driven ones, appends one row.
- **Least privilege is measured, not claimed.** `make probe` records a status code per scope.

## Limits

- Deletion is not synced. A contact removed in HubSpot keeps its sheet row.
- No company or deal objects yet; contacts only.
- The event store is a local JSON file, capped at 5,000 ids. A multi-instance deployment needs
  a shared store (Postgres or Redis) or replays will double-process.
- Full sync reads every contact; the incremental path is a `lastmodifieddate` filter in
  `HubSpot.iter_contacts`, not yet wired to the CLI.
