"""Generate a credential-free configuration draft; never contacts Vapi.

Usage: python scripts/export_vapi.py --server-url HTTPS_URL --credential-id ID
           [--vertical restaurant|salon] [--output PATH]
Flags: --output defaults to .runtime/vapi-assistant.template.json.
Touches: services/api app.tools (TOOLS, AGENT_PROMPT).
Side effects: writes the draft JSON file only; also imported by setup_vapi.py.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))
from app.tools import TOOLS, AGENT_PROMPT

def assistant_config(server_url, credential_id, vertical="restaurant", timezone=None, transcript_retention_calls=0):
    """Build the Vapi assistant payload for a vertical; validates that server_url is a bare HTTPS origin."""
    from urllib.parse import urlsplit
    url = urlsplit(server_url)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ["", "/"]:
        raise ValueError("Use the public HTTPS base URL without a path, credentials or query.")
    restaurant = vertical == "restaurant"
    context = "Current UTC date/time at call start: {{now}}. Resolve relative dates in the timezone returned by get_business_info, never from training dates. "
    if timezone:
        from zoneinfo import ZoneInfo
        ZoneInfo(timezone)
        context = 'Business timezone: ' + timezone + '. Current business date/time at call start: {{"now" | date: "%A, %B %d, %Y, %I:%M %p", "' + timezone + '"}}. Resolve tomorrow from this business-local date. Refresh get_business_info first. '
    tools = [definition for definition in TOOLS if restaurant or definition["name"] not in ["get_wait_estimate", "check_order_capacity", "join_waitlist", "get_order_status"]]
    return {
        "name": "FrontDesk OS · Momentuum Bistro" if restaurant else "FrontDesk OS · Salon",
        "firstMessage": ("Hi, thanks for calling Momentuum Bistro. I'm your automated receptionist. " if restaurant else "Hi, I'm your salon's automated receptionist. ") + ("We save a transcript for service review; let me know if you'd prefer we don't. " if transcript_retention_calls else "") + "How can I help?",
        "server": {"url": server_url.rstrip("/") + "/api/v1/voice/vapi", "credentialId": credential_id, "timeoutSeconds": 20},
        "serverMessages": ["status-update", "transcript", "tool-calls", "end-of-call-report", "user-interrupted"],
        "artifactPlan": {"recordingEnabled": False, "videoRecordingEnabled": False, "pcapEnabled": False, "loggingEnabled": False, "transcriptPlan": {"enabled": False}},
        "maxDurationSeconds": 600,
        "backgroundSound": "off",
        "startSpeakingPlan": {
            "waitSeconds": 0.5,
            "transcriptionEndpointingPlan": {"onPunctuationSeconds": 0.7, "onNoPunctuationSeconds": 1.5, "onNumberSeconds": 1.8},
            # Phone numbers are commonly spoken as three separate groups.
            # This rule overrides punctuation detection during that question.
            "customEndpointingRules": [{"type": "assistant", "regex": "(?:phone|contact) number|number (?:should|can) (?:I|we)", "regexOptions": [{"type": "ignore-case", "enabled": True}], "timeoutSeconds": 2.0}],
        },
        "model": {"provider": "openai", "model": "gpt-4.1-mini", "temperature": 0.1, "messages": [{"role": "system", "content": context + f"This is a {vertical} business. " + AGENT_PROMPT}], "tools": [{"type": "function", "async": False, "messages": [{"type": "request-start", "content": ""}], "function": definition} for definition in tools]},
    }


def main():
    """CLI entry point: write the credential-free draft to --output."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--credential-id", required=True)
    parser.add_argument("--vertical", choices=["restaurant", "salon"], default="restaurant")
    parser.add_argument("--output", default=".runtime/vapi-assistant.template.json")
    args = parser.parse_args()
    try:
        draft = assistant_config(args.server_url, args.credential_id, args.vertical)
    except ValueError as error:
        parser.error(str(error))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(draft, indent=2) + "\n")
    print(f"Draft written to {output}. Select your voice and transcriber in Vapi before activation.")


if __name__ == "__main__":
    main()
