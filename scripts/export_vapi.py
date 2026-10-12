"""Generate a credential-free configuration draft; never contacts Vapi."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))
from app.tools import TOOLS, AGENT_PROMPT

parser = argparse.ArgumentParser()
parser.add_argument("--server-url", required=True)
parser.add_argument("--credential-id", required=True)
parser.add_argument("--output", default="docs/vapi-assistant.template.json")
args = parser.parse_args()
if not args.server_url.startswith("https://"):
    parser.error("The Vapi server URL must be publicly reachable HTTPS")
draft = {
    "name": "FrontDesk OS restaurant (configure voice/model in Vapi)",
    "firstMessage": "Hello, I'm the automated front desk assistant. How can I help you today?",
    "server": {"url": args.server_url.rstrip("/") + "/api/v1/voice/vapi", "credentialId": args.credential_id},
    "serverMessages": ["status-update", "transcript", "tool-calls", "end-of-call-report"],
    "artifactPlan": {"recordingEnabled": False},
    "model": {"provider": "openai", "model": "gpt-4.1-mini", "messages": [{"role": "system", "content": AGENT_PROMPT}], "tools": [{"type": "function", "function": definition} for definition in TOOLS]},
}
Path(args.output).write_text(json.dumps(draft, indent=2) + "\n")
print(f"Draft written to {args.output}. Select your actual voice, STT and model in Vapi before activation.")
