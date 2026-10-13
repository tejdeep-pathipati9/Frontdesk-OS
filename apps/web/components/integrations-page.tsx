"use client";
// Integrations screen: lists providers and drives test/sync/disconnect actions.
import { useCallback, useEffect, useState } from "react";
import { api, mutation } from "@/lib/api";
import { IntegrationCard } from "./integration-card";
import { providerLabel } from "./shared/operational-source";
import { Panel } from "./shared/panel";
import { Button } from "./ui/button";
import { ErrorText } from "./workflows";
import type { Integration, Snapshot } from "@/lib/types";

export function IntegrationsPage({ data, onUpdated }: { data: Snapshot; onUpdated: () => void }) {
  const [connections, setConnections] = useState<Integration[]>([]); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const [software, setSoftware] = useState("mock_opentable"); const [scenario, setScenario] = useState("normal");
  const [booking, setBooking] = useState(data.location.settings.booking_provider || "internal"); const [orders, setOrders] = useState(data.location.settings.order_provider || "internal");
  useEffect(() => { setBooking(data.location.settings.booking_provider || "internal"); setOrders(data.location.settings.order_provider || "internal"); }, [data.location.settings.booking_provider, data.location.settings.order_provider]);
  const load = useCallback(async () => { try { setConnections(await api<Integration[]>(`/integrations?location_id=${data.location.id}`)); } catch (e) { setError((e as Error).message); } }, [data.location.id]);
  useEffect(() => { void load(); }, [load, data.as_of]);
  async function run(path: string, body?: unknown) { setBusy(true); setError(""); try { await mutation(path, body); await load(); onUpdated(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  const connected = connections.filter(c => c.mode === "mock" && !["not_connected", "disconnected"].includes(c.connection_status || ""));
  const visible = connections.filter(c => !["sqlite", "postgresql", "supabase"].includes(c.provider) && (!c.provider.startsWith("mock_") || connected.some(connection => connection.provider === c.provider)));
  return <><ErrorText error={error} />{data.location.vertical === "restaurant" && <Panel title="Operational providers"><div className="panel-body form-grid"><label>Reservations<select value={booking} onChange={e => setBooking(e.target.value)}>{["internal", "staff_requests", ...connected.filter(c => c.capabilities.bookings_write === "supported").map(c => c.provider)].map(p => <option key={p} value={p}>{providerLabel(p)}</option>)}</select></label><label>Takeout orders<select value={orders} onChange={e => setOrders(e.target.value)}>{["internal", ...connected.filter(c => c.capabilities.orders_write === "supported").map(c => c.provider)].map(p => <option key={p} value={p}>{providerLabel(p)}</option>)}</select></label><div className="form-actions span-2"><Button disabled={busy} onClick={() => run(`/locations/${data.location.id}/booking-authority`, { booking_provider: booking, order_provider: orders })}>Save providers</Button></div></div></Panel>}
    <div className="integration-grid">{visible.map(integration => <IntegrationCard key={integration.provider} integration={integration} timezone={data.location.timezone} vertical={data.location.vertical} busy={busy} onSync={connected.some(c => c.provider === integration.provider) ? () => run(`/integrations/${integration.provider}/sync?location_id=${data.location.id}`) : undefined} onDisconnect={() => run(`/integrations/${integration.provider}/disconnect?location_id=${data.location.id}`)} onTest={["square", "toast"].includes(integration.provider) ? () => run(`/integrations/${integration.provider}/test?location_id=${data.location.id}`) : undefined} />)}</div>
    {data.location.vertical === "restaurant" && <details className="panel receptionist-advanced"><summary>Test connections</summary><div className="panel-body form-grid"><label>Provider<select value={software} onChange={e => setSoftware(e.target.value)}>{["mock_opentable", "mock_toast", "mock_square"].map(p => <option key={p} value={p}>{providerLabel(p)}</option>)}</select></label><label>Scenario<select value={scenario} onChange={e => setScenario(e.target.value)}>{["normal", "unavailable", "stale", "failure", "ambiguous"].map(s => <option key={s} value={s}>{s}</option>)}</select></label><div className="form-actions span-2"><Button variant="outline" disabled={busy || data.location.mode !== "demo"} onClick={() => run(`/integrations/${software}/connect`, { location_id: data.location.id, scenario })}>Import test configuration</Button></div></div></details>}
  </>;
}
