"use client";
import { useEffect, useRef, useState } from "react";
import { Mic, PhoneOff, ArrowRight, Check, Loader2, AudioLines } from "lucide-react";
import { Button } from "./ui/button";
import { Badge, ErrorText } from "./workflows";
import { api, mutation, localInput, localToISO } from "@/lib/api";
import type { Snapshot, VoiceConfig, Proposal } from "@/lib/types";

export type VoiceMode = "vapi" | "livekit" | "ollama" | "tools";

export default function VoiceLab({ data, refresh, initialMode }: { data: Snapshot; refresh: () => void; initialMode?: VoiceMode }) {
  const active = useRef(false);
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
  const vapi = useRef<{ stop: () => void } | null>(null); const room = useRef<{ disconnect: () => void } | null>(null); const audioContainer = useRef<HTMLDivElement>(null);
  useEffect(() => { active.current = true; api<VoiceConfig>(`/voice/config?location_id=${data.location.id}`).then(value => { if (active.current) setConfig(value); }).catch(e => { if (active.current) setError(e.message); }); return () => { active.current = false; vapi.current?.stop(); room.current?.disconnect(); if (testRef.current) fetch(`/api/v1/voice/test-calls/${testRef.current}/end`, { method: "POST", keepalive: true }); }; }, [data.location.id]);
  function log(role: string, text: string) { setMessages(m => [...m.slice(-39), { role, text }]); }
  async function startVoice() {
    setError(""); setBusy(true);
    try {
      if (mode === "vapi") {
        const { default: Vapi } = await import("@vapi-ai/web");
        if (!active.current) return;
        const client = new Vapi(config!.vapi.public_key);
        vapi.current = client;
        client.on("call-start", () => { if (!active.current) { void client.stop(); return; } setConnected(true); setBusy(false); log("system", "Vapi web call connected. Your microphone is live."); });
        client.on("call-end", () => { setConnected(false); setBusy(false); refresh(); log("system", "Call ended. Verified actions appear in Calls."); });
        client.on("message", message => { if (message.type === "transcript" && message.transcriptType === "final") log(message.role, message.transcript); });
        client.on("error", error => { setError(error instanceof Error ? error.message : "Vapi call failed. Check assistant and browser permissions."); setBusy(false); setConnected(false); });
        await client.start(config!.vapi.assistant_id);
        if (!active.current) await client.stop();
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
    } catch (e) { setError((e as Error).message); setConnected(false); } finally { setBusy(false); }
  }
  async function stopVoice() { vapi.current?.stop(); room.current?.disconnect(); setConnected(false); refresh(); }
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
    {voiceMode ? <section className="voice-session-controls"><div><strong>{data.location.name}</strong><p>{mode === "vapi" ? "Vapi" : "LiveKit + Ollama"} · {connected ? "Microphone in use" : "Microphone access required"}</p></div><Button disabled={busy || (!connected && !configured)} onClick={connected ? stopVoice : startVoice}>{busy ? <Loader2 size={15} className="spin" /> : connected ? <PhoneOff size={15} /> : <Mic size={15} />}{busy ? "Connecting…" : connected ? "End call" : "Start call"}</Button>{config && !configured && <p className="voice-session-note">This voice provider requires setup before you can start a call.</p>}<ErrorText error={error} /></section> : <div className="panel voice-controls"><div className="panel-header"><h3>{mode === "tools" ? "Action parameters" : "Message"}</h3></div>{mode === "ollama" ? <LocalTextExperiment data={data} model={config?.ollama.model || "qwen3.5:9b"} refresh={refresh} log={log} /> : <div className="tool-form"><label>Guest name<input value={name} onChange={e => setName(e.target.value)} /></label><label>Date & time ({data.location.timezone})<input value={when} onChange={e => { setWhen(e.target.value); setProposal(null); }} /></label>{data.location.vertical === "restaurant" ? <label>Party size<input type="number" value={party} min={1} max={30} onChange={e => { setParty(Number(e.target.value)); setProposal(null); }} /></label> : <><label>Service<select value={service} onChange={e => setService(e.target.value)}>{data.services.map(s => <option value={s.id} key={s.id}>{s.name}</option>)}</select></label><label>Stylist<select value={staff} onChange={e => setStaff(e.target.value)}><option value="">Any qualified stylist</option>{data.resources.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label></>}
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
