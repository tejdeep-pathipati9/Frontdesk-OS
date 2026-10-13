"use client";
// User account dropdown with navigation shortcuts and logout.

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { ChevronUp, LogOut, Settings } from "lucide-react";

export function AccountMenu({ userName, vertical, onNavigate, onLogout }: {
  userName: string;
  vertical: "restaurant" | "salon";
  onNavigate: () => void;
  onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const firstAction = useRef<HTMLAnchorElement>(null);
  const words = userName.trim().split(/\s+/).filter(Boolean);
  const initials = [words[0]?.[0], words.length > 1 ? words.at(-1)?.[0] : ""].join("").toUpperCase() || "?";

  useEffect(() => {
    if (!open) return;
    firstAction.current?.focus();
    function dismiss(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  return <div className="account-menu" ref={root}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}
    onKeyDown={event => {
      if (event.key === "Escape" && open) {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        trigger.current?.focus();
      }
    }}>
    <button ref={trigger} className="owner-profile" aria-label={`Account settings for ${userName}`}
      aria-expanded={open} aria-controls={open ? id : undefined}
      title={userName} onClick={() => setOpen(value => !value)}>
      <span className="owner-avatar" aria-hidden="true">{initials}</span>
      <span className="sidebar-label"><strong>{userName}</strong><small>Account settings</small></span>
      <ChevronUp size={14} className="sidebar-label" aria-hidden="true" />
    </button>
    {open && <nav id={id} className="account-menu-panel" aria-label="Account">
      <Link ref={firstAction} href={`/app/${vertical}/settings`} onClick={() => { setOpen(false); onNavigate(); }}>
        <Settings size={15} aria-hidden="true" />Settings
      </Link>
      <button onClick={() => { setOpen(false); onLogout(); }}><LogOut size={15} aria-hidden="true" />Sign out</button>
    </nav>}
  </div>;
}
