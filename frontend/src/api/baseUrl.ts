/**
 * Single source of truth for the FastAPI backend base URL.
 *
 * Both the pricing client (client.ts) and the agent client (agentClient.ts)
 * import this so they cannot drift apart. The previous bug (Aug 2026) was an
 * agent client hardcoded to port 8003 while the pricing client was on 8002 —
 * the co-pilot's POST /api/agent/sessions silently went to a non-existent
 * port and the agent framework "failed to initiate".
 */

export const API_PORT = 8002;

export function getApiBaseUrl(): string {
  const override = import.meta.env.VITE_API_URL as string | undefined;
  if (override) return override;
  if (typeof window === "undefined") return `http://localhost:${API_PORT}`;
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:${API_PORT}`;
}
