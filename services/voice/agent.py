"""Optional LiveKit + Ollama agent. No independent business logic lives here.

Fit: a LiveKit worker joins a room dispatched by the FrontDesk session endpoint
(job metadata carries call_id). It fetches the call contract (prompt, tool
definitions, transcript notice) from the API using the agent bearer token, runs
local STT/LLM/TTS, forwards each tool call to the API's agent endpoint and mirrors
conversation turns to the transcript endpoint. Prices, capacity and confirmation
are decided server-side; this process only relays.
"""
import asyncio
import json
import logging
import os
from uuid import uuid4
from dotenv import load_dotenv
import httpx
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, function_tool
from livekit.agents.llm import ChatMessage
from livekit.plugins import openai, silero

load_dotenv(".env")
log = logging.getLogger("frontdesk.voice")
server = AgentServer()


class BusinessClient:
    """Authenticated HTTP client for one call's FrontDesk API endpoints.

    pending_events tracks in-flight transcript posts so tool calls and shutdown can wait for them.
    """
    def __init__(self, call_id):
        self.call_id = call_id
        self.http = httpx.AsyncClient(base_url=os.getenv("FRONTDESK_API_URL", "http://127.0.0.1:8000") + "/api/v1", headers={"Authorization": "Bearer " + os.environ["AGENT_API_TOKEN"]}, timeout=15)
        self.pending_events: list[asyncio.Task] = []
        self.event_lock = asyncio.Lock()

    async def event(self, event):
        """Post one transcript turn; serialized by a lock so turns arrive in order."""
        async with self.event_lock:
            response = await self.http.post(f"/voice/agent/calls/{self.call_id}/transcript", json=event)
            response.raise_for_status()

    async def tool(self, name, arguments):
        """Invoke a business tool via the API and return its JSON result.

        Retries once on timeout with the same request_id so the server can deduplicate; if still uncertain returns
        a 'pending' result directing staff follow-up rather than claiming success.
        """
        # Finish transcript processing before commit. Do not fabricate confirmation
        # from the model's tool arguments or from generated, unspoken readbacks.
        if self.pending_events:
            await asyncio.gather(*self.pending_events)
            self.pending_events.clear()
        # Idempotency: reuse this id across the retry so a timed-out request is not executed twice.
        request_id = str(uuid4())
        payload = {"request_id": request_id, "name": name, "arguments": arguments}
        for attempt in range(2):
            try:
                response = await self.http.post(f"/voice/agent/calls/{self.call_id}/tools", json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException:
                if attempt:
                    return {"status": "pending", "message": "Tool result is uncertain. Staff follow-up is required."}
                # Same request ID preserves idempotency after a transport timeout.
        return {"status": "failed"}


def make_tools(client, definitions):
    """Wrap the API-provided tool definitions as LiveKit function tools that proxy to BusinessClient.tool."""
    tools = []
    for definition in definitions:
        def bind(name):
            async def invoke(raw_arguments: dict):
                return json.dumps(await client.tool(name, raw_arguments))
            return invoke
        tools.append(function_tool(bind(definition["name"]), raw_schema={"name": definition["name"], "description": definition["description"], "parameters": definition["parameters"]}))
    return tools


@server.rtc_session(agent_name="frontdesk-local")
async def entrypoint(ctx: agents.JobContext):
    """Per-room job: load the call contract, start the local voice session and greet the caller.

    Raises if the dispatch lacks a call_id or the call is no longer in_progress. On shutdown flushes pending
    transcript posts, then ends the call via the API.
    """
    metadata = json.loads(ctx.job.metadata or "{}")
    call_id = metadata.get("call_id")
    if not call_id:
        raise RuntimeError("Dispatch must originate from the FrontDesk session endpoint")
    client = BusinessClient(call_id)
    response = await client.http.get(f"/voice/agent/calls/{call_id}")
    response.raise_for_status()
    contract = response.json()
    if contract["status"] != "in_progress":
        raise RuntimeError("Call is no longer active")
    llm = openai.LLM.with_ollama(model=os.getenv("OLLAMA_MODEL", "qwen3.5:9b"), base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/") + "/v1", temperature=0.1)
    session = AgentSession(
        llm=llm,
        stt=openai.STT(model=os.getenv("LOCAL_STT_MODEL", "whisper-1"), base_url=os.getenv("LOCAL_STT_BASE_URL", "http://127.0.0.1:8001/v1"), api_key=os.getenv("LOCAL_SPEECH_API_KEY", "local")),
        tts=openai.TTS(model=os.getenv("LOCAL_TTS_MODEL", "kokoro"), voice=os.getenv("LOCAL_TTS_VOICE", "af_heart"), base_url=os.getenv("LOCAL_TTS_BASE_URL", "http://127.0.0.1:8880/v1"), api_key=os.getenv("LOCAL_SPEECH_API_KEY", "local")),
        vad=silero.VAD.load(),
    )

    @session.on("conversation_item_added")
    def conversation(event):
        if isinstance(event.item, ChatMessage) and event.item.role in ["user", "assistant"]:
            payload = {"event_id": event.item.id, "role": event.item.role, "text": event.item.text_content or "", "interrupted": bool(event.item.interrupted)}
            # Post asynchronously but track the task so tool calls can wait for it.
            task = asyncio.create_task(client.event(payload))
            client.pending_events.append(task)

    async def shutdown():
        if client.pending_events:
            await asyncio.gather(*client.pending_events, return_exceptions=True)
        await client.http.post(f"/voice/agent/calls/{call_id}/end")
        await client.http.aclose()
    ctx.add_shutdown_callback(shutdown)
    await ctx.connect()
    await session.start(room=ctx.room, agent=Agent(instructions=contract["prompt"], tools=make_tools(client, contract["tools"])))
    await session.generate_reply(instructions="Introduce yourself as the business's automated assistant. " + contract.get('transcript_notice', '') + " Use get_business_info to check business context before answering questions.")


if __name__ == "__main__":
    agents.cli.run_app(server)
