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
