import { useEffect, useRef, useState } from "react";
import { apiClient, MoversPayload } from "../api/client";

const POLL_INTERVAL_MS = 60_000;

export interface UseMarketMoversResult {
  data: MoversPayload | null;
  isLoading: boolean;
  error: string | null;
  isStale: boolean;
  lastFetchedAt: Date | null;
  refresh: () => void;
}

export function useMarketMovers(): UseMarketMoversResult {
  const [data, setData] = useState<MoversPayload | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isStale, setIsStale] = useState(false);
  const [lastFetchedAt, setLastFetchedAt] = useState<Date | null>(null);
  const inFlight = useRef(false);

  const fetchOnce = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const payload = await apiClient.getMovers();
      setData(payload);
      setError(null);
      setIsStale(false);
      setLastFetchedAt(new Date());
    } catch (e: any) {
      setError(e?.message ?? "Failed to fetch movers");
      setIsStale(true);
    } finally {
      setIsLoading(false);
      inFlight.current = false;
    }
  };

  useEffect(() => {
    fetchOnce();
    const id = setInterval(fetchOnce, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  return { data, isLoading, error, isStale, lastFetchedAt, refresh: fetchOnce };
}
