import type { ReactNode } from "react";

export function Panel({ title, action, children, className = "" }: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`} data-slot="card"><div className="panel-header"><h3>{title}</h3>{action}</div>{children}</section>;
}
