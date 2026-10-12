// Vertrag zwischen services/api (GET /v1/system/health) und den Clients
// (apps/web Statusseite, apps/mobile "Heute"-Screen). Siehe Architekturplan
// Abschnitt 16 (Observability und Support).

export type ServiceStatus = "ok" | "degraded" | "down" | "unknown";

export interface ServiceHealth {
  service: "api" | "database" | "redis" | "worker" | "storage" | "lm_studio";
  status: ServiceStatus;
  detail?: string;
  checkedAt: string;
}

export interface SystemHealth {
  status: ServiceStatus;
  services: ServiceHealth[];
}
