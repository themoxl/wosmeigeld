import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateProject, useProjectList } from "../hooks/useProjects";
import { formatMinorUnitsAsEuro } from "../lib/money";
import { GERMAN_REGION_OPTIONS } from "../lib/regions";
import type { MajorProjectCreateInput, ProjectStatus, ProjectType } from "../lib/types";
import { Modal } from "../components/Modal";

const PROJECT_TYPE_OPTIONS: { value: ProjectType; label: string }[] = [
  { value: "home_build", label: "Hausbau" },
  { value: "property_purchase", label: "Immobilienkauf" },
  { value: "renovation", label: "Sanierung" },
  { value: "vehicle", label: "Fahrzeug" },
  { value: "solar", label: "Photovoltaik" },
  { value: "education", label: "Ausbildung" },
  { value: "business", label: "Gründung" },
  { value: "other", label: "Sonstiges" },
];

const TEMPLATE_OPTIONS: { value: string; label: string; forType: ProjectType[] }[] = [
  { value: "", label: "Leer beginnen", forType: [] },
  { value: "home_build_de", label: "Hausbau Deutschland", forType: ["home_build"] },
  {
    value: "property_purchase_de",
    label: "Immobilienkauf Deutschland",
    forType: ["property_purchase"],
  },
  { value: "renovation_de", label: "Sanierung", forType: ["renovation"] },
  {
    value: "general_major_purchase",
    label: "Allgemeine Großanschaffung",
    forType: ["vehicle", "solar", "education", "business", "other"],
  },
];

const STATUS_LABELS: Record<ProjectStatus, string> = {
  idea: "Idee",
  planning: "Planung",
  approved: "Bestätigt",
  active: "Aktiv",
  completed: "Abgeschlossen",
  cancelled: "Storniert",
  archived: "Archiviert",
};

export function ProjectsPage() {
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [isWizardOpen, setIsWizardOpen] = useState(false);

  const { data: projects, isLoading } = useProjectList({
    status: statusFilter || undefined,
    search: search || undefined,
    includeArchived,
  });
  const createMutation = useCreateProject();

  const [name, setName] = useState("");
  const [projectType, setProjectType] = useState<ProjectType>("home_build");
  const [templateKey, setTemplateKey] = useState("home_build_de");
  const [regionCode, setRegionCode] = useState("");
  const [postalCode, setPostalCode] = useState("");

  const resetWizard = () => {
    setName("");
    setProjectType("home_build");
    setTemplateKey("home_build_de");
    setRegionCode("");
    setPostalCode("");
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    const input: MajorProjectCreateInput = {
      name: name.trim(),
      project_type: projectType,
      template_key: templateKey || undefined,
      region_code: regionCode || undefined,
      postal_code: postalCode.trim() || undefined,
    };
    createMutation.mutate(input, {
      onSuccess: (project) => {
        setIsWizardOpen(false);
        resetWizard();
        navigate(`/vorhaben/${project.id}`);
      },
    });
  };

  const availableTemplates = TEMPLATE_OPTIONS.filter(
    (option) => option.forType.length === 0 || option.forType.includes(projectType),
  );

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Vorhaben</h1>
          <p className="placeholder-note">
            Kostenplan, Finanzierung und Fördermöglichkeiten für Hausbau, Immobilienkauf, Sanierung
            und andere Großanschaffungen.
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={() => setIsWizardOpen(true)}>
          + Vorhaben
        </button>
      </div>

      <div style={{ display: "flex", gap: "var(--space-3)", marginBottom: "var(--space-4)" }}>
        <input
          type="text"
          className="form-input"
          placeholder="Suche nach Name..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="form-input"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">Alle Status</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <label
          className="form-label"
          style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}
        >
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(e) => setIncludeArchived(e.target.checked)}
          />
          Archivierte zeigen
        </label>
      </div>

      {isLoading && <p className="placeholder-note">Lade Vorhaben...</p>}

      {!isLoading && (projects ?? []).length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
          <h2 className="card-heading">Noch kein Vorhaben angelegt</h2>
          <p className="card-body">
            Ein Vorhaben ist mehr als ein Wunsch oder ein Sparziel: Es besitzt Kostenpositionen,
            Zeitverlauf, Finanzierungsmix und Risiken. Legen Sie das erste Vorhaben über den Button
            oben an.
          </p>
        </div>
      )}

      {(projects ?? []).length > 0 && (
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th">Name</th>
                <th className="txn-th">Typ</th>
                <th className="txn-th">Status</th>
                <th className="txn-th">Szenario</th>
                <th className="txn-th txn-th--amount">Projektkosten</th>
                <th className="txn-th">Zieltermin</th>
              </tr>
            </thead>
            <tbody>
              {(projects ?? []).map((project) => (
                <tr
                  key={project.id}
                  className="txn-tr"
                  style={{ cursor: "pointer" }}
                  onClick={() => navigate(`/vorhaben/${project.id}`)}
                >
                  <td className="txn-td">
                    <span className="txn-payee">{project.name}</span>
                  </td>
                  <td className="txn-td">
                    {PROJECT_TYPE_OPTIONS.find((o) => o.value === project.project_type)?.label ??
                      project.project_type}
                  </td>
                  <td className="txn-td">
                    <span className="status-badge status-badge--neutral">
                      {STATUS_LABELS[project.status]}
                    </span>
                  </td>
                  <td className="txn-td">{project.selected_scenario_name ?? "—"}</td>
                  <td className="txn-td txn-td--amount">
                    {project.project_cash_cost_minor !== null &&
                    project.project_cash_cost_minor !== undefined
                      ? formatMinorUnitsAsEuro(project.project_cash_cost_minor)
                      : "—"}
                  </td>
                  <td className="txn-td">{project.planned_completion_on ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {isWizardOpen && (
        <Modal title="Neues Vorhaben" onClose={() => setIsWizardOpen(false)}>
          <form
            onSubmit={handleSubmit}
            style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
          >
            <div className="form-field">
              <label className="form-label" htmlFor="project-name">
                Name *
              </label>
              <input
                id="project-name"
                type="text"
                className="form-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="project-type">
                Art
              </label>
              <select
                id="project-type"
                className="form-input"
                value={projectType}
                onChange={(e) => {
                  const value = e.target.value as ProjectType;
                  setProjectType(value);
                  const firstMatch = TEMPLATE_OPTIONS.find(
                    (option) => option.forType.includes(value) || option.forType.length === 0,
                  );
                  setTemplateKey(firstMatch?.value ?? "");
                }}
              >
                {PROJECT_TYPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="project-template">
                Vorlage
              </label>
              <select
                id="project-template"
                className="form-input"
                value={templateKey}
                onChange={(e) => setTemplateKey(e.target.value)}
              >
                {availableTemplates.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <p className="metric-hint">
                Vorlagen enthalten Kostenkategorien ohne behauptete Marktpreise — Sie tragen die
                realen Beträge selbst ein.
              </p>
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="project-region">
                Bundesland
              </label>
              <select
                id="project-region"
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
              <p className="metric-hint">
                Landesprogramme werden nur gefunden, wenn das Bundesland gesetzt ist. Nachtragen
                geht jederzeit über die Stammdaten des Vorhabens.
              </p>
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="project-postal-code">
                Postleitzahl
              </label>
              <input
                id="project-postal-code"
                type="text"
                inputMode="numeric"
                className="form-input form-input--mono"
                value={postalCode}
                onChange={(e) => setPostalCode(e.target.value)}
                placeholder="optional"
              />
            </div>
            {createMutation.isError && (
              <p className="status-error" role="alert">
                Vorhaben konnte nicht angelegt werden.
              </p>
            )}
            <button type="submit" className="btn-primary" disabled={createMutation.isPending}>
              {createMutation.isPending ? "Wird angelegt..." : "Anlegen"}
            </button>
          </form>
        </Modal>
      )}
    </section>
  );
}
