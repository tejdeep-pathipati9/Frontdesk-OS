"""Webhook-only local origin for the temporary Vapi HTTPS tunnel."""
from pathlib import Path
import secrets
import httpx
from dotenv import dotenv_values
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
MAX_BODY = 2 * 1024 * 1024


@app.post("/api/v1/voice/vapi")
async def forward_webhook(request: Request):
    token = dotenv_values(ROOT / ".env").get("VAPI_WEBHOOK_SECRET") or ""
    supplied = request.headers.get("authorization", "")
    if not token or not secrets.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
        return JSONResponse({"code": "UNAUTHENTICATED_WEBHOOK"}, status_code=401)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            return JSONResponse({"code": "PAYLOAD_TOO_LARGE"}, status_code=413)
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            result = await client.post("http://127.0.0.1:8000/api/v1/voice/vapi", content=bytes(body), headers={"Authorization": supplied, "Content-Type": "application/json"})
    except httpx.HTTPError:
        return JSONResponse({"code": "LOCAL_API_UNREACHABLE"}, status_code=503)
    return Response(content=result.content, status_code=result.status_code, media_type="application/json", headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    import uvicorn
    if not dotenv_values(ROOT / ".env").get("VAPI_WEBHOOK_SECRET"):
        raise SystemExit("Configure VAPI_WEBHOOK_SECRET in .env first.")
    uvicorn.run(app, host="127.0.0.1", port=8010, access_log=False, timeout_graceful_shutdown=3)
