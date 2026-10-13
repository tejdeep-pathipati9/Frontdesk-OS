// Shows which provider is the source of operational data.
import Link from "next/link";
import { Database } from "lucide-react";
import { time } from "@/lib/api";
import type { Snapshot } from "@/lib/types";

export const providerLabel = (provider: string) => ({ internal: "FrontDesk OS", staff_requests: "Staff-confirmed requests", mock_opentable: "OpenTable · Mock", mock_toast: "Toast · Mock", mock_square: "Square · Mock", opentable: "OpenTable", toast: "Toast", square: "Square", sqlite: "SQLite · Local database", postgresql: "PostgreSQL", supabase: "Supabase" }[provider] || provider);

export function OperationalSource({ data }: { data: Snapshot }) {
  if (data.location.vertical !== "restaurant") return null;
  const settings = data.location.settings;
  return <div className="notice"><Database size={15} /><div><strong>Bookings: {providerLabel(settings.booking_provider || "internal")} · Orders: {providerLabel(settings.order_provider || "internal")}</strong><div className="muted text-small">{data.integration_health?.map(c => <span key={c.provider}>{providerLabel(c.provider)}: {c.sync_status} · {c.last_sync_at ? time(c.last_sync_at, data.location.timezone, true) : "Not synchronized"}. </span>)}Floor: {providerLabel(settings.floor_provider || "internal")} · Catalog: {providerLabel(settings.catalog_provider || "internal")}. Provider bookings are local projections of their source. Floor availability requires staff verification. <Link className="text-link" href="/app/restaurant/integrations">Manage sources</Link></div></div></div>;
}
