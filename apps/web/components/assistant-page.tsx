"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { ArrowUp, CalendarDays, ChevronRight, CircleHelp, Loader2, MessageSquare, Phone, Sparkles } from "lucide-react";
import { api, mutation, time } from "@/lib/api";
import type { Snapshot, AssistantSummary, AssistantAnswer } from "@/lib/types";
import { MetricCard } from "./shared/metric-card";
import { Button } from "./ui/button";
import { ErrorText } from "./workflows";

export function AssistantPage({ data, onCall }: { data: Snapshot; onCall: (id: string) => void }) {
  const [summary, setSummary] = useState<AssistantSummary | null>(null);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<{ question: string; result: AssistantAnswer }[]>([]);
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const request = useRef(0); const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { let active = true; api<AssistantSummary>(`/assistant/summary?location_id=${data.location.id}`).then(value => { if (active) setSummary(value); }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [data.location.id, data.as_of]);
  useEffect(() => () => { ++request.current; }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [messages, pending]);
  const suggestions = ["Summarize today’s activity", "Which calls need attention?", data.location.vertical === "restaurant" ? "Show tomorrow’s reservations" : "Show tomorrow’s appointments", "Show cancelled bookings", "What did the latest callers ask?"];
  async function ask(text: string) { if (!text.trim() || pending) return; const id = ++request.current; setPending(text); setQuestion(""); setError(""); try { const result = await mutation<AssistantAnswer>("/assistant/query", { location_id: data.location.id, question: text.trim() }); if (id === request.current) setMessages(rows => [...rows, { question: text, result }]); } catch (e) { if (id === request.current) { setError((e as Error).message); setQuestion(text); } } finally { if (id === request.current) setPending(""); } }
  function submit(event: FormEvent) { event.preventDefault(); void ask(question); }
  return <div className="assistant-page">
    <div className="metric-grid assistant-metrics"><MetricCard label="Calls today" value={summary?.metrics.calls_today ?? "—"} icon={Phone} /><MetricCard label="Confirmed bookings" value={summary?.metrics.confirmed_bookings ?? "—"} icon={CalendarDays} /><MetricCard label="Open requests" value={summary?.metrics.attention_open ?? "—"} icon={CircleHelp} /><MetricCard label="Saved transcripts" value={summary?.metrics.transcripts_saved ?? "—"} detail={summary ? summary.transcript_retention_calls ? `Latest ${summary.transcript_retention_calls} calls` : "Storage off" : undefined} icon={MessageSquare} /></div>
    <section className="panel assistant-conversation" aria-label="Business assistant"><div className="panel-header"><h3><Sparkles size={15} />Business assistant</h3><small className="muted">Saved activity · {time(summary?.as_of || data.as_of, data.location.timezone)}</small></div>
      <div className="assistant-messages" aria-live="polite">{!messages.length && !pending && <div className="assistant-welcome"><Sparkles size={26} /><h2>What would you like to know?</h2><p>Ask about calls, bookings, orders, and staff requests.</p><div className="assistant-suggestions">{suggestions.map(text => <button key={text} onClick={() => { void ask(text); }}>{text}<ChevronRight size={14} /></button>)}</div></div>}
        {messages.map((message, i) => <div className="assistant-exchange" key={i}><div className="assistant-question">{message.question}</div><div className="assistant-answer"><span className="assistant-answer-icon"><Sparkles size={15} /></span><div><p>{message.result.answer}</p><div className="assistant-sources">{message.result.sources.map((source, j) => source.kind === "call" ? <button className="assistant-source" key={`${source.id}-${j}`} onClick={() => onCall(source.id)}><Phone size={12} />{source.label}<ChevronRight size={12} /></button> : <Link className="assistant-source" href={source.href} key={`${source.id}-${j}`}>{source.label}<ChevronRight size={12} /></Link>)}</div><small>As of {time(message.result.as_of, data.location.timezone, true)}</small></div></div></div>)}
        {pending && <div className="assistant-exchange"><div className="assistant-question">{pending}</div><div className="assistant-thinking"><Loader2 size={15} className="spin" />Reviewing saved activity…</div></div>}<div ref={bottom} />
      </div><form className="assistant-composer" onSubmit={submit}><ErrorText error={error} /><div><textarea value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask about your business…" aria-label="Question for business assistant" maxLength={2000} rows={2} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!pending) void ask(question); } }} /><Button type="submit" size="icon" disabled={!!pending || !question.trim()} aria-label="Send question"><ArrowUp size={17} /></Button></div><small>Business changes remain in the operational controls.</small></form>
    </section>
  </div>;
}
