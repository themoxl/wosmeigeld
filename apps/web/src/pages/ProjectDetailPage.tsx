import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  useApplyCompletion,
  useCloneProjectScenario,
  useCompletionPreview,
  useCreateProjectScenario,
  useProject,
  useProjectScenarios,
  useScenarioCalculation,
  useSelectProjectScenario,
  useTransitionProject,
  useUpdateProject,
} from "../hooks/useProjects";
import { formatMinorUnitsAsEuro } from "../lib/money";
import { GERMAN_REGION_OPTIONS } from "../lib/regions";
import type {
  MajorProject,
  MajorProjectUpdateInput,
  ProjectStatus,
  ScenarioKind,
} from "../lib/types";
import { AssumptionsPanel } from "../components/projects/AssumptionsPanel";
import { CostImportExportPanel } from "../components/projects/CostImportExportPanel";
import { CostPlanTable } from "../components/projects/CostPlanTable";
import { FinancingPanel } from "../components/projects/FinancingPanel";
import { ScenarioComparisonView } from "../components/projects/ScenarioComparisonView";
import { Modal } from "../components/Modal";

const STATUS_LABELS: Record<ProjectStatus, string> = {
  idea: "Idee",
  planning: "Planung",
  approved: "Bestätigt",
  active: "Aktiv",
  completed: "Abgeschlossen",
  cancelled: "Storniert",
  archived: "Archiviert",
};

const NEXT_STATUS: Partial<Record<ProjectStatus, ProjectStatus[]>> = {
  idea: ["planning", "cancelled"],
  planning: ["idea", "approved", "cancelled"],
  approved: ["planning", "active", "cancelled"],
  active: ["planning", "completed"],
  cancelled: ["idea"],
  archived: ["idea"],
  completed: [],
};

const WARNING_LABELS: Record<string, string> = {
  cost_schedule_incomplete:
    "Zahlungsplan unvollständig: Die Summe der Abschläge entspricht noch nicht dem Forecast jeder Position.",
  calculation_horizon_reached:
    "Der Rechenhorizont wurde erreicht, ohne dass die Schuld getilgt ist.",
};

type ProjectTab =
  "uebersicht" | "kosten" | "import-export" | "finanzierung" | "annahmen" | "vergleich";

const TABS: { key: ProjectTab; label: string }[] = [
  { key: "uebersicht", label: "Übersicht" },
  { key: "kosten", label: "Kosten" },
  { key: "import-export", label: "Import/Export" },
  { key: "finanzierung", label: "Finanzierung" },
  { key: "annahmen", label: "Haushalt & Belastung" },
  { key: "vergleich", label: "Vergleich" },
];

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = id ?? null;
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const requestedScenarioId = searchParams.get("scenario_id");
  const aiTaskId = searchParams.get("ai_task");
  const focusedFinancingSourceId = searchParams.get("source");
  const routeTab = TABS.some((entry) => entry.key === requestedTab)
    ? (requestedTab as ProjectTab)
    : null;
  const [selectedTab, setSelectedTab] = useState<ProjectTab>("uebersicht");
  const tab = routeTab ?? selectedTab;
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null);
  const [isCloneOpen, setIsCloneOpen] = useState(false);

  const { data: project, isLoading: projectLoading, error: projectError } = useProject(projectId);
  const { data: scenarios } = useProjectScenarios(projectId);
  const transitionMutation = useTransitionProject();
  const selectMutation = useSelectProjectScenario();
  const createScenarioMutation = useCreateProjectScenario();

  const activeScenarioId =
    requestedScenarioId ??
    selectedScenarioId ??
    scenarios?.find((s) => s.status === "selected")?.id ??
    scenarios?.[0]?.id ??
    null;

  const { data: calculation } = useScenarioCalculation(projectId, activeScenarioId);

  if (projectLoading) {
    return (
      <section className="page">
        <p className="placeholder-note">Lade Vorhaben...</p>
      </section>
    );
  }

  if (projectError || !project) {
    return (
      <section className="page">
        <p className="status-error" role="alert">
          Vorhaben konnte nicht geladen werden.
        </p>
        <button type="button" className="btn-secondary" onClick={() => navigate("/vorhaben")}>
          Zurück zur Liste
        </button>
      </section>
    );
  }

  const nextStatuses = NEXT_STATUS[project.status] ?? [];
  const activeScenario = scenarios?.find((s) => s.id === activeScenarioId) ?? null;

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => navigate("/vorhaben")}
            style={{ marginBottom: "var(--space-2)" }}
          >
            ← Vorhaben
          </button>
          <h1 className="page-heading">{project.name}</h1>
          <span className="status-badge status-badge--neutral">
            {STATUS_LABELS[project.status]}
          </span>
        </div>
        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          {nextStatuses.map((next) => (
            <button
              key={next}
              type="button"
              className="btn-secondary"
              onClick={() =>
                transitionMutation.mutate({
                  projectId: project.id,
                  version: project.version,
                  input: { to_status: next },
                })
              }
              disabled={transitionMutation.isPending}
            >
              → {STATUS_LABELS[next]}
            </button>
          ))}
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-3)",
          marginBottom: "var(--space-4)",
        }}
      >
        <label className="form-label" htmlFor="scenario-switcher">
          Szenario
        </label>
        <select
          id="scenario-switcher"
          className="form-input"
          value={activeScenarioId ?? ""}
          onChange={(e) => {
            setSelectedScenarioId(e.target.value);
            if (requestedScenarioId || aiTaskId) {
              const next = new URLSearchParams(searchParams);
              next.delete("scenario_id");
              next.delete("ai_task");
              setSearchParams(next, { replace: true });
            }
          }}
          style={{ maxWidth: "260px" }}
        >
          {(scenarios ?? []).map((scenario) => (
            <option key={scenario.id} value={scenario.id}>
              {scenario.name}
              {scenario.status === "selected" ? " (ausgewählt)" : ""}
            </option>
          ))}
        </select>
        {activeScenario && activeScenario.status !== "selected" && (
          <button
            type="button"
            className="btn-secondary"
            onClick={() =>
              selectMutation.mutate({ projectId: project.id, scenarioId: activeScenario.id })
            }
            disabled={selectMutation.isPending}
          >
            Als Entscheidungsstand auswählen
          </button>
        )}
        <button type="button" className="btn-secondary" onClick={() => setIsCloneOpen(true)}>
          Szenario klonen / Stresstest
        </button>
        {/* Stand bisher nur als Fliesstext-Nachsatz im Klon-Dialog und war
            deshalb praktisch unauffindbar. Ein eigenstaendiges Szenario ist
            aber der normale Weg, um zwei Finanzierungswege nebeneinander zu
            stellen - das gehoert neben den Umschalter, nicht in einen Dialog
            fuer etwas anderes. */}
        <button
          type="button"
          className="btn-secondary"
          onClick={() =>
            createScenarioMutation.mutate(
              { projectId: project.id, input: { name: "Neues Szenario" } },
              { onSuccess: (created) => setSelectedScenarioId(created.id) },
            )
          }
          disabled={createScenarioMutation.isPending}
        >
          + Leeres Szenario
        </button>
      </div>

      {selectMutation.isError && (
        <p className="status-error" role="alert">
          {(selectMutation.error as { message?: string })?.message?.includes(
            "cost_schedule_incomplete",
          )
            ? WARNING_LABELS.cost_schedule_incomplete
            : "Szenario konnte nicht ausgewählt werden."}
        </p>
      )}

      <div className="filter-tabs" role="tablist">
        {TABS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            role="tab"
            aria-selected={tab === entry.key}
            className={`filter-tab ${tab === entry.key ? "filter-tab--active" : ""}`}
            onClick={() => {
              setSelectedTab(entry.key);
              if (routeTab || aiTaskId) {
                const next = new URLSearchParams(searchParams);
                next.delete("tab");
                next.delete("ai_task");
                setSearchParams(next, { replace: true });
              }
            }}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {tab === "uebersicht" && (
        <div className="metric-grid" style={{ marginTop: "var(--space-4)" }}>
          <div className="card metric-card">
            <span className="metric-label">Projektkosten (Cash)</span>
            <span className="metric-value">
              {calculation ? formatMinorUnitsAsEuro(calculation.project_cash_cost_minor) : "—"}
            </span>
            <span className="metric-hint">Forecast plus Kostenpuffer</span>
          </div>
          <div className="card metric-card">
            <span className="metric-label">Ökonomische Kosten</span>
            <span className="metric-value">
              {calculation ? formatMinorUnitsAsEuro(calculation.project_economic_cost_minor) : "—"}
            </span>
            <span className="metric-hint">Inklusive Eigen-/Sachleistung</span>
          </div>
          <div className="card metric-card">
            <span className="metric-label">Kostenpuffer</span>
            <span className="metric-value">
              {calculation ? formatMinorUnitsAsEuro(calculation.contingency_minor) : "—"}
            </span>
          </div>
          <div className="card metric-card">
            <span className="metric-label">Ist-Kosten</span>
            <span className="metric-value">
              {calculation ? formatMinorUnitsAsEuro(calculation.actual_cost_minor) : "—"}
            </span>
            <span className="metric-hint">
              Rest:{" "}
              {calculation ? formatMinorUnitsAsEuro(calculation.remaining_forecast_minor) : "—"}
            </span>
          </div>

          {calculation && calculation.warnings.length > 0 && (
            <div className="card" style={{ gridColumn: "1 / -1" }}>
              <h2 className="card-heading">Warnungen</h2>
              <ul>
                {calculation.warnings.map((code) => (
                  <li key={code}>{WARNING_LABELS[code] ?? code}</li>
                ))}
              </ul>
            </div>
          )}

          <LocationCard key={project.id} project={project} />
        </div>
      )}

      {tab === "kosten" && activeScenarioId && (
        <CostPlanTable
          projectId={project.id}
          scenarioId={activeScenarioId}
          initialAiTaskId={aiTaskId}
          onAiTaskClose={() => {
            if (!aiTaskId) return;
            const next = new URLSearchParams(searchParams);
            next.delete("ai_task");
            setSearchParams(next, { replace: true });
          }}
        />
      )}

      {tab === "import-export" && activeScenarioId && (
        <CostImportExportPanel projectId={project.id} scenarioId={activeScenarioId} />
      )}

      {tab === "finanzierung" && activeScenarioId && (
        <FinancingPanel
          projectId={project.id}
          scenarioId={activeScenarioId}
          focusSourceId={focusedFinancingSourceId}
        />
      )}

      {tab === "annahmen" && activeScenarioId && (
        <AssumptionsPanel projectId={project.id} scenarioId={activeScenarioId} />
      )}

      {tab === "vergleich" && (
        <ScenarioComparisonView projectId={project.id} scenarios={scenarios ?? []} />
      )}

      {project.status === "active" && <CompletionSection projectId={project.id} />}

      {isCloneOpen && activeScenario && (
        <CloneScenarioDialog
          projectId={project.id}
          scenarioId={activeScenario.id}
          onClose={() => setIsCloneOpen(false)}
          onCloned={(scenario) => {
            setSelectedScenarioId(scenario.id);
            setIsCloneOpen(false);
          }}
        />
      )}
    </section>
  );
}

/** Standort des Vorhabens.
 *
 * Das Bundesland ist keine Kosmetik: `region_code` ist die einzige Angabe, über die
 * Landesprogramme im Fördercheck überhaupt gefunden werden. Fehlt sie, filtert das
 * Matching jedes regionale Programm vorab weg — deshalb steht der Hinweis direkt an
 * der Eingabe und nicht in der Dokumentation.
 */
function LocationCard({ project }: { project: MajorProject }) {
  const updateMutation = useUpdateProject();
  const [regionCode, setRegionCode] = useState(project.region_code ?? "");
  const [postalCode, setPostalCode] = useState(project.postal_code ?? "");
  const [municipality, setMunicipality] = useState(project.municipality ?? "");

  const trimmedPostal = postalCode.trim();
  const trimmedMunicipality = municipality.trim();
  const isDirty =
    regionCode !== (project.region_code ?? "") ||
    trimmedPostal !== (project.postal_code ?? "") ||
    trimmedMunicipality !== (project.municipality ?? "");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const clearFields: string[] = [];
    const input: MajorProjectUpdateInput = { clear_fields: clearFields };
    if (regionCode) input.region_code = regionCode;
    else if (project.region_code) clearFields.push("region_code");
    if (trimmedPostal) input.postal_code = trimmedPostal;
    else if (project.postal_code) clearFields.push("postal_code");
    if (trimmedMunicipality) input.municipality = trimmedMunicipality;
    else if (project.municipality) clearFields.push("municipality");

    updateMutation.mutate({ projectId: project.id, version: project.version, input });
  };

  return (
    <div className="card" style={{ gridColumn: "1 / -1" }}>
      <h2 className="card-heading">Standort</h2>
      <form
        onSubmit={handleSubmit}
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: "var(--space-3)",
          alignItems: "end",
        }}
      >
        <div className="form-field">
          <label className="form-label" htmlFor="project-detail-region">
            Bundesland
          </label>
          <select
            id="project-detail-region"
            className="form-input"
            value={regionCode}
            onChange={(e) => setRegionCode(e.target.value)}
          >
            <option value="">Keine Angabe</option>
            {GERMAN_REGION_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="project-detail-postal">
            Postleitzahl
          </label>
          <input
            id="project-detail-postal"
            type="text"
            inputMode="numeric"
            className="form-input form-input--mono"
            value={postalCode}
            onChange={(e) => setPostalCode(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="project-detail-municipality">
            Gemeinde
          </label>
          <input
            id="project-detail-municipality"
            type="text"
            className="form-input"
            value={municipality}
            onChange={(e) => setMunicipality(e.target.value)}
          />
        </div>
        <button
          type="submit"
          className="btn-secondary"
          disabled={!isDirty || updateMutation.isPending}
          style={{ justifySelf: "start" }}
        >
          {updateMutation.isPending ? "Wird gespeichert..." : "Standort speichern"}
        </button>
      </form>
      {!project.region_code && (
        <p className="metric-hint">
          Ohne Bundesland findet der Fördercheck nur bundesweite Programme — Landesprogramme wie die
          BayernLabo-Eigenwohnraumförderung oder das L-Bank-Programm Z15 bleiben unsichtbar.
        </p>
      )}
      {updateMutation.isError && (
        <p className="status-error" role="alert">
          Standort konnte nicht gespeichert werden.
        </p>
      )}
    </div>
  );
}

function CloneScenarioDialog({
  projectId,
  scenarioId,
  onClose,
  onCloned,
}: {
  projectId: string;
  scenarioId: string;
  onClose: () => void;
  onCloned: (scenario: { id: string }) => void;
}) {
  const [name, setName] = useState("Kosten-Stress");
  const [kind, setKind] = useState<ScenarioKind>("stress");
  const [costIncreasePercent, setCostIncreasePercent] = useState("15");
  const [delayMonths, setDelayMonths] = useState("0");
  const cloneMutation = useCloneProjectScenario();
  const createEmptyMutation = useCreateProjectScenario();

  const handleClone = (e: React.FormEvent) => {
    e.preventDefault();
    cloneMutation.mutate(
      {
        projectId,
        scenarioId,
        input: {
          name,
          scenario_kind: kind,
          stress_cost_increase_bps: costIncreasePercent
            ? Math.round(Number(costIncreasePercent.replace(",", ".")) * 100)
            : undefined,
          stress_delay_months: delayMonths ? Number(delayMonths) : undefined,
        },
      },
      { onSuccess: onCloned },
    );
  };

  return (
    <Modal title="Szenario klonen" onClose={onClose}>
      <form
        onSubmit={handleClone}
        style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
      >
        <div className="form-field">
          <label className="form-label" htmlFor="clone-name">
            Name
          </label>
          <input
            id="clone-name"
            className="form-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="clone-kind">
            Art
          </label>
          <select
            id="clone-kind"
            className="form-input"
            value={kind}
            onChange={(e) => setKind(e.target.value as ScenarioKind)}
          >
            <option value="stress">Stress</option>
            <option value="optimistic">Günstig</option>
            <option value="custom">Frei</option>
          </select>
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="clone-cost-increase">
            Kostenaufschlag auf pufferfähige Positionen (%)
          </label>
          <input
            id="clone-cost-increase"
            className="form-input form-input--mono"
            value={costIncreasePercent}
            onChange={(e) => setCostIncreasePercent(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="clone-delay">
            Terminverschiebung (Monate)
          </label>
          <input
            id="clone-delay"
            className="form-input form-input--mono"
            value={delayMonths}
            onChange={(e) => setDelayMonths(e.target.value)}
          />
        </div>
        {cloneMutation.isError && (
          <p className="status-error" role="alert">
            Klonen fehlgeschlagen.
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={cloneMutation.isPending}>
          Klonen
        </button>
      </form>
      <p className="metric-hint" style={{ marginTop: "var(--space-3)" }}>
        Ein leeres, unabhängiges Szenario lässt sich alternativ direkt anlegen:{" "}
        <button
          type="button"
          className="btn-secondary"
          onClick={() =>
            createEmptyMutation.mutate(
              { projectId, input: { name: "Neues Szenario" } },
              { onSuccess: onCloned },
            )
          }
        >
          Leeres Szenario
        </button>
      </p>
    </Modal>
  );
}

function CompletionSection({ projectId }: { projectId: string }) {
  const { data: preview } = useCompletionPreview(projectId);
  const applyMutation = useApplyCompletion();

  if (!preview || preview.already_applied) {
    return null;
  }

  return (
    <div className="card" style={{ marginTop: "var(--space-4)" }}>
      <h2 className="card-heading">Vorhaben abschließen</h2>
      <p className="card-body">
        Legt ein Vermögens-/Schuldenobjekt „{preview.result_asset_name}“ mit{" "}
        {formatMinorUnitsAsEuro(preview.result_asset_value_minor)} an und aktiviert{" "}
        {preview.debt_plans_to_activate.length} verknüpfte(n) Tilgungsplan/-pläne.
      </p>
      {applyMutation.isError && (
        <p className="status-error" role="alert">
          Abschluss fehlgeschlagen.
        </p>
      )}
      <button
        type="button"
        className="btn-primary"
        onClick={() => applyMutation.mutate(projectId)}
        disabled={applyMutation.isPending}
      >
        Vorhaben abschließen
      </button>
    </div>
  );
}
