"use client";
import { useState } from "react";
import { CalendarDays, List, Search } from "lucide-react";
import { Badge, Empty } from "./workflows";
import { Button } from "./ui/button";
import { Panel } from "./shared/panel";
import { DataTable, type Column } from "./shared/data-table";
import { ReservationCalendar } from "./reservation-calendar";
import SalonCalendar from "./salon-calendar";
import { time } from "@/lib/api";
import type { Booking, Snapshot } from "@/lib/types";

export function ReservationsPage({ data, onEdit, onCancel, busy }: { data: Snapshot; onEdit: (booking: Booking) => void; onCancel: (booking: Booking) => void; busy: boolean }) {
  const [view, setView] = useState(data.location.vertical === "salon" ? "calendar" : "table"); const [query, setQuery] = useState(""); const [status, setStatus] = useState("all"); const salon = data.location.vertical === "salon";
  const columns: Column<Booking>[] = [
    { key: "guest", title: "Customer", sortValue: b => b.customer_name, render: b => <><strong>{b.customer_name}</strong><small>#{b.id.slice(0, 8).toUpperCase()}</small></> },
    { key: "start", title: "Date & time", sortValue: b => b.start_at, render: b => <>{time(b.start_at, data.location.timezone, true)}<small>Ends {time(b.end_at, data.location.timezone)}</small></> },
    { key: "party", title: salon ? "Service" : "Guests", render: b => salon ? data.services.find(s => s.id === b.service_id)?.name : b.party_size },
    { key: "resource", title: salon ? "Stylist" : "Table", render: b => b.resources.join(" + ") },
    { key: "status", title: "Status", sortValue: b => b.status, render: b => <Badge status={b.status}>{b.status.replaceAll("_", " ")}</Badge> },
    { key: "source", title: "Source", render: b => <Badge>{b.provider === "internal" ? "Internal engine" : b.provider}</Badge> },
    { key: "actions", title: "", render: b => b.status === "confirmed" && <div className="row-actions"><Button variant="ghost" size="sm" onClick={() => onEdit(b)}>Reschedule</Button><Button variant="ghost" size="sm" disabled={busy} onClick={() => onCancel(b)}>Cancel</Button></div> },
  ];
  const rows = data.reservations.filter(b => (status === "all" || b.status === status) && (b.customer_name + b.id).toLowerCase().includes(query.toLowerCase())).sort((a, b) => a.start_at.localeCompare(b.start_at));
  return <><div className="filter-row"><div className="search-field"><Search size={14} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search customer or reference…" aria-label="Search reservations" /></div><div className="filter-controls"><select aria-label="Booking status" value={status} onChange={e => setStatus(e.target.value)}><option value="all">All statuses</option>{["confirmed", "seated", "completed", "cancelled", "no_show"].map(s => <option key={s} value={s}>{s.replaceAll("_", " ")}</option>)}</select><div className="segmented"><button className={view === "table" ? "selected" : ""} onClick={() => setView("table")}><List size={13} />Table</button><button className={view === "calendar" ? "selected" : ""} onClick={() => setView("calendar")}><CalendarDays size={13} />Calendar</button></div></div></div>{view === "calendar" ? salon ? <SalonCalendar data={{ ...data, reservations: rows }} onEdit={onEdit} /> : <ReservationCalendar data={{ ...data, reservations: rows }} onEdit={onEdit} /> : <Panel title={salon ? "Appointments" : "Guest reservations"} action={<small className="muted">{rows.length} bookings · {data.location.timezone}</small>}><DataTable rows={rows} columns={columns} rowKey={b => b.id} empty={<Empty title="No matching bookings" detail="Create a manual booking or use the AI receptionist to prepare one." />} /></Panel>}</>;
}
