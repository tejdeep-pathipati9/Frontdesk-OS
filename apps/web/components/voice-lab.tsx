"use client";
import { useEffect, useRef, useState } from "react";
import { Mic, Phone, PhoneOff, Terminal, FlaskConical, ArrowRight, Check, Loader2, AudioLines } from "lucide-react";
import { Button } from "./ui/button";
import { Badge, ErrorText } from "./workflows";
import { api, mutation, localInput, localToISO } from "@/lib/api";
import type { Snapshot, VoiceConfig, Proposal } from "@/lib/types";

export default function VoiceLab({ data, refresh }: { data: Snapshot; refresh: () => void }) {
  const [config, setConfig] = useState<VoiceConfig | null>(null);
  const [mode, setMode] = useState<string>(data.location.settings.preferred_voice_provider || "vapi");
  const [connected, setConnected] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const [messages, setMessages] = useState<{ role: string; text: string }[]>([]);
  const [testCall, setTestCall] = useState<string | null>(null); const testRef = useRef<string | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [name, setName] = useState("Demo guest"); const [party, setParty] = useState(4);
  const [when, setWhen] = useState(localInput(new Date(Date.now() + 3600000), data.location.timezone));
  const [service, setService] = useState(data.services[0]?.id || "");
  const [staff, setStaff] = useState("");
  const [selectedItems, setSelectedItems] = useState<string[]>([]);
  const vapi = useRef<{ stop: () => void } | null>(null); const room = useRef<{ disconnect: () => void } | null>(null); const audioContainer = useRef<HTMLDivElement>(null);
  useEffect(() => { api<VoiceConfig>(`/voice/config?location_id=${data.location.id}`).then(setConfig).catch(e => setError(e.message)); return () => { vapi.current?.stop(); room.current?.disconnect(); if (testRef.current) fetch(`/api/v1/voice/test-calls/${testRef.current}/end`, { method: "POST", keepalive: true }); }; }, [data.location.id]);
  function log(role: string, text: string) { setMessages(m => [...m.slice(-39), { role, text }]); }
  async function startVoice() {
    setError(""); setBusy(true);
    try {
      if (mode === "vapi") {
        const { default: Vapi } = await import("@vapi-ai/web");
        const client = new Vapi(config!.vapi.public_key);
        vapi.current = client;
        client.on("call-start", () => { setConnected(true); setBusy(false); log("system", "Vapi web call connected. Your microphone is live."); });
        client.on("call-end", () => { setConnected(false); setBusy(false); refresh(); log("system", "Call ended. Verified actions appear in Calls."); });
        client.on("message", message => { if (message.type === "transcript" && message.transcriptType === "final") log(message.role, message.transcript); });
        client.on("error", error => { setError(error instanceof Error ? error.message : "Vapi call failed. Check assistant and browser permissions."); setBusy(false); setConnected(false); });
        await client.start(config!.vapi.assistant_id);
      } else {
        const { Room, RoomEvent, Track } = await import("livekit-client");
        const session = await mutation<{ token: string; url: string }>("/voice/livekit/session", { location_id: data.location.id });
        const client = new Room(); room.current = client;
        client.on(RoomEvent.TrackSubscribed, track => { if (track.kind === Track.Kind.Audio) { const element = track.attach(); element.autoplay = true; audioContainer.current?.appendChild(element); } });
        client.on(RoomEvent.Disconnected, () => { setConnected(false); refresh(); });
        await client.connect(session.url, session.token);
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
  async function endTest() { if (testCall) { await mutation(`/voice/test-calls/${testCall}/end`); setTestCall(null); testRef.current = null; setProposal(null); refresh(); log("system", "Tool test ended. This was a backend test, with no voice or LLM."); } }
  return <div className="voice-layout"><div className="panel voice-controls"><div className="panel-header"><h3>Choose your voice path</h3><Badge>Prototype</Badge></div><div className="voice-paths">{[{ id: "vapi", icon: Phone, title: "Vapi", subtitle: "Primary phone & web demo" }, { id: "livekit", icon: FlaskConical, title: "LiveKit + Ollama", subtitle: "Local voice experimentation" }, { id: "ollama", icon: Terminal, title: "Ollama text experiment", subtitle: "Local model + shared tools · no audio" }, { id: "tools", icon: Terminal, title: "Business tool test", subtitle: "No audio, no LLM · real backend" }].map(path => <button className={`voice-path ${mode === path.id ? "selected" : ""}`} key={path.id} onClick={() => { if (!connected && !busy) { setMode(path.id); setError(""); } }} disabled={connected || busy}><path.icon size={20} /><span><strong>{path.title}</strong><small>{path.subtitle}</small></span>{mode === path.id && <Check size={16} />}</button>)}</div>
    {mode !== "tools" && mode !== "ollama" ? <><div className="voice-orb"><AudioLines size={46} /></div><h3 className="center">{connected ? "Microphone is live" : "Your front desk is ready to test"}</h3><p className="center muted">{mode === "vapi" ? "Speak naturally. The assistant checks business state before taking an action." : `${config?.ollama.model || "Ollama"} powers the local agent. Speech models run through configurable local endpoints.`}</p><div className="voice-setup"><Badge status={(mode === "vapi" ? config?.vapi.configured : config?.livekit.configured) ? "available" : "requires_permission"}>{(mode === "vapi" ? config?.vapi.configured : config?.livekit.configured) ? "Configuration present" : "Setup required"}</Badge><p>{mode === "vapi" ? "Configure a saved Vapi assistant, authenticated server URL, public key and assistant ID. Recordings are off by default." : "Run the LiveKit server and Python agent worker; configure Ollama plus STT/TTS endpoints. See docs/voice.md."}</p></div><Button className="full-width" disabled={busy || !(mode === "vapi" ? config?.vapi.configured : config?.livekit.configured)} onClick={connected ? stopVoice : startVoice}>{busy ? <Loader2 size={17} className="spin" /> : connected ? <PhoneOff size={17} /> : <Mic size={17} />}{connected ? "End call" : "Start web call"}</Button></> : mode === "ollama" ? <LocalTextExperiment data={data} model={config?.ollama.model || "qwen3.5:9b"} refresh={refresh} log={log} /> : <div className="tool-form"><label>Guest name<input value={name} onChange={e => setName(e.target.value)} /></label><label>Date & time ({data.location.timezone})<input value={when} onChange={e => { setWhen(e.target.value); setProposal(null); }} /></label>{data.location.vertical === "restaurant" ? <label>Party size<input type="number" value={party} min={1} max={30} onChange={e => { setParty(Number(e.target.value)); setProposal(null); }} /></label> : <><label>Service<select value={service} onChange={e => setService(e.target.value)}>{data.services.map(s => <option value={s.id} key={s.id}>{s.name}</option>)}</select></label><label>Stylist<select value={staff} onChange={e => setStaff(e.target.value)}><option value="">Any qualified stylist</option>{data.resources.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label></>}
      <div className="tool-buttons"><Button variant="outline" disabled={busy} onClick={() => { try { void run("check_availability", bookingArgs()); } catch (e) { setError((e as Error).message); } }}>Check availability</Button><Button disabled={busy || !name} onClick={prepareBooking}>Prepare booking</Button>{data.location.vertical === "restaurant" && <Button variant="outline" disabled={busy} onClick={() => run("get_wait_estimate", { party_size: party })}>Seating wait</Button>}</div>
      {data.location.vertical === "restaurant" && <div className="tool-cart"><h4>Test takeout</h4>{data.menu.map(item => <label className="checkbox-label" key={item.id}><input type="checkbox" disabled={!item.available} checked={selectedItems.includes(item.id)} onChange={e => setSelectedItems(items => e.target.checked ? [...items, item.id] : items.filter(id => id !== item.id))} />{item.name}{!item.available && " (unavailable)"}</label>)}<Button variant="outline" disabled={busy || !selectedItems.length || !name} onClick={prepareOrder}>Prepare order</Button></div>}
      {proposal && <div className="decision-box"><p>{proposal.readback}</p><Button onClick={confirm} disabled={busy}><Check size={16} /> Explicitly confirm & commit</Button></div>}{testCall && <Button variant="ghost" onClick={endTest} disabled={busy}>End tool test</Button>}
    </div>}<ErrorText error={error} /><div ref={audioContainer} className="audio-container" /></div>
    <div className="panel conversation"><div className="panel-header"><h3>{mode === "tools" ? "Tool execution console" : "Call conversation"}</h3><Badge status={connected || testCall ? "available" : ""}>{connected || testCall ? "Active" : "Idle"}</Badge></div><div className="conversation-content">{messages.length ? messages.map((m, i) => <div className={`conversation-message message-${m.role}`} key={i}><small>{m.role === "result" ? "Backend result" : m.role}</small><pre>{m.text}</pre></div>) : <div className="conversation-empty"><AudioLines size={32} /><h3>Every commitment starts with a check.</h3><p>Start a call or use the tool test to inspect real availability checks, proposals, and confirmed actions.</p><div className="conversation-prompts"><span>“A table for four at 7 PM”</span><span>“Is the kitchen accepting takeout?”</span><span>“Book a haircut with Maya”</span></div></div>}</div><div className="conversation-footer"><span className="status-dot" />{mode === "tools" ? "Real backend · tool tests are labeled separately in Calls" : "Transcript display is ephemeral · no recording retained"}</div></div>
  </div>;
}

function LocalTextExperiment({ data, model, refresh, log }: { data: Snapshot; model: string; refresh: () => void; log: (role: string, text: string) => void }) {
  const [session, setSession] = useState<string | null>(null); const sessionRef = useRef<string | null>(null); const [text, setText] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => () => { if (sessionRef.current) fetch(`/api/v1/voice/ollama/${sessionRef.current}/end`, { method: "POST", keepalive: true }); }, []);
  async function send(e: React.FormEvent) { e.preventDefault(); setBusy(true); setError(""); try { let id = session; if (!id) { const result = await mutation<{ call_id: string }>("/voice/ollama/session", { location_id: data.location.id }); id = result.call_id; setSession(id); sessionRef.current = id; } log("user", text); const result = await mutation<{ text: string; tool_results: { name: string; result: unknown }[] }>(`/voice/ollama/${id}/chat`, { text }); result.tool_results.forEach(t => log("tool", `${t.name} → ${JSON.stringify(t.result, null, 2)}`)); log("assistant", result.text); setText(""); refresh(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function end() { if (!session) return; await mutation(`/voice/ollama/${session}/end`); setSession(null); sessionRef.current = null; log("system", "Ollama text experiment ended. No voice/audio was used."); refresh(); }
  return <form className="tool-form" onSubmit={send}><Badge>Text only · {model}</Badge><p className="muted">Try the local model before configuring speech. It calls the same backend tools and must ask for confirmation before committing.</p><label>Message<textarea value={text} onChange={e => setText(e.target.value)} required maxLength={2000} placeholder="What tables are available for four in an hour?" /></label><Button disabled={busy || !text.trim()} type="submit">{busy ? <Loader2 size={16} className="spin" /> : <ArrowRight size={16} />}Send to local Ollama</Button>{session && <Button type="button" variant="ghost" disabled={busy} onClick={end}>End local experiment</Button>}<ErrorText error={error} /><small className="muted">Chat context is temporary. Ollama may need time to load the model into memory.</small></form>;
}
