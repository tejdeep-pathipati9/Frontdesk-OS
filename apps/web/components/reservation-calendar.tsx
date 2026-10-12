"use client";
import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./ui/button";
import { Panel } from "./shared/panel";
import { localInput, time } from "@/lib/api";
import type { Booking, Snapshot } from "@/lib/types";

export function ReservationCalendar({ data, onEdit }: { data: Snapshot; onEdit: (booking: Booking) => void }) {
  const zone = data.location.timezone; const [week, setWeek] = useState(localInput(new Date(), zone).slice(0, 10));
  function dayOffset(value: string, count: number) { const date = new Date(`${value}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + count); return date.toISOString().slice(0, 10); }
  const days = Array.from({ length: 7 }, (_, i) => dayOffset(week, i));
  return <Panel title="Reservation calendar" action={<div className="calendar-period-controls"><Button variant="ghost" size="icon" aria-label="Previous week" onClick={() => setWeek(dayOffset(week, -7))}><ChevronLeft size={14} /></Button><Button variant="outline" size="sm" onClick={() => setWeek(localInput(new Date(), zone).slice(0, 10))}>Today</Button><Button variant="ghost" size="icon" aria-label="Next week" onClick={() => setWeek(dayOffset(week, 7))}><ChevronRight size={14} /></Button></div>}><div className="reservation-week">{days.map(day => <div key={day} className="reservation-day"><h4>{new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" }).format(new Date(`${day}T12:00:00Z`))}</h4>{data.reservations.filter(b => ["confirmed", "seated"].includes(b.status) && localInput(new Date(b.start_at), zone).startsWith(day)).sort((a, b) => a.start_at.localeCompare(b.start_at)).map(b => <button key={b.id} className="calendar-booking" onClick={() => onEdit(b)}><strong>{time(b.start_at, zone)} · {b.customer_name}</strong><small>{b.party_size} guests · {b.resources.join(" + ")}</small></button>)}</div>)}</div><div className="panel-footer"><span>{zone} · Select a reservation to reschedule</span></div></Panel>;
}
