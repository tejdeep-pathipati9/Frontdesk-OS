"use client";
import { useState } from "react";
import { ArrowRight, PanelsTopLeft, Loader2 } from "lucide-react";
import { mutation } from "@/lib/api";
import { Button } from "./ui/button";
import { ErrorText } from "./workflows";

export function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  async function login() { setBusy(true); setError(""); try { await mutation("/auth/login", { email, password }); onLogin(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  return <div className="login-page"><div className="login-form"><div className="brand"><PanelsTopLeft size={22} /><span>FrontDesk OS</span></div><h1>Momentuum Bistro</h1><p>Sign in to manage your front desk.</p><form onSubmit={e => { e.preventDefault(); void login(); }}><label>Email<input value={email} onChange={e => setEmail(e.target.value)} type="email" autoComplete="username" required placeholder="owner@momentuumbistro.local" /></label><label>Password<input value={password} onChange={e => setPassword(e.target.value)} type="password" autoComplete="current-password" required /></label><Button type="submit" disabled={busy}>{busy ? <Loader2 size={15} className="spin" /> : null}Sign in <ArrowRight size={14} /></Button></form><ErrorText error={error} /></div></div>;
}
