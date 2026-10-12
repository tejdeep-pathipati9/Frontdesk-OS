"use client";
import { useState } from "react";
import { ArrowDownLeft, ChevronRight, Search } from "lucide-react";
import { Badge, Empty } from "./workflows";
import { Panel } from "./shared/panel";
import { DataTable, type Column } from "./shared/data-table";
import { time, localInput } from "@/lib/api";
import type { Call, Snapshot } from "@/lib/types";

export function CallsPage({ data, onOpen, compact = false }: { data: Snapshot; onOpen: (id: string) => void; compact?: boolean }) {
  const [query, setQuery] = useState(""); const [outcome, setOutcome] = useState("all"); const [source, setSource] = useState("all"); const [date, setDate] = useState("");
  const timezone = data.location.timezone;
  const rows = data.calls.filter(call => (outcome === "all" || call.outcome === outcome) && (source === "all" || call.provider === source) && (!date || localInput(new Date(call.created_at), timezone).startsWith(date)) && (call.id + call.intent + call.caller_masked + call.summary).toLowerCase().includes(query.toLowerCase()));
  const columns: Column<Call>[] = [
    { key: "caller", title: "Caller", render: call => <div className="call-identity"><span className="call-icon"><ArrowDownLeft size={14} /></span><div><strong>{call.caller_masked}</strong><small>#{call.id.slice(0, 8)}</small></div></div> },
    { key: "time", title: "Received", sortValue: call => call.created_at, render: call => <><span>{time(call.created_at, timezone, true)}</span><small>{call.ended_at ? `${Math.round((Date.parse(call.ended_at) - Date.parse(call.created_at)) / 1000)}s` : "In progress"}</small></> },
    { key: "request", title: "Request", render: call => <span className="capitalize">{call.intent.replaceAll("_", " ")}</span> },
    { key: "source", title: "Source", render: call => <Badge>{call.provider === "tool_test" ? "Tool test" : call.provider === "ollama_text" ? "Ollama text" : call.provider}</Badge> },
    { key: "outcome", title: "Outcome", sortValue: call => call.outcome, render: call => <Badge status={call.outcome}>{call.outcome.replaceAll("_", " ")}</Badge> },
    { key: "view", title: "", render: call => <button className="icon-button" aria-label={`Open call ${call.id}`} onClick={event => { event.stopPropagation(); onOpen(call.id); }}><ChevronRight size={14} /></button> },
  ];
  return <>{!compact && <div className="filter-row"><div className="search-field"><Search size={14} /><input placeholder="Search caller, request, or call ID…" value={query} onChange={e => setQuery(e.target.value)} aria-label="Search calls" /></div><div className="filter-controls"><select aria-label="Filter calls by outcome" value={outcome} onChange={e => setOutcome(e.target.value)}><option value="all">All outcomes</option>{["fulfilled", "pending", "escalated", "failed", "information_only"].map(value => <option value={value} key={value}>{value.replaceAll("_", " ")}</option>)}</select><select aria-label="Filter calls by provider" value={source} onChange={e => setSource(e.target.value)}><option value="all">All sources</option>{["vapi", "livekit", "ollama_text", "tool_test"].map(value => <option value={value} key={value}>{value.replaceAll("_", " ")}</option>)}</select><input type="date" aria-label="Filter calls by date" value={date} onChange={e => setDate(e.target.value)} /></div></div>}<Panel title={compact ? "Recent customer calls" : "Incoming & completed calls"} action={<small className="muted">{compact ? "Latest activity" : `${rows.length} records`}</small>}><DataTable rows={compact ? rows.slice(0, 5) : rows} columns={compact ? columns.filter(c => c.key !== "source") : columns} rowKey={call => call.id} onRowClick={call => onOpen(call.id)} empty={<Empty title="No matching calls" detail="Real voice calls and separately labeled tool tests appear here when they occur." />} /></Panel></>;
}
