import { useQuery } from "@tanstack/react-query";
import { fetchSystemHealth } from "../api/health";

export const SYSTEM_HEALTH_QUERY_KEY = ["system-health"] as const;

/**
 * Liest GET /v1/system/health (siehe src/api/health.ts) und haelt den Wert
 * per Polling aktuell, damit die Statusanzeige auf dem "Heute"-Screen zeitnah
 * merkt, wenn der lokale Stack (docker compose) hoch- oder heruntergefahren
 * wird.
 */
export function useSystemHealth() {
  return useQuery({
    queryKey: SYSTEM_HEALTH_QUERY_KEY,
    queryFn: ({ signal }) => fetchSystemHealth(signal),
    refetchInterval: 30_000,
  });
}
