"""Read-only public design-reference inspection. Does not operate a browser.

Usage: python scripts/inspect_reference.py   (no flags)
Touches: reads .runtime/crm-reference.html, fetches the referenced public CSS over
HTTPS from the reference site and prints structural hints.
Side effects: writes the downloaded CSS files into .runtime/.
"""
from html.parser import HTMLParser
from pathlib import Path
import re
import httpx

ROOT = "https://21st-crm.vercel.app"
HTML = Path(".runtime/crm-reference.html").read_text()
class Reference(HTMLParser):
    def handle_starttag(self, tag, attributes):
        data = dict(attributes)
        if data.get("data-slot") or tag in ["aside", "h1", "h2", "thead", "th", "td"]:
            print(tag, data)
Reference().feed(HTML)
for path in re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', HTML):
    response = httpx.get(ROOT + path, timeout=20)
    response.raise_for_status()
    css = response.text
    Path(".runtime/" + path.rsplit("/", 1)[-1]).write_text(css)
    for block in re.findall(r"[^{}]*(?:font-face|:root)[^{}]*\{[^}]+\}", css):
        print(block[:12000])
