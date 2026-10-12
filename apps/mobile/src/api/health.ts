import type { SystemHealth } from "@finanzplaner/contracts";
import { getApiBaseUrl } from "./config";

// Vertrag: GET /v1/system/health, siehe
// services/api/app/api/v1/routes/system.py und
// packages/contracts/src/health.ts. Wird vom "Heute"-Screen genutzt, um
// laut Abnahmekriterium aus Phase 0 sichtbar zu demonstrieren, dass sich die
// Expo-App mit der lokalen API verbindet.
export async function fetchSystemHealth(signal?: AbortSignal): Promise<SystemHealth> {
  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}/v1/system/health`, { signal });
  } catch (cause) {
    throw new Error(
      `API unter ${getApiBaseUrl()} nicht erreichbar. Läuft die lokale API (docker compose up)?`,
      { cause },
    );
  }

  if (!response.ok) {
    throw new Error(`Systemstatus konnte nicht geladen werden (HTTP ${response.status}).`);
  }

  return (await response.json()) as SystemHealth;
}
