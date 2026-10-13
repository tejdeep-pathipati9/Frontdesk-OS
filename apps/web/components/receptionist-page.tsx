"use client";
// AI receptionist screen: voice provider status, configuration and call history.
import { useState } from "react";
import Link from "next/link";
import { ChevronDown, Phone, Play, Settings2 } from "lucide-react";
import VoiceLab, { type VoiceMode } from "./voice-lab";
import { Panel } from "./shared/panel";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";
import { Badge, ErrorText } from "./workflows";
import { mutation } from "@/lib/api";
import type { Snapshot, VoiceConfig } from "@/lib/types";

export function ReceptionistPage({ data, voice, onUpdated }: { data: Snapshot; voice: VoiceConfig | null; onUpdated: () => void }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const [session, setSession] = useState<VoiceMode | null>(null);
  const [advanced, setAdvanced] = useState(false);
  async function save(body: unknown) { setBusy(true); setError(""); try { await mutation(`/locations/${data.location.id}`, body, "PATCH"); onUpdated(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  const actions = [{ key: "bookings", label: data.location.vertical === "restaurant" ? "Reservations" : "Appointments", description: "Book available times after the customer confirms." }, { key: "booking_changes", label: "Booking changes", description: "Cancel or reschedule after verifying customer details." }, ...(data.location.vertical === "restaurant" ? [{ key: "takeout", label: "Takeout orders", description: "Accept available menu items when the kitchen has capacity." }, { key: "waitlist", label: "Waitlist", description: "Add guests and share an estimated seating time." }] : [])];
  const preferred = data.location.settings.preferred_voice_provider || "vapi";
  const configured = preferred === "vapi" ? voice?.vapi.configured : voice?.livekit.configured;
  const enabledCount = actions.filter(action => data.location.settings.ai_actions?.[action.key] !== false).length;
  const calls = data.calls.filter(call => ["vapi", "livekit"].includes(call.provider));
  const activeCalls = calls.filter(call => call.status === "in_progress").length;

  return <div className="receptionist-page">
    <section className="panel receptionist-summary" aria-label="Receptionist status">
      <div className="receptionist-identity"><span className="receptionist-icon"><Phone size={21} /></span><div><h2>{data.location.name}</h2><p>AI receptionist <span>·</span> <Badge>{voice ? configured ? "Configured" : "Setup required" : "Checking connection"}</Badge></p></div></div>
      <div className="receptionist-summary-actions"><Link className="text-link" href={`/app/${data.location.vertical}/calls`}>Call history</Link>{configured ? <Button disabled={busy} onClick={() => setSession(preferred)}><Phone size={15} />Test call</Button> : <Button variant="outline" disabled={!voice} onClick={() => setAdvanced(true)}><Settings2 size={15} />Configure voice</Button>}</div>
      <dl className="receptionist-facts"><div><dt>New requests</dt><dd>{data.location.settings.intake_paused ? "Paused" : "Enabled"}</dd></div><div><dt>Supported actions</dt><dd>{enabledCount} of {actions.length} enabled</dd></div><div><dt>Calls in progress</dt><dd>{activeCalls}</dd></div></dl>
    </section>
    <ErrorText error={error} />
    <div className="receptionist-workspace">
      <Panel title="What your receptionist can do" action={<Badge>{enabledCount} enabled</Badge>}>
        <div className="control-switches">{actions.map(action => { const enabled = data.location.settings.ai_actions?.[action.key] !== false; return <div className="control-switch-row" key={action.key}><div><strong>{action.label}</strong><p>{action.description}</p></div><button role="switch" aria-checked={enabled} aria-label={action.label} className={`switch ${enabled ? "on" : ""}`} disabled={busy} onClick={() => save({ ai_actions: { [action.key]: !enabled } })}><span /></button></div>; })}</div>
        <div className="receptionist-intake"><span>{data.location.settings.intake_paused ? data.location.vertical === "restaurant" ? "New reservations and orders are paused." : "New appointments are paused." : "New requests enabled."}</span><Button size="sm" variant="outline" disabled={busy} onClick={() => save({ intake_paused: !data.location.settings.intake_paused })}>{data.location.settings.intake_paused ? "Resume requests" : "Pause requests"}</Button></div>
      </Panel>
    </div>
    <details className="panel receptionist-advanced" open={advanced} onToggle={event => setAdvanced(event.currentTarget.open)}>
      <summary><span><Settings2 size={15} />Advanced settings</span><ChevronDown size={15} /></summary>
      <div className="receptionist-advanced-body"><div className="receptionist-connection"><label>Voice provider<select value={preferred} disabled={busy} onChange={event => save({ preferred_voice_provider: event.target.value })}><option value="vapi">Vapi</option><option value="livekit">LiveKit + Ollama</option></select></label><small>{configured ? "Voice provider configured." : "Voice provider setup required."}</small></div><div className="receptionist-diagnostics"><strong>Diagnostics</strong><p>Validate a conversation or business action before taking calls.</p><div className="row-actions"><Button variant="outline" size="sm" onClick={() => setSession("ollama")}><Play size={13} />Text test</Button><Button variant="outline" size="sm" onClick={() => setSession("tools")}>Action diagnostics</Button></div></div></div>
    </details>
    {session && <Dialog open onClose={() => setSession(null)} title={session === "tools" ? "Action diagnostics" : session === "ollama" ? "Text test" : "Test your receptionist"} description={session === "tools" ? "Action diagnostics" : session === "ollama" ? "Text only" : undefined} wide><VoiceLab key={`${data.location.id}-${session}`} initialMode={session} data={data} refresh={onUpdated} /></Dialog>}
  </div>;
}
