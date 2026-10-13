"use client";
// Day calendar of salon appointments by staff member.
import { useState, useRef, useEffect } from "react";
import { ChevronLeft, ChevronRight, Clock3 } from "lucide-react";
import { Button } from "./ui/button";
import { localInput, time } from "@/lib/api";
import type { Snapshot, Booking, Resource } from "@/lib/types";

export default function SalonCalendar({ data, onEdit }: { data: Snapshot; onEdit: (booking: Booking) => void }) {
  const zone = data.location.timezone;
  const [day, setDay] = useState(localInput(new Date(), zone).slice(0, 10));
  const [view, setView] = useState("day");
  function offset(date: string, count: number) { const value = new Date(`${date}T12:00:00Z`); value.setUTCDate(value.getUTCDate() + count); return value.toISOString().slice(0, 10); }
  const days = view === "day" ? [day] : Array.from({ length: 7 }, (_, i) => offset(day, i));
  const opening = Math.max(0, data.location.settings.open_hour); const closing = Math.min(24, data.location.settings.close_hour);
  // Display business-local wall time with a fixed hourly row height; labels and
  // placement are calculated independently of the browser's timezone.
  function minutes(iso: string) { const value = localInput(new Date(iso), zone).split("T")[1].split(":").map(Number); return value[0] * 60 + value[1]; }
  const hours = Array.from({ length: closing - opening }, (_, i) => i + opening);
  const bookings = data.reservations.filter(r => ["confirmed", "seated"].includes(r.status));
  const columns = days.flatMap<{ date: string; staff: Resource | null }>(date => view === "day" ? data.resources.map(staff => ({ date, staff })) : [{ date, staff: null }]);
  return <div className="panel salon-calendar"><div className="calendar-toolbar"><div><Button variant="ghost" size="icon" aria-label="Previous calendar period" onClick={() => setDay(offset(day, view === "day" ? -1 : -7))}><ChevronLeft size={17} /></Button><Button variant="outline" size="sm" onClick={() => setDay(localInput(new Date(), zone).slice(0, 10))}>Today</Button><Button variant="ghost" size="icon" aria-label="Next calendar period" onClick={() => setDay(offset(day, view === "day" ? 1 : 7))}><ChevronRight size={17} /></Button><input type="date" aria-label="Calendar date" value={day} onChange={e => setDay(e.target.value)} /></div><div className="segmented"><button className={view === "day" ? "selected" : ""} onClick={() => setView("day")}>Day</button><button className={view === "week" ? "selected" : ""} onClick={() => setView("week")}>Week</button></div></div><div className="calendar-scroll"><div className="calendar-grid" style={{ gridTemplateColumns: `60px repeat(${Math.max(1, columns.length)}, minmax(${view === "day" ? 180 : 120}px, 1fr))` }}><div className="calendar-head"><Clock3 size={14} /></div>{columns.map((col, i) => <div className="calendar-head" key={i}>{col.staff ? <><span className="calendar-staff-dot" />{col.staff.name}</> : new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${col.date}T12:00:00Z`))}</div>)}<div className="calendar-hours">{hours.map(hour => <div key={hour}>{hour === 0 ? "12 AM" : hour < 12 ? `${hour} AM` : hour === 12 ? "12 PM" : `${hour - 12} PM`}</div>)}</div>{columns.map((col, i) => <div className="calendar-day-column" key={i} style={{ height: hours.length * 52 }}>
    {hours.map(hour => <div className="calendar-hour-line" style={{ top: (hour - opening) * 52 }} key={hour} />)}
    {col.staff?.attributes.blocked?.filter(b => localInput(new Date(b.start_at), zone).startsWith(col.date)).map((b, index) => <div className="calendar-block" key={index} style={{ top: Math.max(0, (minutes(b.start_at) - opening * 60) / 60 * 52), height: Math.max(20, (minutes(b.end_at) - minutes(b.start_at)) / 60 * 52) }}>Blocked</div>)}
    {bookings.filter(b => localInput(new Date(b.start_at), zone).startsWith(col.date) && (!col.staff || b.resource_ids.includes(col.staff.id))).map(b => {
      const staff = data.resources.find(r => r.id === b.resource_ids[0]);
      const top = (minutes(b.start_at) - opening * 60) / 60 * 52;
      const height = Math.max(36, (new Date(b.end_at).getTime() - new Date(b.start_at).getTime()) / 3600000 * 52);
      return <button className="calendar-appointment" style={{ top: Math.max(0, top), height }} key={b.id} onClick={() => onEdit(b)}><strong>{b.customer_name}</strong><small>{time(b.start_at, zone)} · {data.services.find(s => s.id === b.service_id)?.name}</small>{view === "week" && <small>{staff?.name}</small>}</button>;
    })}</div>)}</div></div><div className="panel-footer"><span>{zone} · service duration includes cleanup buffer</span><small>Select an appointment to reschedule</small></div></div>;
}
