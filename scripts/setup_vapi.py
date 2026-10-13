"""Configure saved Vapi assistants using keys from the ignored local .env.

Does not place calls, purchase numbers, or print keys/provider payloads.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import httpx
from dotenv import dotenv_values, set_key
from export_vapi import assistant_config

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--vertical", choices=["restaurant", "salon", "both"], default="restaurant")
    parser.add_argument("--apply", action="store_true", help="Create/update the saved assistant and webhook credential")
    args = parser.parse_args()
    try:
        assistant_config(args.server_url, "credential-placeholder")
    except ValueError as error:
        parser.error(str(error))
    values = dotenv_values(ENV)
    required = ["VAPI_PUBLIC_KEY", "VAPI_PRIVATE_KEY", "VAPI_WEBHOOK_SECRET"]
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise SystemExit("Set these keys locally in .env: " + ", ".join(missing))
    if not args.apply:
        print("Local keys present. Use --apply to configure saved assistants.")
        return

    def save(key, value):
        set_key(str(ENV), key, value)
        ENV.chmod(0o600)
        values[key] = value

    with httpx.Client(base_url="https://api.vapi.ai", headers={"Authorization": f"Bearer {values['VAPI_PRIVATE_KEY']}"}, timeout=30) as client:
        def request(method, path, body=None):
            try:
                response = client.request(method, path, json=body)
            except httpx.HTTPError:
                raise SystemExit("Vapi request did not return a confirmed response. Check the dashboard before retrying; no automatic write retry was performed.")
            if not response.is_success:
                raise SystemExit(f"Vapi {method} {path.split('?')[0]} returned HTTP {response.status_code}. Provider payload omitted to protect secrets; inspect the dashboard for details.")
            return response.json()

        # Check account access before writing any resource.
        assistants = request("GET", "/assistant")
        if not isinstance(assistants, list):
            raise SystemExit("Unexpected assistant list response; no resources changed.")
        print("Private key verified with Vapi.", flush=True)
        credential_id = values.get("VAPI_CREDENTIAL_ID")
        credential_name = "FrontDesk OS webhook " + hashlib.sha256(values["VAPI_WEBHOOK_SECRET"].encode()).hexdigest()[:12]
        if not credential_id:
            credentials = request("GET", "/credential")
            matches = [c for c in credentials if c.get("name") == credential_name and c.get("provider") == "custom-credential"] if isinstance(credentials, list) else []
            if len(matches) > 1:
                raise SystemExit("Multiple matching webhook credentials. Set the intended VAPI_CREDENTIAL_ID in .env.")
            credential = matches[0] if matches else request("POST", "/credential", {"provider": "custom-credential", "name": credential_name, "authenticationPlan": {"type": "bearer", "token": values["VAPI_WEBHOOK_SECRET"], "headerName": "Authorization", "bearerPrefixEnabled": True}})
            credential_id = credential["id"]
            save("VAPI_CREDENTIAL_ID", credential_id)
        print("Saved webhook credential configured.", flush=True)

        for vertical in ["restaurant", "salon"] if args.vertical == "both" else [args.vertical]:
            key = f"VAPI_{vertical.upper()}_ASSISTANT_ID"
            from app.db import Session
            from app.models import Location
            from app.seed import RESTAURANT, SALON
            with Session() as db:
                location = db.get(Location, RESTAURANT if vertical == "restaurant" else SALON)
                timezone = location.timezone if location else None
                retention = int(location.settings.get('transcript_retention_calls', 0)) if location else 0
            payload = assistant_config(args.server_url, credential_id, vertical, timezone, retention)
            payload["voice"] = {"provider": "vapi", "voiceId": "Elliot", "version": "2"}
            payload["transcriber"] = {"provider": "deepgram", "model": "nova-3", "language": "en", "numerals": True}
            assistant_id = values.get(key)
            if not assistant_id:
                # Reconcile a previous create whose response was interrupted.
                matches = [a for a in assistants if a.get("name") == payload["name"] and a.get("server", {}).get("credentialId") == credential_id]
                if len(matches) > 1:
                    raise SystemExit(f"Multiple matching {vertical} assistants. Set {key} in .env.")
                assistant_id = matches[0]["id"] if matches else None
            if assistant_id:
                result = request("PATCH", f"/assistant/{assistant_id}", payload)
            else:
                result = request("POST", "/assistant", payload)
            assistant_id = result["id"]
            save(key, assistant_id)
            verified = request("GET", f"/assistant/{assistant_id}")
            server = verified.get("server", {})
            if server.get("url") != payload["server"]["url"] or server.get("credentialId") != credential_id:
                raise SystemExit("Assistant was saved but webhook verification failed. Inspect the Vapi dashboard.")
            model = verified.get("model", {})
            if model.get("messages") != payload["model"]["messages"] or model.get("model") != payload["model"]["model"]:
                raise SystemExit("Assistant was saved but prompt/model verification failed. Inspect the Vapi dashboard.")
            path = ROOT / ".runtime" / f"vapi-{vertical}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n")
            print(f"{vertical.capitalize()} assistant saved and verified: {assistant_id}", flush=True)
        save("VAPI_SERVER_URL", args.server_url.rstrip("/"))
        print("Assistant IDs saved to .env. Restart FastAPI, then open AI Receptionist → Vapi.", flush=True)


if __name__ == "__main__":
    main()
