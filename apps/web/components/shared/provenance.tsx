// Badge showing where a value came from and how fresh it is.
import { Database, UserRound, Calculator, Clock3 } from "lucide-react";

export function Provenance({ source, updatedAt, timezone, stale = false }: { source: "staff_update" | "connected_system" | "calculated_estimate" | "internal"; updatedAt?: string; timezone?: string; stale?: boolean }) {
  const Icon = source === "connected_system" ? Database : source === "calculated_estimate" ? Calculator : source === "staff_update" ? UserRound : Database;
  const label = { staff_update: "Staff update", connected_system: "Connected system", calculated_estimate: "Calculated estimate", internal: "FrontDesk OS" }[source];
  return <span className="provenance"><Icon size={12} /><span>{label}</span>{updatedAt && <span>· {new Intl.DateTimeFormat("en-US", { timeZone: timezone, hour: "numeric", minute: "2-digit" }).format(new Date(updatedAt))}</span>}{stale && <span className="stale-label"><Clock3 size={11} />Update required</span>}</span>;
}
