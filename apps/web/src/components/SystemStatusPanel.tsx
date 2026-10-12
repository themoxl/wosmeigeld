import { useQuery } from "@tanstack/react-query";
import type { ServiceHealth } from "@finanzplaner/contracts";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { fetchSystemHealth, getAIHealth } from "../lib/api";
import type { AIHealthResponse } from "../lib/types";
import { StatusBadge } from "./StatusBadge";

// Abschnitt 16 (Observability und Support) sowie das Abnahmekriterium von
// Phase 0 & Phase 6: Dieses Panel ruft GET /v1/system/health und GET /v1/ai/health
// per TanStack Query ab und zeigt fuer jeden Dienst und die lokale KI den Status an.
// Wird sowohl von der oeffentlichen /status-Seite (StatusPage.tsx, ohne Login
// erreichbar - siehe docs/BETRIEB.md) als auch vom System-Register der
// Einstellungen eingebettet, deshalb ohne eigenen Seitentitel.

const SERVICE_LABEL: Record<ServiceHealth["service"], string> = {
  api: "API",
  database: "Datenbank",
  redis: "Redis",
  worker: "Worker",
  storage: "Storage",
  lm_studio: "LM Studio (lokale KI)",
};

function formatCheckedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleTimeString("de-DE");
}

function useSafeAuthAndHousehold() {
  let token: string | null = null;
  let householdId: string | null = null;
  try {
    const auth = useAuth();
    token = auth.accessToken;
  } catch {
    // optional ausserhalb des AuthProviders (z. B. in isolierten Tests)
  }
  try {
    const household = useHousehold();
    householdId = household.selectedHouseholdId;
  } catch {
    // optional ausserhalb des HouseholdProviders
  }
  return { token, householdId };
}

export function SystemStatusPanel() {
  const { token, householdId } = useSafeAuthAndHousehold();

  const { data, error, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ["system-health"],
    queryFn: fetchSystemHealth,
  });

  const {
    data: aiHealth,
    isLoading: isAiLoading,
    isError: isAiError,
    error: aiError,
    isFetching: isAiFetching,
    refetch: refetchAiHealth,
  } = useQuery<AIHealthResponse>({
    queryKey: ["ai-health", householdId],
    queryFn: () => getAIHealth(token!, householdId!),
    enabled: Boolean(token && householdId),
  });

  return (
    <>
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          marginBottom: "var(--space-3)",
        }}
      >
        <button
          type="button"
          className="btn-secondary"
          onClick={() => void refetch()}
          disabled={isFetching}
        >
          {isFetching ? "Aktualisiere…" : "Aktualisieren"}
        </button>
      </div>

      {isLoading && <p role="status">Lade Systemstatus…</p>}

      {isError && (
        <p role="alert" className="status-error">
          {error instanceof Error ? error.message : "Systemstatus konnte nicht geladen werden."}
        </p>
      )}

      {data && (
        <>
          <p>
            Gesamtstatus: <StatusBadge status={data.status} />
          </p>

          <ul className="status-list">
            {data.services.map((service) => (
              <li key={service.service} className="status-row">
                <span className="status-row-label">
                  {SERVICE_LABEL[service.service] ?? service.service}
                </span>
                <StatusBadge status={service.status} />
                {service.detail && <span className="status-row-detail">{service.detail}</span>}
                <span className="status-row-time">{formatCheckedAt(service.checkedAt)}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {/* Dedizierte Lokale KI (LM Studio) Statuskarte (Phase 6) */}
      <div className="card" style={{ marginTop: "var(--space-4)" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "var(--space-3)",
          }}
        >
          <h2 className="card-heading" style={{ margin: 0 }}>
            {aiHealth?.provider === "anthropic" ? "KI (Claude)" : "Lokale KI (LM Studio)"}
          </h2>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => void refetchAiHealth()}
            disabled={isAiFetching || !token || !householdId}
          >
            {isAiFetching ? "Prüfe…" : "Verbindung prüfen"}
          </button>
        </div>

        {isAiLoading && <p className="placeholder-note">Lade KI-Status…</p>}

        {isAiError && (
          <p role="alert" className="status-error">
            {aiError instanceof Error ? aiError.message : "KI-Status konnte nicht geladen werden."}
          </p>
        )}

        {aiHealth && (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
              <span className="form-label" style={{ minWidth: "140px" }}>
                Status:
              </span>
              <span
                className={`status-badge ${
                  aiHealth.is_connected && aiHealth.active_model
                    ? "status-badge--positive"
                    : aiHealth.is_connected
                      ? "status-badge--highlight"
                      : "status-badge--warning"
                }`}
              >
                <span
                  style={{
                    display: "inline-block",
                    width: "8px",
                    height: "8px",
                    borderRadius: "50%",
                    marginRight: "6px",
                    backgroundColor:
                      aiHealth.is_connected && aiHealth.active_model
                        ? "var(--color-positive)"
                        : aiHealth.is_connected
                          ? "var(--color-highlight)"
                          : "var(--color-warning)",
                  }}
                />
                {aiHealth.is_connected && aiHealth.active_model
                  ? "Verbunden (Connected)"
                  : aiHealth.is_connected
                    ? "Kein Modell geladen (No Model)"
                    : "Nicht verbunden (Disconnected)"}
              </span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
              <span className="form-label" style={{ minWidth: "140px" }}>
                Base-URL:
              </span>
              <code style={{ fontFamily: "var(--font-mono)", fontSize: "13px" }}>
                {aiHealth.base_url || "http://localhost:1234"}
              </code>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
              <span className="form-label" style={{ minWidth: "140px" }}>
                Aktuelles Modell:
              </span>
              <span style={{ fontWeight: 500 }}>
                {aiHealth.active_model ?? "Kein Modell geladen"}
              </span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
              <span className="form-label" style={{ minWidth: "140px" }}>
                Canary Test:
              </span>
              <span
                className={`status-badge ${
                  aiHealth.canary_passed ? "status-badge--positive" : "status-badge--warning"
                }`}
              >
                {aiHealth.canary_passed ? "Bestanden (OK)" : "Nicht bestanden"}
              </span>
            </div>

            {aiHealth.message && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-2)",
                  marginTop: "4px",
                }}
              >
                <span className="form-label" style={{ minWidth: "140px" }}>
                  Hinweis:
                </span>
                <span className="placeholder-note">{aiHealth.message}</span>
              </div>
            )}
          </div>
        )}

        {!aiHealth && !isAiLoading && !isAiError && (
          <p className="placeholder-note">
            Verbindung zu LM Studio noch nicht geprüft oder kein Haushalt ausgewählt.
          </p>
        )}
      </div>
    </>
  );
}
