import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  monthlyMinorOf,
  useContractContext,
  useContractCostTarget,
  useContractList,
  useContractScenarios,
  useCreateContractScenario,
  useDeleteContractScenario,
  useUpdateContractCostTarget,
  useUpdateContractScenario,
  yearlyMinorOf,
} from "../../hooks/useContracts";
import { useCategoryTree } from "../../hooks/useCategoryCatalog";
import { CategoryDot } from "../CategoryDot";
import { SortableTableHeader } from "../SortableTableHeader";
import {
  buildCategoryIndex,
  UNCATEGORIZED_LABEL,
  type ResolvedCategory,
} from "../../lib/categoryTypes";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import type {
  Contract,
  ContractCostTarget,
  ContractScenario,
  ContractScenarioAction,
  ContractScenarioItemInput,
} from "../../lib/types";

/**
 * Register "Ziel" der Vertragsverwaltung (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, I7/I8).
 *
 * Die Fixkostenquote im Budget beantwortet "wie stehe ich da". Diese Ansicht
 * beantwortet die beiden Fragen dahinter: "wo will ich hin" und "was bringt es,
 * wenn ich X kuendige".
 *
 * Durchgaengige Trennlinie: ein Szenario aendert KEINEN Vertrag. Es ist eine
 * Absichtserklaerung, kein Vollzug - deshalb bleiben Fixkostensumme, Budget und
 * Quote unberuehrt, solange nicht wirklich gekuendigt wurde. Alles, was das
 * Szenario liefert, ist bewusst als "geplant" beschriftet.
 */

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function formatPercent(value: number): string {
  return `${value.toLocaleString("de-DE", { maximumFractionDigits: 1 })} %`;
}

// ---------------------------------------------------------------------------
// Zielkarte
// ---------------------------------------------------------------------------

interface TargetCardProps {
  target: ContractCostTarget;
  canEdit: boolean;
}

function TargetCard({ target, canEdit }: TargetCardProps) {
  const mutation = useUpdateContractCostTarget();
  const [editing, setEditing] = useState(false);
  const [ratio, setRatio] = useState(String(target.target_ratio_percent));
  const [absolute, setAbsolute] = useState(
    target.target_monthly_minor === null || target.target_monthly_minor === undefined
      ? ""
      : (target.target_monthly_minor / 100).toFixed(2).replace(".", ","),
  );
  const [targetDate, setTargetDate] = useState(target.target_date ?? "");
  const [formError, setFormError] = useState<string | null>(null);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);

    const parsedRatio = Number(ratio.replace(",", "."));
    if (!Number.isFinite(parsedRatio) || parsedRatio <= 0 || parsedRatio > 100) {
      setFormError("Die Zielquote muss zwischen 0 und 100 % liegen.");
      return;
    }
    // Leeres Feld heisst "kein absoluter Zielbetrag", nicht "0 EUR" - sonst
    // waere jedes geleerte Feld ein Ziel von null Fixkosten.
    const parsedAbsolute = absolute.trim() ? parseAmountToMinorUnits(absolute) : null;
    if (absolute.trim() && (parsedAbsolute === null || parsedAbsolute < 0)) {
      setFormError("Der Zielbetrag ist keine gültige Zahl.");
      return;
    }

    mutation.mutate(
      {
        target_ratio_percent: parsedRatio,
        target_monthly_minor: parsedAbsolute,
        target_date: targetDate || null,
        note: null,
      },
      {
        onSuccess: () => setEditing(false),
        onError: (error) =>
          setFormError(errorText(error, "Das Ziel konnte nicht gespeichert werden.")),
      },
    );
  };

  // Bezugsgroesse der Anzeige ist die groessere von Ist und Ziel, damit die
  // Zielmarke bei einer Zielverfehlung nicht aus dem Balken laeuft.
  const scale = Math.max(target.current_monthly_minor, target.effective_target_monthly_minor, 1);
  const fillPercent = Math.min(100, Math.round((target.current_monthly_minor / scale) * 100));
  const markerPercent = Math.min(
    100,
    Math.round((target.effective_target_monthly_minor / scale) * 100),
  );

  return (
    <div className="card">
      <div className="page-header" style={{ marginBottom: "var(--space-3)" }}>
        <div>
          <h2 className="card-heading">Fixkostenziel</h2>
          <p className="placeholder-note">
            {target.is_configured
              ? `Angestrebt sind ${formatPercent(target.target_ratio_percent)} der Einnahmen${
                  target.target_date ? ` bis ${target.target_date}` : ""
                }.`
              : "Noch kein Ziel gesetzt — unten festlegen, dann rechnet die Lücke mit."}
          </p>
        </div>
        {canEdit && !editing && (
          <button type="button" className="btn-secondary" onClick={() => setEditing(true)}>
            {target.is_configured ? "Ziel ändern" : "Ziel festlegen"}
          </button>
        )}
      </div>

      <div className="metric-grid">
        <div className="card metric-card">
          <span className="metric-label">Fixkosten heute</span>
          <span className="metric-value">
            {formatMinorUnitsAsEuro(target.current_monthly_minor)}
          </span>
          <span className="metric-hint">
            {formatMinorUnitsAsEuro(target.current_yearly_minor)} im Jahr · {target.contract_count}{" "}
            Verträge
          </span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Zielbetrag</span>
          <span className="metric-value">
            {formatMinorUnitsAsEuro(target.effective_target_monthly_minor)}
          </span>
          <span className="metric-hint">
            {target.target_monthly_minor === null || target.target_monthly_minor === undefined
              ? `${formatPercent(target.target_ratio_percent)} von ${formatMinorUnitsAsEuro(
                  target.income_basis_minor,
                )}`
              : "Fest vorgegebener Betrag"}
          </span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">{target.on_track ? "Puffer" : "Noch zu senken"}</span>
          <span
            className={`metric-value ${
              target.on_track ? "transaction-amount--positive" : "transaction-amount--negative"
            }`}
          >
            {formatMinorUnitsAsEuro(Math.abs(target.gap_monthly_minor))}
          </span>
          <span className="metric-hint">
            {formatMinorUnitsAsEuro(Math.abs(target.gap_yearly_minor))} im Jahr
          </span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Quote heute</span>
          <span className="metric-value">{formatPercent(target.current_ratio_percent)}</span>
          {/* Ohne diesen Hinweis saehe eine Quote auf Vertragsbasis genauso aus
              wie eine auf gebuchten Einnahmen - und niemand wuesste, warum sie
              sich am Monatsende ploetzlich verschiebt. */}
          <span className="metric-hint">
            Basis:{" "}
            {target.income_basis === "actual"
              ? "gebuchte Einnahmen"
              : "Einnahmeverträge (noch nichts gebucht)"}
          </span>
        </div>
      </div>

      <div className="target-gauge">
        <span className="target-gauge__track">
          <span
            className={`target-gauge__fill ${
              target.on_track ? "progress-bar-fill--positive" : "progress-bar-fill--warning"
            }`}
            style={{ width: `${fillPercent}%` }}
          />
          <span className="target-gauge__marker" style={{ left: `${markerPercent}%` }} />
        </span>
        <span className="target-gauge__legend">
          <span>Ist {formatMinorUnitsAsEuro(target.current_monthly_minor)}</span>
          <span>Ziel {formatMinorUnitsAsEuro(target.effective_target_monthly_minor)}</span>
        </span>
      </div>

      {editing && (
        <form className="target-form" onSubmit={submit}>
          <label className="form-field">
            <span className="form-label">Zielquote in % der Einnahmen</span>
            <input
              className="form-input"
              inputMode="decimal"
              value={ratio}
              onChange={(event) => setRatio(event.target.value)}
            />
          </label>
          <label className="form-field">
            <span className="form-label">Fester Zielbetrag pro Monat (optional)</span>
            <input
              className="form-input"
              inputMode="decimal"
              placeholder="leer = aus Quote"
              value={absolute}
              onChange={(event) => setAbsolute(event.target.value)}
            />
          </label>
          <label className="form-field">
            <span className="form-label">Bis wann (optional)</span>
            <input
              className="form-input"
              type="date"
              value={targetDate}
              onChange={(event) => setTargetDate(event.target.value)}
            />
          </label>
          <div className="scenario-action">
            <button type="submit" className="btn-primary" disabled={mutation.isPending}>
              {mutation.isPending ? "Wird gespeichert…" : "Ziel speichern"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setEditing(false)}>
              Abbrechen
            </button>
          </div>
          {formError && (
            <p role="alert" className="status-error">
              {formError}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Planeditor
// ---------------------------------------------------------------------------

interface DraftItem {
  action: ContractScenarioAction;
  /** Rohtext des Eingabefelds; erst beim Speichern in Minor-Einheiten geparst. */
  amountText: string;
}

const ACTION_LABEL: Record<ContractScenarioAction, string> = {
  cancel: "Kündigen",
  reduce: "Senken auf",
};

interface ScenarioRow {
  contract: Contract;
  monthly: number;
  yearly: number;
}

/** Was ein Vorhaben (Kuendigen/Senken) an dieser Zeile monatlich spart - 0
 *  ohne gueltiges Vorhaben. Eigenstaendige Funktion statt Inline-Logik, weil
 *  sowohl die Zeile selbst als auch die Kategorie-Zwischensumme darueber
 *  denselben Wert brauchen. */
function rowSaving(row: ScenarioRow, entry: DraftItem | undefined): number {
  if (!entry) return 0;
  if (entry.action === "cancel") return row.monthly;
  if (entry.action !== "reduce") return 0;
  const parsed = parseAmountToMinorUnits(entry.amountText);
  if (parsed === null || parsed < 0 || parsed >= row.contract.amount_minor) return 0;
  return row.monthly - monthlyMinorOf(parsed, row.contract.frequency);
}

interface ScenarioRowGroup {
  key: string;
  label: string;
  color: string | null;
  rows: ScenarioRow[];
}

type ScenarioSortKey = "name" | "monthly" | "yearly" | "saving";

function compareScenarioRows(
  key: ScenarioSortKey,
  a: ScenarioRow,
  b: ScenarioRow,
  draft: Record<string, DraftItem>,
): number {
  switch (key) {
    case "name":
      return a.contract.name.localeCompare(b.contract.name, "de");
    case "monthly":
      return a.monthly - b.monthly;
    case "saving":
      return rowSaving(a, draft[a.contract.id]) - rowSaving(b, draft[b.contract.id]);
    default:
      return a.yearly - b.yearly;
  }
}

interface ScenarioEditorProps {
  scenario: ContractScenario;
  contracts: Contract[];
  target: ContractCostTarget;
  canEdit: boolean;
  categoryIndex: Map<string, ResolvedCategory>;
}

/**
 * Bearbeitet die Positionen eines Szenarios.
 *
 * Die Vorschau rechnet lokal mit derselben Normierung wie der Server
 * (`monthlyMinorOf` spiegelt `contract_service.monthly_minor`). Ohne sie
 * muesste jeder Haken erst gespeichert werden, bevor er eine Wirkung zeigt -
 * und ein Durchspielen von fuenf Varianten waere fuenf Schreibvorgaenge.
 *
 * Die Zeilen stehen nach Kategorie gruppiert (Zwischensummen im Stil von
 * BudgetPage), nicht mehr als eine einzige flache Liste: bei einem Dutzend
 * Vertraegen beantwortet erst die Gruppierung "wo sitzt eigentlich das
 * grosse Geld", eine reine Jahreskosten-Rangliste vermischt Miete und
 * Streaming-Abos in derselben Reihenfolge.
 *
 * Wird vom Elternteil mit `key={scenario.id}` gerendert: beim Wechsel des
 * Szenarios entsteht die Komponente neu und der Entwurf startet aus den
 * gespeicherten Positionen, statt den des vorigen Plans weiterzutragen.
 */
function ScenarioEditor({
  scenario,
  contracts,
  target,
  canEdit,
  categoryIndex,
}: ScenarioEditorProps) {
  const mutation = useUpdateContractScenario();
  const [sort, setSort] = useState<{ key: ScenarioSortKey; direction: "asc" | "desc" }>({
    key: "yearly",
    direction: "desc",
  });
  // Aufgeklappt ist der Startzustand, wie im Kategorie-Register der
  // Vertragsliste - erst gezielt einklappen, wenn eine Gruppe den Blick auf
  // die anderen verstellt.
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const toggleGroupCollapsed = (key: string) => {
    setCollapsedGroups((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const toggleSort = (key: ScenarioSortKey) => {
    setSort((previous) =>
      previous.key === key
        ? { key, direction: previous.direction === "asc" ? "desc" : "asc" }
        : { key, direction: key === "name" ? "asc" : "desc" },
    );
  };
  const [draft, setDraft] = useState<Record<string, DraftItem>>(() =>
    Object.fromEntries(
      scenario.items.map((item) => [
        item.contract_id,
        {
          action: item.action,
          amountText:
            item.target_amount_minor === null || item.target_amount_minor === undefined
              ? ""
              : (item.target_amount_minor / 100).toFixed(2).replace(".", ","),
        },
      ]),
    ),
  );
  const [formError, setFormError] = useState<string | null>(null);

  const rows = useMemo(
    () =>
      contracts
        .filter((contract) => contract.direction === "expense")
        .filter((contract) => contract.status === "active" || contract.status === "cancelled")
        .map((contract) => ({
          contract,
          monthly: monthlyMinorOf(contract.amount_minor, contract.frequency),
          yearly: yearlyMinorOf(contract.amount_minor, contract.frequency),
        }))
        // Teuerste zuerst nach JAHRESkosten: eine Jahrespraemie ueber 600 EUR
        // steht in einer Monatsrangliste hinter einem Abo fuer 9,99 EUR, ist
        // aber der groessere Hebel.
        .sort((a, b) => b.yearly - a.yearly || a.contract.name.localeCompare(b.contract.name)),
    [contracts],
  );

  const groups = useMemo<ScenarioRowGroup[]>(() => {
    // Gruppiert nach Kategorie-GRUPPE (Oberpunkt), nicht nach einzelner
    // Kategorie: sonst stuenden "Kranken- & Pflegeversicherung" und "Private
    // Altersvorsorge" als zwei eigene Zwischensummen nebeneinander, obwohl
    // beide zu "Gesundheit, Versicherung & Vorsorge" gehoeren - dasselbe
    // Problem, das die Oberpunkte im Kategorie-Register loesen.
    const buckets = new Map<string, ScenarioRowGroup>();
    for (const row of rows) {
      const resolved = row.contract.category_id
        ? categoryIndex.get(row.contract.category_id)
        : undefined;
      const key = resolved?.groupId ?? "uncategorized";
      let bucket = buckets.get(key);
      if (!bucket) {
        bucket = {
          key,
          label: resolved?.groupName ?? UNCATEGORIZED_LABEL,
          color: resolved?.groupColor ?? null,
          rows: [],
        };
        buckets.set(key, bucket);
      }
      bucket.rows.push(row);
    }
    const factor = sort.direction === "asc" ? 1 : -1;
    const sortRows = (list: ScenarioRow[]) =>
      [...list].sort(
        (a, b) =>
          factor * compareScenarioRows(sort.key, a, b, draft) ||
          a.contract.name.localeCompare(b.contract.name, "de"),
      );
    const groupValue = (group: ScenarioRowGroup): number => {
      switch (sort.key) {
        case "monthly":
          return group.rows.reduce((sum, row) => sum + row.monthly, 0);
        case "saving":
          return group.rows.reduce((sum, row) => sum + rowSaving(row, draft[row.contract.id]), 0);
        default:
          return group.rows.reduce((sum, row) => sum + row.yearly, 0);
      }
    };
    return [...buckets.values()]
      .map((group) => ({ ...group, rows: sortRows(group.rows) }))
      .sort((a, b) => {
        // Nicht kategorisiert faellt ans Ende, unabhaengig von der
        // Sortierung - dasselbe Prinzip wie bei der Kategorie-Sortierung der
        // Vertragsliste und dem Kategorie-Register (ContractsPage.tsx).
        if (a.key === "uncategorized" && b.key !== "uncategorized") return 1;
        if (a.key !== "uncategorized" && b.key === "uncategorized") return -1;
        if (sort.key === "name") return factor * a.label.localeCompare(b.label, "de");
        return factor * (groupValue(a) - groupValue(b)) || a.label.localeCompare(b.label, "de");
      });
  }, [rows, categoryIndex, sort, draft]);

  const preview = useMemo(() => {
    const saving = rows.reduce((total, row) => {
      const entry = draft[row.contract.id];
      if (!entry) return total;
      if (entry.action === "cancel") return total + row.monthly;
      const parsed = parseAmountToMinorUnits(entry.amountText);
      if (parsed === null || parsed < 0 || parsed >= row.contract.amount_minor) return total;
      return total + (row.monthly - monthlyMinorOf(parsed, row.contract.frequency));
    }, 0);
    const resulting = target.current_monthly_minor - saving;
    return {
      saving,
      resulting,
      reachesTarget: resulting <= target.effective_target_monthly_minor,
      remainingGap: resulting - target.effective_target_monthly_minor,
    };
  }, [draft, rows, target]);

  const setAction = (contractId: string, action: ContractScenarioAction | "none") => {
    setDraft((current) => {
      if (action === "none") {
        const { [contractId]: _removed, ...rest } = current;
        return rest;
      }
      return {
        ...current,
        [contractId]: { action, amountText: current[contractId]?.amountText ?? "" },
      };
    });
  };

  const setAmount = (contractId: string, amountText: string) => {
    setDraft((current) => {
      const entry = current[contractId];
      if (!entry) return current;
      return { ...current, [contractId]: { ...entry, amountText } };
    });
  };

  const save = () => {
    setFormError(null);
    const items: ContractScenarioItemInput[] = [];
    const invalid: string[] = [];

    for (const row of rows) {
      const entry = draft[row.contract.id];
      if (!entry) continue;
      if (entry.action === "cancel") {
        items.push({
          contract_id: row.contract.id,
          action: "cancel",
          target_amount_minor: null,
          note: null,
        });
        continue;
      }
      const parsed = parseAmountToMinorUnits(entry.amountText);
      if (parsed === null || parsed < 0 || parsed >= row.contract.amount_minor) {
        invalid.push(row.contract.name);
        continue;
      }
      items.push({
        contract_id: row.contract.id,
        action: "reduce",
        target_amount_minor: parsed,
        note: null,
      });
    }

    if (invalid.length > 0) {
      setFormError(
        `Ein niedrigerer Betrag als bisher fehlt bei: ${invalid.join(", ")}. Eine „Senkung" auf denselben oder einen höheren Betrag ist keine.`,
      );
      return;
    }

    mutation.mutate(
      { scenarioId: scenario.id, input: { items } },
      {
        onError: (error) =>
          setFormError(errorText(error, "Der Plan konnte nicht gespeichert werden.")),
      },
    );
  };

  return (
    <>
      <div className="metric-grid">
        <div className="card metric-card">
          <span className="metric-label">Geplante Ersparnis</span>
          <span className="metric-value transaction-amount--positive">
            {formatMinorUnitsAsEuro(preview.saving)}
          </span>
          <span className="metric-hint">{formatMinorUnitsAsEuro(preview.saving * 12)} im Jahr</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Fixkosten danach</span>
          <span className="metric-value">{formatMinorUnitsAsEuro(preview.resulting)}</span>
          <span className="metric-hint">
            heute {formatMinorUnitsAsEuro(target.current_monthly_minor)}
          </span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Ziel erreicht?</span>
          <span
            className={`status-badge ${
              preview.reachesTarget ? "status-badge--positive" : "status-badge--warning"
            }`}
          >
            {preview.reachesTarget ? "Ziel erreicht" : "Ziel verfehlt"}
          </span>
          <span className="metric-hint">
            {preview.reachesTarget
              ? `${formatMinorUnitsAsEuro(-preview.remainingGap)} Puffer`
              : `${formatMinorUnitsAsEuro(preview.remainingGap)} fehlen noch`}
          </span>
        </div>
      </div>

      {/* Der Satz steht bewusst hier und nicht nur in der Dokumentation: die
          Tabelle darunter sieht aus wie eine Massenbearbeitung von Vertraegen,
          ist aber keine. */}
      <p className="placeholder-note">
        Der Plan ändert keinen Vertrag. Fixkostensumme, Budget und Quote bleiben, bis wirklich
        gekündigt wurde.
      </p>

      {rows.length === 0 && (
        <p className="placeholder-note">Es gibt keine laufenden Ausgabenverträge.</p>
      )}

      {rows.length > 0 && (
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <SortableTableHeader
                  label="Vertrag"
                  sortKey="name"
                  active={sort.key === "name"}
                  direction={sort.direction}
                  onSort={toggleSort}
                />
                <SortableTableHeader
                  label="Pro Monat"
                  sortKey="monthly"
                  active={sort.key === "monthly"}
                  direction={sort.direction}
                  onSort={toggleSort}
                  align="amount"
                />
                <SortableTableHeader
                  label="Pro Jahr"
                  sortKey="yearly"
                  active={sort.key === "yearly"}
                  direction={sort.direction}
                  onSort={toggleSort}
                  align="amount"
                />
                <th className="txn-th">Vorhaben</th>
                <SortableTableHeader
                  label="Spart"
                  sortKey="saving"
                  active={sort.key === "saving"}
                  direction={sort.direction}
                  onSort={toggleSort}
                  align="amount"
                />
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => {
                const groupMonthly = group.rows.reduce((sum, row) => sum + row.monthly, 0);
                const groupYearly = group.rows.reduce((sum, row) => sum + row.yearly, 0);
                const groupSaving = group.rows.reduce(
                  (sum, row) => sum + rowSaving(row, draft[row.contract.id]),
                  0,
                );
                const collapsed = collapsedGroups.has(group.key);
                return (
                  <Fragment key={group.key}>
                    {/* Zwischensumme je Kategorie-Gruppe, im Stil von
                        BudgetPage: erst sie beantwortet "wo sitzt das grosse
                        Geld", eine reine Rangliste vermischt Miete und
                        Streaming-Abos in derselben Reihenfolge. */}
                    <tr className="txn-tr grouped-table-row">
                      <td className="txn-td">
                        <button
                          type="button"
                          className="grouped-table-toggle"
                          onClick={() => toggleGroupCollapsed(group.key)}
                          aria-expanded={!collapsed}
                          aria-label={
                            collapsed ? `${group.label} aufklappen` : `${group.label} zuklappen`
                          }
                        >
                          {collapsed ? "▸" : "▾"}
                        </button>
                        <CategoryDot color={group.color} />
                        {group.label}
                      </td>
                      <td className="txn-td txn-td--amount">
                        {formatMinorUnitsAsEuro(groupMonthly)}
                      </td>
                      <td className="txn-td txn-td--amount">
                        {formatMinorUnitsAsEuro(groupYearly)}
                      </td>
                      <td className="txn-td" />
                      <td
                        className={`txn-td txn-td--amount ${
                          groupSaving > 0 ? "transaction-amount--positive" : ""
                        }`}
                      >
                        {groupSaving > 0 ? formatMinorUnitsAsEuro(groupSaving) : "—"}
                      </td>
                    </tr>
                    {!collapsed &&
                      group.rows.map((row) => {
                        const entry = draft[row.contract.id];
                        const saving = rowSaving(row, entry);
                        const category = row.contract.category_id
                          ? categoryIndex.get(row.contract.category_id)
                          : undefined;
                        return (
                          <tr key={row.contract.id} className="txn-tr">
                            <td className="txn-td" style={{ paddingLeft: "var(--space-6)" }}>
                              <span className="txn-payee">{row.contract.name}</span>
                              {/* Mehrere Kategorien koennen zur selben Gruppe
                                  gehoeren (z. B. "Kranken-" und
                                  "Altersvorsorge" unter "Gesundheit,
                                  Versicherung & Vorsorge") - ohne diese
                                  Zeile waere nicht mehr zu sehen, welche
                                  Kategorie genau gemeint ist. */}
                              {category && <span className="txn-note">{category.name}</span>}
                              {row.contract.status === "cancelled" && (
                                <span className="txn-note">gekündigt</span>
                              )}
                            </td>
                            <td className="txn-td txn-td--amount">
                              {formatMinorUnitsAsEuro(row.monthly)}
                            </td>
                            <td className="txn-td txn-td--amount">
                              {formatMinorUnitsAsEuro(row.yearly)}
                            </td>
                            <td className="txn-td">
                              <div className="scenario-action">
                                <select
                                  className="form-input"
                                  aria-label={`Vorhaben für ${row.contract.name}`}
                                  disabled={!canEdit}
                                  value={entry?.action ?? "none"}
                                  onChange={(event) =>
                                    setAction(
                                      row.contract.id,
                                      event.target.value as ContractScenarioAction | "none",
                                    )
                                  }
                                >
                                  <option value="none">Behalten</option>
                                  <option value="cancel">{ACTION_LABEL.cancel}</option>
                                  <option value="reduce">{ACTION_LABEL.reduce}</option>
                                </select>
                                {entry?.action === "reduce" && (
                                  <input
                                    className="form-input"
                                    inputMode="decimal"
                                    aria-label={`Neuer Betrag für ${row.contract.name}`}
                                    disabled={!canEdit}
                                    placeholder={(row.contract.amount_minor / 100)
                                      .toFixed(2)
                                      .replace(".", ",")}
                                    value={entry.amountText}
                                    onChange={(event) =>
                                      setAmount(row.contract.id, event.target.value)
                                    }
                                  />
                                )}
                              </div>
                            </td>
                            <td
                              className={`txn-td txn-td--amount ${
                                saving > 0 ? "transaction-amount--positive" : ""
                              }`}
                            >
                              {saving > 0 ? formatMinorUnitsAsEuro(saving) : "—"}
                            </td>
                          </tr>
                        );
                      })}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {formError && (
        <p role="alert" className="status-error">
          {formError}
        </p>
      )}

      {canEdit && rows.length > 0 && (
        <div className="scenario-action" style={{ marginTop: "var(--space-3)" }}>
          <button
            type="button"
            className="btn-primary"
            onClick={save}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? "Wird gespeichert…" : "Plan speichern"}
          </button>
          {mutation.isSuccess && !formError && (
            <span className="placeholder-note">
              Gespeichert: {formatMinorUnitsAsEuro(mutation.data.monthly_saving_minor)} pro Monat
              geplant.
            </span>
          )}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export function FixedCostsTargetPanel() {
  const { canEdit } = useContractContext();
  const targetQuery = useContractCostTarget();
  const contractsQuery = useContractList();
  const scenariosQuery = useContractScenarios();
  const createScenario = useCreateContractScenario();
  const updateScenario = useUpdateContractScenario();
  const deleteScenario = useDeleteContractScenario();
  const { data: categoryGroups } = useCategoryTree();
  const categoryIndex = useMemo(() => buildCategoryIndex(categoryGroups), [categoryGroups]);

  const scenarios = useMemo(() => scenariosQuery.data ?? [], [scenariosQuery.data]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [panelError, setPanelError] = useState<string | null>(null);

  // Vorauswahl ohne Effekt: die Ableitung aus den geladenen Daten stimmt auch
  // im ersten Renderdurchlauf, ein useEffect zeigte vorher einen Takt lang
  // "kein Plan vorhanden".
  const selected =
    scenarios.find((scenario) => scenario.id === selectedId) ??
    scenarios.find((scenario) => scenario.is_active) ??
    scenarios[0] ??
    null;

  const target = targetQuery.data;

  if (targetQuery.isLoading || !target) {
    return <p className="placeholder-note">Ziel wird geladen…</p>;
  }

  return (
    <>
      <TargetCard target={target} canEdit={canEdit} />

      <div className="card">
        <div className="page-header" style={{ marginBottom: "var(--space-3)" }}>
          <div>
            <h2 className="card-heading">Senkungsplan</h2>
            {/* Seit Stufe 7 ist `household_scenarios` das allgemeine System;
                diese Ansicht bleibt der Spezialfall fuer reine
                Fixkostensenkung. Ohne den Hinweis pflegt man dieselbe
                Kuendigung an zwei Stellen. */}
            <p className="placeholder-note">
              Für Szenarien mit Einnahmen, Einmalzahlungen oder mehreren Änderungen:{" "}
              <Link to="/vorschau?tab=simulation">Vorschau → Was wäre wenn</Link>. Pläne von hier
              stehen dort bereits.
            </p>
            <p className="placeholder-note">
              Verträge durchspielen und sehen, wo die Fixkosten danach stehen.
            </p>
          </div>
        </div>

        <div className="scenario-toolbar">
          {scenarios.length > 0 && (
            <label className="form-field">
              <span className="form-label">Plan</span>
              <select
                className="form-input"
                value={selected?.id ?? ""}
                onChange={(event) => setSelectedId(event.target.value)}
              >
                {scenarios.map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>
                    {scenario.name}
                    {scenario.is_active ? " (aktiv)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}

          {canEdit && selected && !selected.is_active && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setPanelError(null);
                updateScenario.mutate(
                  { scenarioId: selected.id, input: { is_active: true } },
                  {
                    onError: (error) =>
                      setPanelError(errorText(error, "Der Plan konnte nicht aktiviert werden.")),
                  },
                );
              }}
            >
              Als aktiven Plan setzen
            </button>
          )}

          <span className="scenario-toolbar__spacer" />

          {canEdit && (
            <div className="scenario-action">
              <input
                className="form-input"
                placeholder="Name des neuen Plans"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
              />
              <button
                type="button"
                className="btn-secondary"
                disabled={!newName.trim() || createScenario.isPending}
                onClick={() => {
                  setPanelError(null);
                  createScenario.mutate(
                    { name: newName.trim(), is_active: scenarios.length === 0, items: [] },
                    {
                      onSuccess: (scenario) => {
                        setSelectedId(scenario.id);
                        setNewName("");
                      },
                      onError: (error) =>
                        setPanelError(errorText(error, "Der Plan konnte nicht angelegt werden.")),
                    },
                  );
                }}
              >
                Plan anlegen
              </button>
              {selected && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => {
                    setPanelError(null);
                    deleteScenario.mutate(selected.id, {
                      onSuccess: () => setSelectedId(null),
                      onError: (error) =>
                        setPanelError(errorText(error, "Der Plan konnte nicht gelöscht werden.")),
                    });
                  }}
                >
                  Plan löschen
                </button>
              )}
            </div>
          )}
        </div>

        {panelError && (
          <p role="alert" className="status-error">
            {panelError}
          </p>
        )}

        {scenarios.length === 0 && (
          <p className="placeholder-note">
            Noch kein Plan angelegt. Ein Plan sammelt die Verträge, die gekündigt oder gesenkt
            werden sollen, und rechnet vor, wo die Fixkosten danach stehen.
          </p>
        )}

        {selected && (
          <ScenarioEditor
            key={selected.id}
            scenario={selected}
            contracts={contractsQuery.data ?? []}
            target={target}
            canEdit={canEdit}
            categoryIndex={categoryIndex}
          />
        )}
      </div>
    </>
  );
}
