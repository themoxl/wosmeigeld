import type { ServiceHealth, ServiceStatus } from "@finanzplaner/contracts";
import {
  getServiceStatusColor,
  SERVICE_LABELS,
  SERVICE_STATUS_LABELS,
} from "../theme/serviceStatus";
import { useTheme, type AccentTone } from "../theme";
import { Badge, DataRow } from "./ui";
import { StatusDot } from "./StatusDot";

type Props = {
  service: ServiceHealth;
  /** Letzte Zeile einer Liste laesst die Trennlinie weg. */
  divider?: boolean;
};

/**
 * Der Punkt allein waere eine reine Farbaussage. Er bleibt als schneller
 * Anker links stehen, die Pille rechts nennt denselben Zustand ausgeschrieben.
 */
function toneForStatus(status: ServiceStatus): AccentTone {
  switch (status) {
    case "ok":
      return "positive";
    case "degraded":
      return "highlight";
    case "down":
      return "warning";
    case "unknown":
    default:
      return "neutral";
  }
}

/** Eine Zeile im Systemstatus-Bereich (API, Datenbank, Worker, Speicher, LM Studio). */
export function ServiceHealthRow({ service, divider = true }: Props) {
  const theme = useTheme();

  return (
    <DataRow
      title={SERVICE_LABELS[service.service]}
      subtitle={service.detail ?? undefined}
      leading={<StatusDot color={getServiceStatusColor(service.status, theme)} />}
      trailing={
        <Badge label={SERVICE_STATUS_LABELS[service.status]} tone={toneForStatus(service.status)} />
      }
      divider={divider}
    />
  );
}
