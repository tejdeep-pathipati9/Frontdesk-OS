"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { AttentionItem, Location, Snapshot, VoiceConfig } from "@/lib/types";

export function useWorkspace(vertical: "restaurant" | "salon") {
  const [auth, setAuth] = useState<"loading" | "signed_in" | "signed_out">("loading");
  const [locations, setLocations] = useState<Location[]>([]); const [locationId, setLocationId] = useState("");
  const [data, setData] = useState<Snapshot | null>(null); const [attention, setAttention] = useState<AttentionItem[]>([]); const [voice, setVoice] = useState<VoiceConfig | null>(null);
  const [error, setError] = useState(""); const [realtime, setRealtime] = useState("connecting"); const refreshId = useRef(0);
  const load = useCallback(async () => {
    try { await api("/auth/me"); const rows = await api<Location[]>("/locations"); setLocations(rows); const stored = sessionStorage.getItem(`frontdesk-${vertical}-location`); const preferred = rows.find(r => r.id === stored && r.vertical === vertical) || rows.find(r => r.vertical === vertical); setLocationId(preferred?.id || ""); setAuth("signed_in"); setError(""); } catch (e) { setAuth("signed_out"); if ((e as ApiError).status !== 401) setError((e as Error).message); }
  }, [vertical]);
  useEffect(() => { void load(); }, [load]);
  const refresh = useCallback(async () => {
    if (!locationId) return;
    const requestId = ++refreshId.current;
    try {
      const [snapshot, queue, voiceConfig] = await Promise.all([api<Snapshot>(`/locations/${locationId}/snapshot`), api<AttentionItem[]>(`/attention?location_id=${locationId}`), api<VoiceConfig>(`/voice/config?location_id=${locationId}`)]);
      if (requestId !== refreshId.current) return;
      setData(snapshot); setAttention(queue); setVoice(voiceConfig); setError("");
    } catch (e) { if (requestId !== refreshId.current) return; setError((e as Error).message); if ((e as ApiError).status === 401) setAuth("signed_out"); }
  }, [locationId]);
  useEffect(() => {
    ++refreshId.current; setData(null); setAttention([]); setVoice(null); void refresh();
    if (!locationId) return;
    const events = new EventSource(`/api/v1/realtime?location_id=${locationId}`);
    events.onopen = () => setRealtime("connected");
    events.onerror = () => setRealtime("reconnecting");
    events.addEventListener("state", () => { void refresh(); });
    const interval = setInterval(() => { void refresh(); }, 15000);
    return () => { events.close(); clearInterval(interval); ++refreshId.current; };
  }, [locationId, refresh]);
  return { auth, setAuth, locations, locationId, setLocationId, data, attention, voice, error, setError, realtime, refresh, load };
}
