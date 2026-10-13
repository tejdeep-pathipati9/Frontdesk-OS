import { Database, Plug, RefreshCw } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./workflows";
import { Panel } from "./shared/panel";
import { time } from "@/lib/api";
import type { Integration } from "@/lib/types";
import { providerLabel } from "./shared/operational-source";

export function IntegrationCard({ integration, timezone, busy, onTest, onSync, onDisconnect }: { integration: Integration; timezone: string; busy: boolean; onTest?: () => void; onSync?: () => void; onDisconnect?: () => void }) {
  const c = integration;
  const title = providerLabel(c.provider);
  return <Panel title={title} action={<span className="provider-logo">{["internal", "supabase", "sqlite", "postgresql"].includes(c.provider) ? <Database size={18} /> : <Plug size={18} />}</span>}><div className="panel-body"><Badge status={c.mode}>{c.provider === "internal" && c.mode === "demo" ? "Local" : c.mode.replaceAll("_", " ")}</Badge><div className="capability-list">{Object.entries(c.capabilities).map(([capability, state]) => <div key={capability}><span>{capability.replaceAll("_", " ")}</span><Badge status={state}>{state.replaceAll("_", " ")}</Badge></div>)}</div><div className="integration-health"><small>Last tested</small><span>{c.last_tested_at ? time(c.last_tested_at, timezone, true) : "Not tested"}</span><small>Last synchronized</small><span>{c.last_sync_at ? `${c.sync_status} · ${time(c.last_sync_at, timezone, true)}` : c.connection_status || (c.provider === "internal" ? "Current" : ["supabase", "sqlite", "postgresql"].includes(c.provider) && c.mode === "connected" ? "Connected" : "Not activated")}</span></div>{(c.booking_authority || c.order_authority) && <p className="text-small">Selected for {[c.booking_authority && "bookings", c.order_authority && "orders"].filter(Boolean).join(" and ")}.</p>}{onSync && <div className="form-actions"><Button variant="outline" disabled={busy} onClick={onSync}><RefreshCw size={13} />Synchronize</Button><Button variant="ghost" disabled={busy} onClick={onDisconnect}>Disconnect</Button></div>}{c.error_code && <div className="notice">{c.error_code.replaceAll("_", " ")}</div>}{onTest && <Button variant="outline" className="full-width" disabled={busy} onClick={onTest}><RefreshCw size={13} />Test connection</Button>}</div></Panel>;
}
