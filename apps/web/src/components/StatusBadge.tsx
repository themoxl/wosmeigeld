import type { ServiceStatus } from "@finanzplaner/contracts";

// Farbzuordnung laut Aufgabenstellung / Abschnitt 16:
// ok -> positive (Waldgruen), degraded -> highlight (Gold),
// down -> warning (Koralle), unknown -> neutrale Border-/Textfarbe.
const STATUS_LABEL: Record<ServiceStatus, string> = {
  ok: "OK",
  degraded: "Eingeschränkt",
  down: "Ausgefallen",
  unknown: "Unbekannt",
};

const STATUS_MODIFIER: Record<ServiceStatus, string> = {
  ok: "status-badge--positive",
  degraded: "status-badge--highlight",
  down: "status-badge--warning",
  unknown: "status-badge--neutral",
};

interface StatusBadgeProps {
  status: ServiceStatus;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  return <span className={`status-badge ${STATUS_MODIFIER[status]}`}>{STATUS_LABEL[status]}</span>;
}
