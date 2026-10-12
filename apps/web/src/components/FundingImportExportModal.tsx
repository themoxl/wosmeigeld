import { useCallback, useRef, useState } from "react";
import { Modal } from "./Modal";
import {
  useExportFundingPrograms,
  useFundingPackages,
  useLoadFundingPackage,
  usePreviewFundingImport,
  useRunFundingImport,
} from "../hooks/useFunding";
import type {
  FundingImportResult,
  FundingPackagePayload,
  FundingPreviewItem,
  FundingPreviewResponse,
  FundingPreviewStatus,
  FundingProgramSummary,
} from "../lib/types";

type Tab = "packages" | "import" | "export";

const STATUS_LABELS: Record<FundingPreviewStatus, string> = {
  new: "Neu",
  updated: "Aktualisiert",
  unchanged: "Unverändert",
  conflict: "Konflikt",
  invalid: "Ungültig",
};

function describeItem(item: FundingPreviewItem): string | null {
  switch (item.status) {
    case "updated": {
      const changed = item.changed_fields ?? [];
      const fields = changed.length ? changed.join(", ") : "Inhalt weicht ab";
      const version = item.existing_source_version
        ? ` Löst Stand „${item.existing_source_version}“ ab.`
        : "";
      return `Ändert sich: ${fields}.${version}`;
    }
    case "unchanged":
      return "Inhaltlich identisch mit dem bereits eingespielten Stand.";
    case "conflict":
      return "Überschreibt ein mitgeliefertes Programm mit demselben Schlüssel. Der mitgelieferte Stand bleibt erhalten, wird aber verdeckt.";
    case "invalid":
      return (item.problems ?? []).join(" · ");
    default:
      return null;
  }
}

/** Positionen, die der Import auslassen soll (alles, was nicht angehakt ist). */
function deselectedIndices(preview: FundingPreviewResponse, selected: Set<number>): number[] {
  return preview.items.filter((item) => !selected.has(item.index)).map((item) => item.index);
}

function defaultSelection(preview: FundingPreviewResponse): Set<number> {
  return new Set(preview.items.filter((item) => item.recommended).map((item) => item.index));
}

function downloadPackage(payload: FundingPackagePayload): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `foerderprogramme-${new Date().toISOString().slice(0, 10)}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

interface FundingImportExportModalProps {
  onClose: () => void;
  programs: FundingProgramSummary[];
}

export function FundingImportExportModal({ onClose, programs }: FundingImportExportModalProps) {
  const [activeTab, setActiveTab] = useState<Tab>("packages");

  const packagesQuery = useFundingPackages();
  const loadPackage = useLoadFundingPackage();
  const previewMutation = usePreviewFundingImport();
  const importMutation = useRunFundingImport();
  const exportMutation = useExportFundingPrograms();

  // Paket-Tab und Datei-Tab fuehren getrennte Zustaende: sonst uebernaehme ein
  // Dateiimport die Haken des zuletzt geoeffneten Pakets.
  const [packagePayload, setPackagePayload] = useState<FundingPackagePayload | null>(null);
  const [packagePreview, setPackagePreview] = useState<FundingPreviewResponse | null>(null);
  const [packageSelected, setPackageSelected] = useState<Set<number>>(new Set());
  const [packageResult, setPackageResult] = useState<FundingImportResult | null>(null);

  const [filePayload, setFilePayload] = useState<FundingPackagePayload | null>(null);
  const [filePreview, setFilePreview] = useState<FundingPreviewResponse | null>(null);
  const [fileSelected, setFileSelected] = useState<Set<number>>(new Set());
  const [fileResult, setFileResult] = useState<FundingImportResult | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [exportName, setExportName] = useState("Meine Förderprogramme");
  const [exportSelected, setExportSelected] = useState<Set<string>>(new Set());

  const ownPrograms = programs.filter((program) => program.is_own);

  const openPackage = (packageId: string) => {
    setPackageResult(null);
    loadPackage.mutate(packageId, {
      onSuccess: (payload) => {
        setPackagePayload(payload);
        previewMutation.mutate(
          { package: payload, skip_indices: [] },
          {
            onSuccess: (preview) => {
              setPackagePreview(preview);
              setPackageSelected(defaultSelection(preview));
            },
          },
        );
      },
    });
  };

  const readFile = useCallback(
    (file: File) => {
      setFileError(null);
      setFileResult(null);
      void file.text().then((raw) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(raw);
        } catch {
          setFileError("Die Datei ist kein gültiges JSON.");
          return;
        }
        const candidate = parsed as Partial<FundingPackagePayload>;
        if (!candidate || !Array.isArray(candidate.programs)) {
          setFileError(
            "Das ist kein Förderpaket: Es fehlt die Liste „programs“. Erwartet wird eine Datei, wie sie der Export erzeugt.",
          );
          return;
        }
        const payload = candidate as FundingPackagePayload;
        setFilePayload(payload);
        previewMutation.mutate(
          { package: payload, skip_indices: [] },
          {
            onSuccess: (preview) => {
              setFilePreview(preview);
              setFileSelected(defaultSelection(preview));
            },
            onError: () => setFileError("Die Vorschau konnte nicht erzeugt werden."),
          },
        );
      });
    },
    [previewMutation],
  );

  const runImport = (
    payload: FundingPackagePayload,
    preview: FundingPreviewResponse,
    selected: Set<number>,
    onDone: (result: FundingImportResult) => void,
  ) => {
    importMutation.mutate(
      { package: payload, skip_indices: deselectedIndices(preview, selected) },
      { onSuccess: onDone },
    );
  };

  const renderPreview = (
    preview: FundingPreviewResponse,
    selected: Set<number>,
    setSelected: (next: Set<number>) => void,
  ) => (
    <>
      <h3 className="card-heading">Vorschau — noch nichts gespeichert</h3>
      <div className="metric-grid" style={{ marginBottom: "var(--space-3)" }}>
        <div className="card metric-card">
          <span className="metric-label">Programme</span>
          <span className="metric-value">{preview.items.length}</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Neu</span>
          <span className="metric-value">{preview.new_count}</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Aktualisiert</span>
          <span className="metric-value">{preview.updated_count}</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Unverändert</span>
          <span className="metric-value">{preview.unchanged_count}</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Ungültig</span>
          <span className="metric-value">{preview.invalid_count}</span>
        </div>
      </div>
      <div className="fie-preview-list">
        {preview.items.map((item) => {
          const isSelectable = item.status !== "invalid";
          const isSelected = selected.has(item.index);
          const reason = describeItem(item);
          return (
            <label
              key={item.index}
              className="fie-preview-item"
              data-status={item.status}
              data-selected={isSelected}
            >
              <input
                type="checkbox"
                checked={isSelected}
                disabled={!isSelectable}
                onChange={() => {
                  const next = new Set(selected);
                  if (next.has(item.index)) next.delete(item.index);
                  else next.add(item.index);
                  setSelected(next);
                }}
              />
              <span className="fie-preview-main">
                <span>{item.title}</span>
                <span className="fie-preview-pattern">
                  {item.provider_name} · {item.semantic_key}
                </span>
                {reason && <span className="fie-preview-reason">{reason}</span>}
                {(item.adopted_by_projects ?? 0) > 0 && (
                  <span className="fie-preview-reason">
                    {item.adopted_by_projects} Finanzierungsquelle(n) nutzen den bisherigen Stand —
                    sie behalten ihn und rechnen unverändert weiter.
                  </span>
                )}
              </span>
              <span className="fie-preview-status" data-status={item.status}>
                {STATUS_LABELS[item.status]}
              </span>
            </label>
          );
        })}
      </div>
    </>
  );

  const renderResult = (result: FundingImportResult) => (
    <div className="card" style={{ marginTop: "var(--space-3)" }}>
      <p className="card-body">
        {result.programs_created} Programm(e) neu, {result.versions_created} Stand/Stände
        eingespielt, {result.unchanged} unverändert, {result.skipped_deselected} abgewählt
        {result.invalid > 0 ? `, ${result.invalid} ungültig übersprungen` : ""}.
      </p>
      {(result.errors ?? []).length > 0 && (
        <ul>
          {(result.errors ?? []).map((error) => (
            <li key={error} className="status-error">
              {error}
            </li>
          ))}
        </ul>
      )}
    </div>
  );

  return (
    <Modal title="Förderprogramme importieren & exportieren" onClose={onClose}>
      <div className="fie-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "packages"}
          className={`fie-tab ${activeTab === "packages" ? "fie-tab--active" : ""}`}
          onClick={() => setActiveTab("packages")}
        >
          📦 Pakete
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "import"}
          className={`fie-tab ${activeTab === "import" ? "fie-tab--active" : ""}`}
          onClick={() => setActiveTab("import")}
        >
          📁 Importieren
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "export"}
          className={`fie-tab ${activeTab === "export" ? "fie-tab--active" : ""}`}
          onClick={() => setActiveTab("export")}
        >
          📤 Exportieren
        </button>
      </div>

      <div className="fie-tab-content">
        {activeTab === "packages" && (
          <>
            {!packagePreview && (
              <div className="fie-packages-grid">
                {(packagesQuery.data ?? []).map((pkg) => {
                  const fullyInstalled =
                    pkg.installed_count === pkg.program_count && pkg.outdated_count === 0;
                  const buttonLabel = fullyInstalled
                    ? "Aktuell"
                    : pkg.outdated_count > 0
                      ? "Aktualisieren"
                      : pkg.installed_count > 0
                        ? "Ergänzen"
                        : "Installieren";
                  return (
                    <div
                      key={pkg.id}
                      className="fie-package-card"
                      data-installed={fullyInstalled ? "full" : "partial"}
                    >
                      <span className="fie-package-icon">{pkg.icon}</span>
                      <h3 className="fie-package-name">{pkg.name}</h3>
                      <p className="fie-package-desc">{pkg.description}</p>
                      <span className="fie-package-meta">
                        {pkg.program_count} Programme · {pkg.publisher || "ohne Herausgeber"}
                        {pkg.researched_on ? ` · recherchiert ${pkg.researched_on}` : ""}
                      </span>
                      <button
                        type="button"
                        className="btn-secondary fie-package-btn"
                        onClick={() => openPackage(pkg.id)}
                        disabled={loadPackage.isPending || previewMutation.isPending}
                      >
                        {buttonLabel}
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            {packagePreview && packagePayload && (
              <>
                {renderPreview(packagePreview, packageSelected, setPackageSelected)}
                {packageResult && renderResult(packageResult)}
                <div
                  style={{ display: "flex", gap: "var(--space-3)", marginTop: "var(--space-3)" }}
                >
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={importMutation.isPending || packageSelected.size === 0}
                    onClick={() =>
                      runImport(packagePayload, packagePreview, packageSelected, (result) => {
                        setPackageResult(result);
                        setPackagePreview(null);
                      })
                    }
                  >
                    {packageSelected.size} Programm(e) einspielen
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => {
                      setPackagePreview(null);
                      setPackagePayload(null);
                    }}
                  >
                    Zurück
                  </button>
                </div>
              </>
            )}
          </>
        )}

        {activeTab === "import" && (
          <>
            <div
              className={`fie-dropzone ${isDragging ? "fie-dropzone--active" : ""}`}
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragging(false);
                const file = e.dataTransfer.files[0];
                if (file) readFile(file);
              }}
            >
              <span style={{ fontSize: 32 }}>📁</span>
              <span>Förderpaket (.json) hier ablegen oder klicken</span>
              <span className="metric-hint">
                Erwartet wird eine Datei im Paketformat — dieselbe, die der Export erzeugt.
              </span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".json,application/json"
                style={{ display: "none" }}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) readFile(file);
                  // Sonst löst dieselbe Datei beim zweiten Mal kein change-Ereignis aus.
                  e.target.value = "";
                }}
              />
            </div>

            {fileError && (
              <p className="status-error" role="alert" style={{ marginTop: "var(--space-3)" }}>
                {fileError}
              </p>
            )}

            {filePreview && filePayload && (
              <div style={{ marginTop: "var(--space-4)" }}>
                {renderPreview(filePreview, fileSelected, setFileSelected)}
                <button
                  type="button"
                  className="btn-primary"
                  style={{ marginTop: "var(--space-3)" }}
                  disabled={importMutation.isPending || fileSelected.size === 0}
                  onClick={() =>
                    runImport(filePayload, filePreview, fileSelected, (result) => {
                      setFileResult(result);
                      setFilePreview(null);
                    })
                  }
                >
                  {fileSelected.size} Programm(e) einspielen
                </button>
              </div>
            )}
            {fileResult && renderResult(fileResult)}
          </>
        )}

        {activeTab === "export" && (
          <>
            <div className="form-field">
              <label className="form-label" htmlFor="fie-export-name">
                Paketname
              </label>
              <input
                id="fie-export-name"
                className="form-input"
                value={exportName}
                onChange={(e) => setExportName(e.target.value)}
              />
            </div>
            {ownPrograms.length === 0 ? (
              <p className="metric-hint">
                Es gibt noch keine eigenen Programme. Mitgelieferte Programme lassen sich nicht
                exportieren — sie stehen ohnehin in jeder Installation.
              </p>
            ) : (
              <>
                <div className="fie-export-list">
                  {ownPrograms.map((program) => (
                    <label key={program.id} className="fie-export-item">
                      <input
                        type="checkbox"
                        checked={exportSelected.has(program.id)}
                        onChange={() => {
                          const next = new Set(exportSelected);
                          if (next.has(program.id)) next.delete(program.id);
                          else next.add(program.id);
                          setExportSelected(next);
                        }}
                      />
                      <span>
                        {program.title}
                        <span className="fie-preview-pattern"> · {program.provider_name}</span>
                      </span>
                    </label>
                  ))}
                </div>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={exportMutation.isPending}
                  onClick={() =>
                    exportMutation.mutate(
                      {
                        program_ids: [...exportSelected],
                        package_name: exportName,
                        description: "",
                        include_curated: false,
                      },
                      { onSuccess: downloadPackage },
                    )
                  }
                >
                  {exportSelected.size > 0
                    ? `${exportSelected.size} Programm(e) exportieren`
                    : "Alle eigenen exportieren"}
                </button>
              </>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
