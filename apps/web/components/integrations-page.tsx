"use client";
import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { api, mutation } from "@/lib/api";
import { IntegrationCard } from "./integration-card";
import { ErrorText } from "./workflows";
import type { Integration, Snapshot, StorageStatus } from "@/lib/types";

export function IntegrationsPage({ data }: { data: Snapshot }) {
  const [connections, setConnections] = useState<Integration[]>([]); const [storage, setStorage] = useState<StorageStatus | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const load = useCallback(async () => { try { const [items, health] = await Promise.all([api<Integration[]>(`/integrations?location_id=${data.location.id}`), api<StorageStatus>("/health")]); setConnections(items); setStorage(health); } catch (e) { setError((e as Error).message); } }, [data.location.id]);
  useEffect(() => { void load(); }, [load]);
  async function test(provider: string) { setBusy(true); setError(""); try { const result = await mutation<Integration>(`/integrations/${provider}/test?location_id=${data.location.id}`); setConnections(current => current.map(c => c.provider === provider ? result : c)); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  const supabase: Integration = { provider: "supabase", mode: storage?.database.provider === "supabase" && storage.database.connected ? "connected" : "not_configured", capabilities: { postgres_storage: storage?.database.provider === "supabase" ? "supported" : "not_configured", operational_writes: "via FastAPI", realtime_updates: "via FastAPI SSE" }, detail: `FastAPI remains the authorized business API. Current database: ${storage?.database.provider || "checking"}.` };
  return <><div className="notice"><ShieldCheck size={15} /><span>Connection and write permissions are checked separately. Missing or unsupported capabilities stay disabled.</span></div><ErrorText error={error} /><div className="integration-grid">{connections.map(integration => <IntegrationCard key={integration.provider} integration={integration} timezone={data.location.timezone} busy={busy} onTest={["square", "toast"].includes(integration.provider) ? () => test(integration.provider) : undefined} />)}<IntegrationCard integration={supabase} timezone={data.location.timezone} busy={false} /></div></>;
}
