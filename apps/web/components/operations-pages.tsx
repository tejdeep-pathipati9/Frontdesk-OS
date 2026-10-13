"use client";
// Waitlist, menu availability, staff and services management pages.
import { useState } from "react";
import { Clock3, Scissors, Search, Users } from "lucide-react";
import { Panel } from "./shared/panel";
import { DataTable, type Column } from "./shared/data-table";
import { Provenance } from "./shared/provenance";
import { Button } from "./ui/button";
import { Badge, Empty, ErrorText } from "./workflows";
import { money, mutation, time } from "@/lib/api";
import type { Resource, Snapshot, Waitlist } from "@/lib/types";

export function WaitlistPage({ data, onUpdated, onSeat }: { data: Snapshot; onUpdated: () => void; onSeat: () => void }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [history, setHistory] = useState(false);
  async function remove(row: Waitlist) { setBusy(true); try { await mutation(`/waitlist/${row.id}`, { status: "removed" }, "PATCH"); onUpdated(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  const rows = data.waitlist.filter(w => history || w.status === "waiting").sort((a, b) => a.created_at.localeCompare(b.created_at));
  const columns: Column<Waitlist>[] = [
    { key: "name", title: "Customer", render: w => <strong>{w.name}</strong> },
    { key: "party", title: "Guests", render: w => w.party_size },
    { key: "joined", title: "Joined", sortValue: w => w.created_at, render: w => time(w.created_at, data.location.timezone, true) },
    { key: "estimate", title: "Estimate at join", render: w => <>{w.estimate_min === null ? "Unknown" : `${w.estimate_min}–${w.estimate_max} min`}<small>Calculated estimate</small></> },
    { key: "status", title: "Status", render: w => <Badge>{w.status}</Badge> },
    { key: "action", title: "", render: w => w.status === "waiting" && <div className="row-actions"><Button variant="outline" size="sm" onClick={onSeat}>Manage seating</Button><Button variant="ghost" size="sm" disabled={busy} onClick={() => remove(w)}>Remove</Button></div> },
  ];
  return <><div className="filter-row"><div className="segmented"><button className={!history ? "selected" : ""} onClick={() => setHistory(false)}>Waiting</button><button className={history ? "selected" : ""} onClick={() => setHistory(true)}>All entries</button></div></div><ErrorText error={error} /><Panel title="Guest waitlist"><DataTable rows={rows} columns={columns} rowKey={w => w.id} empty={<Empty title="No waiting guests" detail="Add a guest to see their estimated seating time." />} /></Panel><div className="notice"><Clock3 size={15} /><span>Estimates shown at join time.</span></div></>;
}

export function MenuPage({ data, onToggle, busy }: { data: Snapshot; onToggle: (id: string, available: boolean) => void; busy: boolean }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all"); const categories = [...new Set(data.menu.map(item => item.category))].sort((a, b) => ["Starters", "Mains", "Sides", "Desserts", "Drinks"].indexOf(a) - ["Starters", "Mains", "Sides", "Desserts", "Drinks"].indexOf(b));
  const columns: Column<Snapshot["menu"][number]>[] = [
    { key: "name", title: "Menu item", sortValue: item => item.name, render: item => <><strong>{item.name}</strong><small>{item.description}</small></> },
    { key: "category", title: "Category", render: item => item.category },
    { key: "price", title: "Price", sortValue: item => item.price_minor, render: item => money(item.price_minor) },
    { key: "modifiers", title: "Modifiers", render: item => <div className="modifier-list">{item.modifiers.length ? item.modifiers.map(modifier => <span key={modifier.id}>{modifier.name}{modifier.price_minor > 0 ? ` +${money(modifier.price_minor)}` : ""}</span>) : <span className="muted">None</span>}</div> },
    { key: "work", title: "Kitchen workload", render: item => item.work_units },
    { key: "available", title: "Available", render: item => <button role="switch" aria-checked={item.available} aria-label={`${item.name} availability`} className={`switch ${item.available ? "on" : ""}`} disabled={busy} onClick={() => onToggle(item.id, !item.available)}><span /></button> },
  ];
  const rows = data.menu.filter(item => (category === "all" || item.category === category) && `${item.name} ${item.description}`.toLowerCase().includes(query.toLowerCase()));
  return <><div className="filter-row"><div className="segmented"><button className={category === "all" ? "selected" : ""} onClick={() => setCategory("all")}>All items</button>{categories.map(c => <button key={c} className={category === c ? "selected" : ""} onClick={() => setCategory(c)}>{c}</button>)}</div><Provenance source={data.location.settings.catalog_provider ? "connected_system" : "internal"} /></div><div className="filter-row"><div className="search-field"><Search size={14} /><input aria-label="Search menu" placeholder="Search dishes…" value={query} onChange={event => setQuery(event.target.value)} /></div><span className="muted text-small">{data.menu.filter(item => item.available).length} available · {data.menu.length} items</span></div><Panel title="Restaurant menu"><DataTable rows={rows} columns={columns} rowKey={item => item.id} empty={<Empty title={data.menu.length ? "No matching dishes" : "No menu items"} detail={data.menu.length ? "Try another search or category." : "Connect a menu provider in Integrations."} />} /></Panel></>;
}

export function StaffPage({ data, onEdit, onToggle, busy }: { data: Snapshot; onEdit: (resource: Resource) => void; onToggle: (resource: Resource) => void; busy: boolean }) {
  return <div className="staff-grid">{data.resources.map(resource => <Panel title={resource.name} key={resource.id} action={<span className="staff-avatar">{resource.name.split(" ").map(n => n[0]).join("")}</span>}><div className="panel-body"><Badge>{resource.state === "available" ? "Available" : "Unavailable"}</Badge><div className="staff-info"><span>Working hours</span><strong>{resource.attributes.shift_start}:00–{resource.attributes.shift_end}:00</strong></div><div className="staff-info"><span>Qualified services</span><div className="service-tags">{data.services.filter(s => s.eligible_staff.includes(resource.id)).map(s => <span key={s.id}>{s.name}</span>)}</div></div><div className="staff-info"><span>Blocked intervals</span><strong>{resource.attributes.blocked?.length || 0}</strong></div><div className="row-actions"><Button variant="outline" size="sm" onClick={() => onEdit(resource)}>Edit schedule</Button><Button variant="ghost" size="sm" disabled={busy} onClick={() => onToggle(resource)}>{resource.state === "available" ? "Mark unavailable" : "Mark available"}</Button></div></div></Panel>)}{!data.resources.length && <Empty title="No staff configured" detail="Add stylists and assign their qualified services." />}</div>;
}

export function ServicesPage({ data, onToggle, busy }: { data: Snapshot; onToggle: (id: string, enabled: boolean) => void; busy: boolean }) {
  return <div className="menu-grid">{data.services.map(service => <Panel title={service.name} key={service.id} action={<Scissors size={17} className="muted" />}><div className="panel-body"><p className="muted">{service.duration_min} min + {service.buffer_min} min buffer</p><strong className="service-price">{money(service.price_minor)}</strong><div className="service-tags">{data.resources.filter(r => service.eligible_staff.includes(r.id)).map(r => <span key={r.id}>{r.name}</span>)}</div><div className="menu-card-bottom"><Badge>{service.enabled ? "Enabled" : "Disabled"}</Badge><button role="switch" aria-checked={service.enabled} aria-label={`${service.name} enabled`} className={`switch ${service.enabled ? "on" : ""}`} disabled={busy} onClick={() => onToggle(service.id, !service.enabled)}><span /></button></div></div></Panel>)}</div>;
}
