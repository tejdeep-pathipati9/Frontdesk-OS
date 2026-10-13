"use client";
// Dashboard chrome: sidebar, header, account menu and mobile drawer around page content.
import { useEffect, useState, type ReactNode } from "react";
import { Bell, PanelLeft, RefreshCw } from "lucide-react";
import { AppSidebar, pageTitles } from "./app-sidebar";
import type { Location } from "@/lib/types";

/** Shell composing sidebar, header and content area. */
export function DashboardLayout({ userName, vertical, section, location, locations, attentionCount, onSelectLocation, onCreateLocation, onLogout, onRefresh, onAttention, actions, children }: { userName: string; vertical: "restaurant" | "salon"; section: string; location?: Location; locations: Location[]; attentionCount: number; onSelectLocation: (loc: Location) => void; onCreateLocation: () => void; onLogout: () => void; onRefresh: () => void; onAttention: () => void; actions?: ReactNode; children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false); const [mobileOpen, setMobileOpen] = useState(false);
  useEffect(() => { setCollapsed(localStorage.getItem("frontdesk-sidebar-collapsed") === "true"); }, []);
  function toggle() { if (window.innerWidth < 768) setMobileOpen(v => !v); else setCollapsed(v => { localStorage.setItem("frontdesk-sidebar-collapsed", String(!v)); return !v; }); }
  const date = new Intl.DateTimeFormat("en-US", { timeZone: location?.timezone || "America/New_York", month: "short", day: "numeric", weekday: "short" }).format(new Date());
  const recordLayout = ["reservations", "calendar", "waitlist", "orders", "calls"].includes(section);
  return <div className={`app-shell ${collapsed ? "shell-collapsed" : ""}`}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <AppSidebar userName={userName} vertical={vertical} section={section} location={location} locations={locations} collapsed={collapsed} mobileOpen={mobileOpen} attentionCount={attentionCount} onToggle={toggle} onNavigate={() => setMobileOpen(false)} onSelect={onSelectLocation} onCreate={onCreateLocation} onLogout={onLogout} />
    {mobileOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
    <div className={`app-main ${vertical === "restaurant" && section === "overview" ? "restaurant-overview" : ""}`}>
      <header className="topbar">
        <div className="topbar-left"><button className="icon-button" aria-label="Toggle sidebar" aria-expanded={mobileOpen || !collapsed} onClick={toggle}><PanelLeft size={16} /></button><span className="header-divider" /><h1>{pageTitles[section]}</h1></div>
        <div className="topbar-right">{section !== "overview" && <span className="topbar-date">{date}</span>}<div className="header-actions">{actions}</div><button className="icon-button" aria-label="Refresh data" title="Refresh data" onClick={onRefresh}><RefreshCw size={15} /></button><button className="icon-button" onClick={onAttention} aria-label={`Needs attention: ${attentionCount} requests`}><Bell size={15} />{attentionCount > 0 && <span className="notification-count">{attentionCount}</span>}</button></div>
      </header>
      <main className={`main-content ${recordLayout ? "record-layout" : ""}`} id="main-content">
        {children}
      </main>
    </div>
  </div>;
}
