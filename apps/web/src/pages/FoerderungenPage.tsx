import { useState } from "react";
import { useProjectList, useProjectScenarios } from "../hooks/useProjects";
import { FundingPanel } from "../components/projects/FundingPanel";
import {
  useDeactivateFundingProgram,
  useFundingCatalogStatus,
  useFundingPrograms,
} from "../hooks/useFunding";
import { useFormatDate } from "../hooks/useProfile";
import {
  BENEFIT_TYPE_LABELS,
  FUNDING_SCOPE_LABELS,
  NORMALIZATION_STATUS_LABELS,
} from "../lib/funding";
import { regionLabel } from "../lib/regions";
import { FundingImportExportModal } from "../components/FundingImportExportModal";
import { FundingProgramEditorModal } from "../components/FundingProgramEditorModal";

type FoerderTab = "register" | "pruefung";

export function FoerderungenPage() {
  // Zwei Fragen, zwei Register: "was gibt es" und "steht mir das zu". Die
  // Pruefung sass frueher als eigener Tab im Vorhaben - dort gehoerte sie
  // nicht hin, weil im Vorhaben Geld geplant wird und nicht Berechtigung
  // geklaert. Geld kommt seither ausschliesslich ueber "+ Foerderung" im
  // Register Finanzierung ins Vorhaben.
  const [tab, setTab] = useState<FoerderTab>("register");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [editingProgramId, setEditingProgramId] = useState<string | null>(null);
  const { data: programs, isLoading } = useFundingPrograms({ includeInactive });
  const { data: catalogStatus } = useFundingCatalogStatus();
  const deactivate = useDeactivateFundingProgram();
  const { formatDate: formatDateValue } = useFormatDate();
  const formatDate = (value: string | null | undefined) => (value ? formatDateValue(value) : "—");

  const rows = programs ?? [];
  const ownCount = rows.filter((program) => program.is_own).length;
  const staleCount = rows.filter((program) => program.is_stale).length;
  const lastRun = (catalogStatus?.providers ?? [])
    .map((provider) => provider.last_success_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Förderprogramme</h1>
          <p className="placeholder-note">
            Der Katalog, gegen den der Fördercheck in einem Vorhaben prüft. Mitgelieferte Programme
            sind fest eingebaut; eigene spielen Sie als Paket oder Datei ein und können sie auch
            wieder exportieren.
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={() => setIsImportOpen(true)}>
          Importieren / Exportieren
        </button>
      </div>

      <div className="filter-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "register"}
          className={`filter-tab ${tab === "register" ? "filter-tab--active" : ""}`}
          onClick={() => setTab("register")}
        >
          Register
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "pruefung"}
          className={`filter-tab ${tab === "pruefung" ? "filter-tab--active" : ""}`}
          onClick={() => setTab("pruefung")}
        >
          Prüfung
        </button>
      </div>

      {tab === "pruefung" && <FundingCheckSection />}

      {tab === "register" && (
        <>
          <div className="metric-grid" style={{ marginBottom: "var(--space-4)" }}>
            <div className="card metric-card">
              <span className="metric-label">Programme im Katalog</span>
              <span className="metric-value">{rows.length}</span>
              <span className="metric-hint">{ownCount} davon selbst eingespielt</span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Veraltet</span>
              <span className="metric-value">{staleCount}</span>
              <span className="metric-hint">
                Der Fördercheck stuft solche Treffer bereits auf „unklar“ zurück.
              </span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Letzter Katalogstand</span>
              <span className="metric-value">{formatDate(lastRun)}</span>
            </div>
          </div>

          <label
            className="form-label"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              marginBottom: "var(--space-3)",
            }}
          >
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)}
            />
            Deaktivierte Programme zeigen
          </label>

          {isLoading && <p className="placeholder-note">Lade Förderprogramme...</p>}

          {!isLoading && rows.length === 0 && (
            <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
              <h2 className="card-heading">Kein Programm im Katalog</h2>
              <p className="card-body">
                Über „Importieren / Exportieren“ lassen sich mitgelieferte Förderpakete einspielen
                oder eine eigene Paketdatei laden.
              </p>
            </div>
          )}

          {rows.length > 0 && (
            <div className="txn-table-wrapper">
              <table className="txn-table">
                <thead>
                  <tr>
                    <th className="txn-th">Programm</th>
                    <th className="txn-th">Art</th>
                    <th className="txn-th">Geltungsbereich</th>
                    <th className="txn-th">Stand</th>
                    <th className="txn-th">Recherchestand</th>
                    <th className="txn-th txn-th--actions-wide"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((program) => (
                    <tr className="txn-tr" key={program.id}>
                      <td className="txn-td">
                        <span className="txn-payee">{program.title}</span>
                        <div className="metric-hint">{program.provider_name}</div>
                        <div className="fie-badge-row">
                          <span className="status-badge status-badge--neutral">
                            {program.is_own ? "Eigener Eintrag" : "Mitgeliefert"}
                          </span>
                          {program.is_stale && (
                            <span className="status-badge status-badge--warning">Veraltet</span>
                          )}
                          {!program.is_active && (
                            <span className="status-badge status-badge--error">Deaktiviert</span>
                          )}
                        </div>
                      </td>
                      <td className="txn-td">
                        {program.benefit_type
                          ? (BENEFIT_TYPE_LABELS[program.benefit_type] ?? program.benefit_type)
                          : "—"}
                      </td>
                      <td className="txn-td">
                        {FUNDING_SCOPE_LABELS[program.scope] ?? program.scope}
                        {program.region_codes.length > 0 && (
                          <div className="metric-hint">
                            {program.region_codes.map(regionLabel).join(", ")}
                          </div>
                        )}
                      </td>
                      <td className="txn-td">
                        {program.source_version ?? "—"}
                        <div className="metric-hint">
                          abgerufen {formatDate(program.retrieved_at)} · gültig bis{" "}
                          {formatDate(program.review_due_at)}
                        </div>
                      </td>
                      <td className="txn-td">
                        {program.normalization_status
                          ? (NORMALIZATION_STATUS_LABELS[program.normalization_status] ??
                            program.normalization_status)
                          : "—"}
                      </td>
                      <td className="txn-td txn-td--actions-wide">
                        <div className="actions-cell" style={{ justifyContent: "flex-end" }}>
                          <a
                            className="btn-secondary"
                            href={program.source_url ?? program.official_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Quelle
                          </a>
                          {/* Konditionen sind auch bei mitgelieferten Programmen
                          nachtragbar - der Zinssatz steht in keinem davon, weil
                          Förderbanken ihn täglich neu festsetzen. Die Eingabe
                          legt eine eigene Version über den kuratierten Stand,
                          ohne ihn zu verändern. Deaktivieren bleibt eigenen
                          Einträgen vorbehalten: einen mitgelieferten Eintrag
                          stilllegen hieße, den Katalog zu beschneiden. */}
                          {program.is_active && (
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={() => setEditingProgramId(program.id)}
                            >
                              Konditionen
                            </button>
                          )}
                          {program.is_own && program.is_active && (
                            <>
                              <button
                                type="button"
                                className="btn-secondary"
                                onClick={() => deactivate.mutate(program.id)}
                                disabled={deactivate.isPending}
                                title="Nimmt das Programm aus dem Fördercheck. Bereits übernommene Finanzierungsquellen bleiben unverändert."
                              >
                                Deaktivieren
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {isImportOpen && (
        <FundingImportExportModal onClose={() => setIsImportOpen(false)} programs={rows} />
      )}
      {editingProgramId && (
        <FundingProgramEditorModal
          programId={editingProgramId}
          onClose={() => setEditingProgramId(null)}
        />
      )}
    </section>
  );
}

/**
 * Foerderfaehigkeit pruefen - Profil, Regelpruefung und Kombinierbarkeit.
 *
 * Die Pruefung braucht ein Vorhaben, weil Region und Vorhabenart darueber
 * entscheiden, welche Programme ueberhaupt in Frage kommen. Sie sitzt aber
 * hier statt im Vorhaben: "steht mir das zu" gehoert zum Foerderkatalog,
 * "was kostet es mich" zur Finanzierung.
 */
function FundingCheckSection() {
  const { data: projects, isLoading } = useProjectList();
  const [projectId, setProjectId] = useState<string | null>(null);
  const effectiveProjectId = projectId ?? projects?.[0]?.id ?? null;
  const { data: scenarios } = useProjectScenarios(effectiveProjectId);
  const scenarioId =
    scenarios?.find((scenario) => scenario.status === "selected")?.id ?? scenarios?.[0]?.id ?? null;

  if (isLoading) {
    return <p className="placeholder-note">Lade Vorhaben...</p>;
  }
  if (!projects || projects.length === 0) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
        <h2 className="card-heading">Noch kein Vorhaben</h2>
        <p className="card-body">
          Die Prüfung braucht ein Vorhaben, weil Region und Vorhabenart bestimmen, welche Programme
          in Frage kommen. Lege zuerst eines unter „Vorhaben“ an.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="form-field" style={{ maxWidth: "26rem" }}>
        <label className="form-label" htmlFor="funding-check-project">
          Prüfen für Vorhaben
        </label>
        <select
          id="funding-check-project"
          className="form-input"
          value={effectiveProjectId ?? ""}
          onChange={(e) => setProjectId(e.target.value)}
        >
          {projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
      </div>
      {effectiveProjectId && scenarioId ? (
        <FundingPanel projectId={effectiveProjectId} scenarioId={scenarioId} />
      ) : (
        <p className="placeholder-note">Für dieses Vorhaben gibt es noch kein Szenario.</p>
      )}
    </div>
  );
}
