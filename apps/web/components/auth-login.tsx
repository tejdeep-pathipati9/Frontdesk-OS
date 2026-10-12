"use client";
import { useEffect, useState } from "react";
import { ArrowRight, PanelsTopLeft, Loader2 } from "lucide-react";
import { api, mutation } from "@/lib/api";
import { Button } from "./ui/button";
import { ErrorText } from "./workflows";

export function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [demo, setDemo] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  useEffect(() => { api<{ demo_mode: boolean }>("/health").then(r => setDemo(r.demo_mode)).catch(() => setError("The backend is unavailable. Start FastAPI on port 8000.")); }, []);
  async function login(isDemo: boolean) { setBusy(true); setError(""); try { await mutation("/auth/login", { email, password, demo: isDemo }); onLogin(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  return <div className="login-page"><div className="login-form"><div className="brand"><PanelsTopLeft size={22} /><span>FrontDesk OS</span></div><h1>Welcome to your front desk</h1><p>Sign in to manage your business operations.</p><form onSubmit={e => { e.preventDefault(); void login(false); }}><label>Email<input value={email} onChange={e => setEmail(e.target.value)} type="email" autoComplete="username" required placeholder="owner@example.com" /></label><label>Password<input value={password} onChange={e => setPassword(e.target.value)} type="password" autoComplete="current-password" required /></label><Button type="submit" disabled={busy}>{busy ? <Loader2 size={15} className="spin" /> : null}Sign in <ArrowRight size={14} /></Button></form>{demo && <><div className="divider">Prototype workspace</div><Button className="full-width" variant="outline" disabled={busy} onClick={() => login(true)}>Open local demo</Button><small className="demo-note">Sample restaurant and salon data. Live voice and external providers require configuration.</small></>}<ErrorText error={error} /></div></div>;
}
