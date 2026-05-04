import { useEffect, useState } from "react";
import { apiClient } from "../api/client";
import type { Briefing } from "../types";

export interface UseBriefingResult {
  data: Briefing | null;
  isLoading: boolean;
  error: string | null;
}

export function useBriefing(): UseBriefingResult {
  const [data, setData] = useState<Briefing | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await apiClient.getBriefing();
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      } catch (e: unknown) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Briefing unavailable");
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return { data, isLoading, error };
}
