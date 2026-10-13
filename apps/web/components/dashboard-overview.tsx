"use client";
// Overview section: metrics, attention queue, floor and activity summary.

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, PhoneIncoming } from "lucide-react";
import { Panel } from "./shared/panel";
import { isStale, tableStateLabel } from "./floor-plan";
import { AttentionQueue } from "./attention-queue";
import { localInput, mutation, time } from "@/lib/api";
import type { AttentionItem, Call, Decision, Resource, Snapshot, VoiceConfig } from "@/lib/types";

type OverviewProps = {
  data: Snapshot;
  attention: AttentionItem[];
  voice: VoiceConfig | null;
  onTable: (resource: Resource) => void;
  onCall: (id: string) => void;
  onUpdated: () => void;
  onPause: () => void;
};

function OverviewMetric({ label, value, href }: { label: string; value: string | number; href: string }) {
  return <Link className="operations-metric" href={href}><strong>{value}</strong><span>{label}</span></Link>;
}

function ViewAll({ href, children = "View all" }: { href: string; children?: React.ReactNode }) {
  return <Link className="operations-link" href={href}>{children}<ArrowRight size={15} aria-hidden="true" /></Link>;
}

function callLabel(call: Call) {
  const labels: Record<string, string> = {
    reservation: "Reservation request", create_reservation: "Reservation request",
    cancel_reservation: "Reservation cancellation", modify_reservation: "Reservation change",
    order: "Takeout order", takeout: "Takeout inquiry", create_takeout_order: "Takeout order",
    waitlist: "Waitlist request", join_waitlist: "Waitlist request",
    appointment: "Appointment request", create_salon_appointment: "Appointment request",
    availability: "Availability inquiry", check_availability: "Availability inquiry",
    seating_wait: "Seating wait inquiry", seating_request: "Seating request", request_staff_booking: "Seating request",
    menu: "Menu inquiry", services: "Service inquiry", order_status: "Order status inquiry",
    get_business_info: "Business inquiry", get_operating_status: "Availability inquiry",
    transfer_to_human: "Staff assistance request", information: "Customer inquiry", information_only: "Customer inquiry",
  };
  return labels[call.intent] || "Customer call";
}

function callOutcome(call: Call) {
  if (call.status !== "ended") return { label: "In progress", tone: "neutral" };
  if (["escalated", "failed"].includes(call.outcome)) return { label: "Needs staff", tone: "neutral" };
  if (call.outcome === "pending") return { label: "Pending confirmation", tone: "neutral" };
  if (call.outcome === "fulfilled") {
    const outcomes: Record<string, string> = { reservation: "Confirmed", appointment: "Confirmed", create_reservation: "Confirmed", create_salon_appointment: "Confirmed", cancel_reservation: "Cancelled", modify_reservation: "Updated", reschedule_salon_appointment: "Rescheduled", order: "Accepted", create_takeout_order: "Accepted", waitlist: "Added to waitlist" };
    return { label: outcomes[call.intent] || "Completed", tone: "positive" };
  }
  if (call.outcome === "information_only") return { label: "Completed", tone: "positive" };
  return { label: "Ended", tone: "neutral" };
}

function CallActivity({ data, voice, onCall, onPause }: Pick<OverviewProps, "data" | "voice" | "onCall" | "onPause">) {
  const provider = data.location.settings.preferred_voice_provider === "livekit" ? "livekit" : "vapi";
  const configured = voice?.[provider].configured;
  const calls = data.calls.filter(call => ["vapi", "livekit"].includes(call.provider))
    .sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 3);
  return <Panel className="operations-calls" title="AI call activity" action={<ViewAll href={`/app/${data.location.vertical}/calls`}>View transcripts</ViewAll>}>
    <div className="operations-call-list">{calls.map(call => {
      const outcome = callOutcome(call);
      return <button className="operations-call-row" key={call.id} onClick={() => onCall(call.id)}>
        <PhoneIncoming size={20} aria-hidden="true" /><span className="operations-call-title">{callLabel(call)}</span>
        <span className="operations-call-time">{time(call.created_at, data.location.timezone, true)}</span>
        <span className={`operations-outcome ${outcome.tone}`}>{outcome.label}</span>
      </button>;
    })}{!calls.length && <p className="operations-empty">No calls yet.</p>}</div>
    <div className="operations-receptionist"><Link href={`/app/${data.location.vertical}/voice`}>AI receptionist <span>{data.location.settings.intake_paused ? "Paused" : configured ? "Configured" : "Setup required"}</span></Link><button onClick={onPause}>{data.location.settings.intake_paused ? "Resume requests" : "Pause requests"}</button></div>
  </Panel>;
}

export function DashboardOverview(props: OverviewProps) {
  const { data, attention, onTable, onCall, onUpdated } = props;
  const zone = data.location.timezone;
  const restaurant = data.location.vertical === "restaurant";
  const root = `/app/${data.location.vertical}`;
  const today = localInput(new Date(), zone).slice(0, 10);
  const [estimate, setEstimate] = useState<Decision | null>(null);
  const [party, setParty] = useState(2);
  const [estimateState, setEstimateState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    if (!restaurant) return;
    let valid = true;
    setEstimateState("loading");
    mutation<Decision>("/wait-estimates/seating", { location_id: data.location.id, name: "Dashboard estimate", party_size: party })
      .then(result => { if (valid) { setEstimate(result); setEstimateState("ready"); } })
      .catch(() => { if (valid) { setEstimate(null); setEstimateState("error"); } });
    return () => { valid = false; };
  }, [data.location.id, data.as_of, party, restaurant]);

  const tables = data.resources.filter(resource => resource.kind === "table").sort((a, b) => a.name.localeCompare(b.name));
  const ready = tables.filter(table => table.state === "available" && table.attributes.staff_verified !== false && !isStale(table, data));
  const bookings = data.reservations.filter(booking => ["confirmed", "seated", "pending_confirmation"].includes(booking.status) && localInput(new Date(booking.start_at), zone).startsWith(today))
    .sort((a, b) => a.start_at.localeCompare(b.start_at));
  const arrivals = bookings.filter(booking => Date.parse(booking.end_at) > Date.now());
  const waiting = data.waitlist.filter(entry => entry.status === "waiting");
  const preparing = data.orders.filter(order => order.state === "preparing");
  const active = data.orders.filter(order => ["accepted", "preparing", "ready"].includes(order.state));
  const nextPickup = active.sort((a, b) => a.pickup_min.localeCompare(b.pickup_min))[0];
  const kitchen = data.kitchen;
  const kitchenFresh = !!kitchen && (data.location.settings.staff_state_persistent || Date.now() - Date.parse(kitchen.verified_at) <= data.location.settings.freshness_minutes * 60000);
  const takeoutEnabled = kitchen?.kitchen_open && kitchen.accepting_takeout && !data.location.settings.intake_paused;
  const kitchenLabel = !kitchen ? "Not configured" : data.location.settings.intake_paused ? "Requests paused" : !kitchen.kitchen_open ? "Closed" : !kitchen.accepting_takeout ? "Orders paused" : !kitchenFresh ? "Status unavailable" : "Takeout enabled";
  const waitValue = estimateState === "loading" ? "Updating…" : estimateState === "error" ? "Unavailable" : estimate?.wait_minutes ? `${estimate.wait_minutes.min}–${estimate.wait_minutes.max} min` : estimate?.status === "AVAILABLE_NOW" ? "Available now" : estimate?.status === "NO_CAPACITY" ? "No capacity" : "Status unavailable";
  const waitTitle = estimate?.evidence ? `${estimate.evidence.sources.join(", ").replaceAll("_", " ")} · ${estimate.evidence.confidence} confidence · ${time(estimate.evidence.as_of, zone, true)}` : "Seating estimate";

  return <div className="operations-overview">
    <div className="operations-metrics">
      <OverviewMetric label={restaurant ? "Reservations" : "Appointments"} value={bookings.length} href={`${root}/${restaurant ? "reservations" : "calendar"}`} />
      <OverviewMetric label={restaurant ? "Tables ready" : "Available staff"} value={restaurant ? `${ready.length}/${tables.length}` : `${data.resources.filter(resource => resource.state === "available").length}/${data.resources.length}`} href={`${root}/${restaurant ? "floor" : "staff"}`} />
      <OverviewMetric label={restaurant ? "Waiting parties" : "Calls today"} value={restaurant ? waiting.length : data.calls.filter(call => ["vapi", "livekit"].includes(call.provider) && localInput(new Date(call.created_at), zone).startsWith(today)).length} href={`${root}/${restaurant ? "waitlist" : "calls"}`} />
      <OverviewMetric label="Need attention" value={attention.length} href={`${root}/attention`} />
    </div>

    <div className="operations-grid">
      <Panel className="operations-arrivals" title={restaurant ? "Arrivals & reservations" : "Today's appointments"} action={<ViewAll href={`${root}/${restaurant ? "reservations" : "calendar"}`} />}>
        <div className="operations-arrival-list">{arrivals.slice(0, 3).map(booking => <Link className="operations-arrival-row" href={`${root}/${restaurant ? "reservations" : "calendar"}`} key={booking.id}>
          <time dateTime={booking.start_at}>{time(booking.start_at, zone)}</time><div><strong>{restaurant ? `Party of ${booking.party_size}` : data.services.find(service => service.id === booking.service_id)?.name || "Appointment"}</strong><span>{booking.status === "seated" ? "Checked in" : booking.status === "pending_confirmation" ? "Pending confirmation" : "Expected"} · {booking.customer_name}</span></div>
        </Link>)}{!arrivals.length && <p className="operations-empty">No more arrivals today.</p>}</div>
      </Panel>

      <div className="operations-attention"><AttentionQueue resources={data.resources} items={attention} timezone={zone} onCall={onCall} onTable={id => { const resource = data.resources.find(table => table.id === id); if (resource) onTable(resource); }} onUpdated={onUpdated} compact /></div>

      <Panel className="operations-floor" title={restaurant ? "Floor snapshot" : "Team availability"} action={<ViewAll href={`${root}/${restaurant ? "floor" : "staff"}`} />}>
        <div className="operations-table-grid">{(restaurant ? tables : data.resources).map(resource => {
          const unverified = restaurant && (resource.attributes.staff_verified === false || isStale(resource, data));
          const state = unverified && resource.state === "available" ? "unverified" : resource.state;
          const label = state === "unverified" ? "unknown" : state === "available" ? "ready" : tableStateLabel(state).toLowerCase();
          if (!restaurant) return <Link className={`operations-table table-${state}`} href={`${root}/staff`} key={resource.id}><strong>{resource.name}</strong><span>{resource.state === "available" ? "available" : "unavailable"}</span></Link>;
          return <button className={`operations-table table-${state}`} key={resource.id} onClick={() => onTable(resource)} aria-label={`${resource.name}, ${resource.capacity} seats, ${label}${unverified && state !== "unverified" ? ", needs verification" : ""}`} title={unverified ? "Staff verification required" : `${resource.capacity} seats`}><strong>{resource.name}</strong><span>{label}</span></button>;
        })}{!(restaurant ? tables : data.resources).length && <p className="operations-empty">No resources configured.</p>}</div>
        {restaurant && <div className="operations-wait"><label>Seating wait<select aria-label="Party size for seating estimate" value={party} onChange={event => setParty(Number(event.target.value))}>{[2, 4, 6, 8, 12].map(size => <option value={size} key={size}>{size} guests</option>)}</select></label><span title={waitTitle} aria-live="polite">{waitValue}</span></div>}
      </Panel>

      {restaurant ? <Panel className="operations-kitchen" title="Kitchen & takeout" action={<ViewAll href={`${root}/kitchen`} />}>
        <dl className="operations-kitchen-stats"><div><dt>Kitchen</dt><dd className={kitchenFresh && takeoutEnabled ? "positive" : ""}>{kitchenLabel}</dd></div><div><dt>Preparing</dt><dd>{preparing.length} {preparing.length === 1 ? "order" : "orders"}</dd></div><div><dt>Next pickup</dt><dd>{nextPickup ? `${time(nextPickup.pickup_min, zone)}–${time(nextPickup.pickup_max, zone)}` : "—"}</dd></div></dl>
        <div className="operations-kitchen-footer"><Link href={`${root}/kitchen`}>{active.length} active {active.length === 1 ? "order" : "orders"}</Link><span>{active.filter(order => order.state === "ready").length} ready for pickup</span></div>
      </Panel> : <Panel title="Services"><div className="operations-arrival-list">{data.services.filter(service => service.enabled).map(service => <Link className="operations-arrival-row" href={`${root}/services`} key={service.id}><div><strong>{service.name}</strong><span>{service.duration_min} min</span></div></Link>)}</div></Panel>}
    </div>

    <CallActivity data={data} voice={props.voice} onCall={onCall} onPause={props.onPause} />
  </div>;
}
