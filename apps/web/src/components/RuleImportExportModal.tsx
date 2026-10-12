import { useState, useRef, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Modal } from "./Modal";
import {
  listRulePackages,
  getRulePackage,
  previewRuleImport,
  importRules,
  exportRules,
} from "../lib/api";
import type {
  ClassificationRule,
  CategoryGroup,
  RulePackageMeta,
  RulePackage,
  RuleImportItemStatus,
  RuleImportPreview,
  RuleImportPreviewItem,
  RuleImportResult,
} from "../lib/types";

type Tab = "packages" | "import" | "export";

// Vorbelegt ist nur, was der Bestand noch nicht kennt. Duplikate, Konflikte
// und Ueberschneidungen bleiben sichtbar und anwaehlbar - die Entscheidung
// gehoert dem Nutzer, die Warnung dem Dialog.
const IMPORT_STATUS_LABELS: Record<RuleImportItemStatus, string> = {
  new: "Neu",
  duplicate: "Duplikat",
  conflict: "Konflikt",
  overlap: "Ähnlich",
};

function describeImportItem(item: RuleImportPreviewItem): string | null {
  switch (item.status) {
    case "duplicate":
      return item.in_package_duplicate
        ? "Steht in diesem Paket schon weiter oben."
        : `Gibt es bereits als „${item.existing_rule_name ?? "vorhandene Regel"}“.`;
    case "conflict":
      return `„${item.existing_rule_name ?? "Eine vorhandene Regel"}“ trifft dieselben Buchungen, schreibt sie aber nach „${
        item.existing_category_name ?? "keine Kategorie"
      }“.`;
    case "overlap":
      return `${item.shared_terms.join(", ")} deckt „${
        item.existing_rule_name ?? "eine vorhandene Regel"
      }“ bereits ab.`;
    default:
      return null;
  }
}

/** Positionen, die der Import auslassen soll (alles, was nicht angehakt ist). */
function deselectedIndices(preview: RuleImportPreview, selected: Set<number>): number[] {
  return preview.items.filter((item) => !selected.has(item.index)).map((item) => item.index);
}

interface RuleImportExportModalProps {
  onClose: () => void;
  token: string;
  householdId: string;
  rules: ClassificationRule[];
  categoryGroups: CategoryGroup[];
}

export function RuleImportExportModal({
  onClose,
  token,
  householdId,
  rules,
  categoryGroups,
}: RuleImportExportModalProps) {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<Tab>("packages");

  // ---------- Packages tab state ----------
  const [selectedPackageId, setSelectedPackageId] = useState<string | null>(null);
  const [preview, setPreview] = useState<RuleImportPreview | null>(null);
  const [importResult, setImportResult] = useState<RuleImportResult | null>(null);
  const [categoryOverrides, setCategoryOverrides] = useState<Record<string, string>>({});
  // Angehakte Positionen der Vorschau. Beide Tabs fuehren eine eigene Auswahl,
  // sonst uebernaehme ein Dateiimport die Haken des zuletzt geoeffneten Pakets.
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set());

  // ---------- File import tab state ----------
  const [filePackage, setFilePackage] = useState<RulePackage | null>(null);
  const [filePreview, setFilePreview] = useState<RuleImportPreview | null>(null);
  const [fileImportResult, setFileImportResult] = useState<RuleImportResult | null>(null);
  const [fileCategoryOverrides, setFileCategoryOverrides] = useState<Record<string, string>>({});
  const [fileSelectedIndices, setFileSelectedIndices] = useState<Set<number>>(new Set());
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ---------- Export tab state ----------
  const [selectedRuleIds, setSelectedRuleIds] = useState<Set<string>>(new Set());
  const [exportName, setExportName] = useState("Meine Regeln");
  const [exportDescription, setExportDescription] = useState("");

  // Build flat category list for dropdowns
  const allCategories = categoryGroups.flatMap((g) =>
    g.categories
      .filter((c) => !c.is_archived)
      .map((c) => ({ id: c.id, name: c.name, groupName: g.name })),
  );

  // ---------- Queries & Mutations ----------

  const { data: packages, isLoading: packagesLoading } = useQuery<RulePackageMeta[]>({
    queryKey: ["rulePackages", householdId],
    queryFn: () => listRulePackages(token, householdId),
    enabled: activeTab === "packages",
  });

  const loadPackageMutation = useMutation({
    mutationFn: (packageId: string) => getRulePackage(token, householdId, packageId),
    onSuccess: (pkg) => {
      // After loading package, immediately preview it
      previewMutation.mutate({
        package: pkg,
        category_overrides: {},
        skip_duplicates: true,
      });
    },
  });

  const previewMutation = useMutation({
    mutationFn: (input: {
      package: RulePackage;
      category_overrides: Record<string, string>;
      skip_duplicates: boolean;
    }) => previewRuleImport(token, householdId, input),
    onSuccess: (result) => {
      // Vorbelegung: alles Unbekannte an, alles Bekannte aus.
      const recommended = new Set(
        result.items.filter((item) => item.recommended).map((item) => item.index),
      );
      if (activeTab === "packages") {
        setPreview(result);
        setSelectedIndices(recommended);
      } else {
        setFilePreview(result);
        setFileSelectedIndices(recommended);
      }
    },
  });

  const importMutation = useMutation({
    mutationFn: (input: {
      package: RulePackage;
      category_overrides: Record<string, string>;
      skip_duplicates: boolean;
      skip_indices: number[];
    }) => importRules(token, householdId, input),
    onSuccess: (result) => {
      if (activeTab === "packages") {
        setImportResult(result);
      } else {
        setFileImportResult(result);
      }
      void queryClient.invalidateQueries({ queryKey: ["rules", householdId] });
      // Die Paketkacheln zeigen, wie viele Regeln schon installiert sind -
      // ohne diese Entwertung bliebe die Zahl bis zum Neuladen stehen.
      void queryClient.invalidateQueries({ queryKey: ["rulePackages", householdId] });
    },
  });

  const exportMutation = useMutation({
    mutationFn: () =>
      exportRules(token, householdId, {
        rule_ids: [...selectedRuleIds],
        package_name: exportName,
        description: exportDescription,
      }),
    onSuccess: (pkg) => {
      const json = JSON.stringify(pkg, null, 2);
      const blob = new Blob([json], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${exportName.replace(/\s+/g, "_").toLowerCase()}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    },
  });

  // ---------- Handlers ----------

  const handleInstallPackage = (packageId: string) => {
    setSelectedPackageId(packageId);
    setPreview(null);
    setImportResult(null);
    setCategoryOverrides({});
    setSelectedIndices(new Set());
    loadPackageMutation.mutate(packageId);
  };

  const handleExecuteImport = () => {
    const isPackages = activeTab === "packages";
    const pkg = isPackages ? loadPackageMutation.data : filePackage;
    const overrides = isPackages ? categoryOverrides : fileCategoryOverrides;
    const currentPreview = isPackages ? preview : filePreview;
    const selection = isPackages ? selectedIndices : fileSelectedIndices;
    if (!pkg || !currentPreview) return;
    importMutation.mutate({
      package: pkg,
      category_overrides: overrides,
      skip_duplicates: true,
      skip_indices: deselectedIndices(currentPreview, selection),
    });
  };

  const handleBackToPackages = () => {
    setSelectedPackageId(null);
    setPreview(null);
    setImportResult(null);
    setCategoryOverrides({});
    setSelectedIndices(new Set());
    loadPackageMutation.reset();
    importMutation.reset();
  };

  const handleBackToFileUpload = () => {
    setFilePackage(null);
    setFilePreview(null);
    setFileImportResult(null);
    setFileCategoryOverrides({});
    setFileSelectedIndices(new Set());
    importMutation.reset();
  };

  const toggleImportSelection = (index: number) => {
    const setSelection = activeTab === "packages" ? setSelectedIndices : setFileSelectedIndices;
    setSelection((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  const handleFileSelected = useCallback(
    (file: File) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        try {
          const data = JSON.parse(e.target?.result as string) as RulePackage;
          if (!data.rules || !Array.isArray(data.rules)) {
            alert("Ungültiges Dateiformat: Die Datei enthält keine gültigen Regeln.");
            return;
          }
          // Set defaults if missing
          data.format_version = data.format_version || "1.0";
          data.name = data.name || file.name.replace(".json", "");
          data.description = data.description || "";
          data.author = data.author || "";

          setFilePackage(data);
          setFileCategoryOverrides({});
          previewMutation.mutate({
            package: data,
            category_overrides: {},
            skip_duplicates: true,
          });
        } catch {
          alert("Die Datei konnte nicht als JSON gelesen werden.");
        }
      };
      reader.readAsText(file);
    },
    [previewMutation],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragActive(false);
      const file = e.dataTransfer.files?.[0];
      if (file?.name.endsWith(".json")) {
        handleFileSelected(file);
      }
    },
    [handleFileSelected],
  );

  const handleCategoryOverrideChange = (key: string, categoryId: string) => {
    if (activeTab === "packages") {
      setCategoryOverrides((prev) => ({ ...prev, [key]: categoryId }));
    } else {
      setFileCategoryOverrides((prev) => ({ ...prev, [key]: categoryId }));
    }
  };

  const toggleRuleForExport = (ruleId: string) => {
    setSelectedRuleIds((prev) => {
      const next = new Set(prev);
      if (next.has(ruleId)) {
        next.delete(ruleId);
      } else {
        next.add(ruleId);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedRuleIds.size === rules.length) {
      setSelectedRuleIds(new Set());
    } else {
      setSelectedRuleIds(new Set(rules.map((r) => r.id)));
    }
  };

  // ---------- Preview/Result Sub-components ----------

  const renderPreview = (
    currentPreview: RuleImportPreview,
    overrides: Record<string, string>,
    selection: Set<number>,
  ) => {
    const knownCount =
      currentPreview.duplicates + currentPreview.conflicts + currentPreview.overlaps;
    return (
      <div>
        <div className="rie-stats">
          <div className="rie-stat">
            <strong>{currentPreview.total_rules}</strong>
            <span>Regeln</span>
          </div>
          <div className="rie-stat">
            <strong>{currentPreview.recommended}</strong>
            <span>Neu</span>
          </div>
          <div className="rie-stat">
            <strong>{currentPreview.duplicates}</strong>
            <span>Duplikate</span>
          </div>
          <div className="rie-stat">
            <strong>{currentPreview.conflicts}</strong>
            <span>Konflikte</span>
          </div>
          <div className="rie-stat">
            <strong>{currentPreview.overlaps}</strong>
            <span>Ähnlich</span>
          </div>
          <div className="rie-stat">
            <strong>{currentPreview.unmapped}</strong>
            <span>Offene Kategorie</span>
          </div>
        </div>

        {knownCount > 0 && (
          <p className="rie-preview-hint" role="status">
            {knownCount} von {currentPreview.total_rules} Regeln kennt dieser Haushalt schon — sie
            sind abgewählt, damit der Regelbestand nicht doppelt wächst.
          </p>
        )}

        <div className="rie-preview-list">
          {currentPreview.items.map((item) => {
            const explanation = describeImportItem(item);
            const checked = selection.has(item.index);
            return (
              <label
                key={item.index}
                className="rie-preview-item"
                data-status={item.status}
                data-selected={checked ? "true" : "false"}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleImportSelection(item.index)}
                  aria-label={`${item.rule_name} importieren`}
                />
                <div className="rie-preview-main">
                  <div>
                    <strong>{item.rule_name}</strong>
                    {item.resolved_category_name && (
                      <span className="placeholder-note" style={{ marginLeft: 8 }}>
                        → {item.resolved_category_name}
                      </span>
                    )}
                  </div>
                  {item.match_payee_raw && (
                    <code className="rie-preview-pattern">{item.match_payee_raw}</code>
                  )}
                  {explanation && <span className="rie-preview-reason">{explanation}</span>}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="rie-preview-status" data-status={item.status}>
                    {IMPORT_STATUS_LABELS[item.status]}
                  </span>
                  {item.category_key && item.resolved_category_id && (
                    <span className="rie-preview-status" data-status="mapped">
                      ✓ Zugeordnet
                    </span>
                  )}
                  {item.category_key && !item.resolved_category_id && (
                    <>
                      <span className="rie-preview-status" data-status="unmapped">
                        ⚠ Offen
                      </span>
                      <select
                        className="rie-category-select"
                        value={overrides[item.category_key] || ""}
                        onChange={(e) =>
                          handleCategoryOverrideChange(item.category_key!, e.target.value)
                        }
                      >
                        <option value="">Kategorie wählen...</option>
                        {allCategories.map((cat) => (
                          <option key={cat.id} value={cat.id}>
                            {cat.groupName} → {cat.name}
                          </option>
                        ))}
                      </select>
                    </>
                  )}
                  {!item.category_key && (
                    <span className="rie-preview-status" data-status="unmapped">
                      Ohne Kategorie
                    </span>
                  )}
                </div>
              </label>
            );
          })}
        </div>

        <div
          style={{
            display: "flex",
            gap: "var(--space-3)",
            justifyContent: "flex-end",
            marginTop: "var(--space-4)",
          }}
        >
          <button
            type="button"
            className="btn-outline"
            onClick={activeTab === "packages" ? handleBackToPackages : handleBackToFileUpload}
          >
            Zurück
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleExecuteImport}
            disabled={importMutation.isPending || selection.size === 0}
          >
            {importMutation.isPending ? "Importiere..." : `${selection.size} Regeln importieren`}
          </button>
        </div>
      </div>
    );
  };

  const renderResult = (result: RuleImportResult, onBack: () => void) => (
    <div className="rie-result">
      <h3 style={{ margin: 0, fontSize: 18 }}>✅ Import abgeschlossen</h3>
      <div className="rie-stats">
        <div className="rie-stat">
          <strong style={{ color: "var(--color-positive)" }}>{result.created}</strong>
          <span>Erstellt</span>
        </div>
        <div className="rie-stat">
          <strong>{result.skipped_duplicates}</strong>
          <span>Duplikate übersprungen</span>
        </div>
        <div className="rie-stat">
          <strong>{result.skipped_deselected}</strong>
          <span>Abgewählt</span>
        </div>
        {result.errors.length > 0 && (
          <div className="rie-stat">
            <strong style={{ color: "var(--color-warning, #dc2626)" }}>
              {result.errors.length}
            </strong>
            <span>Fehler</span>
          </div>
        )}
      </div>
      {result.errors.length > 0 && (
        <div
          className="card status-error"
          style={{ textAlign: "left", marginTop: "var(--space-3)" }}
        >
          {result.errors.map((err, i) => (
            <p key={i} style={{ margin: "2px 0", fontSize: 13 }}>
              {err}
            </p>
          ))}
        </div>
      )}
      <div
        style={{
          display: "flex",
          gap: "var(--space-3)",
          justifyContent: "center",
          marginTop: "var(--space-4)",
        }}
      >
        <button type="button" className="btn-outline" onClick={onBack}>
          Weitere importieren
        </button>
        <button type="button" className="btn-primary" onClick={onClose}>
          Schließen
        </button>
      </div>
    </div>
  );

  // ---------- Tab Renderers ----------

  const renderPackagesTab = () => {
    // Show result after import
    if (importResult) {
      return renderResult(importResult, handleBackToPackages);
    }

    // Show preview of selected package
    if (selectedPackageId && preview) {
      return (
        <div>
          <h3 style={{ margin: "0 0 var(--space-3) 0", fontSize: 16 }}>
            Vorschau: {preview.package_name}
          </h3>
          {renderPreview(preview, categoryOverrides, selectedIndices)}
        </div>
      );
    }

    // Show loading state while fetching package
    if (selectedPackageId && (loadPackageMutation.isPending || previewMutation.isPending)) {
      return <p className="placeholder-note">Lade Paket-Vorschau...</p>;
    }

    // Show error
    if (loadPackageMutation.isError) {
      return (
        <div>
          <div className="card status-error" style={{ marginBottom: "var(--space-4)" }}>
            Fehler beim Laden des Pakets.
          </div>
          <button type="button" className="btn-outline" onClick={handleBackToPackages}>
            Zurück
          </button>
        </div>
      );
    }

    // Show package grid
    if (packagesLoading) {
      return <p className="placeholder-note">Lade verfügbare Pakete...</p>;
    }

    return (
      <div className="rie-packages-grid">
        {packages?.map((pkg) => {
          // Ohne diese Anzeige sieht man erst nach dem Oeffnen, dass ein Paket
          // schon installiert ist - und klickt es beim naechsten Mal erneut an.
          const fully = pkg.installed_count >= pkg.rule_count;
          const partly = pkg.installed_count > 0 && !fully;
          return (
            <div
              key={pkg.id}
              className="rie-package-card"
              data-installed={fully ? "full" : partly ? "partial" : "none"}
            >
              <span className="rie-package-icon">{pkg.icon}</span>
              <h3 className="rie-package-name">{pkg.name}</h3>
              <p className="rie-package-desc">{pkg.description}</p>
              <span className="rie-package-meta">
                {pkg.rule_count} Regeln
                {fully && " · bereits installiert"}
                {partly && ` · ${pkg.installed_count} davon installiert`}
              </span>
              <button
                type="button"
                className={fully ? "btn-outline rie-package-btn" : "btn-primary rie-package-btn"}
                onClick={() => handleInstallPackage(pkg.id)}
              >
                {fully ? "Prüfen" : partly ? "Ergänzen" : "Installieren"}
              </button>
            </div>
          );
        })}
      </div>
    );
  };

  const renderImportTab = () => {
    // Show result after import
    if (fileImportResult) {
      return renderResult(fileImportResult, handleBackToFileUpload);
    }

    // Show preview of uploaded file
    if (filePackage && filePreview) {
      return (
        <div>
          <h3 style={{ margin: "0 0 var(--space-3) 0", fontSize: 16 }}>
            Vorschau: {filePreview.package_name}
          </h3>
          {renderPreview(filePreview, fileCategoryOverrides, fileSelectedIndices)}
        </div>
      );
    }

    // Loading preview
    if (filePackage && previewMutation.isPending) {
      return <p className="placeholder-note">Analysiere Datei...</p>;
    }

    // File upload zone
    return (
      <div>
        <div
          className={`rie-dropzone${dragActive ? " rie-dropzone--active" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <span style={{ fontSize: 40 }}>📁</span>
          <strong>JSON-Datei hierher ziehen</strong>
          <span className="placeholder-note">oder klicken zum Auswählen</span>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json"
            style={{ display: "none" }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleFileSelected(file);
            }}
          />
        </div>
        <p className="placeholder-note" style={{ marginTop: "var(--space-3)" }}>
          Unterstütztes Format: JSON-Dateien mit Regelpaket-Struktur (z.B. exportierte Pakete aus
          dieser App oder vorgefertigte Pakete).
        </p>
      </div>
    );
  };

  const renderExportTab = () => {
    if (!rules.length) {
      return (
        <div style={{ textAlign: "center", padding: "var(--space-6)" }}>
          <p className="placeholder-note">Keine Regeln zum Exportieren vorhanden.</p>
        </div>
      );
    }

    // Build a category map for displaying names
    const categoryMap = new Map<string, string>();
    categoryGroups.forEach((g) => {
      g.categories.forEach((c) => categoryMap.set(c.id, c.name));
    });

    return (
      <div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "var(--space-3)",
          }}
        >
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={selectedRuleIds.size === rules.length}
              onChange={toggleSelectAll}
            />
            <strong>
              {selectedRuleIds.size} von {rules.length} ausgewählt
            </strong>
          </label>
        </div>

        <div className="rie-export-list">
          {rules.map((rule) => (
            <label key={rule.id} className="rie-export-item" style={{ cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={selectedRuleIds.has(rule.id)}
                onChange={() => toggleRuleForExport(rule.id)}
              />
              <div style={{ flex: 1 }}>
                <strong>{rule.name}</strong>
                {rule.action_category_id && (
                  <span className="placeholder-note" style={{ marginLeft: 8 }}>
                    → {categoryMap.get(rule.action_category_id) || "?"}
                  </span>
                )}
              </div>
              {rule.match_payee_raw && (
                <code style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>
                  {rule.match_payee_raw}
                </code>
              )}
            </label>
          ))}
        </div>

        <div style={{ display: "flex", gap: "var(--space-3)", marginBottom: "var(--space-4)" }}>
          <label style={{ flex: 1 }}>
            <span className="placeholder-note">Paketname</span>
            <input
              type="text"
              className="form-control"
              value={exportName}
              onChange={(e) => setExportName(e.target.value)}
              placeholder="Meine Regeln"
            />
          </label>
          <label style={{ flex: 1 }}>
            <span className="placeholder-note">Beschreibung</span>
            <input
              type="text"
              className="form-control"
              value={exportDescription}
              onChange={(e) => setExportDescription(e.target.value)}
              placeholder="Optional"
            />
          </label>
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button
            type="button"
            className="btn-primary"
            onClick={() => exportMutation.mutate()}
            disabled={selectedRuleIds.size === 0 || exportMutation.isPending}
          >
            {exportMutation.isPending
              ? "Exportiere..."
              : `${selectedRuleIds.size} Regeln exportieren`}
          </button>
        </div>
      </div>
    );
  };

  // ---------- Render ----------

  return (
    <Modal title="Regeln importieren & exportieren" onClose={onClose} size="wide">
      <div className="rie-tabs">
        <button
          type="button"
          className={`rie-tab${activeTab === "packages" ? " rie-tab--active" : ""}`}
          onClick={() => setActiveTab("packages")}
        >
          📦 Pakete
        </button>
        <button
          type="button"
          className={`rie-tab${activeTab === "import" ? " rie-tab--active" : ""}`}
          onClick={() => setActiveTab("import")}
        >
          📁 Importieren
        </button>
        <button
          type="button"
          className={`rie-tab${activeTab === "export" ? " rie-tab--active" : ""}`}
          onClick={() => setActiveTab("export")}
        >
          📤 Exportieren
        </button>
      </div>

      <div className="rie-tab-content">
        {activeTab === "packages" && renderPackagesTab()}
        {activeTab === "import" && renderImportTab()}
        {activeTab === "export" && renderExportTab()}
      </div>
    </Modal>
  );
}
