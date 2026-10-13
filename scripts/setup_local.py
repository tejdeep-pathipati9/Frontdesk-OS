"""Prepare the local SQLite workspace without resetting records or voice keys."""
import argparse
import os
import secrets
import sys
from pathlib import Path
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-login", action="store_true", help="Show the local owner's credentials")
    args = parser.parse_args()
    os.chdir(ROOT)
    env = ROOT / ".env"
    env.touch(exist_ok=True)
    values = dotenv_values(env)
    url = values.get("DATABASE_URL") or "sqlite:///./frontdesk.db"
    if not url.startswith("sqlite:"):
        raise SystemExit("Local setup requires SQLite. Preserve your existing database before changing DATABASE_URL.")
    updates = {"DATABASE_URL": url, "DEMO_MODE": "true", "ALLOW_DEMO_LOGIN": "false", "RESTAURANT_NAME": values.get("RESTAURANT_NAME") or "Momentuum Bistro", "OWNER_EMAIL": values.get("OWNER_EMAIL") if values.get("OWNER_EMAIL") not in [None, "", "owner@frontdesk.local"] else "owner@momentuumbistro.local"}
    if not values.get("OWNER_PASSWORD"):
        updates["OWNER_PASSWORD"] = "Momentuum!" + secrets.token_urlsafe(9)
    if not values.get("SESSION_SECRET") or values["SESSION_SECRET"].startswith("local-demo"):
        updates["SESSION_SECRET"] = secrets.token_urlsafe(48)
    for key, value in updates.items():
        set_key(str(env), key, value)
    env.chmod(0o600)
    sys.path.insert(0, str(ROOT / "services/api"))
    from app.db import init_db, transaction
    from app.seed import seed
    init_db()
    with transaction() as db:
        seed(db)
    print("Momentuum Bistro · local SQLite workspace ready. Existing records preserved.")
    if args.show_login:
        current = dotenv_values(env)
        print("Email:", current["OWNER_EMAIL"])
        print("Password:", current["OWNER_PASSWORD"])


if __name__ == "__main__":
    main()
