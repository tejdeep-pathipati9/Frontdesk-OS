import type { LucideIcon } from "lucide-react";

export function MetricCard({ label, value, detail, icon: Icon }: { label: string; value: string | number; detail?: string; icon: LucideIcon }) {
  return <section className="metric-card" data-slot="card"><div className="metric-top"><span>{label}</span><Icon size={16} aria-hidden="true" /></div><strong>{value}</strong>{detail && <small>{detail}</small>}</section>;
}
