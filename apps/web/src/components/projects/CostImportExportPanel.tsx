import { useState } from "react";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import {
  newRequestId,
  useApplyCostImport,
  useCostImportProfiles,
  useCreateCostImportProfile,
  useDeleteCostImportProfile,
  usePreviewCostImport,
} from "../../hooks/useProjectCostImport";
import { apiErrorDetail, downloadCostItemsCsv, downloadScenarioXlsx } from "../../lib/api";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type {
  CostImportApplyResult,
  CostImportPreviewResponse,
  CostImportPreviewRow,
  CostImportRowResolution,
} from "../../lib/types";
import { Modal } from "../Modal";

// Excel-/CSV-Import und -Export des Kostenplans (Plan 10, P4). Eigener Tab
// statt Abschnitt im Kosten-Tab: anders als die Ist-Kosten-Zuordnung (die an
// genau einer Position haengt und deshalb in CostPlanTable.tsx lebt) betreffen
// Import/Export immer das gesamte Szenario und sind ein eigenstaendiger,
// mehrstufiger Ablauf.

interface TargetField {
  key: string;
  label: string;
}

// Spiegelt COLUMN_ALIASES aus
// services/api/app/services/project_cost_import_service.py: die jeweils
// ersten Aliasse sind die deutschen Spaltennamen aus Plan 10.1 und dienen als
// Vorbelegung, damit Standard-Kopfzeilen nicht von Hand eingetippt werden
// muessen.
const TARGET_FIELDS: TargetField[] = [
  { key: "section_name", label: "Abschnitt" },
  { key: "item_name", label: "Position" },
  { key: "status", label: "Status" },
  { key: "quantity", label: "Menge" },
  { key: "unit_label", label: "Einheit" },
  { key: "unit_price_minor", label: "Einzelpreis" },
  { key: "estimate_manual_minor", label: "Schätzung" },
  { key: "quoted_amount_minor", label: "Angebot" },
  { key: "committed_amount_minor", label: "Beauftragt" },
  { key: "expected_start_on", label: "Start" },
  { key: "expected_end_on", label: "Ende" },
  { key: "vendor_label", label: "Anbieter" },
  { key: "note", label: "Notiz" },
];

function defaultMapping(): Record<string, string> {
  return Object.fromEntries(TARGET_FIELDS.map((field) => [field.key, field.label]));
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

type RowAction = CostImportRowResolution["action"];

// Die Wahl liegt IMMER beim Anwender: der Server darf nie still anhand
// aehnlicher Namen ueberschreiben (Plan 10.2 Punkt 7). Ein Treffer aus dem
// Preview ist nur eine Vorbelegung der UI-Auswahl, keine Serverentscheidung.
function defaultActionForRow(row: CostImportPreviewRow): RowAction {
  if (row.errors.length > 0) return "skip";
  if (row.matched_item_id) return "update";
  return "create";
}

interface CostImportExportPanelProps {
  projectId: string;
  scenarioId: string;
}

export function CostImportExportPanel({ projectId, scenarioId }: CostImportExportPanelProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const [exportError, setExportError] = useState<string | null>(null);
  const [isWizardOpen, setIsWizardOpen] = useState(false);

  const handleExportXlsx = async () => {
    setExportError(null);
    try {
      const blob = await downloadScenarioXlsx(
        accessToken as string,
        selectedHouseholdId as string,
        projectId,
        scenarioId,
      );
      triggerDownload(blob, "vorhaben-kostenplan.xlsx");
    } catch {
      setExportError("Export als Excel fehlgeschlagen.");
    }
  };

  const handleExportCsv = async () => {
    setExportError(null);
    try {
      const blob = await downloadCostItemsCsv(
        accessToken as string,
        selectedHouseholdId as string,
        projectId,
        scenarioId,
      );
      triggerDownload(blob, "kostenpositionen.csv");
    } catch {
      setExportError("Export der Kostenpositionen fehlgeschlagen.");
    }
  };

  return (
    <div style={{ marginTop: "var(--space-4)" }}>
      <div className="card">
        <h2 className="card-heading">Export</h2>
        <p className="metric-hint">
          Die Excel-Arbeitsmappe enthält alle Berechnungsblätter des Szenarios (Zusammenfassung,
          Kosten, Finanzierung, Tilgungspläne, Cashflow, Förderungen, Annahmen, Ist-Zuordnungen);
          der CSV-Export beschränkt sich auf die Kostenpositionen.
        </p>
        <div style={{ display: "flex", gap: "var(--space-3)" }}>
          <button type="button" className="btn-secondary" onClick={() => void handleExportXlsx()}>
            Als Excel exportieren
          </button>
          <button type="button" className="btn-secondary" onClick={() => void handleExportCsv()}>
            Kostenpositionen als CSV exportieren
          </button>
        </div>
        {exportError && (
          <p className="status-error" role="alert" style={{ marginTop: "var(--space-3)" }}>
            {exportError}
          </p>
        )}
      </div>

      <div className="card" style={{ marginTop: "var(--space-4)" }}>
        <h2 className="card-heading">Import</h2>
        <p className="metric-hint">
          Kostenabschnitte und -positionen aus einer Excel-/CSV-Datei importieren - erst Vorschau
          mit Summen und Fehlern, dann ausdrückliche Bestätigung je Zeile.
        </p>
        <button type="button" className="btn-primary" onClick={() => setIsWizardOpen(true)}>
          Kostenplan importieren
        </button>
      </div>

      {isWizardOpen && (
        <ImportWizard
          projectId={projectId}
          scenarioId={scenarioId}
          onClose={() => setIsWizardOpen(false)}
        />
      )}
    </div>
  );
}

type WizardStep = "select" | "preview" | "done";

function ImportWizard({
  projectId,
  scenarioId,
  onClose,
}: {
  projectId: string;
  scenarioId: string;
  onClose: () => void;
}) {
  const [step, setStep] = useState<WizardStep>("select");
  const [file, setFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>(defaultMapping());
  const [sheetName, setSheetName] = useState("");
  const [headerRow, setHeaderRow] = useState("1");
  const [profileName, setProfileName] = useState("");
  const [selectError, setSelectError] = useState<string | null>(null);

  const [preview, setPreview] = useState<CostImportPreviewResponse | null>(null);
  const [resolutions, setResolutions] = useState<Record<number, RowAction>>({});
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applyResult, setApplyResult] = useState<CostImportApplyResult | null>(null);
  const [requestId, setRequestId] = useState(() => newRequestId());

  const profilesQuery = useCostImportProfiles();
  const createProfile = useCreateCostImportProfile();
  const deleteProfile = useDeleteCostImportProfile();
  const previewMutation = usePreviewCostImport();
  const applyMutation = useApplyCostImport();

  const handleApplyProfile = (profileId: string) => {
    const profile = profilesQuery.data?.find((p) => p.id === profileId);
    if (!profile) return;
    setMapping({ ...defaultMapping(), ...(profile.column_mapping ?? {}) });
  };

  const handlePreview = () => {
    if (!file) {
      setSelectError("Bitte zuerst eine Datei auswählen.");
      return;
    }
    setSelectError(null);
    previewMutation.mutate(
      {
        projectId,
        scenarioId,
        file,
        mapping,
        sheetName: sheetName.trim() || undefined,
        headerRow: Number(headerRow) || 1,
      },
      {
        onSuccess: (result) => {
          setPreview(result);
          const initialResolutions: Record<number, RowAction> = {};
          for (const row of result.rows) {
            initialResolutions[row.row_number] = defaultActionForRow(row);
          }
          setResolutions(initialResolutions);
          setStep("preview");
        },
        onError: () => {
          setSelectError(
            "Vorschau konnte nicht erstellt werden. Bitte Datei, Blatt und Spaltenzuordnung prüfen.",
          );
        },
      },
    );
  };

  const handleSaveProfile = () => {
    if (!profileName.trim() || !file) return;
    const format = file.name.toLowerCase().endsWith(".csv") ? "csv_generic" : "xlsx_generic";
    createProfile.mutate(
      { name: profileName.trim(), format, column_mapping: mapping },
      { onSuccess: () => setProfileName("") },
    );
  };

  const setRowAction = (rowNumber: number, action: RowAction) => {
    setResolutions((prev) => ({ ...prev, [rowNumber]: action }));
  };

  const buildResolutions = (): CostImportRowResolution[] => {
    if (!preview) return [];
    return preview.rows.map((row) => {
      const action = resolutions[row.row_number] ?? defaultActionForRow(row);
      return {
        row_number: row.row_number,
        action,
        target_item_id: action === "update" ? (row.matched_item_id ?? null) : null,
      };
    });
  };

  const handleApply = () => {
    if (!file || !preview) return;
    setApplyError(null);
    applyMutation.mutate(
      {
        projectId,
        scenarioId,
        file,
        mapping,
        sheetName: sheetName.trim() || undefined,
        headerRow: Number(headerRow) || 1,
        resolutions: buildResolutions(),
        requestId,
        previewFingerprint: preview.preview_fingerprint,
      },
      {
        onSuccess: (result) => {
          setApplyResult(result);
          setStep("done");
        },
        onError: (error) => {
          if (apiErrorDetail(error) === "preview_fingerprint_mismatch") {
            setApplyError(
              "Die Daten haben sich seit der Vorschau geändert - bitte die Vorschau erneut laden, bevor der Import angewendet wird.",
            );
          } else {
            setApplyError("Import konnte nicht angewendet werden.");
          }
        },
      },
    );
  };

  const handleBackToSelect = () => {
    setApplyError(null);
    setStep("select");
  };

  const handleRepreview = () => {
    setApplyError(null);
    setRequestId(newRequestId());
    handlePreview();
  };

  return (
    <Modal title="Kostenplan importieren" size="wide" onClose={onClose}>
      {step === "select" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <div className="form-field form-field--file">
            <label className="form-label" htmlFor="cost-import-file">
              Datei (.csv, .xlsx) *
            </label>
            <input
              id="cost-import-file"
              type="file"
              className="form-input"
              accept=".csv,.xlsx"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>

          {profilesQuery.data && profilesQuery.data.length > 0 && (
            <div className="form-field">
              <label className="form-label" htmlFor="cost-import-profile">
                Gespeichertes Mappingprofil übernehmen
              </label>
              <select
                id="cost-import-profile"
                className="form-input"
                defaultValue=""
                onChange={(e) => {
                  if (e.target.value) handleApplyProfile(e.target.value);
                }}
              >
                <option value="">-- Profil wählen --</option>
                {profilesQuery.data.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)" }}>
            <div className="form-field">
              <label className="form-label" htmlFor="cost-import-sheet">
                Blattname (nur XLSX, optional)
              </label>
              <input
                id="cost-import-sheet"
                className="form-input"
                value={sheetName}
                onChange={(e) => setSheetName(e.target.value)}
                placeholder="automatisch"
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="cost-import-header-row">
                Kopfzeile (Zeilennummer)
              </label>
              <input
                id="cost-import-header-row"
                className="form-input form-input--mono"
                value={headerRow}
                onChange={(e) => setHeaderRow(e.target.value)}
              />
            </div>
          </div>

          <div>
            <h3 className="card-heading">Spaltenzuordnung</h3>
            <p className="metric-hint">
              Vorbelegt mit den üblichen deutschen Spaltennamen (Plan 10.1) - bei abweichenden
              Kopfzeilen bitte anpassen.
            </p>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)" }}>
              {TARGET_FIELDS.map((field) => (
                <div className="form-field" key={field.key}>
                  <label className="form-label" htmlFor={`mapping-${field.key}`}>
                    {field.label}
                  </label>
                  <input
                    id={`mapping-${field.key}`}
                    className="form-input"
                    value={mapping[field.key] ?? ""}
                    onChange={(e) =>
                      setMapping((prev) => ({ ...prev, [field.key]: e.target.value }))
                    }
                  />
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "flex-end" }}>
            <div className="form-field" style={{ flex: 1 }}>
              <label className="form-label" htmlFor="cost-import-profile-name">
                Als Profil speichern
              </label>
              <input
                id="cost-import-profile-name"
                className="form-input"
                value={profileName}
                onChange={(e) => setProfileName(e.target.value)}
                placeholder="Profilname"
              />
            </div>
            <button
              type="button"
              className="btn-secondary"
              disabled={!profileName.trim() || !file || createProfile.isPending}
              onClick={handleSaveProfile}
            >
              Profil speichern
            </button>
          </div>

          {profilesQuery.data && profilesQuery.data.length > 0 && (
            <div>
              <h3 className="card-heading">Gespeicherte Profile</h3>
              <ul style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                {profilesQuery.data.map((profile) => (
                  <li
                    key={profile.id}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <span>{profile.name}</span>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => deleteProfile.mutate(profile.id)}
                      disabled={deleteProfile.isPending}
                    >
                      Löschen
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {selectError && (
            <p className="status-error" role="alert">
              {selectError}
            </p>
          )}

          <button
            type="button"
            className="btn-primary"
            onClick={handlePreview}
            disabled={previewMutation.isPending}
            style={{ alignSelf: "start" }}
          >
            {previewMutation.isPending ? "Vorschau wird erstellt..." : "Vorschau anzeigen"}
          </button>
        </div>
      )}

      {step === "preview" && preview && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <div className="metric-grid">
            <div className="card metric-card">
              <span className="metric-label">Zeilen</span>
              <span className="metric-value">{preview.sums.row_count}</span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Neue Positionen</span>
              <span className="metric-value">{preview.sums.new_items_count}</span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Erkannte Positionen</span>
              <span className="metric-value">{preview.sums.matched_items_count}</span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Zeilen mit Fehlern</span>
              <span className="metric-value">{preview.sums.rows_with_errors_count}</span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Summe Schätzung</span>
              <span className="metric-value">
                {formatMinorUnitsAsEuro(preview.sums.estimate_total_minor)}
              </span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Summe Angebot</span>
              <span className="metric-value">
                {formatMinorUnitsAsEuro(preview.sums.quoted_total_minor)}
              </span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Summe Beauftragt</span>
              <span className="metric-value">
                {formatMinorUnitsAsEuro(preview.sums.committed_total_minor)}
              </span>
            </div>
          </div>

          <div className="txn-table-wrapper">
            <table className="txn-table">
              <thead>
                <tr>
                  <th className="txn-th">#</th>
                  <th className="txn-th">Abschnitt</th>
                  <th className="txn-th">Position</th>
                  <th className="txn-th txn-th--calc-mode">Berechnung</th>
                  <th className="txn-th txn-th--amount-wide">Schätzung</th>
                  <th className="txn-th txn-th--amount-wide">Angebot</th>
                  <th className="txn-th txn-th--amount-wide">Beauftragt</th>
                  <th className="txn-th">Fehler</th>
                  <th className="txn-th">Aktion</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row) => {
                  const action = resolutions[row.row_number] ?? defaultActionForRow(row);
                  const hasErrors = row.errors.length > 0;
                  return (
                    <tr className="txn-tr" key={row.row_number}>
                      <td className="txn-td">{row.row_number}</td>
                      <td className="txn-td">{row.section_name ?? "—"}</td>
                      <td className="txn-td">{row.item_name ?? "—"}</td>
                      <td className="txn-td txn-td--calc-mode">
                        {row.calculation_mode === "quantity" ? "Menge × Preis" : "Fest"}
                      </td>
                      <td className="txn-td txn-td--amount-wide">
                        {row.estimate_manual_minor !== null
                          ? formatMinorUnitsAsEuro(row.estimate_manual_minor)
                          : "—"}
                      </td>
                      <td className="txn-td txn-td--amount-wide">
                        {row.quoted_amount_minor !== null
                          ? formatMinorUnitsAsEuro(row.quoted_amount_minor)
                          : "—"}
                      </td>
                      <td className="txn-td txn-td--amount-wide">
                        {row.committed_amount_minor !== null
                          ? formatMinorUnitsAsEuro(row.committed_amount_minor)
                          : "—"}
                      </td>
                      <td className="txn-td">
                        {hasErrors ? (
                          <span className="status-error">{row.errors.join("; ")}</span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="txn-td">
                        <select
                          className="form-input"
                          aria-label={`Aktion für Zeile ${row.row_number}`}
                          value={action}
                          disabled={hasErrors}
                          onChange={(e) =>
                            setRowAction(row.row_number, e.target.value as RowAction)
                          }
                        >
                          <option value="create">Neu anlegen</option>
                          {row.matched_item_id && <option value="update">Aktualisieren</option>}
                          <option value="skip">
                            {hasErrors ? "Übersprungen (Fehler)" : "Überspringen"}
                          </option>
                        </select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {applyError && (
            <div>
              <p className="status-error" role="alert">
                {applyError}
              </p>
              {apiErrorDetail(applyMutation.error) === "preview_fingerprint_mismatch" && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleRepreview}
                  disabled={previewMutation.isPending}
                >
                  Vorschau neu laden
                </button>
              )}
            </div>
          )}

          <div style={{ display: "flex", gap: "var(--space-3)" }}>
            <button type="button" className="btn-secondary" onClick={handleBackToSelect}>
              Zurück
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={handleApply}
              disabled={applyMutation.isPending}
            >
              {applyMutation.isPending ? "Wird angewendet..." : "Import anwenden"}
            </button>
          </div>
        </div>
      )}

      {step === "done" && applyResult && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          <p className="card-body">
            {applyResult.created_item_ids.length} Position(en) angelegt,{" "}
            {applyResult.updated_item_ids.length} aktualisiert,{" "}
            {applyResult.skipped_row_numbers.length} übersprungen.
          </p>
          {applyResult.idempotent_replay && (
            <p className="metric-hint">
              Dieser Import wurde bereits zuvor mit derselben Anfrage-ID angewendet - das Ergebnis
              stammt aus dem ursprünglichen Lauf.
            </p>
          )}
          <button
            type="button"
            className="btn-primary"
            onClick={onClose}
            style={{ alignSelf: "start" }}
          >
            Schließen
          </button>
        </div>
      )}
    </Modal>
  );
}
