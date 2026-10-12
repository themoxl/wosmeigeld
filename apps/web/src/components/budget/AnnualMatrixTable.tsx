import { useMemo, useState } from "react";
import { monthNameLong, monthNameShort } from "../../lib/dates";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { AnnualMatrixResponse, AnnualMatrixRow } from "../../lib/types";
import "./AnnualMatrixTable.css";

// ---------------------------------------------------------------------------
// Oeffentliche Schnittstelle
// ---------------------------------------------------------------------------

/** Argumente des Zell-Klicks - deckungsgleich mit dem Drilldown der Budgetseite. */
export interface AnnualMatrixDrilldownArgs {
  categoryId: string;
  categoryName: string;
  /** Kalendermonat 1..12 (nicht nullbasiert). */
  month: number;
}

export interface AnnualMatrixTableProps {
  /** Serverantwort von GET /v1/budgets/{year}/matrix. */
  matrix: AnnualMatrixResponse;
  /** Jahr der Matrix - steuert die Hervorhebung des laufenden Monats. */
  year: number;
  /**
   * Auch Kategorien ohne jede Jahresbewegung zeigen. Der Schalter selbst bleibt
   * auf der Seite (er gilt dort auch fuer das Monatsbudget), die Filterregel
   * lebt hier: {@link hasAnnualActivity}.
   */
  showEmpty: boolean;
  /** Klick/Enter auf eine Zelle mit gebuchtem Ist. Zellen ohne Ist sind inaktiv. */
  onDrilldown: (args: AnnualMatrixDrilldownArgs) => void;
}

/** Ist / Plan / Abweichung - was in den Monatszellen steht. */
export type AnnualMatrixMode = "actual" | "planned" | "deviation";

/**
 * Eine Kategoriezeile hat im Jahr ueberhaupt etwas zu zeigen. Einzige Wahrheit
 * fuer den "ohne Bewegung"-Filter: die Budgetseite zaehlt damit auch die Zahl
 * der ausgeblendeten Zeilen, deshalb liegt die Regel hier und nicht dort.
 */
// Bewusst neben der Komponente (wie AuthProvider/HouseholdProvider): eine
// eigene Datei fuer zwei Zeilen Filterregel wuerde die Wahrheit weiter vom
// Ort ihrer Anwendung entfernen. Kostet nur Hot-Reload-Komfort.
// eslint-disable-next-line react-refresh/only-export-components
export function hasAnnualActivity(row: AnnualMatrixRow): boolean {
  return (
    row.total_planned_minor !== 0 ||
    row.total_spent_minor !== 0 ||
    row.total_income_actual_minor !== 0
  );
}

// ---------------------------------------------------------------------------
// Kompaktformat
// ---------------------------------------------------------------------------

// Bewusst KEIN zweites Geldformat neben money.ts: formatMinorUnitsAsEuro
// bleibt die einzige Anzeige eines Betrags. Zwoelf Monatsspalten nebeneinander
// brauchen aber eine Rasterbreite, in die "1.234,56 EUR" nicht passt - die
// Matrix ist ein Vergleichsraster, kein Kontoauszug. Deshalb hier eine
// bewusste Verkuerzung auf ganze Euro ohne Waehrungszeichen; der exakte Betrag
// steht unverkuerzt (aus money.ts) im title und im aria-label jeder Zelle.
const COMPACT_NUMBER = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

/**
 * Minor Units -> kompakte Rasterzahl: "115000" -> "1.150", "21526" -> "215".
 *
 * @param minorUnits Betrag in Cent.
 * @param signed     Im Abweichungsmodus traegt das Vorzeichen die Aussage,
 *                   deshalb wird es dort auch bei positiven Werten gesetzt.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function formatCompactMinorUnits(minorUnits: number, signed = false): string {
  if (minorUnits === 0) return signed ? "±0" : "0";
  // Unter 50 Cent rundet die Anzeige auf 0 - eine "0" wuerde eine leere Zelle
  // vortaeuschen, obwohl gebucht wurde.
  if (Math.abs(minorUnits) < 50) {
    if (!signed) return "<1";
    return minorUnits > 0 ? "+<1" : "-<1";
  }
  const rounded = Math.round(minorUnits / 100);
  const body = COMPACT_NUMBER.format(Math.abs(rounded));
  if (rounded < 0) return `-${body}`;
  return signed ? `+${body}` : body;
}

// ---------------------------------------------------------------------------
// Modell
// ---------------------------------------------------------------------------

const MONTHS: number[] = Array.from({ length: 12 }, (_, index) => index + 1);
const MONTH_LABELS: string[] = MONTHS.map((month) => monthNameShort(month - 1));
const EMPTY_CELL = "–";
/** Zahl der Heatmap-Stufen; die Deckkraft je Stufe steht im CSS. */
const HEAT_STEPS = 5;

/**
 * Farbrolle einer Zelle. expense/income tragen im Ist- und Planmodus die
 * Herkunft (warm = Ausgabe, kuehl = Einnahme), favorable/adverse im
 * Abweichungsmodus die Richtung. Dass sich die Farbtoene paarweise decken, ist
 * gewollt - die Bedeutung steht im Umschalter darueber.
 */
type CellTone = "expense" | "income" | "favorable" | "adverse" | "neutral";

interface MatrixCell {
  month: number;
  value: number;
  empty: boolean;
  /** Es gibt gebuchte Bewegungen - nur dann ist ein Drilldown sinnvoll. */
  clickable: boolean;
  tone: CellTone;
  /** 0 = kein Hintergrund, 1..HEAT_STEPS = Intensitaetsstufe. */
  heat: number;
  title: string;
  isCurrent: boolean;
  isFuture: boolean;
}

interface MatrixRowModel {
  key: string;
  /** Nur Kategoriezeilen sind anklickbar; Gruppensummen haben keine Id. */
  categoryId: string | null;
  label: string;
  isIncome: boolean;
  tone: CellTone;
  cells: MatrixCell[];
  values: number[];
  total: number;
  /** Zeilenmaximum der Betraege - Bezugsgroesse fuer Heatmap und Sparkline. */
  peak: number;
}

interface MatrixGroupModel {
  groupId: string;
  groupName: string;
  summary: MatrixRowModel;
  rows: MatrixRowModel[];
}

interface MatrixFooterRow {
  /** Der Saldo faerbt sich nach Vorzeichen, die beiden Summenzeilen nicht. */
  kind: "expense" | "income" | "balance";
  label: string;
  values: number[];
  total: number;
}

interface MatrixModel {
  groups: MatrixGroupModel[];
  footer: MatrixFooterRow[];
  /** 0 = die Matrix zeigt nicht das laufende Jahr. */
  currentMonth: number;
  /** Ab diesem Monat liegt die Spalte in der Zukunft; 13 = keine. */
  futureFrom: number;
}

/** Rohwerte einer Kategoriezeile, aus denen jeder Modus abgeleitet wird. */
interface RawRow {
  row: AnnualMatrixRow;
  isIncome: boolean;
  actual: number[];
  planned: number[];
}

function readRawRow(row: AnnualMatrixRow): RawRow {
  // Die Trennung ist der Kern des Budgetkerns: bei Einnahmekategorien steht
  // das Ist in income_actual_minor, spent_minor ist dort immer 0. Wer beides
  // zusammenwirft, verbucht das Gehalt als Ausgabe.
  const isIncome = row.default_type === "income";
  const actual = new Array<number>(12).fill(0);
  const planned = new Array<number>(12).fill(0);
  for (const month of row.months) {
    const index = month.month - 1;
    if (index < 0 || index > 11) continue;
    actual[index] = isIncome ? month.income_actual_minor : month.spent_minor;
    planned[index] = month.planned_minor;
  }
  return { row, isIncome, actual, planned };
}

/** Indexzugriff auf die 12er-Reihen (tsconfig: noUncheckedIndexedAccess). */
function at(values: number[], index: number): number {
  return values[index] ?? 0;
}

function modeValue(
  raw: Pick<RawRow, "actual" | "planned">,
  mode: AnnualMatrixMode,
  index: number,
): number {
  if (mode === "actual") return at(raw.actual, index);
  if (mode === "planned") return at(raw.planned, index);
  return at(raw.actual, index) - at(raw.planned, index);
}

function toneFor(
  mode: AnnualMatrixMode,
  value: number,
  isIncome: boolean,
  mixed: boolean,
): CellTone {
  if (mixed) return "neutral";
  if (mode !== "deviation") return isIncome ? "income" : "expense";
  if (value === 0) return "neutral";
  // Mehr Einnahme als geplant ist gut, mehr Ausgabe als geplant ist schlecht -
  // dieselbe Zahl kippt je nach Kategorieart die Farbe.
  const favorable = isIncome ? value > 0 : value < 0;
  return favorable ? "favorable" : "adverse";
}

/** Betrag -> Heatstufe, Skala relativ zum Zeilenmaximum (nicht zur Tabelle). */
function heatStep(value: number, peak: number): number {
  if (value === 0 || peak === 0) return 0;
  const share = Math.abs(value) / peak;
  return Math.min(HEAT_STEPS, Math.max(1, Math.ceil(share * HEAT_STEPS)));
}

function signedEuro(minorUnits: number): string {
  const text = formatMinorUnitsAsEuro(minorUnits);
  return minorUnits > 0 ? `+${text}` : text;
}

function cellTitle(
  label: string,
  year: number,
  month: number,
  actual: number,
  planned: number,
): string {
  return (
    `${label} · ${monthNameLong(month - 1)} ${year} — ` +
    `Ist ${formatMinorUnitsAsEuro(actual)} · Plan ${formatMinorUnitsAsEuro(planned)} · ` +
    `Abweichung ${signedEuro(actual - planned)}`
  );
}

function buildRowModel(args: {
  key: string;
  categoryId: string | null;
  label: string;
  isIncome: boolean;
  mixed: boolean;
  raw: Pick<RawRow, "actual" | "planned">;
  mode: AnnualMatrixMode;
  year: number;
  currentMonth: number;
  futureFrom: number;
}): MatrixRowModel {
  const { key, categoryId, label, isIncome, mixed, raw, mode, year } = args;
  const values = MONTHS.map((_, index) => modeValue(raw, mode, index));
  const peak = values.reduce((max, value) => Math.max(max, Math.abs(value)), 0);

  const cells = MONTHS.map((month, index) => {
    const value = at(values, index);
    const actual = at(raw.actual, index);
    const planned = at(raw.planned, index);
    const clickable = categoryId !== null && actual !== 0;
    // Im Abweichungsmodus ist "0" eine Aussage ("auf Plan") - leer bleibt die
    // Zelle dort nur, wenn weder gebucht noch geplant wurde.
    const empty = mode === "deviation" ? actual === 0 && planned === 0 : value === 0;
    return {
      month,
      value,
      empty,
      clickable,
      tone: toneFor(mode, value, isIncome, mixed),
      heat: empty ? 0 : heatStep(value, peak),
      title: empty ? "" : cellTitle(label, year, month, actual, planned),
      isCurrent: month === args.currentMonth,
      isFuture: month >= args.futureFrom,
    } satisfies MatrixCell;
  });

  return {
    key,
    categoryId,
    label,
    isIncome,
    // Zeilenton fuer die Sparkline: die Herkunft der Zeile, nicht der Modus -
    // im Abweichungsmodus faerbt die Sparkline ohnehin je Balken.
    tone: mixed ? "neutral" : isIncome ? "income" : "expense",
    cells,
    values,
    total: values.reduce((sum, value) => sum + value, 0),
    peak,
  };
}

function buildModel(
  matrix: AnnualMatrixResponse,
  year: number,
  showEmpty: boolean,
  mode: AnnualMatrixMode,
): MatrixModel {
  const today = new Date();
  const currentMonth = year === today.getFullYear() ? today.getMonth() + 1 : 0;
  // Ab welchem Monat die Spalte in der Zukunft liegt: im laufenden Jahr ab dem
  // Folgemonat, in kuenftigen Jahren ab Januar, in vergangenen nie (13).
  const futureFrom =
    year > today.getFullYear() ? 1 : year === today.getFullYear() ? currentMonth + 1 : 13;

  const visible = showEmpty ? matrix.rows : matrix.rows.filter(hasAnnualActivity);

  // Die Zeilen kommen bereits nach (group_sort_order, category_sort_order)
  // sortiert vom Backend - die Map haelt genau diese Reihenfolge fest.
  const buckets = new Map<string, { name: string; rows: RawRow[] }>();
  for (const row of visible) {
    let bucket = buckets.get(row.group_id);
    if (!bucket) {
      bucket = { name: row.group_name, rows: [] };
      buckets.set(row.group_id, bucket);
    }
    bucket.rows.push(readRawRow(row));
  }

  const groups: MatrixGroupModel[] = [];
  for (const [groupId, bucket] of buckets) {
    const mixed =
      bucket.rows.some((entry) => entry.isIncome) && bucket.rows.some((entry) => !entry.isIncome);
    const groupIsIncome = bucket.rows.every((entry) => entry.isIncome);

    // Die Gruppenzeile ist die Spaltensumme genau der Zeilen darunter - auch in
    // einer gemischten Gruppe. Sie bekommt dann den neutralen Ton, weil eine
    // Summe aus Einnahme und Ausgabe keine Richtung hat.
    const groupRaw = {
      actual: new Array<number>(12).fill(0),
      planned: new Array<number>(12).fill(0),
    };
    for (const entry of bucket.rows) {
      for (let index = 0; index < 12; index += 1) {
        groupRaw.actual[index] = at(groupRaw.actual, index) + at(entry.actual, index);
        groupRaw.planned[index] = at(groupRaw.planned, index) + at(entry.planned, index);
      }
    }

    groups.push({
      groupId,
      groupName: bucket.name,
      summary: buildRowModel({
        key: `group:${groupId}`,
        categoryId: null,
        label: bucket.name,
        isIncome: groupIsIncome,
        mixed,
        raw: groupRaw,
        mode,
        year,
        currentMonth,
        futureFrom,
      }),
      rows: bucket.rows.map((entry) =>
        buildRowModel({
          key: entry.row.category_id,
          categoryId: entry.row.category_id,
          label: entry.row.category_name,
          isIncome: entry.isIncome,
          mixed: false,
          raw: entry,
          mode,
          year,
          currentMonth,
          futureFrom,
        }),
      ),
    });
  }

  // Fusszeile ueber alle gefilterten Kategoriezeilen - nicht ueber die
  // Gruppensummen (das waere doppelt) und bewusst unabhaengig davon, ob eine
  // Gruppe gerade eingeklappt ist: Einklappen ist Ansicht, keine Auswertung.
  const expense = new Array<number>(12).fill(0);
  const income = new Array<number>(12).fill(0);
  for (const group of groups) {
    for (const row of group.rows) {
      const target = row.isIncome ? income : expense;
      for (let index = 0; index < 12; index += 1) {
        target[index] = at(target, index) + at(row.values, index);
      }
    }
  }
  const balance = MONTHS.map((_, index) => at(income, index) - at(expense, index));
  const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);
  const suffix = mode === "actual" ? " (Ist)" : mode === "planned" ? " (Plan)" : " (Ist − Plan)";

  return {
    groups,
    footer: [
      { kind: "expense", label: `Ausgaben${suffix}`, values: expense, total: sum(expense) },
      { kind: "income", label: `Einnahmen${suffix}`, values: income, total: sum(income) },
      { kind: "balance", label: `Saldo${suffix}`, values: balance, total: sum(balance) },
    ],
    currentMonth,
    futureFrom,
  };
}

// ---------------------------------------------------------------------------
// Sparkline
// ---------------------------------------------------------------------------

const SPARK_WIDTH = 64;
const SPARK_HEIGHT = 18;
const SPARK_SLOT = SPARK_WIDTH / 12;
const SPARK_BAR = 3.6;

/**
 * Zwoelf Balken statt einer Linie: Monatswerte sind Einzelbetraege, keine
 * stetige Groesse - eine Linie wuerde zwischen zwei Monaten interpolieren, was
 * es hier nicht gibt. Rein dekorativ (aria-hidden), der Wert steht daneben in
 * der Gesamtspalte.
 */
function Sparkline({
  row,
  diverging,
  currentMonth,
}: {
  row: MatrixRowModel;
  diverging: boolean;
  currentMonth: number;
}) {
  if (row.peak === 0) return null;
  const baseline = diverging ? SPARK_HEIGHT / 2 : SPARK_HEIGHT - 1;
  const span = diverging ? SPARK_HEIGHT / 2 - 1 : SPARK_HEIGHT - 2;

  return (
    <svg
      className="budget-matrix__spark"
      viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
      width={SPARK_WIDTH}
      height={SPARK_HEIGHT}
      aria-hidden="true"
      focusable="false"
    >
      {diverging && (
        <line
          className="budget-matrix__spark-zero"
          x1="0"
          y1={baseline}
          x2={SPARK_WIDTH}
          y2={baseline}
        />
      )}
      {row.values.map((value, index) => {
        if (value === 0) return null;
        const height = Math.max(1.5, (Math.abs(value) / row.peak) * span);
        const up = diverging ? value > 0 : true;
        const y = up ? baseline - height : baseline;
        const favorable = row.isIncome ? value > 0 : value < 0;
        const tone = diverging ? (favorable ? "favorable" : "adverse") : row.tone;
        return (
          <rect
            key={index}
            x={index * SPARK_SLOT + (SPARK_SLOT - SPARK_BAR) / 2}
            y={y}
            width={SPARK_BAR}
            height={height}
            rx="1"
            data-tone={tone}
            data-current={index + 1 === currentMonth ? "true" : undefined}
          />
        );
      })}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Komponente
// ---------------------------------------------------------------------------

const MODE_LABELS: { value: AnnualMatrixMode; label: string; hint: string }[] = [
  { value: "actual", label: "Ist", hint: "Tatsächlich gebuchte Beträge je Monat." },
  { value: "planned", label: "Plan", hint: "Hinterlegte Planbeträge je Monat." },
  {
    value: "deviation",
    label: "Abweichung",
    hint: "Ist minus Plan. Grün heißt im Rahmen, rot heißt daneben.",
  },
];

export function AnnualMatrixTable({
  matrix,
  year,
  showEmpty,
  onDrilldown,
}: AnnualMatrixTableProps) {
  const [mode, setMode] = useState<AnnualMatrixMode>("actual");
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set<string>());

  // Ein einziger Durchlauf fuer Gruppensummen, Spaltensummen und
  // Zeilenmaxima. Ein- und Ausklappen aendert daran nichts und rechnet deshalb
  // auch nicht neu.
  const model = useMemo(
    () => buildModel(matrix, year, showEmpty, mode),
    [matrix, year, showEmpty, mode],
  );

  const allCollapsed =
    model.groups.length > 0 && model.groups.every((g) => collapsed.has(g.groupId));

  function toggleGroup(groupId: string) {
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }

  function toggleAll() {
    setCollapsed(allCollapsed ? new Set<string>() : new Set(model.groups.map((g) => g.groupId)));
  }

  const diverging = mode === "deviation";
  const signed = diverging;

  if (model.groups.length === 0) {
    return (
      <p className="placeholder-note">
        Für {year} gibt es weder Planbeträge noch Buchungen. Setze den Haken oben, um alle
        Kategorien einzublenden.
      </p>
    );
  }

  return (
    <section className="budget-matrix" aria-label={`Jahresmatrix ${year}`}>
      <div className="budget-matrix__toolbar">
        <div className="direction-toggle" role="group" aria-label="Angezeigter Wert">
          {MODE_LABELS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`direction-btn ${mode === option.value ? "direction-btn--active" : ""}`}
              aria-pressed={mode === option.value}
              title={option.hint}
              data-testid={`budget-matrix-mode-${option.value}`}
              onClick={() => setMode(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>

        <p className="budget-matrix__legend" aria-hidden="true">
          <span className="budget-matrix__legend-label">{diverging ? "unter Plan" : "wenig"}</span>
          {diverging ? (
            <>
              <span className="budget-matrix__swatch" data-tone="favorable" data-heat="4" />
              <span className="budget-matrix__swatch" data-tone="favorable" data-heat="2" />
              <span className="budget-matrix__swatch" data-tone="neutral" data-heat="0" />
              <span className="budget-matrix__swatch" data-tone="adverse" data-heat="2" />
              <span className="budget-matrix__swatch" data-tone="adverse" data-heat="4" />
            </>
          ) : (
            [1, 2, 3, 4, 5].map((step) => (
              <span
                key={step}
                className="budget-matrix__swatch"
                data-tone="expense"
                data-heat={step}
              />
            ))
          )}
          <span className="budget-matrix__legend-label">
            {diverging ? "über Plan" : "viel · Skala je Zeile"}
          </span>
        </p>

        <button
          type="button"
          className="btn-secondary budget-matrix__collapse-all"
          onClick={toggleAll}
        >
          {allCollapsed ? "Alle Gruppen aufklappen" : "Alle Gruppen einklappen"}
        </button>
      </div>

      <div className="budget-matrix__shell">
        <div
          className="budget-matrix__scroll"
          tabIndex={0}
          role="region"
          aria-label={`Jahresmatrix ${year}, seitlich scrollbar`}
        >
          <table className="budget-matrix__table" data-testid="budget-matrix">
            <caption className="budget-matrix__sr">
              Jahresmatrix {year}: {MODE_LABELS.find((option) => option.value === mode)?.label} je
              Kategorie und Monat, Beträge auf ganze Euro gerundet.
            </caption>
            <thead>
              <tr>
                <th className="budget-matrix__label budget-matrix__label--head" scope="col">
                  Kategorie
                </th>
                {MONTHS.map((month, index) => (
                  <th
                    key={month}
                    className="budget-matrix__month"
                    scope="col"
                    data-current={month === model.currentMonth ? "true" : undefined}
                    data-future={month >= model.futureFrom ? "true" : undefined}
                  >
                    <span>{MONTH_LABELS[index]}</span>
                    {month === model.currentMonth && <small>laufend</small>}
                  </th>
                ))}
                <th className="budget-matrix__trend" scope="col">
                  Verlauf
                </th>
                <th className="budget-matrix__total budget-matrix__total--head" scope="col">
                  Gesamt
                </th>
              </tr>
            </thead>

            {model.groups.map((group) => {
              const isCollapsed = collapsed.has(group.groupId);
              return (
                <tbody key={group.groupId} className="budget-matrix__group">
                  <tr className="budget-matrix__row budget-matrix__row--group">
                    <th className="budget-matrix__label" scope="row">
                      <button
                        type="button"
                        className="budget-matrix__group-toggle"
                        aria-expanded={!isCollapsed}
                        data-testid={`budget-matrix-group-${group.groupId}`}
                        onClick={() => toggleGroup(group.groupId)}
                      >
                        <span className="budget-matrix__caret" aria-hidden="true">
                          {isCollapsed ? "▸" : "▾"}
                        </span>
                        <span className="budget-matrix__group-name">{group.groupName}</span>
                        <span className="budget-matrix__group-count">
                          {group.rows.length}
                          <span className="budget-matrix__sr">
                            {group.rows.length === 1 ? " Kategorie" : " Kategorien"}
                          </span>
                        </span>
                      </button>
                    </th>
                    {group.summary.cells.map((cell) => (
                      <MatrixValueCell key={cell.month} cell={cell} signed={signed} />
                    ))}
                    <td className="budget-matrix__trend">
                      <Sparkline
                        row={group.summary}
                        diverging={diverging}
                        currentMonth={model.currentMonth}
                      />
                    </td>
                    <td
                      className="budget-matrix__total"
                      title={formatMinorUnitsAsEuro(group.summary.total)}
                    >
                      {formatCompactMinorUnits(group.summary.total, signed)}
                    </td>
                  </tr>

                  {!isCollapsed &&
                    group.rows.map((row) => (
                      <tr key={row.key} className="budget-matrix__row">
                        <th className="budget-matrix__label" scope="row">
                          <span className="budget-matrix__category">{row.label}</span>
                          {row.isIncome && (
                            <span className="status-badge status-badge--positive">Einnahme</span>
                          )}
                        </th>
                        {row.cells.map((cell) => (
                          <MatrixValueCell
                            key={cell.month}
                            cell={cell}
                            signed={signed}
                            categoryName={row.label}
                            onActivate={() => {
                              if (!row.categoryId) return;
                              onDrilldown({
                                categoryId: row.categoryId,
                                categoryName: row.label,
                                month: cell.month,
                              });
                            }}
                            testId={`budget-matrix-cell-${row.categoryId}-${cell.month}`}
                          />
                        ))}
                        <td className="budget-matrix__trend">
                          <Sparkline
                            row={row}
                            diverging={diverging}
                            currentMonth={model.currentMonth}
                          />
                        </td>
                        <td
                          className="budget-matrix__total"
                          title={formatMinorUnitsAsEuro(row.total)}
                        >
                          {formatCompactMinorUnits(row.total, signed)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              );
            })}

            <tfoot>
              {model.footer.map((footerRow) => {
                const isBalance = footerRow.kind === "balance";
                return (
                  <tr
                    key={footerRow.kind}
                    className={`budget-matrix__row budget-matrix__row--footer ${
                      isBalance ? "budget-matrix__row--balance" : ""
                    }`}
                    data-testid={`budget-matrix-footer-${footerRow.kind}`}
                  >
                    <th className="budget-matrix__label" scope="row">
                      {footerRow.label}
                    </th>
                    {footerRow.values.map((value, index) => (
                      <td
                        key={index + 1}
                        className={`budget-matrix__cell budget-matrix__cell--footer ${footerSign(value, isBalance)}`}
                        data-current={index + 1 === model.currentMonth ? "true" : undefined}
                        title={formatMinorUnitsAsEuro(value)}
                      >
                        <span className="budget-matrix__value budget-matrix__value--static">
                          {value === 0 ? EMPTY_CELL : formatCompactMinorUnits(value, signed)}
                        </span>
                      </td>
                    ))}
                    <td className="budget-matrix__trend" />
                    <td
                      className={`budget-matrix__total ${footerSign(footerRow.total, isBalance)}`}
                      title={formatMinorUnitsAsEuro(footerRow.total)}
                    >
                      {formatCompactMinorUnits(footerRow.total, signed)}
                    </td>
                  </tr>
                );
              })}
            </tfoot>
          </table>
        </div>
      </div>
    </section>
  );
}

/** Nur der Saldo traegt eine Vorzeichenfarbe - Summenzeilen bleiben ruhig. */
function footerSign(value: number, isBalance: boolean): string {
  if (!isBalance || value === 0) return "";
  return value > 0 ? "transaction-amount--positive" : "transaction-amount--negative";
}

/**
 * Eine Monatszelle. Anklickbare Zellen bekommen einen echten <button> statt
 * role="button" am <td>: so bleibt die Tabellensemantik fuer Screenreader
 * erhalten, Enter/Leertaste und der Fokusring kommen vom Browser. Das
 * aria-label wiederholt den Betrag, weil es den Zelltext sonst verdeckt.
 */
function MatrixValueCell({
  cell,
  signed,
  categoryName,
  onActivate,
  testId,
}: {
  cell: MatrixCell;
  signed: boolean;
  categoryName?: string;
  onActivate?: () => void;
  testId?: string;
}) {
  const text = cell.empty ? EMPTY_CELL : formatCompactMinorUnits(cell.value, signed);
  const interactive = cell.clickable && onActivate !== undefined && !cell.empty;

  return (
    <td
      className="budget-matrix__cell"
      data-tone={cell.tone}
      data-heat={cell.heat}
      data-current={cell.isCurrent ? "true" : undefined}
      data-future={cell.isFuture ? "true" : undefined}
      title={cell.title || undefined}
      data-testid={testId}
    >
      {interactive ? (
        <button
          type="button"
          className="budget-matrix__value budget-matrix__value--action"
          aria-label={`${categoryName} im ${monthNameLong(cell.month - 1)} anzeigen, ${formatMinorUnitsAsEuro(cell.value)}`}
          onClick={onActivate}
        >
          {text}
        </button>
      ) : (
        <span className="budget-matrix__value budget-matrix__value--static">{text}</span>
      )}
    </td>
  );
}
