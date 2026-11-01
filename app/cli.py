"""Command line: probe scopes, run the sync, serve webhooks, sign a test payload."""
from __future__ import annotations

import argparse
import json
import sys

from .config import EVENT_STORE, GOOGLE_SA_JSON, HUBSPOT_TOKEN, SHEET_ID, SYNC_TAB, WEBHOOK_SECRET
from .http import Transport
from .hubspot import HubSpot
from .sheets import Sheets, service_account_token_provider
from .sync import run_sync
from .webhooks import EventStore, compute_signature


def _hubspot() -> HubSpot:
    if not HUBSPOT_TOKEN:
        raise SystemExit("HUBSPOT_TOKEN is not set (see .env.example)")
    return HubSpot(HUBSPOT_TOKEN, transport=Transport())


def _sheets() -> Sheets:
    if not SHEET_ID:
        raise SystemExit("SHEET_ID is not set. Share the sheet with the service account first.")
    return Sheets(SHEET_ID, service_account_token_provider(GOOGLE_SA_JSON))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hubspot-sheets-sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("probe", help="test which HubSpot scopes the token actually has")
    sub.add_parser("sync", help="pull every contact and upsert the sheet")
    serve_parser = sub.add_parser("serve", help="run the webhook receiver")
    serve_parser.add_argument("--port", type=int, default=8090)

    sign_parser = sub.add_parser("sign", help="print a valid signature for a payload")
    sign_parser.add_argument("--body", required=True)
    sign_parser.add_argument("--timestamp", required=True)
    sign_parser.add_argument("--uri", default="/webhooks/hubspot")

    args = parser.parse_args(argv)

    if args.command == "probe":
        results = _hubspot().probe()
        print(json.dumps(results, indent=2))
        return 0

    if args.command == "sync":
        result = run_sync(_hubspot(), _sheets())
        print(json.dumps(result.summary(), indent=2))
        return 0 if result.errors == 0 else 1

    if args.command == "serve":
        from .server import WebhookApp, serve

        if not WEBHOOK_SECRET:
            print("warning: HUBSPOT_WEBHOOK_SECRET is empty; every webhook will be rejected", file=sys.stderr)
        app = WebhookApp(
            secret=WEBHOOK_SECRET,
            hubspot=_hubspot(),
            sheets=_sheets(),
            events=EventStore(EVENT_STORE),
        )
        server = serve(args.port, app)
        print(f"listening on http://127.0.0.1:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        return 0

    if args.command == "sign":
        print(compute_signature(WEBHOOK_SECRET, "POST", args.uri, args.body.encode(), args.timestamp))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
