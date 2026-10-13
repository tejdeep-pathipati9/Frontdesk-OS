"""Server-side polling worker for explicitly connected mock providers.

Usage: python scripts/poll_integrations.py [--once] [--interval SECONDS]
Flags: --once      run a single polling pass and exit (local verification)
       --interval  seconds between passes (default 60)
Touches: the configured application database via app.db.transaction.
Side effects: applies normalized provider records to the database.

Use --once for local verification. Live reads must fetch via authorized vendor
clients outside the write transaction and then apply normalized records inside it.
"""
import argparse
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))
from sqlalchemy import select
from app.db import transaction
from app.models import IntegrationConnection, Location
from app.integration_service import sync

parser = argparse.ArgumentParser()
parser.add_argument("--once", action="store_true")
parser.add_argument("--interval", type=int, default=60)
args = parser.parse_args()
if args.interval < 15:
    parser.error("Polling interval must be at least 15 seconds")
try:
    while True:
        with transaction() as db:
            for conn in db.scalars(select(IntegrationConnection).where(IntegrationConnection.mode == "mock", IntegrationConnection.status != "disconnected")):
                loc = db.get(Location, conn.location_id)
                if loc and loc.mode == "demo":
                    result = sync(db, loc, conn)
                    print(f"{conn.provider}: {result['status']}", flush=True)
        if args.once:
            break
        time.sleep(args.interval)
except KeyboardInterrupt:
    pass
