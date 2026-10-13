import { Database, Plug, RefreshCw } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./workflows";
import { Panel } from "./shared/panel";
import { time } from "@/lib/api";
import type { Integration } from "@/lib/types";
import { providerLabel } from "./shared/operational-source";

const capabilityLabels: Record<string, string> = {
  configuration_read: "Import business settings", tables_read: "Import tables",
  table_status_read: "Table status", table_occupancy: "Live table occupancy",
  pos_signals_read: "POS activity", reservations_read: "View reservations",
  bookings_read: "View appointments", availability_read: "Check availability",
  bookings_write: "Create bookings", bookings_cancel: "Cancel bookings",
  bookings_modify: "Reschedule bookings", orders_read: "View orders",
  orders_write: "Submit orders", menu_read: "Import menu", locations_read: "Business locations",
};
const stateLabels: Record<string, string> = { supported: "Available", read_only: "Read only", requires_permission: "Permission required", unsupported: "Not available", not_configured: "Not connected", degraded: "Connection issue", demo: "Test data" };

export function IntegrationCard({ integration: c, timezone, vertical, busy, onTest, onSync, onDisconnect }: {
  integration: Integration; timezone: string; vertical: "restaurant" | "salon"; busy: boolean;
  onTest?: () => void; onSync?: () => void; onDisconnect?: () => void;
}) {
  const internal = c.provider === "internal";
  const sample = c.provider.startsWith("mock_");
  const disconnected = c.mode === "not_configured" || ["not_connected", "disconnected"].includes(c.connection_status || "");
  const issue = !!c.error_code || ["degraded", "needs_attention"].includes(c.mode) || ["failed", "degraded", "stale"].includes(c.sync_status || "");
  const status = internal ? "Active" : issue ? "Needs attention" : disconnected ? "Not connected" : sample ? "Test connection" : c.mode === "configured_untested" ? "Ready to verify" : c.mode === "connected_read_only" ? "Connected · Read only" : "Connected";
  const provider = c.provider.replace("mock_", "");
  const description = internal ? vertical === "restaurant" ? "Manage reservations, tables, and takeout in FrontDesk OS." : "Manage appointments and staff schedules in FrontDesk OS." : provider === "opentable" ? "Reservations and seating availability." : provider === "toast" ? "Restaurant menu, orders, and POS activity." : vertical === "salon" ? "Appointments and staff availability." : "Menu catalog and takeout orders.";
  const features = [
    { label: vertical === "restaurant" ? "Reservations" : "Appointments", write: "bookings_write", read: ["reservations_read", "bookings_read"] },
    ...(vertical === "restaurant" ? [
      { label: "Table status", write: "table_status_read", read: ["tables_read"] },
      { label: "Menu", write: "menu_read", read: [] },
      { label: "Takeout orders", write: "orders_write", read: ["orders_read"] },
    ] : []),
  ].flatMap(feature => {
    const enabled = c.capabilities[feature.write] === "supported";
    const readOnly = feature.read.some(key => ["supported", "read_only"].includes(c.capabilities[key]));
    return enabled || readOnly ? [{ label: feature.label, status: enabled ? "Enabled" : "Read only" }] : [];
  });
  return <Panel title={providerLabel(provider)} className="integration-card" action={<span className="provider-logo">{internal ? <Database size={18} /> : <Plug size={18} />}</span>}>
    <div className="panel-body">
      <div className="integration-summary"><Badge>{status}</Badge><p>{description}</p></div>
      {sample && <p className="integration-test-label">Sample data · No live provider connection</p>}
      {(c.booking_authority || c.order_authority) && <p className="integration-selected">Selected for {[c.booking_authority && (vertical === "restaurant" ? "reservations" : "appointments"), c.order_authority && "orders"].filter(Boolean).join(" and ")}</p>}
      {!disconnected && features.length > 0 && <div className="integration-features">{features.map(feature => <div key={feature.label}><span>{feature.label}</span><span>{feature.status}</span></div>)}</div>}
      {c.last_sync_at && <p className="integration-last-sync">Last synced {time(c.last_sync_at, timezone, true)}{issue ? " · Update required" : ""}</p>}
      {issue && <p className="integration-issue">Review this connection before accepting new requests through it.</p>}
      {(onSync || onTest && !disconnected) && <div className="integration-actions">
        {onSync && <Button variant="outline" size="sm" disabled={busy} onClick={onSync}><RefreshCw size={13} />Sync now</Button>}
        {onTest && !disconnected && <Button variant="outline" size="sm" disabled={busy} onClick={onTest}>Check connection</Button>}
        {onSync && onDisconnect && <Button variant="ghost" size="sm" disabled={busy} onClick={onDisconnect}>Disconnect</Button>}
      </div>}
      <details className="integration-details"><summary>{disconnected ? "Connection requirements" : "Connection details"}</summary>
        {disconnected && <p>Authorized account access is required to activate this connection.</p>}
        <div className="capability-list">{Object.entries(c.capabilities).map(([capability, state]) => <div key={capability}><span>{capabilityLabels[capability] || capability.replaceAll("_", " ")}</span><span>{stateLabels[state] || state.replaceAll("_", " ")}</span></div>)}</div>
        {c.last_tested_at && <div className="integration-health"><small>Last checked</small><span>{time(c.last_tested_at, timezone, true)}</span></div>}
        {c.error_code && <p className="muted text-small">Connection error: {c.error_code}</p>}
      </details>
    </div>
  </Panel>;
}
