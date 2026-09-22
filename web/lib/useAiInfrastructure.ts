"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { AiSnapshot } from "./aiInfrastructure";
export async function fetchAiInfrastructure(url: string, signal?: AbortSignal): Promise<AiSnapshot> {
  const response = await fetch(url, { cache: "no-store", ...(signal ? { signal } : {}) });
  if (!response.ok) throw new Error(`AI infrastructure source could not be reached (HTTP ${response.status}).`);
  const data = await response.json();
  if (data?.version !== 1 || !Array.isArray(data.indicators) || !Array.isArray(data.sources) || !data.shadow) throw new Error("AI infrastructure returned an incompatible snapshot.");
  return data;
}
/** One page-level cache snapshot, no market socket or duplicate dashboard polling. */
export function useAiInfrastructure() {
  const [data, setData] = useState<AiSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const request = useRef<AbortController | null>(null);
  const refresh = useCallback(() => {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setLoading(true);
    void fetchAiInfrastructure("/api/ai-cycle", controller.signal).then(snapshot => { if (!controller.signal.aborted) { setData(snapshot); setError(null); } }).catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "AI infrastructure unavailable."); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
  }, []);
  useEffect(() => { refresh(); const timer = setInterval(refresh, 300_000); return () => { clearInterval(timer); request.current?.abort(); }; }, [refresh]);
  return { data, error, loading, refresh };
}
