import type { ServiceHealth, ServiceStatus } from "@finanzplaner/contracts";
import type { ThemeTokens } from "./ThemeProvider";

// Deutsche Anzeige-Labels fuer die Dienste/Status aus @finanzplaner/contracts
// (siehe packages/contracts/src/health.ts und services/api .../system.py).
export const SERVICE_LABELS: Record<ServiceHealth["service"], string> = {
  api: "API",
  database: "Datenbank",
  redis: "Redis",
  worker: "Worker",
  storage: "Speicher",
  lm_studio: "LM Studio",
};

export const SERVICE_STATUS_LABELS: Record<ServiceStatus, string> = {
  ok: "Ok",
  degraded: "Eingeschränkt",
  down: "Ausgefallen",
  unknown: "Unbekannt",
};

// Kanonische Anzeigereihenfolge, unabhaengig von der Reihenfolge, in der die
// API die Dienste liefert.
const SERVICE_ORDER: ServiceHealth["service"][] = [
  "api",
  "database",
  "redis",
  "worker",
  "storage",
  "lm_studio",
];

export function sortServices(services: ServiceHealth[]): ServiceHealth[] {
  return [...services].sort((a, b) => {
    const indexA = SERVICE_ORDER.indexOf(a.service);
    const indexB = SERVICE_ORDER.indexOf(b.service);
    return (
      (indexA === -1 ? SERVICE_ORDER.length : indexA) -
      (indexB === -1 ? SERVICE_ORDER.length : indexB)
    );
  });
}

// Farbzuordnung laut Auftrag: ok -> positive (Waldgruen), degraded ->
// highlight (Gold), down -> warning (Koralle), unknown -> neutrale Farbe.
// Bewusst ueber die Theme-Tokens und nicht ueber hartkodierte Hex-Werte.
export function getServiceStatusColor(status: ServiceStatus, theme: ThemeTokens): string {
  switch (status) {
    case "ok":
      return theme.positive;
    case "degraded":
      return theme.highlight;
    case "down":
      return theme.warning;
    case "unknown":
    default:
      return theme.textSecondary;
  }
}

export function getServiceStatusMutedColor(status: ServiceStatus, theme: ThemeTokens): string {
  switch (status) {
    case "ok":
      return theme.positiveMuted;
    case "degraded":
      return theme.highlightMuted;
    case "down":
      return theme.warningMuted;
    case "unknown":
    default:
      return theme.border;
  }
}
