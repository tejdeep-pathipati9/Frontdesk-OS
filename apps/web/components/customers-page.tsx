"use client";
// Customers screen: masked contact list with booking/order history.
import { useCallback, useEffect, useState } from "react";
import { Search, Pencil } from "lucide-react";
import { api, mutation, time } from "@/lib/api";
import type { Customer, Snapshot } from "@/lib/types";
import { Panel } from "./shared/panel";
import { DataTable, type Column } from "./shared/data-table";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";
import { Empty, ErrorText } from "./workflows";

export function CustomersPage({ data, onUpdated }: { data: Snapshot; onUpdated: () => void }) {
  const [customers, setCustomers] = useState<Customer[]>([]); const [query, setQuery] = useState(""); const [error, setError] = useState(""); const [editing, setEditing] = useState<Customer | null>(null); const [name, setName] = useState(""); const [phone, setPhone] = useState(""); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { try { setCustomers(await api<Customer[]>(`/customers?location_id=${data.location.id}`)); } catch (e) { setError((e as Error).message); } }, [data.location.id]);
  useEffect(() => { void load(); }, [load, data.as_of]);
  async function save(event: React.FormEvent) { event.preventDefault(); setBusy(true); setError(""); try { await mutation(`/customers/${editing!.id}`, { display_name: name, ...(phone ? { phone } : {}) }, "PATCH"); setEditing(null); await load(); onUpdated(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  const columns: Column<Customer>[] = [
    { key: "name", title: "Customer", sortValue: c => c.display_name, render: c => <strong>{c.display_name}</strong> },
    { key: "phone", title: "Contact", render: c => c.phone_masked },
    { key: "bookings", title: "Bookings", sortValue: c => c.booking_count, render: c => c.booking_count },
    { key: "orders", title: "Takeout orders", sortValue: c => c.order_count, render: c => c.order_count },
    { key: "last", title: "Last activity", sortValue: c => c.last_activity_at, render: c => time(c.last_activity_at, data.location.timezone, true) },
    { key: "edit", title: "", render: c => <Button variant="ghost" size="sm" onClick={() => { setEditing(c); setName(c.display_name); setPhone(""); setError(""); }}><Pencil size={12} />Edit</Button> },
  ];
  return <><div className="filter-row"><div className="search-field"><Search size={14} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search customers…" aria-label="Search customers" /></div><span className="muted text-small">Customers from real booking and order records</span></div><ErrorText error={error} /><Panel title="Customer records" action={<span className="muted text-small">{customers.length} records</span>}><DataTable rows={customers.filter(c => (c.display_name + c.phone_masked).toLowerCase().includes(query.toLowerCase()))} columns={columns} rowKey={c => c.id} empty={<Empty title="No customer records yet" detail="Customers are created when a booking or order is committed." />} /></Panel><Dialog open={!!editing} onClose={() => setEditing(null)} title="Edit customer" description="Customer contact remains masked in list views."><form className="form-grid" onSubmit={save}><label className="span-2">Customer name<input value={name} onChange={e => setName(e.target.value)} required /></label><label className="span-2">Replace phone (optional)<input value={phone} onChange={e => setPhone(e.target.value)} placeholder="+12125550123" /><small>Leave blank to keep the existing contact number.</small></label><ErrorText error={error} /><div className="form-actions span-2"><Button disabled={busy} type="submit">Save customer</Button></div></form></Dialog></>;
}
