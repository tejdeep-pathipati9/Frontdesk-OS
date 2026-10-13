"""Download the Inter font files used by the web app.

Usage: python scripts/download_frontend_font.py   (run from the repo root; no flags)
Touches: writes inter-latin.woff2 and Inter-LICENSE.txt into apps/web/public/fonts/
(created if missing), fetched over HTTPS with httpx.
Side effects: network access and overwriting those two files.
"""
from pathlib import Path
import httpx

target = Path("apps/web/public/fonts")
target.mkdir(parents=True, exist_ok=True)
for url, name in [
    ("https://21st-crm.vercel.app/_next/static/immutable/media/83afe278b6a6bb3c-s.p.45535valc9rzk.woff2", "inter-latin.woff2"),
    ("https://raw.githubusercontent.com/rsms/inter/master/LICENSE.txt", "Inter-LICENSE.txt"),
]:
    response = httpx.get(url, follow_redirects=True, timeout=20)
    response.raise_for_status()
    (target / name).write_bytes(response.content)
    print(name, len(response.content), "bytes")
