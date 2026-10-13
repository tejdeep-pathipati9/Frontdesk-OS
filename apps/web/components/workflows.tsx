"use client";
// Shared status badge/empty/error primitives plus the booking and order creation forms (prepare, readback, confirm).
import { useState } from "react";
import { Check, ArrowRight, Loader2, Plus, Minus } from "lucide-react";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";
import { api, mutation, money, localInput, localToISO, time } from "@/lib/api";
import type { Snapshot, Proposal, Decision, Booking } from "@/lib/types";

/** Colored status pill. */
export function Badge({ children, status = "" }: { children: React.ReactNode; status?: string }) {
  const green = ["available", "confirmed", "accepted", "ready", "fulfilled", "supported", "AVAILABLE", "AVAILABLE_NOW", "connected_read_write"].includes(status);
  const red = ["failed", "out_of_service", "cancelled", "NO_CAPACITY", "degraded", "blocked"].includes(status);
  const amber = ["seated", "held", "cleaning", "pending", "requires_permission", "ESTIMATED_WAIT", "UNKNOWN", "preparing", "escalated"].includes(status);
  return <span className={`badge ${green ? "badge-green" : red ? "badge-red" : amber ? "badge-amber" : "badge-neutral"}`}>{children}</span>;
}
export function Empty({ title, detail, action }: { title: string; detail?: string; action?: React.ReactNode }) { return <div className="empty"><div className="empty-symbol"><Plus size={22} /></div><h3>{title}</h3>{detail && <p>{detail}</p>}{action}</div>; }
export function ErrorText({ error }: { error: string }) { return error ? <div role="alert" className="error-text">{error}</div> : null; }

/** Create or edit a booking via prepare -> readback -> confirm, supporting reservations and salon appointments. */
export function BookingForm({ data, open, onClose, onSuccess, editing }: { data: Snapshot; open: boolean; onClose: () => void; onSuccess: (message: string) => void; editing?: Booking }) {
  const [name, setName] = useState(editing?.customer_name || "");
  const [phone, setPhone] = useState("");
  const [party, setParty] = useState(editing?.party_size || 2);
  const [when, setWhen] = useState(localInput(editing ? new Date(editing.start_at) : new Date(Date.now() + 3600000), data.location.timezone));
  const [service, setService] = useState(editing?.service_id || data.services[0]?.id || "");
  const [staff, setStaff] = useState(editing?.resource_ids[0] || "");
  const [decision, setDecision] = useState<Decision | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const salon = data.location.vertical === "salon";
  const query = () => ({ location_id: data.location.id, start_at: localToISO(when, data.location.timezone), party_size: party, ...(salon ? { service_id: service, ...(staff ? { staff_id: staff } : {}) } : {}) });
  async function check() { setBusy(true); setError(""); try { setDecision(await mutation<Decision>(`/availability/${salon ? "salon" : "restaurant"}`, query())); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function requestStaff() { setBusy(true); setError(""); try { await mutation("/capacity-requests", { ...query(), reason: decision?.reason_codes?.join(", ") || "Staff seating arrangement requested" }); onSuccess("Staff review requested · booking not confirmed"); onClose(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function prepareBooking(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      if (editing) { await mutation(`/reservations/${editing.id}/reschedule`, query()); onSuccess("Booking rescheduled"); onClose(); return; }
      const result = await mutation<Proposal>("/actions/prepare", { ...query(), kind: salon ? "appointment" : "reservation", name, phone }); setProposal(result);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function confirm() { setBusy(true); setError(""); try { await mutation(`/actions/${proposal!.action_id}/confirm`); const result = await mutation<{ reference: string; status: string }>("/actions/commit", { action_id: proposal!.action_id }); onSuccess(`${result.status === "pending_confirmation" ? "Awaiting provider confirmation" : "Confirmed"} · ${result.reference}`); onClose(); } catch (e) { setError((e as Error).message); setProposal(null); } finally { setBusy(false); } }
  return <Dialog open={open} onClose={onClose} title={proposal ? "Review and confirm" : editing ? "Reschedule booking" : salon ? "New appointment" : "New reservation"} description={data.location.timezone}>
    {proposal ? <div className="confirmation"><div className="confirmation-icon"><Check /></div><p>{proposal.readback}</p><small>Proposal expires at {time(proposal.expires_at, data.location.timezone)}. </small><div className="form-actions"><Button variant="outline" onClick={() => setProposal(null)}>Edit details</Button><Button disabled={busy} onClick={confirm}>{busy ? <Loader2 className="spin" size={16} /> : <Check size={16} />} Confirm booking</Button></div></div> : <form onSubmit={prepareBooking} className="form-grid">
      {!editing && <><label>Guest name<input value={name} onChange={e => setName(e.target.value)} required placeholder="Full name" /></label><label>Phone (optional)<input value={phone} onChange={e => setPhone(e.target.value)} placeholder="+12125550123" /></label></>}
      <label className="span-2">Date & time<input value={when} onChange={e => { setWhen(e.target.value); setDecision(null); }} required placeholder="YYYY-MM-DDTHH:mm" /><small>Time at this location.</small></label>
      {salon ? <><label>Service<select value={service} onChange={e => setService(e.target.value)} disabled={!!editing}>{data.services.filter(s => s.enabled).map(s => <option key={s.id} value={s.id}>{s.name} · {s.duration_min} min</option>)}</select></label><label>Stylist<select value={staff} onChange={e => setStaff(e.target.value)}><option value="">Any qualified stylist</option>{data.resources.filter(r => r.kind === "stylist" && data.services.find(s => s.id === service)?.eligible_staff.includes(r.id)).map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label></> : <label>Party size<input type="number" min={1} max={30} value={party} onChange={e => setParty(Number(e.target.value))} /></label>}
      {decision && <div className="decision-box span-2"><Badge status={decision.status}>{decision.status.replaceAll("_", " ")}</Badge>{decision.resources && <p>{decision.resources.join(" + ")}</p>}{decision.reason_codes?.length ? <p>{decision.reason_codes.join(", ").replaceAll("_", " ").toLowerCase()}</p> : null}{!salon && decision.status !== "AVAILABLE" && <Button variant="outline" type="button" disabled={busy} onClick={requestStaff}>Request staff review</Button>}{decision.alternatives?.map(a => <button type="button" className="alternative" key={a.start_at} onClick={() => { setWhen(localInput(new Date(a.start_at), data.location.timezone)); setDecision(null); }}>{time(a.start_at, data.location.timezone, true)} · {(a.resources || []).join(" + ")} <ArrowRight size={14} /></button>)}</div>}
      <div className="span-2"><ErrorText error={error} /></div><div className="form-actions span-2"><Button type="button" variant="outline" disabled={busy} onClick={check}>Check availability</Button><Button type="submit" disabled={busy || (salon && !service)}>{busy ? <Loader2 size={16} className="spin" /> : <ArrowRight size={16} />} {editing ? "Reschedule" : "Review booking"}</Button></div>
    </form>}
    {proposal && <ErrorText error={error} />}
  </Dialog>;
}

type Cart = Record<string, { quantity: number; modifiers: string[] }>;
/** Create a takeout/dine-in order via the prepare/confirm flow. */
export function OrderForm({ data, open, onClose, onSuccess }: { data: Snapshot; open: boolean; onClose: () => void; onSuccess: (message: string) => void }) {
  const [cart, setCart] = useState<Cart>({}); const [name, setName] = useState(""); const [proposal, setProposal] = useState<Proposal | null>(null); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  function quantity(id: string, delta: number) { setCart(c => ({ ...c, [id]: { quantity: Math.max(0, (c[id]?.quantity || 0) + delta), modifiers: c[id]?.modifiers || [] } })); }
  const subtotal = data.menu.reduce((total, item) => total + (cart[item.id]?.quantity || 0) * (item.price_minor + item.modifiers.filter(m => cart[item.id]?.modifiers.includes(m.id)).reduce((sum, m) => sum + m.price_minor, 0)), 0);
  async function review(e: React.FormEvent) { e.preventDefault(); setError(""); setBusy(true); try { setProposal(await mutation<Proposal>("/actions/prepare", { location_id: data.location.id, kind: "order", name, items: Object.entries(cart).filter(([, v]) => v.quantity > 0).map(([id, v]) => ({ item_id: id, ...v })) })); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  async function confirm() { setBusy(true); setError(""); try { await mutation(`/actions/${proposal!.action_id}/confirm`); const result = await mutation<{ reference: string; status: string }>("/actions/commit", { action_id: proposal!.action_id }); onSuccess(`${result.status === "pending_confirmation" ? "Awaiting provider confirmation" : "Order accepted"} · ${result.reference}`); onClose(); } catch (e) { setError((e as Error).message); setProposal(null); } finally { setBusy(false); } }
  return <Dialog open={open} onClose={onClose} title={proposal ? "Confirm takeout order" : "New takeout order"}  wide>
    {proposal ? <div className="confirmation"><p>{proposal.readback}</p><div className="price-total"><span>Total, including tax</span><strong>{money(proposal.decision.total_minor as number)}</strong></div><div className="form-actions"><Button variant="outline" onClick={() => setProposal(null)}>Edit cart</Button><Button onClick={confirm} disabled={busy}>Confirm order</Button></div></div> : <form onSubmit={review}><label>Guest name<input required value={name} onChange={e => setName(e.target.value)} placeholder="Name for pickup" /></label><div className="cart-list">{data.menu.map(item => <div key={item.id} className={`cart-item ${!item.available ? "muted" : ""}`}><div><strong>{item.name}</strong><p>{money(item.price_minor)} · {item.available ? item.description : "Currently unavailable"}</p>{(cart[item.id]?.quantity || 0) > 0 && item.modifiers.map(m => <label key={m.id} className="checkbox-label"><input type="checkbox" checked={cart[item.id].modifiers.includes(m.id)} onChange={e => setCart(c => ({ ...c, [item.id]: { ...c[item.id], modifiers: e.target.checked ? [...c[item.id].modifiers, m.id] : c[item.id].modifiers.filter(id => id !== m.id) } }))} />{m.name}{m.price_minor > 0 && ` +${money(m.price_minor)}`}</label>)}</div><div className="quantity"><Button type="button" variant="ghost" size="icon" aria-label={`Remove ${item.name}`} onClick={() => quantity(item.id, -1)} disabled={!cart[item.id]?.quantity}><Minus size={15} /></Button><span>{cart[item.id]?.quantity || 0}</span><Button type="button" variant="ghost" size="icon" aria-label={`Add ${item.name}`} onClick={() => quantity(item.id, 1)} disabled={!item.available || cart[item.id]?.quantity >= 20}><Plus size={15} /></Button></div></div>)}</div><div className="price-total"><span>Subtotal before tax</span><strong>{money(subtotal)}</strong></div><div className="form-actions"><Button type="submit" disabled={busy || subtotal === 0}>{busy ? <Loader2 size={16} className="spin" /> : <ArrowRight size={16} />} Review order</Button></div></form>}
    <ErrorText error={error} />
  </Dialog>;
}
