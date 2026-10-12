"use client";
import { useEffect, useState, type ReactNode } from "react";
import { Bell, PanelLeft, RefreshCw } from "lucide-react";
import { AppSidebar, pageTitles } from "./app-sidebar";
import { Button } from "./ui/button";
import { Badge } from "./workflows";
import type { Location } from "@/lib/types";

export function DashboardLayout({ vertical, section, location, locations, attentionCount, onSelectLocation, onCreateLocation, onLogout, onRefresh, onAttention, actions, children }: { vertical: "restaurant" | "salon"; section: string; location?: Location; locations: Location[]; attentionCount: number; onSelectLocation: (loc: Location) => void; onCreateLocation: () => void; onLogout: () => void; onRefresh: () => void; onAttention: () => void; actions?: ReactNode; children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false); const [mobileOpen, setMobileOpen] = useState(false);
  useEffect(() => { setCollapsed(localStorage.getItem("frontdesk-sidebar-collapsed") === "true"); }, []);
  function toggle() { if (window.innerWidth < 768) setMobileOpen(v => !v); else setCollapsed(v => { localStorage.setItem("frontdesk-sidebar-collapsed", String(!v)); return !v; }); }
  const date = new Intl.DateTimeFormat("en-US", { timeZone: location?.timezone || "America/New_York", month: "short", day: "numeric", weekday: "short" }).format(new Date());
  const recordLayout = ["reservations", "calendar", "waitlist", "orders", "calls", "customers"].includes(section);
  return <div className={`app-shell ${collapsed ? "shell-collapsed" : ""}`}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <AppSidebar vertical={vertical} section={section} location={location} locations={locations} collapsed={collapsed} mobileOpen={mobileOpen} attentionCount={attentionCount} onToggle={toggle} onNavigate={() => setMobileOpen(false)} onSelect={onSelectLocation} onCreate={onCreateLocation} onLogout={onLogout} />
    {mobileOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
    <div className="app-main">
      <header className="topbar">
        <div className="topbar-left"><button className="icon-button" aria-label="Toggle sidebar" aria-expanded={mobileOpen || !collapsed} onClick={toggle}><PanelLeft size={16} /></button><span className="header-divider" /><h1>{pageTitles[section]}</h1></div>
        <div className="topbar-right"><span className="topbar-date">{date}</span><Badge>{location?.mode === "demo" ? "Demo" : "Live"}</Badge><div className="header-actions">{actions}</div><button className="icon-button" onClick={onAttention} aria-label={`Needs attention: ${attentionCount} requests`}><Bell size={15} />{attentionCount > 0 && <span className="notification-count">{attentionCount}</span>}</button></div>
      </header>
      <main className={`main-content ${recordLayout ? "record-layout" : ""}`} id="main-content">
        <div className="page-heading"><p>{section === "overview" ? `Today at ${location?.name || "your business"}` : pageDescriptions[section]}</p><Button variant="ghost" size="icon" aria-label="Refresh operational data" onClick={onRefresh}><RefreshCw size={14} /></Button></div>
        {children}
      </main>
    </div>
  </div>;
}
const pageDescriptions: Record<string, string> = { floor: "Manage table states, capacity, and availability.", reservations: "Upcoming guests, booking controls, and calendar views.", waitlist: "Waiting guests and seating estimates.", kitchen: "Manage order intake and the preparation queue.", orders: "All takeout orders and their latest status.", menu: "The catalog and modifiers available to your receptionist.", calls: "Customer requests, conversation history, and verified actions.", attention: "Requests that need a staff decision or state verification.", voice: "Supported actions, voice providers, and agent testing.", customers: "Customers associated with this location’s bookings and orders.", integrations: "Connection capabilities, health, and configuration.", settings: "Operating hours and business policies.", calendar: "Appointments by day, week, and stylist.", staff: "Working hours, qualified services, and blocked time.", services: "Bookable services and eligible staff." };
