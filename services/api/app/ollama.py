"""An optional real Ollama text experiment for testing shared tools without audio.

Chat context is transient process memory, expires after 30 minutes and is removed
when a session ends. It is never presented as a LiveKit or Vapi voice call.

Fit: powers the dashboard's text chat. Ollama only proposes tool calls; every tool
runs through tools.execute_tool (the same path as Vapi/LiveKit), so FastAPI remains
the sole business authority. A prepare-style tool returns an authoritative readback
which ends the turn; the caller's next message is what confirms it.
"""
import threading
from dataclasses import dataclass, field
from datetime import timedelta
import httpx
from .config import settings
from .models import Call, Location, now
from .db import Session, transaction
from .domain import DomainError, observe_transcript, emit
from .tools import TOOLS, AGENT_PROMPT, execute_tool


@dataclass
class Chat:
    """Per-call in-memory conversation: message history, a non-blocking turn lock and a last-used time for expiry."""
    messages: list = field(default_factory=lambda: [{"role": "system", "content": AGENT_PROMPT + " This is a text-only local experiment. Never claim an audio call happened."}])
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_used: object = field(default_factory=now)


chats: dict[str, Chat] = {}


def run_chat(call_id, text, factory=Session):
    """Run one user turn of the local text experiment and return {text, tool_results, provider, model}.

    Args: call_id of an in-progress 'ollama_text' call; text the user message; factory a DB session factory.
    Raises DomainError for concurrent turns (409), session length over 50 messages, ended calls, or an
    unusable Ollama response (503). Side effects: records transcript turns and executes business tools in
    their own transactions. Idle chats older than 30 minutes are evicted on entry.
    """
    for id_, chat in list(chats.items()):
        if chat.last_used < now() - timedelta(minutes=30):
            chats.pop(id_, None)
    chat = chats.setdefault(call_id, Chat())
    if not chat.lock.acquire(blocking=False):
        raise DomainError("TURN_IN_PROGRESS", "Wait for the current turn to finish", 409)
    try:
        chat.last_used = now()
        if len(chat.messages) > 50:
            raise DomainError("SESSION_LIMIT", "End this experiment and start a fresh session", 409)
        with transaction(factory) as db:
            call = db.get(Call, call_id)
            if not call or call.status != "in_progress" or call.provider != "ollama_text":
                raise DomainError("CALL_ENDED", "Local experiment is no longer active")
            observe_transcript(db, call, "user", text)
        chat.messages.append({"role": "user", "content": text})
        tool_results = []
        # Bounded loop prevents runaway local generations/tool requests.
        for _ in range(5):
            try:
                response = httpx.post(settings().ollama_base_url.rstrip("/") + "/api/chat", json={"model": settings().ollama_model, "messages": chat.messages, "tools": [{"type": "function", "function": t} for t in TOOLS], "stream": False, "think": False, "options": {"temperature": 0.1, "num_predict": 700, "num_ctx": 8192}}, timeout=90)
                response.raise_for_status()
                message = response.json()["message"]
            except (httpx.HTTPError, ValueError, KeyError):
                raise DomainError("OLLAMA_UNAVAILABLE", "Ollama did not return a usable response. Check the service and installed model", 503)
            chat.messages.append(message)
            if not message.get("tool_calls"):
                content = message.get("content", "")[:4000]
                with transaction(factory) as db:
                    observe_transcript(db, db.get(Call, call_id), "assistant", content)
                return {"text": content or "Please try asking a specific business question.", "tool_results": tool_results, "provider": "ollama_text", "model": settings().ollama_model}
            for index, requested in enumerate(message["tool_calls"][:8]):
                function = requested.get("function", {})
                # Before a commit runs, let the confirmation interpreter evaluate the caller's latest reply (never model-supplied text).
                if function.get("name") == "commit_action":
                    from .confirmation_intent import resolve
                    resolve(call_id, factory)
                with transaction(factory) as db:
                    call = db.get(Call, call_id)
                    result = execute_tool(db, call, function.get("name", ""), function.get("arguments", {}), requested.get("id") or f"ollama-{len(chat.messages)}-{index}")
                tool_results.append({"name": function.get("name"), "result": result})
                import json
                chat.messages.append({"role": "tool", "tool_name": function.get("name"), "content": json.dumps(result)})
                if result.get("readback"):
                    # Emit the exact authoritative readback and stop this turn.
                    # Confirmation on the next text turn binds to this proposal.
                    content = result["readback"]
                    chat.messages.append({"role": "assistant", "content": content})
                    with transaction(factory) as db:
                        observe_transcript(db, db.get(Call, call_id), "assistant", content)
                    return {"text": content, "tool_results": tool_results, "provider": "ollama_text", "model": settings().ollama_model}
        return {"text": "I couldn't finish this request. Please ask staff for assistance.", "tool_results": tool_results, "provider": "ollama_text", "model": settings().ollama_model}
    finally:
        chat.lock.release()
