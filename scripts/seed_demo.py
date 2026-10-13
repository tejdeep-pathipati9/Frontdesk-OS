"""Seed the local workspace with demo data.

Usage: python scripts/seed_demo.py   (no flags)
Touches: initialises the configured database (app.db.init_db) and runs app.seed.seed.
Side effects: inserts demo tenants/resources if absent; existing operations are preserved.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))
from app.db import init_db, transaction
from app.seed import seed

init_db()
with transaction() as db:
    seed(db)
print("Local workspace is ready. Existing operations were preserved.")
