import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  applyAllRulesToExisting,
  applyRuleToExisting,
  createClassificationRule,
  deleteClassificationRule,
  listCategories,
  listClassificationRules,
  updateClassificationRule,
} from "../lib/api";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";
import type {
  ClassificationRule,
  ClassificationRuleUpdateInput,
  RuleApplyAllResult,
  RuleApplyItem,
  RuleApplyResult,
} from "../lib/types";
import { Modal } from "../components/Modal";
import { CategoryPicker } from "../components/CategoryPicker";
import { RuleImportExportModal } from "../components/RuleImportExportModal";

/** Minor Units als reinen Eingabewert ("1234,50") ohne Waehrungszeichen. */
function minorToInputValue(minor: number | null | undefined): string {
  if (minor == null) return "";
  return (minor / 100).toFixed(2).replace(".", ",");
}

// Klartext statt Statuscode - die Gruende kommen so aus dem Backend
// (services/api/app/services/rules_service.py, apply_rule_to_existing).
const APPLY_REASON_LABELS: Record<string, string> = {
  already_categorized: "Hat bereits eine Kategorie",
  already_assigned: "Nichts zu ändern",
  split_transaction: "Aufgeteilte Buchung — bitte einzeln bearbeiten",
  category_inactive: "Zielkategorie zwischenzeitlich archiviert",
  version_conflict: "Buchung wurde zwischenzeitlich geändert",
};

const APPLY_STATUS_LABELS: Record<RuleApplyItem["status"], string> = {
  applied: "Wird geändert",
  unchanged: "Unverändert",
  skipped: "Übersprungen",
  conflict: "Konflikt",
};

// Warum eine Regel im Sammellauf gar nicht erst mitgelaufen ist - die Gruende
// kommen so aus dem Backend (routes/rules.py, _prepare_rules_for_bulk_run).
const APPLY_EXCLUDED_LABELS: Record<string, string> = {
  inactive: "Inaktiv",
  no_effect: "Setzt weder Kategorie noch Transfer",
  category_inactive: "Zielkategorie archiviert",
};

/** Nimmt Einzel- und Sammellauf, beide liefern dieselben Zähler. */
function describeApplyResult(result: {
  dry_run: boolean;
  matched: number;
  applied: number;
  unchanged: number;
  skipped: number;
  conflict: number;
}): string {
  const parts = [`${result.matched} Treffer`];
  if (result.applied > 0) {
    parts.push(result.dry_run ? `${result.applied} zu ändern` : `${result.applied} geändert`);
  }
  if (result.unchanged > 0) parts.push(`${result.unchanged} unverändert`);
  if (result.skipped > 0) parts.push(`${result.skipped} übersprungen`);
  if (result.conflict > 0) parts.push(`${result.conflict} Konflikt`);
  return parts.join(", ");
}

export function RulesPage() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  const [isModalOpen, setIsModalOpen] = useState(false);
  // Gesetzt, solange eine bestehende Regel bearbeitet wird; null heisst
  // "neue Regel anlegen". Beide Faelle teilen sich dasselbe Formular.
  const [editingRule, setEditingRule] = useState<ClassificationRule | null>(null);
  const [name, setName] = useState<string>("");
  const [priority, setPriority] = useState<number>(10);
  const [isActive, setIsActive] = useState<boolean>(true);
  const [matchPayeeRaw, setMatchPayeeRaw] = useState<string>("");
  const [matchPayeeExactWord, setMatchPayeeExactWord] = useState<boolean>(false);
  const [matchPurpose, setMatchPurpose] = useState<string>("");
  const [matchPurposeExactWord, setMatchPurposeExactWord] = useState<boolean>(false);
  const [excludePayeeRaw, setExcludePayeeRaw] = useState<string>("");
  const [excludePurpose, setExcludePurpose] = useState<string>("");
  const [actionCategoryId, setActionCategoryId] = useState<string>("");
  const [actionSetTransfer, setActionSetTransfer] = useState<boolean>(false);
  const [actionNote, setActionNote] = useState<string>("");
  const [minAmountEuro, setMinAmountEuro] = useState<string>("");
  const [maxAmountEuro, setMaxAmountEuro] = useState<string>("");
  const [matchAmountMode, setMatchAmountMode] = useState<"inside" | "outside">("inside");
  const [showAdvancedOptions, setShowAdvancedOptions] = useState<boolean>(false);
  const [showRegexHelp, setShowRegexHelp] = useState<boolean>(false);

  // Rueckwirkender Lauf: gesetzt, solange der Dialog fuer genau eine Regel
  // offen ist. Das Ergebnis der letzten Vorschau bzw. Anwendung haengt daran.
  const [applyRule, setApplyRule] = useState<ClassificationRule | null>(null);
  const [applyScope, setApplyScope] = useState<"uncategorized" | "all">("uncategorized");
  const [applyFrom, setApplyFrom] = useState<string>("");
  const [applyTo, setApplyTo] = useState<string>("");
  const [applyResult, setApplyResult] = useState<RuleApplyResult | null>(null);
  const [applyDone, setApplyDone] = useState<boolean>(false);
  const [showImportExport, setShowImportExport] = useState(false);

  // Sammellauf ueber alle Regeln. Eigener Zustand statt einer Erweiterung des
  // Einzellaufs: die Ergebnisstruktur ist eine andere (Zaehler je Regel), und
  // beide Dialoge duerfen sich nicht gegenseitig die Vorschau entwerten.
  const [showApplyAll, setShowApplyAll] = useState(false);
  const [applyAllScope, setApplyAllScope] = useState<"uncategorized" | "all">("uncategorized");
  const [applyAllFrom, setApplyAllFrom] = useState<string>("");
  const [applyAllResult, setApplyAllResult] = useState<RuleApplyAllResult | null>(null);
  const [applyAllDone, setApplyAllDone] = useState<boolean>(false);

  const {
    data: rules,
    isLoading,
    error,
  } = useQuery<ClassificationRule[]>({
    queryKey: ["rules", householdId],
    queryFn: () => listClassificationRules(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const { data: categoryGroups } = useQuery({
    queryKey: ["categories", householdId],
    queryFn: () => listCategories(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const categoryMap = new Map<string, string>();
  categoryGroups?.forEach((g) => {
    g.categories.forEach((c) => categoryMap.set(c.id, c.name));
  });

  const closeModal = () => {
    setIsModalOpen(false);
    setEditingRule(null);
    resetForm();
  };

  const createMutation = useMutation({
    mutationFn: (input: Parameters<typeof createClassificationRule>[2]) =>
      createClassificationRule(token!, householdId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["rules", householdId] });
      closeModal();
    },
  });

  // PATCH /v1/rules/{id}: nur mitgeschickte Felder werden geschrieben. Das
  // Formular schickt bewusst alle Felder, damit ein geleertes Muster auch
  // wirklich auf null zurueckgesetzt wird.
  const updateMutation = useMutation({
    mutationFn: (vars: { ruleId: string; input: ClassificationRuleUpdateInput }) =>
      updateClassificationRule(token!, householdId, vars.ruleId, vars.input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["rules", householdId] });
      closeModal();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (ruleId: string) => deleteClassificationRule(token!, householdId, ruleId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["rules", householdId] });
    },
  });

  // Vorschau und Anwendung teilen sich eine Mutation - sie unterscheiden sich
  // nur im dryRun-Flag und liefern dieselbe Ergebnisstruktur.
  const applyMutation = useMutation({
    mutationFn: (vars: { ruleId: string; dryRun: boolean }) =>
      applyRuleToExisting(
        token!,
        householdId,
        vars.ruleId,
        {
          scope: applyScope,
          from_date: applyFrom || null,
          to_date: applyTo || null,
        },
        { dryRun: vars.dryRun },
      ),
    onSuccess: (result, vars) => {
      setApplyResult(result);
      setApplyDone(!vars.dryRun);
      if (!vars.dryRun) {
        // Ein Lauf aendert Kategorien, Transferkennzeichen und damit jede
        // abgeleitete Auswertung - Buchungen, Budget und Review-Inbox muessen
        // neu geladen werden, sonst zeigt die Oberflaeche veraltete Summen.
        void queryClient.invalidateQueries({ queryKey: ["transactions"] });
        void queryClient.invalidateQueries({ queryKey: ["budget"] });
        void queryClient.invalidateQueries({ queryKey: ["review"] });
      }
    },
  });

  const applyAllMutation = useMutation({
    mutationFn: (vars: { dryRun: boolean }) =>
      applyAllRulesToExisting(
        token!,
        householdId,
        {
          scope: applyAllScope,
          from_date: applyAllFrom || null,
        },
        { dryRun: vars.dryRun },
      ),
    onSuccess: (result, vars) => {
      setApplyAllResult(result);
      setApplyAllDone(!vars.dryRun);
      if (!vars.dryRun) {
        void queryClient.invalidateQueries({ queryKey: ["transactions"] });
        void queryClient.invalidateQueries({ queryKey: ["budget"] });
        void queryClient.invalidateQueries({ queryKey: ["review"] });
      }
    },
  });

  const openApplyAllDialog = () => {
    setShowApplyAll(true);
    setApplyAllScope("uncategorized");
    setApplyAllFrom("");
    setApplyAllResult(null);
    setApplyAllDone(false);
    applyAllMutation.reset();
  };

  const closeApplyAllDialog = () => {
    setShowApplyAll(false);
    setApplyAllResult(null);
    setApplyAllDone(false);
    applyAllMutation.reset();
  };

  const openApplyDialog = (rule: ClassificationRule) => {
    setApplyRule(rule);
    setApplyScope("uncategorized");
    setApplyFrom("");
    setApplyTo("");
    setApplyResult(null);
    setApplyDone(false);
    applyMutation.reset();
  };

  const closeApplyDialog = () => {
    setApplyRule(null);
    setApplyResult(null);
    setApplyDone(false);
    applyMutation.reset();
  };

  // Jede Änderung am Umfang entwertet eine vorhandene Vorschau - sonst stünde
  // über der Trefferliste ein Zeitraum, nach dem gar nicht gesucht wurde.
  const resetPreview = () => {
    setApplyResult(null);
    setApplyDone(false);
  };

  const resetForm = () => {
    setName("");
    setPriority(10);
    setIsActive(true);
    setMatchPayeeRaw("");
    setMatchPayeeExactWord(false);
    setMatchPurpose("");
    setMatchPurposeExactWord(false);
    setExcludePayeeRaw("");
    setExcludePurpose("");
    setActionCategoryId("");
    setActionSetTransfer(false);
    setActionNote("");
    setMinAmountEuro("");
    setMaxAmountEuro("");
    setMatchAmountMode("inside");
    setShowAdvancedOptions(false);
    setShowRegexHelp(false);
  };

  const handleOpenCreate = () => {
    setEditingRule(null);
    resetForm();
    setIsModalOpen(true);
  };

  const handleOpenEdit = (rule: ClassificationRule) => {
    setEditingRule(rule);
    setName(rule.name);
    setPriority(rule.priority);
    setIsActive(rule.is_active);
    setMatchPayeeRaw(rule.match_payee_raw ?? "");
    setMatchPayeeExactWord(Boolean(rule.match_payee_exact_word));
    setMatchPurpose(rule.match_purpose ?? "");
    setMatchPurposeExactWord(Boolean(rule.match_purpose_exact_word));
    setExcludePayeeRaw(rule.exclude_payee_raw ?? "");
    setExcludePurpose(rule.exclude_purpose ?? "");
    setActionCategoryId(rule.action_category_id ?? "");
    setActionSetTransfer(rule.action_set_transfer);
    setActionNote(rule.action_note ?? "");
    setMinAmountEuro(minorToInputValue(rule.match_min_amount_minor));
    setMaxAmountEuro(minorToInputValue(rule.match_max_amount_minor));
    setMatchAmountMode(rule.match_amount_mode === "outside" ? "outside" : "inside");

    // Automatisch aufklappen, falls die Regel bereits erweiterte Optionen nutzt
    const hasAdvanced =
      Boolean(rule.match_payee_exact_word) ||
      Boolean(rule.match_purpose_exact_word) ||
      Boolean(rule.exclude_payee_raw) ||
      Boolean(rule.exclude_purpose) ||
      rule.match_amount_mode === "outside" ||
      rule.match_min_amount_minor != null ||
      rule.match_max_amount_minor != null;
    setShowAdvancedOptions(hasAdvanced);
    setIsModalOpen(true);
  };

  const handleSubmitRule = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    const minMinor = minAmountEuro ? parseAmountToMinorUnits(minAmountEuro) : null;
    const maxMinor = maxAmountEuro ? parseAmountToMinorUnits(maxAmountEuro) : null;

    if (editingRule) {
      updateMutation.mutate({
        ruleId: editingRule.id,
        input: {
          name: name.trim(),
          priority,
          is_active: isActive,
          match_payee_raw: matchPayeeRaw.trim() || null,
          match_payee_exact_word: matchPayeeExactWord,
          match_purpose: matchPurpose.trim() || null,
          match_purpose_exact_word: matchPurposeExactWord,
          exclude_payee_raw: excludePayeeRaw.trim() || null,
          exclude_purpose: excludePurpose.trim() || null,
          match_min_amount_minor: minMinor,
          match_max_amount_minor: maxMinor,
          match_amount_mode: matchAmountMode,
          action_category_id: actionCategoryId || null,
          action_set_transfer: actionSetTransfer,
          action_note: actionNote.trim() || null,
        },
      });
      return;
    }

    createMutation.mutate({
      name: name.trim(),
      priority,
      is_active: isActive,
      match_payee_raw: matchPayeeRaw.trim() || undefined,
      match_payee_exact_word: matchPayeeExactWord,
      match_purpose: matchPurpose.trim() || undefined,
      match_purpose_exact_word: matchPurposeExactWord,
      exclude_payee_raw: excludePayeeRaw.trim() || undefined,
      exclude_purpose: excludePurpose.trim() || undefined,
      match_min_amount_minor: minMinor ?? undefined,
      match_max_amount_minor: maxMinor ?? undefined,
      match_amount_mode: matchAmountMode,
      action_category_id: actionCategoryId || undefined,
      action_set_transfer: actionSetTransfer,
      action_note: actionNote.trim() || undefined,
    });
  };

  const saveError = editingRule ? updateMutation.error : createMutation.error;
  const isSaving = createMutation.isPending || updateMutation.isPending;

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Klassifikationsregeln</h1>
          <p className="placeholder-note">
            Automatische Regel-Engine zur Zuweisung von Kategorien für eingehende Buchungen.
          </p>
        </div>
        <div style={{ display: "flex", gap: "var(--space-3)" }}>
          <button
            type="button"
            className="btn-outline"
            onClick={openApplyAllDialog}
            disabled={!rules || rules.length === 0}
            title={
              !rules || rules.length === 0
                ? "Es gibt noch keine Regeln, die angewendet werden könnten."
                : undefined
            }
          >
            ▶ Alle Regeln auf Bestand anwenden
          </button>
          <button type="button" className="btn-outline" onClick={() => setShowImportExport(true)}>
            📦 Import / Export
          </button>
          <button type="button" className="btn-primary" onClick={handleOpenCreate}>
            + Neue Regel
          </button>
        </div>
      </div>

      {isLoading && <p className="placeholder-note">Lade Regeln...</p>}

      {error && (
        <div className="card status-error" style={{ marginBottom: "var(--space-4)" }}>
          Fehler beim Laden der Klassifikationsregeln.
        </div>
      )}

      {deleteMutation.error && (
        <div className="card status-error" style={{ marginBottom: "var(--space-4)" }} role="alert">
          {deleteMutation.error instanceof Error
            ? deleteMutation.error.message
            : "Die Regel konnte nicht gelöscht werden."}
        </div>
      )}

      {!isLoading && !error && (!rules || rules.length === 0) && (
        <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
          <h2 className="card-heading">Keine Regeln vorhanden</h2>
          <p className="card-body">
            Erstellen Sie Ihre erste Regel, um Buchungen automatisch zu kategorisieren.
          </p>
        </div>
      )}

      {!isLoading && rules && rules.length > 0 && (
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th">Prio</th>
                <th className="txn-th">Name</th>
                <th className="txn-th">Empfänger-Muster</th>
                <th className="txn-th">Verwendungszweck-Muster</th>
                <th className="txn-th">Betragsgrenzen</th>
                <th className="txn-th">Zielkategorie</th>
                <th className="txn-th">Quelle</th>
                <th className="txn-th" style={{ textAlign: "right" }}>
                  Aktionen
                </th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id} className="txn-tr">
                  <td
                    className="txn-td"
                    style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}
                  >
                    {rule.priority}
                  </td>
                  <td className="txn-td" style={{ fontWeight: 500 }}>
                    {rule.name}
                    {!rule.is_active && (
                      <span
                        className="status-badge status-badge--neutral"
                        style={{ marginLeft: 8 }}
                      >
                        Inaktiv
                      </span>
                    )}
                  </td>
                  <td className="txn-td">
                    <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                      <div>
                        <span className="txn-payee">{rule.match_payee_raw || "—"}</span>
                        {rule.match_payee_exact_word && (
                          <span
                            className="status-badge status-badge--neutral"
                            style={{ marginLeft: 6, fontSize: "10px" }}
                          >
                            Exaktes Wort
                          </span>
                        )}
                      </div>
                      {rule.exclude_payee_raw && (
                        <span
                          className="status-badge status-badge--warning"
                          style={{ fontSize: "10px", alignSelf: "flex-start" }}
                        >
                          NICHT: {rule.exclude_payee_raw}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="txn-td">
                    <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                      <div>
                        <span className="txn-note">{rule.match_purpose || "—"}</span>
                        {rule.match_purpose_exact_word && (
                          <span
                            className="status-badge status-badge--neutral"
                            style={{ marginLeft: 6, fontSize: "10px" }}
                          >
                            Exaktes Wort
                          </span>
                        )}
                      </div>
                      {rule.exclude_purpose && (
                        <span
                          className="status-badge status-badge--warning"
                          style={{ fontSize: "10px", alignSelf: "flex-start" }}
                        >
                          NICHT: {rule.exclude_purpose}
                        </span>
                      )}
                    </div>
                  </td>
                  <td
                    className="txn-td"
                    style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}
                  >
                    {rule.match_min_amount_minor != null || rule.match_max_amount_minor != null ? (
                      <>
                        {rule.match_amount_mode === "outside" && (
                          <span
                            style={{
                              color: "var(--color-warning, #f59e0b)",
                              fontWeight: 600,
                              display: "block",
                              fontSize: "11px",
                            }}
                          >
                            NICHT zwischen:
                          </span>
                        )}
                        {rule.match_min_amount_minor != null
                          ? formatMinorUnitsAsEuro(rule.match_min_amount_minor)
                          : "min"}
                        {" bis "}
                        {rule.match_max_amount_minor != null
                          ? formatMinorUnitsAsEuro(rule.match_max_amount_minor)
                          : "max"}
                      </>
                    ) : (
                      "Beliebig"
                    )}
                  </td>
                  <td className="txn-td">
                    {rule.action_set_transfer ? (
                      <span className="status-badge status-badge--neutral">Transfer</span>
                    ) : rule.action_category_id ? (
                      categoryMap.get(rule.action_category_id) || rule.action_category_id
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="txn-td">
                    <span
                      className={`status-badge ${rule.source === "ai_confirmed" ? "status-badge--highlight" : "status-badge--neutral"}`}
                    >
                      {rule.source === "ai_confirmed"
                        ? "KI-bestätigt"
                        : rule.source === "imported"
                          ? "Import"
                          : "Manuell"}
                    </span>
                  </td>
                  <td className="txn-td" style={{ textAlign: "right" }}>
                    <div className="actions-cell" style={{ justifyContent: "flex-end" }}>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => openApplyDialog(rule)}
                        disabled={!rule.action_category_id && !rule.action_set_transfer}
                        title={
                          !rule.action_category_id && !rule.action_set_transfer
                            ? "Diese Regel setzt weder Kategorie noch Transferkennzeichen und kann am Bestand nichts ändern."
                            : undefined
                        }
                        aria-label={`Regel ${rule.name} auf Bestand anwenden`}
                      >
                        Auf Bestand anwenden
                      </button>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => handleOpenEdit(rule)}
                        aria-label={`Regel ${rule.name} bearbeiten`}
                      >
                        Bearbeiten
                      </button>
                      <button
                        type="button"
                        className="icon-btn icon-btn--danger"
                        onClick={() => deleteMutation.mutate(rule.id)}
                        disabled={deleteMutation.isPending}
                        aria-label="Regel löschen"
                      >
                        Löschen
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showApplyAll && (
        <Modal title="Alle Regeln auf Bestand anwenden" onClose={closeApplyAllDialog}>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            <p className="placeholder-note">
              Alle aktiven Regeln laufen in einem Durchgang über die vorhandenen Buchungen. Je
              Buchung gewinnt die erste passende Regel in Prioritätsreihenfolge — genau wie beim
              Import.
            </p>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)" }}>
              <div className="form-field">
                <label className="form-label" htmlFor="apply-all-scope">
                  Umfang
                </label>
                <select
                  id="apply-all-scope"
                  className="form-select"
                  value={applyAllScope}
                  onChange={(e) => {
                    setApplyAllScope(e.target.value as "uncategorized" | "all");
                    setApplyAllResult(null);
                    setApplyAllDone(false);
                  }}
                >
                  <option value="uncategorized">Nur Buchungen ohne Kategorie</option>
                  <option value="all">Alle Treffer (vorhandene Kategorien überschreiben)</option>
                </select>
              </div>
              <div className="form-field">
                <label className="form-label" htmlFor="apply-all-from">
                  Von (optional)
                </label>
                <input
                  id="apply-all-from"
                  type="date"
                  className="form-input"
                  value={applyAllFrom}
                  onChange={(e) => {
                    setApplyAllFrom(e.target.value);
                    setApplyAllResult(null);
                    setApplyAllDone(false);
                  }}
                />
              </div>
            </div>

            {applyAllMutation.error && (
              <p className="status-error" role="alert">
                {applyAllMutation.error instanceof Error
                  ? applyAllMutation.error.message
                  : "Der Sammellauf ist fehlgeschlagen."}
              </p>
            )}

            {applyAllResult && (
              <div className={`card ${applyAllDone ? "status-success" : ""}`}>
                <p style={{ fontWeight: 600 }}>
                  {applyAllDone ? "Angewendet: " : "Vorschau: "}
                  {describeApplyResult(applyAllResult)}
                </p>
                <p className="placeholder-note">
                  {applyAllResult.rules_evaluated} von {applyAllResult.rules_total} Regeln
                  ausgewertet, {applyAllResult.transactions_scanned} Buchungen geprüft.
                </p>
                {applyAllResult.truncated && (
                  <p className="status-error" role="alert">
                    Es treffen mehr Buchungen zu, als ein Lauf verarbeitet. Nach dem Anwenden erneut
                    ausführen.
                  </p>
                )}
                <div className="txn-table-wrapper" style={{ maxHeight: 320, overflowY: "auto" }}>
                  <table className="txn-table">
                    <thead>
                      <tr>
                        <th className="txn-th">Prio</th>
                        <th className="txn-th">Regel</th>
                        <th className="txn-th" style={{ textAlign: "right" }}>
                          Treffer
                        </th>
                        <th className="txn-th" style={{ textAlign: "right" }}>
                          {applyAllDone ? "Geändert" : "Zu ändern"}
                        </th>
                        <th className="txn-th">Hinweis</th>
                      </tr>
                    </thead>
                    <tbody>
                      {applyAllResult.rules.map((summary) => (
                        <tr key={summary.rule_id} className="txn-tr">
                          <td
                            className="txn-td"
                            style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}
                          >
                            {summary.priority}
                          </td>
                          <td className="txn-td">{summary.rule_name}</td>
                          <td
                            className="txn-td"
                            style={{ fontFamily: "var(--font-mono)", textAlign: "right" }}
                          >
                            {summary.matched}
                          </td>
                          <td
                            className="txn-td"
                            style={{ fontFamily: "var(--font-mono)", textAlign: "right" }}
                          >
                            {summary.applied}
                          </td>
                          <td className="txn-td">
                            {summary.excluded_reason ? (
                              <span className="status-badge status-badge--neutral">
                                {APPLY_EXCLUDED_LABELS[summary.excluded_reason] ??
                                  summary.excluded_reason}
                              </span>
                            ) : summary.skipped > 0 || summary.conflict > 0 ? (
                              <span className="txn-note">
                                {summary.skipped > 0 && `${summary.skipped} übersprungen`}
                                {summary.skipped > 0 && summary.conflict > 0 && ", "}
                                {summary.conflict > 0 && `${summary.conflict} Konflikt`}
                              </span>
                            ) : (
                              "—"
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: "var(--space-2)",
                marginTop: "var(--space-3)",
              }}
            >
              <button type="button" className="btn-secondary" onClick={closeApplyAllDialog}>
                {applyAllDone ? "Schließen" : "Abbrechen"}
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => applyAllMutation.mutate({ dryRun: true })}
                disabled={applyAllMutation.isPending}
              >
                Vorschau
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => applyAllMutation.mutate({ dryRun: false })}
                // Erst sehen, dann schreiben - dieselbe Linie wie beim
                // Einzellauf.
                disabled={
                  applyAllMutation.isPending ||
                  applyAllDone ||
                  !applyAllResult ||
                  applyAllResult.applied === 0
                }
              >
                {applyAllMutation.isPending ? "Läuft..." : "Anwenden"}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {applyRule && (
        <Modal title={`Auf Bestand anwenden: ${applyRule.name}`} onClose={closeApplyDialog}>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            <p className="placeholder-note">
              Regeln greifen sonst nur beim Import. Hier wird the Regel auf bereits gebuchte
              Transaktionen angewendet — entweder nur auf offene Fälle oder auf alle Treffer.
            </p>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)" }}>
              <div className="form-field">
                <label className="form-label" htmlFor="apply-scope">
                  Umfang
                </label>
                <select
                  id="apply-scope"
                  className="form-select"
                  value={applyScope}
                  onChange={(e) => {
                    setApplyScope(e.target.value as "uncategorized" | "all");
                    resetPreview();
                  }}
                >
                  <option value="uncategorized">Nur Buchungen ohne Kategorie</option>
                  <option value="all">Alle Treffer (vorhandene Kategorien überschreiben)</option>
                </select>
              </div>
              <div className="form-field">
                <label className="form-label" htmlFor="apply-from">
                  Von (optional)
                </label>
                <input
                  id="apply-from"
                  type="date"
                  className="form-input"
                  value={applyFrom}
                  onChange={(e) => {
                    setApplyFrom(e.target.value);
                    resetPreview();
                  }}
                />
              </div>
            </div>

            {applyMutation.error && (
              <p className="status-error" role="alert">
                {applyMutation.error instanceof Error
                  ? applyMutation.error.message
                  : "Der Regellauf ist fehlgeschlagen."}
              </p>
            )}

            {applyResult && (
              <div className={`card ${applyDone ? "status-success" : ""}`}>
                <p style={{ fontWeight: 600 }}>
                  {applyDone ? "Angewendet: " : "Vorschau: "}
                  {describeApplyResult(applyResult)}
                </p>
                {applyResult.truncated && (
                  <p className="status-error" role="alert">
                    Es treffen mehr Buchungen zu, als ein Lauf verarbeitet (
                    {applyResult.items.length}
                    ). Nach dem Anwenden erneut ausführen.
                  </p>
                )}
                {applyResult.items.length > 0 && (
                  <div className="txn-table-wrapper" style={{ maxHeight: 320, overflowY: "auto" }}>
                    <table className="txn-table">
                      <thead>
                        <tr>
                          <th className="txn-th">Datum</th>
                          <th className="txn-th">Verwendungszweck</th>
                          <th className="txn-th" style={{ textAlign: "right" }}>
                            Betrag
                          </th>
                          <th className="txn-th">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {applyResult.items.map((item) => (
                          <tr key={item.transaction_id} className="txn-tr">
                            <td className="txn-td" style={{ fontFamily: "var(--font-mono)" }}>
                              {item.booked_date}
                            </td>
                            <td className="txn-td">
                              <span className="txn-note">{item.purpose || item.payee || "—"}</span>
                            </td>
                            <td
                              className="txn-td"
                              style={{ fontFamily: "var(--font-mono)", textAlign: "right" }}
                            >
                              {formatMinorUnitsAsEuro(item.amount_minor)}
                            </td>
                            <td className="txn-td">
                              {APPLY_STATUS_LABELS[item.status]}
                              {item.reason && (
                                <span className="txn-note" style={{ display: "block" }}>
                                  {APPLY_REASON_LABELS[item.reason] ?? item.reason}
                                </span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: "var(--space-2)",
                marginTop: "var(--space-3)",
              }}
            >
              <button type="button" className="btn-secondary" onClick={closeApplyDialog}>
                {applyDone ? "Schließen" : "Abbrechen"}
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => applyMutation.mutate({ ruleId: applyRule.id, dryRun: true })}
                disabled={applyMutation.isPending}
              >
                Vorschau
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => applyMutation.mutate({ ruleId: applyRule.id, dryRun: false })}
                // Erst sehen, dann schreiben: ohne Vorschau mit tatsaechlichen
                // Treffern bleibt der Anwenden-Knopf gesperrt.
                disabled={
                  applyMutation.isPending || applyDone || !applyResult || applyResult.applied === 0
                }
              >
                {applyMutation.isPending ? "Läuft..." : "Anwenden"}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {isModalOpen && (
        <Modal
          title={
            editingRule ? `Regel bearbeiten: ${editingRule.name}` : "Neue Klassifikationsregel"
          }
          onClose={closeModal}
        >
          <form
            onSubmit={handleSubmitRule}
            style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
          >
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "var(--space-3)" }}>
              <div className="form-field">
                <label className="form-label" htmlFor="rule-name">
                  Name *
                </label>
                <input
                  id="rule-name"
                  type="text"
                  className="form-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="z. B. Rewe oder Amazon Regel"
                  required
                />
              </div>

              <div className="form-field">
                <label className="form-label" htmlFor="rule-priority-input">
                  Priorität
                </label>
                <input
                  id="rule-priority-input"
                  type="number"
                  className="form-input form-input--mono"
                  value={priority}
                  onChange={(e) => setPriority(Number(e.target.value))}
                  required
                />
              </div>
            </div>

            {/* Standard-Bedingungen */}
            <div className="form-field">
              <div
                style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
              >
                <label className="form-label" htmlFor="rule-payee-pattern">
                  Empfänger / Händler (Enthält)
                </label>
                <button
                  type="button"
                  className="status-badge status-badge--neutral"
                  style={{
                    fontSize: "11px",
                    fontWeight: 500,
                    cursor: "pointer",
                    border: "1px solid var(--color-border, #444)",
                    background: showRegexHelp
                      ? "var(--color-surface-raised, rgba(255, 255, 255, 0.1))"
                      : "transparent",
                    display: "flex",
                    alignItems: "center",
                    gap: "4px",
                  }}
                  onClick={() => setShowRegexHelp(!showRegexHelp)}
                  title="Klicken für Beispiele zu Regulären Ausdrücken (Regex)"
                >
                  <span>ⓘ Regex-Hilfe</span>
                </button>
              </div>
              <input
                id="rule-payee-pattern"
                type="text"
                className="form-input"
                value={matchPayeeRaw}
                onChange={(e) => setMatchPayeeRaw(e.target.value)}
                placeholder="z. B. Rewe, Otto oder ^(AMZN|Amazon)"
              />

              {showRegexHelp && (
                <div
                  className="card"
                  style={{
                    padding: "12px",
                    fontSize: "12px",
                    marginTop: "8px",
                    backgroundColor: "var(--color-surface-raised, #1e1e1e)",
                    border: "1px solid var(--color-accent)",
                    borderRadius: "6px",
                    display: "flex",
                    flexDirection: "column",
                    gap: "8px",
                    boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                  }}
                >
                  <div
                    style={{
                      fontWeight: 600,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      color: "var(--color-text-primary)",
                    }}
                  >
                    <span>💡 Regex-Möglichkeiten & Beispiele:</span>
                    <button
                      type="button"
                      style={{
                        background: "none",
                        border: "none",
                        color: "var(--color-text-secondary)",
                        cursor: "pointer",
                        fontSize: "14px",
                      }}
                      onClick={() => setShowRegexHelp(false)}
                      aria-label="Hilfe schließen"
                    >
                      ✕
                    </button>
                  </div>
                  <ul
                    style={{
                      paddingLeft: "16px",
                      margin: 0,
                      display: "flex",
                      flexDirection: "column",
                      gap: "6px",
                      color: "var(--color-text-primary)",
                    }}
                  >
                    <li>
                      <strong>
                        Exaktes Wort (<code>\b...\b</code>):
                      </strong>{" "}
                      <code>\bOtto\b</code> matcht <em>Otto</em>, schließt aber <em>Lotto</em> &{" "}
                      <em>Motto</em> aus.
                    </li>
                    <li>
                      <strong>
                        Oder-Verknüpfung (<code>|</code>):
                      </strong>{" "}
                      <code>^(AMZN|Amazon)</code> matcht <em>AMZN</em> oder <em>Amazon</em> am
                      Anfang.
                    </li>
                    <li>
                      <strong>Mehrere Händler:</strong> <code>(Rewe|Penny|Aldi)</code> matcht jeden
                      dieser Supermärkte.
                    </li>
                    <li>
                      <strong>
                        Anfang / Ende (<code>^</code> / <code>$</code>):
                      </strong>{" "}
                      <code>^musterberatung</code> (beginnt mit musterberatung) | <code>GmbH$</code>{" "}
                      (endet auf GmbH).
                    </li>
                    <li>
                      <strong>
                        Variable Lücken (<code>.*</code>):
                      </strong>{" "}
                      <code>Amazon.*Digital</code> matcht z. B. <em>Amazon Services Digital</em>.
                    </li>
                  </ul>
                  <span
                    style={{
                      fontSize: "11px",
                      color: "var(--color-text-secondary)",
                      fontStyle: "italic",
                    }}
                  >
                    Hinweis: Groß-/Kleinschreibung wird von der Regel-Engine automatisch ignoriert.
                  </span>
                </div>
              )}
            </div>

            <div className="form-field">
              <label className="form-label" htmlFor="rule-purpose-pattern">
                Verwendungszweck (Enthält)
              </label>
              <input
                id="rule-purpose-pattern"
                type="text"
                className="form-input"
                value={matchPurpose}
                onChange={(e) => setMatchPurpose(e.target.value)}
                placeholder="z. B. Miete, Gehalt, Versicherung..."
              />
            </div>

            <div className="form-field">
              <label className="form-label" htmlFor="rule-action-category">
                Zielkategorie
              </label>
              <CategoryPicker
                id="rule-action-category"
                categoryGroups={categoryGroups}
                value={actionCategoryId}
                onChange={(catId) => setActionCategoryId(catId)}
                noneLabel="-- Keine Kategorie zuweisen --"
              />
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <input
                type="checkbox"
                id="rule-set-transfer"
                checked={actionSetTransfer}
                onChange={(e) => setActionSetTransfer(e.target.checked)}
              />
              <label htmlFor="rule-set-transfer" style={{ fontSize: "13px", cursor: "pointer" }}>
                Passende Buchungen als Transfer markieren
              </label>
            </div>

            {/* Erweiterte Optionen Toggler */}
            <button
              type="button"
              className="btn-secondary"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: "8px",
                padding: "8px 12px",
                fontSize: "13px",
                marginTop: "4px",
              }}
              onClick={() => setShowAdvancedOptions(!showAdvancedOptions)}
            >
              {showAdvancedOptions
                ? "▲ Erweiterte Filter & Beträge ausblenden"
                : "▼ Erweiterte Filter & Beträge anzeigen (Ausschlüsse, Betragsspannen)"}
            </button>

            {/* Ausklappbarer Bereich fuer erweiterte Optionen */}
            {showAdvancedOptions && (
              <div
                className="card"
                style={{
                  padding: "var(--space-3)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-3)",
                  backgroundColor: "var(--color-surface-raised, rgba(255, 255, 255, 0.03))",
                  border: "1px solid var(--color-border, #333)",
                }}
              >
                <div
                  style={{
                    fontWeight: 600,
                    fontSize: "13px",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  Erweiterte Ausschlüsse & Betragsspannen
                </div>

                {/* Ausschluss-Felder */}
                <div
                  style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)" }}
                >
                  <div className="form-field">
                    <label
                      className="form-label"
                      htmlFor="rule-exclude-payee"
                      style={{ fontSize: "12px" }}
                    >
                      Empfänger ausschließen (Enthält NICHT)
                    </label>
                    <input
                      id="rule-exclude-payee"
                      type="text"
                      className="form-input"
                      value={excludePayeeRaw}
                      onChange={(e) => setExcludePayeeRaw(e.target.value)}
                      placeholder="z. B. Digital, Prime..."
                    />
                  </div>

                  <div className="form-field">
                    <label
                      className="form-label"
                      htmlFor="rule-exclude-purpose"
                      style={{ fontSize: "12px" }}
                    >
                      Zweck ausschließen (Enthält NICHT)
                    </label>
                    <input
                      id="rule-exclude-purpose"
                      type="text"
                      className="form-input"
                      value={excludePurpose}
                      onChange={(e) => setExcludePurpose(e.target.value)}
                      placeholder="z. B. Storno, Gutschrift..."
                    />
                  </div>
                </div>

                {/* Betrag */}
                <div className="form-field" style={{ marginTop: "4px" }}>
                  <label
                    className="form-label"
                    htmlFor="rule-amount-mode"
                    style={{ fontSize: "12px" }}
                  >
                    Betrags-Bedingung
                  </label>
                  <select
                    id="rule-amount-mode"
                    className="form-select"
                    value={matchAmountMode}
                    onChange={(e) => setMatchAmountMode(e.target.value as "inside" | "outside")}
                  >
                    <option value="inside">
                      Betrag muss INNERHALB der Spanne liegen (Standard)
                    </option>
                    <option value="outside">
                      Betrag darf NICHT in dieser Spanne liegen (Ausschluss-Spanne)
                    </option>
                  </select>
                </div>

                <div
                  style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-3)" }}
                >
                  <div className="form-field">
                    <label
                      className="form-label"
                      htmlFor="rule-min-amount"
                      style={{ fontSize: "12px" }}
                    >
                      Mindestbetrag (€)
                    </label>
                    <input
                      id="rule-min-amount"
                      type="text"
                      className="form-input form-input--mono"
                      value={minAmountEuro}
                      onChange={(e) => setMinAmountEuro(e.target.value)}
                      placeholder="z. B. -8000,00"
                    />
                  </div>
                  <div className="form-field">
                    <label
                      className="form-label"
                      htmlFor="rule-max-amount"
                      style={{ fontSize: "12px" }}
                    >
                      Höchstbetrag (€)
                    </label>
                    <input
                      id="rule-max-amount"
                      type="text"
                      className="form-input form-input--mono"
                      value={maxAmountEuro}
                      onChange={(e) => setMaxAmountEuro(e.target.value)}
                      placeholder="z. B. -6000,00"
                    />
                  </div>
                </div>
              </div>
            )}

            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <input
                type="checkbox"
                id="rule-is-active"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
              />
              <label htmlFor="rule-is-active" style={{ fontSize: "13px", cursor: "pointer" }}>
                Regel aktiv (wird bei neuen Buchungen ausgewertet)
              </label>
            </div>

            <div className="form-field">
              <label className="form-label" htmlFor="rule-note">
                Notiz (optional)
              </label>
              <input
                id="rule-note"
                type="text"
                className="form-input"
                value={actionNote}
                onChange={(e) => setActionNote(e.target.value)}
              />
            </div>

            {saveError && (
              <p className="status-error" role="alert">
                {saveError instanceof Error
                  ? saveError.message
                  : "Die Regel konnte nicht gespeichert werden."}
              </p>
            )}

            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: "var(--space-2)",
                marginTop: "var(--space-3)",
              }}
            >
              <button type="button" className="btn-secondary" onClick={closeModal}>
                Abbrechen
              </button>
              <button type="submit" className="btn-primary" disabled={isSaving || !name.trim()}>
                {isSaving
                  ? "Speichere..."
                  : editingRule
                    ? "Änderungen speichern"
                    : "Regel erstellen"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {showImportExport && (
        <RuleImportExportModal
          onClose={() => {
            setShowImportExport(false);
            void queryClient.invalidateQueries({ queryKey: ["rules", householdId] });
          }}
          token={token!}
          householdId={householdId}
          rules={rules ?? []}
          categoryGroups={categoryGroups ?? []}
        />
      )}
    </section>
  );
}
