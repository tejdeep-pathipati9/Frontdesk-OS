"use client";
// Voice lab for exercising Vapi, LiveKit, Ollama text chat and raw tool endpoints.
import { useEffect, useRef, useState } from "react";
import { Mic, PhoneOff, ArrowRight, Check, Loader2, AudioLines, Volume2 } from "lucide-react";
import { Button } from "./ui/button";
import { Badge, ErrorText } from "./workflows";
import { api, mutation, localInput, localToISO } from "@/lib/api";
import type VapiClient from "@vapi-ai/web";
import { CallPlayback, startVoiceClient, stopVoiceClient, waitForVoiceTeardown } from "@/lib/voice-playback";
import type { Snapshot, VoiceConfig, Proposal } from "@/lib/types";

export type VoiceMode = "vapi" | "livekit" | "ollama" | "tools";

/** Tabbed testing surface for voice providers and tool endpoints. */
export default function VoiceLab({ data, refresh, initialMode }: { data: Snapshot; refresh: () => void; initialMode?: VoiceMode }) {
  const active = useRef(false);
  const operation = useRef(false);
  const playback = useRef<CallPlayback | null>(null);
  const [ending, setEnding] = useState(false);
  const [audioBlocked, setAudioBlocked] = useState(false);
  const connectionId = useRef<string | null>(null);
  const [config, setConfig] = useState<VoiceConfig | null>(null);
  const mode = initialMode || data.location.settings.preferred_voice_provider || "vapi";
  const [connected, setConnected] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const [messages, setMessages] = useState<{ role: string; text: string }[]>([]);
  const [testCall, setTestCall] = useState<string | null>(null); const testRef = useRef<string | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [name, setName] = useState("Guest"); const [party, setParty] = useState(4);
  const [when, setWhen] = useState(localInput(new Date(Date.now() + 3600000), data.location.timezone));
  const [service, setService] = useState(data.services[0]?.id || "");
  const [staff, setStaff] = useState("");
  const [selectedItems, setSelectedItems] = useState<string[]>([]);
  const vapi = useRef<VapiClient | null>(null); const room = useRef<{ disconnect: () => void } | null>(null); const audioContainer = useRef<HTMLDivElement>(null);
  useEffect(() => { active.current = true; api<VoiceConfig>(`/voice/config?location_id=${data.location.id}`).then(value => { if (active.current) setConfig(value); }).catch(e => { if (active.current) setError(e.message); }); return () => { active.current = false; const client = vapi.current; vapi.current = null; playback.current?.dispose(); playback.current = null; if (client) void stopVoiceClient(client).finally(() => client.removeAllListeners()).catch(() => {}); room.current?.disconnect(); if (testRef.current) fetch(`/api/v1/voice/test-calls/${testRef.current}/end`, { method: "POST", keepalive: true }); }; }, [data.location.id]);
  useEffect(() => {
    if (!connected || mode !== "vapi") return;
    let cancelled = false;
    let checking = false;
    const client = vapi.current;
    const interval = setInterval(async () => {
      if (checking) return;
      checking = true;
      try {
        const status = await mutation<{ connection_id: string }>("/voice/vapi/readiness", { location_id: data.location.id });
        if (status.connection_id !== connectionId.current) throw new Error("Voice connection changed");
      }
      catch {
        if (!cancelled && active.current && vapi.current === client) {
          setError("Voice connection was interrupted. Reconnect the service and start a new call.");
          log("system", "Call stopped because the business connection was lost.");
          if (client) await endVapi(client);
        }
      } finally { checking = false; }
    }, 15000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [connected, mode, data.location.id]);
  function log(role: string, text: string) { setMessages(m => [...m.slice(-39), { role, text }]); }
  async function endVapi(client: VapiClient) {
    if (vapi.current !== client) return;
    vapi.current = null;
    operation.current = true;
    playback.current?.dispose(); playback.current = null;
    if (active.current) { setEnding(true); setBusy(true); setConnected(false); setAudioBlocked(false); }
    try {
      await stopVoiceClient(client);
      if (active.current) { refresh(); log("system", "Call ended. Verified actions appear in Calls."); }
    } catch {
      if (active.current) setError("Call cleanup failed. Reload this page before starting another call.");
    } finally {
      client.removeAllListeners();
      operation.current = false;
      if (active.current) { setEnding(false); setBusy(false); }
    }
  }
  async function startVoice() {
    if (operation.current || vapi.current) return;
    operation.current = true;
    setError(""); setAudioBlocked(false); setMessages([]); setBusy(true);
    try {
      if (mode === "vapi") {
        await waitForVoiceTeardown();
        const readiness = await mutation<{ connection_id: string }>("/voice/vapi/readiness", { location_id: data.location.id });
        connectionId.current = readiness.connection_id;
        if (!active.current) return;
        const { default: Vapi } = await import("@vapi-ai/web");
        if (!active.current) return;
        const client = new Vapi(config!.vapi.public_key);
        vapi.current = client;
        const current = () => active.current && vapi.current === client;
        const speaker = new CallPlayback(value => { if (current()) setAudioBlocked(value); });
        playback.current = speaker;
        client.setVolume(1);
        client.on("audio", player => { if (current()) speaker.attach(player); });
        client.on("daily-participant-updated", participant => {
          if (!current() || participant.local || participant.user_name !== "Vapi Speaker") return;
          const track = participant.tracks.audio.persistentTrack || participant.tracks.audio.track;
          if (track) speaker.rememberTrack(track);
        });
        client.on("call-start", () => { if (current()) { setConnected(true); log("system", "Vapi web call connected. Your microphone is live."); } });
        client.on("call-end", () => { if (current()) void endVapi(client); });
        client.on("message", message => { if (current() && message.type === "transcript" && message.transcriptType === "final") log(message.role, message.transcript); });
        client.on("error", error => {
          if (!current()) return;
          const detail = error as { type?: string; error?: { message?: string } };
          if (detail.type === "audio-start-failed") { setAudioBlocked(true); return; }
          if (detail.type?.startsWith("audio-processing-") || detail.type === "local-audio-level-observer-error") return;
          setError(error instanceof Error ? error.message : detail.error?.message || "Vapi call failed. Check assistant and browser permissions.");
          void endVapi(client);
        });
        const started = await startVoiceClient(client, () => client.start(config!.vapi.assistant_id));
        if (!current()) await stopVoiceClient(client);
        else if (!started) { setError("The call could not start. Please try again."); await endVapi(client); }
      } else {
        const { Room, RoomEvent, Track } = await import("livekit-client");
        if (!active.current) return;
        const session = await mutation<{ token: string; url: string }>("/voice/livekit/session", { location_id: data.location.id });
        const client = new Room(); room.current = client;
        client.on(RoomEvent.TrackSubscribed, track => { if (track.kind === Track.Kind.Audio) { const element = track.attach(); element.autoplay = true; audioContainer.current?.appendChild(element); } });
        client.on(RoomEvent.Disconnected, () => { setConnected(false); refresh(); });
        await client.connect(session.url, session.token);
        if (!active.current) { await client.disconnect(); return; }
        await client.localParticipant.setMicrophoneEnabled(true);
        await client.startAudio();
        setConnected(true); log("system", "LiveKit room connected. The local agent worker must be running to answer.");
      }
    } catch (e) { if (active.current) { setError((e as Error).message); setConnected(false); } if (vapi.current) await endVapi(vapi.current); }
    finally { operation.current = false; if (active.current) setBusy(false); }
  }
  async function stopVoice() {
    if (vapi.current) { await endVapi(vapi.current); return; }
    setBusy(true); setEnding(true);
    try { await room.current?.disconnect(); room.current = null; audioContainer.current?.replaceChildren(); setConnected(false); refresh(); }
    finally { setBusy(false); setEnding(false); }
  }
  async function ensureCall() {
    if (testCall) return testCall;
    const call = await mutation<{ id: string }>("/voice/test-calls", { location_id: data.location.id });
    if (!active.current) { await mutation(`/voice/test-calls/${call.id}/end`); throw new Error("Test session closed."); }
    setTestCall(call.id); testRef.current = call.id; return call.id;
  }
  async function tool(name: string, args: Record<string, unknown>) {
    const call = await ensureCall(); log("tool", `${name} → ${JSON.stringify(args)}`);
    const result = await mutation<Record<string, unknown>>(`/voice/test-calls/${call}/tools`, { request_id: crypto.randomUUID(), name, arguments: args });
    log("result", JSON.stringify(result, null, 2));
    if (result.status === "failed") throw new Error(String(result.message));
    refresh(); return result;
  }
  async function run(name: string, args: Record<string, unknown>) { setError(""); setBusy(true); try { return await tool(name, args); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  const bookingArgs = () => ({ start_at: localToISO(when, data.location.timezone), ...(data.location.vertical === "restaurant" ? { party_size: party } : { service_id: service, ...(staff ? { staff_id: staff } : {}) }) });
  async function prepareBooking() { setError(""); setBusy(true); try { const result = await tool("prepare_action", { kind: data.location.vertical === "restaurant" ? "reservation" : "appointment", name, ...bookingArgs() }); setProposal(result as unknown as Proposal); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function prepareOrder() { const result = await run("prepare_action", { kind: "order", name, items: selectedItems.map(id => ({ item_id: id, quantity: 1, modifiers: [] })) }); if (result) setProposal(result as unknown as Proposal); }
  async function confirm() { setError(""); setBusy(true); try { await mutation(`/voice/test-calls/${testCall}/confirm`, { action_id: proposal!.action_id }); await tool("commit_action", { action_id: proposal!.action_id }); setProposal(null); } catch (e) { setError((e as Error).message); setProposal(null); } finally { setBusy(false); } }
  async function endTest() { if (testCall) { await mutation(`/voice/test-calls/${testCall}/end`); setTestCall(null); testRef.current = null; setProposal(null); refresh(); log("system", "Action diagnostics complete."); } }
  const voiceMode = mode === "vapi" || mode === "livekit";
  const configured = mode === "vapi" ? config?.vapi.configured : config?.livekit.configured;
  return <div className={`voice-session voice-mode-${mode}`}>
    {voiceMode ? <section className="voice-session-controls"><div><strong>{data.location.name}</strong><p>{mode === "vapi" ? "Vapi" : "LiveKit + Ollama"} · {connected ? "Microphone in use" : "Microphone access required"}</p></div><Button disabled={busy || ending || (!connected && !configured)} onClick={connected ? stopVoice : startVoice}>{busy ? <Loader2 size={15} className="spin" /> : connected ? <PhoneOff size={15} /> : <Mic size={15} />}{ending ? "Ending…" : busy ? "Connecting…" : connected ? "End call" : "Start call"}</Button>{audioBlocked && mode === "vapi" && <div className="voice-session-note"><span>Audio playback is paused by your browser.</span><Button variant="outline" size="sm" onClick={() => { void playback.current?.resume(); }}><Volume2 size={15} />Enable audio</Button></div>}{config && !configured && <p className="voice-session-note">This voice provider requires setup before you can start a call.</p>}<ErrorText error={error} /></section> : <div className="panel voice-controls"><div className="panel-header"><h3>{mode === "tools" ? "Action parameters" : "Message"}</h3></div>{mode === "ollama" ? <LocalTextExperiment data={data} model={config?.ollama.model || "qwen3.5:9b"} refresh={refresh} log={log} /> : <div className="tool-form"><label>Guest name<input value={name} onChange={e => setName(e.target.value)} /></label><label>Date & time ({data.location.timezone})<input value={when} onChange={e => { setWhen(e.target.value); setProposal(null); }} /></label>{data.location.vertical === "restaurant" ? <label>Party size<input type="number" value={party} min={1} max={30} onChange={e => { setParty(Number(e.target.value)); setProposal(null); }} /></label> : <><label>Service<select value={service} onChange={e => setService(e.target.value)}>{data.services.map(s => <option value={s.id} key={s.id}>{s.name}</option>)}</select></label><label>Stylist<select value={staff} onChange={e => setStaff(e.target.value)}><option value="">Any qualified stylist</option>{data.resources.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label></>}
      <div className="tool-buttons"><Button variant="outline" disabled={busy} onClick={() => { try { void run("check_availability", bookingArgs()); } catch (e) { setError((e as Error).message); } }}>Check availability</Button><Button disabled={busy || !name} onClick={prepareBooking}>Prepare booking</Button>{data.location.vertical === "restaurant" && <Button variant="outline" disabled={busy} onClick={() => run("get_wait_estimate", { party_size: party })}>Seating wait</Button>}</div>
      {data.location.vertical === "restaurant" && <div className="tool-cart"><h4>Test takeout</h4>{data.menu.map(item => <label className="checkbox-label" key={item.id}><input type="checkbox" disabled={!item.available} checked={selectedItems.includes(item.id)} onChange={e => setSelectedItems(items => e.target.checked ? [...items, item.id] : items.filter(id => id !== item.id))} />{item.name}{!item.available && " (unavailable)"}</label>)}<Button variant="outline" disabled={busy || !selectedItems.length || !name} onClick={prepareOrder}>Prepare order</Button></div>}
      {proposal && <div className="decision-box"><p>{proposal.readback}</p><Button onClick={confirm} disabled={busy}><Check size={16} /> Confirm action</Button></div>}{testCall && <Button variant="ghost" onClick={endTest} disabled={busy}>End tool test</Button>}
    </div>}<ErrorText error={error} /></div>}
    <div ref={audioContainer} className="audio-container" />
    <div className="panel conversation"><div className="panel-header"><h3>{mode === "tools" ? "Results" : mode === "livekit" ? "Call activity" : "Conversation"}</h3><Badge>{connected || testCall ? "In progress" : messages.length ? "Session activity" : "Not started"}</Badge></div><div className="conversation-content">{messages.length ? messages.map((m, i) => <div className={`conversation-message message-${m.role}`} key={i}><small>{({ user: "You", assistant: "Receptionist", system: "Call update", tool: "Action", result: "Result" } as Record<string, string>)[m.role] || m.role}</small><pre>{m.text}</pre></div>) : <div className="conversation-empty"><p>{mode === "tools" ? "Run a check to view its result." : mode === "ollama" ? "Send a message to start the conversation." : "Call updates appear here once the call begins."}</p>{voiceMode && <small>{data.location.vertical === "restaurant" ? "Try asking about table availability or placing a takeout order." : "Try booking an appointment with your preferred stylist."}</small>}</div>}</div>{voiceMode && <div className="conversation-footer">Audio recording off · {data.location.settings.transcript_retention_calls ? "Transcript policy: last 10 calls" : "Transcript storage off"}</div>}</div>
  </div>;
}

function LocalTextExperiment({ data, model, refresh, log }: { data: Snapshot; model: string; refresh: () => void; log: (role: string, text: string) => void }) {
  const [session, setSession] = useState<string | null>(null); const sessionRef = useRef<string | null>(null); const [text, setText] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => () => { if (sessionRef.current) fetch(`/api/v1/voice/ollama/${sessionRef.current}/end`, { method: "POST", keepalive: true }); }, []);
  async function send(e: React.FormEvent) { e.preventDefault(); setBusy(true); setError(""); try { let id = session; if (!id) { const result = await mutation<{ call_id: string }>("/voice/ollama/session", { location_id: data.location.id }); id = result.call_id; setSession(id); sessionRef.current = id; } log("user", text); const result = await mutation<{ text: string; tool_results: { name: string; result: unknown }[] }>(`/voice/ollama/${id}/chat`, { text }); result.tool_results.forEach(t => log("tool", `${t.name} → ${JSON.stringify(t.result, null, 2)}`)); log("assistant", result.text); setText(""); refresh(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function end() { if (!session) return; await mutation(`/voice/ollama/${session}/end`); setSession(null); sessionRef.current = null; log("system", "Text conversation ended. No voice/audio was used."); refresh(); }
  return <form className="tool-form" onSubmit={send}><Badge>Text only · {model}</Badge><p className="muted">Send a message to check how your receptionist responds. This session uses text only.</p><label>Message<textarea value={text} onChange={e => setText(e.target.value)} required maxLength={2000} placeholder="What tables are available for four in an hour?" /></label><Button disabled={busy || !text.trim()} type="submit">{busy ? <Loader2 size={16} className="spin" /> : <ArrowRight size={16} />}Send to local Ollama</Button>{session && <Button type="button" variant="ghost" disabled={busy} onClick={end}>End conversation</Button>}<ErrorText error={error} /><small className="muted">Conversation history is cleared when the session ends.</small></form>;
}
