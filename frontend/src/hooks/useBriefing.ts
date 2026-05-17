import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../api/client";
import type { Briefing } from "../types";

export interface UseBriefingResult {
  data: Briefing | null;
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
  isStale: boolean;
  asOf: string | null;
}

export function useBriefing(): UseBriefingResult {
  const [data, setData] = useState<Briefing | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  const fetchOnce = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setIsLoading(true);
    try {
      const payload = await apiClient.getBriefing();
      setData(payload);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Briefing unavailable");
    } finally {
      setIsLoading(false);
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    fetchOnce();
    const id = setInterval(fetchOnce, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, [fetchOnce]);

  const asOf = data?.as_of ?? null;
  const ageMs = asOf ? Date.now() - new Date(asOf).getTime() : 0;
  const isStale = asOf !== null && ageMs > 4 * 3600 * 1000;

  return { data, isLoading, error, refresh: fetchOnce, isStale, asOf };
}
