import { Fragment, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Modal } from "../components/Modal";
import { AiReasonNotice } from "../components/AiReasonNotice";
import { CategoryDot } from "../components/CategoryDot";
import { CategoryPicker } from "../components/CategoryPicker";
import { SortableTableHeader } from "../components/SortableTableHeader";
import { RowMenu } from "../components/RowMenu";
import { AmountLookupPanel } from "../components/contracts/AmountLookupPanel";
import { FixedCostsTargetPanel } from "../components/contracts/FixedCostsTargetPanel";
import { IncomeOverview } from "../components/contracts/IncomeOverview";
import { usePeople } from "../hooks/usePeople";
import { ContractAmountHistoryPanel } from "../components/contracts/ContractAmountHistoryPanel";
import { TabPanel, Tabs, type TabDefinition } from "../components/Tabs";
import { useFormatDate } from "../hooks/useProfile";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";
import { apiErrorDetail } from "../lib/api";
import { aiReasonCodeIn } from "../lib/aiReasons";
import type {
  CashflowGranularity,
  Contract,
  ContractAICrosscheckItem,
  ContractAICrosscheckResponse,
  ContractDirection,
  ContractSettings,
  ContractFrequency,
  ContractMergeResult,
  ContractOccurrence,
  ContractProviderSuggestion,
  ContractRescanResult,
  ContractStatus,
  ContractTermDeadline,
  ContractTermSoon,
  ContractCategorySummary,
  ContractType,
  ContractUpdateInput,
} from "../lib/types";
import {
  buildCategoryIndex,
  UNCATEGORIZED_LABEL,
  type ResolvedCategory,
} from "../lib/categoryTypes";
import { useCategoryTree } from "../hooks/useCategoryCatalog";
import {
  CONTRACT_TYPE_LABEL as TYPE_LABEL,
  newRequestId,
  useAddContractAmount,
  useApplyContractMerge,
  useContractContext,
  useContractDuplicateCheck,
  useContractDuplicateSuggestions,
  useContractCashflow,
  useContractEvents,
  useContractList,
  useContractMergePreview,
  useContractMergeUndoPreview,
  useContractOccurrences,
  useContractProviderSuggestions,
  useContractSettings,
  useContractSummary,
  useCreateContract,
  useDeleteContract,
  useDismissContractDuplicate,
  useRescanContracts,
  useSetContractStatus,
  useUndoContractMerge,
  useUpdateContract,
  useUpdateContractSettings,
} from "../hooks/useContracts";
import { isAiTaskWorking, useAiTask, useEnqueueAiTask } from "../hooks/useAiTaskQueue";

/**
 * Vertragsverwaltung (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Phase 1).
 *
 * Loest RecurringPage ab. Der wesentliche Unterschied ist nicht die Optik,
 * sondern dass jede Zeile bearbeitbar, beendbar, zusammenfuehrbar und loeschbar
 * ist - und dass Ausgaben und Einnahmen getrennte Register haben, damit ein
 * Gehaltsvertrag die Fixkostensumme nicht verwaessert.
 */

const FREQUENCY_LABEL: Record<ContractFrequency, string> = {
  weekly: "Wöchentlich",
  monthly: "Monatlich",
  quarterly: "Quartalsweise",
  semiannual: "Halbjährlich",
  yearly: "Jährlich",
};

const STATUS_LABEL: Record<ContractStatus, string> = {
  active: "Aktiv",
  paused: "Pausiert",
  cancelled: "Gekündigt",
  ended: "Beendet",
  not_a_contract: "Kein Vertrag",
};

const STATUS_CLASS: Record<ContractStatus, string> = {
  active: "status-badge--positive",
  paused: "status-badge--neutral",
  cancelled: "status-badge--warning",
  ended: "status-badge--neutral",
  not_a_contract: "status-badge--neutral",
};

const PERIODS_PER_YEAR: Record<ContractFrequency, number> = {
  weekly: 52,
  monthly: 12,
  quarterly: 4,
  semiannual: 2,
  yearly: 1,
};

function isReserveEligible(direction: ContractDirection, frequency: ContractFrequency): boolean {
  return direction === "expense" && ["quarterly", "semiannual", "yearly"].includes(frequency);
}

/**
 * Oberstes Register der Seite.
 *
 * "Ziel" ist bewusst eine eigene Ansicht und keine weitere Karte unter der
 * Vertragsliste: es rechnet gegen dieselbe Fixkostensumme, beschriftet sie aber
 * im Verhaeltnis zu einem Wunschwert. Beides gleichzeitig ergaebe zwei
 * Kennzahlenbloecke uebereinander, die dasselbe Geld verschieden benennen.
 */
type PanelTab = "list" | "cashflow" | "deadlines" | "categories" | "target";

const PANEL_TABS: { key: PanelTab; label: string }[] = [
  { key: "list", label: "Verträge" },
  { key: "cashflow", label: "Cashflow-Prognose" },
  { key: "deadlines", label: "Kündigungsfristen" },
  { key: "categories", label: "Nach Kategorie" },
  { key: "target", label: "Fixkostenziel" },
];

type StatusFilter = "active" | "ending" | "ended" | "all";

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: "active", label: "Aktiv" },
  { key: "ending", label: "Auslaufend" },
  { key: "ended", label: "Beendet" },
  { key: "all", label: "Alle" },
];

/**
 * Sortierung, wahlweise nach Jahreskosten (Plan I11).
 *
 * Der Monatsbetrag ist als Rangfolge irrefuehrend: eine Jahrespraemie ueber
 * 600 EUR steht dort hinter einem Abo fuer 9,99 EUR, kostet aber das Fuenffache.
 * Deshalb ist "Jahreskosten" eine eigene Sortierung und nicht nur eine Spalte.
 */
type SortKey = "due" | "yearly" | "deadline" | "name" | "category";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "due", label: "Nächste Fälligkeit" },
  { key: "yearly", label: "Jahreskosten (teuerste zuerst)" },
  { key: "deadline", label: "Kündigungsfrist" },
  { key: "name", label: "Name" },
  { key: "category", label: "Kategorie" },
];

type DialogState =
  | { kind: "create" }
  | { kind: "edit"; contract: Contract }
  | { kind: "detail"; contract: Contract; initialTab?: DetailTab }
  | { kind: "amount"; contract: Contract }
  | { kind: "delete"; contract: Contract }
  | { kind: "merge"; targetId: string; sourceIds: string[] };

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function monthlyMinor(contract: Contract): number {
  return Math.round((contract.amount_minor * PERIODS_PER_YEAR[contract.frequency]) / 12);
}

function yearlyMinor(contract: Contract): number {
  return Math.round(contract.amount_minor * PERIODS_PER_YEAR[contract.frequency]);
}

/**
 * Was tatsaechlich geflossen ist - gezaehlt, nicht hochgerechnet.
 *
 * `yearlyMinor` oben multipliziert den HEUTIGEN Betrag mit der Periodenzahl und
 * weiss deshalb nichts von Preisaenderungen, Pausen, einem unterjaehrigen
 * Beginn oder einem Ende. Bei einem Vertrag, der von 49,95 auf 39,95 gefallen
 * ist, behauptet sie zwoelf Monate zum neuen Preis. Diese Funktion beantwortet
 * stattdessen die Frage, die man wirklich stellt: was habe ich bezahlt?
 *
 * Bewusst nur `matched`: 'missing' hat keinen Betrag und 'skipped' ist
 * ausdruecklich nichts, was geflossen ist.
 */
function paidSoFar(occurrences: ContractOccurrence[] | undefined): {
  totalMinor: number;
  count: number;
  since: string;
} {
  const matched = (occurrences ?? []).filter(
    (item) => item.status === "matched" && item.amount_minor !== null,
  );
  const dates = matched
    .map((item) => item.booked_date ?? item.due_date)
    .filter((value): value is string => Boolean(value))
    .sort();
  return {
    totalMinor: matched.reduce((sum, item) => sum + Math.abs(item.amount_minor ?? 0), 0),
    count: matched.length,
    since: dates[0] ?? "—",
  };
}

/** Ausgaben werden negativ dargestellt, Einnahmen positiv - unabhaengig davon,
 * dass beide intern positiv gespeichert sind. */
function signedForDisplay(contract: Contract, amountMinor: number): number {
  return contract.direction === "expense" ? -amountMinor : amountMinor;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function positiveAmountForPreview(value: string): number {
  return Math.abs(parseAmountToMinorUnits(value) ?? 0);
}

function firstReserveMonth(startedOn: string): string | null {
  const parts = startedOn.split("-").map(Number);
  if (parts.length !== 3 || parts.some((part) => !Number.isInteger(part))) return null;
  const year = parts[0];
  const month = parts[1];
  if (year === undefined || month === undefined) return null;
  const nextYear = month === 12 ? year + 1 : year;
  const nextMonth = month === 12 ? 1 : month + 1;
  return `${nextYear}-${String(nextMonth).padStart(2, "0")}-01`;
}

function contractCountLabel(count: number): string {
  return count === 1 ? "1 Vertrag" : `${count} Verträge`;
}

/**
 * Ergebnis des Abgleichs als ein Satz.
 *
 * Geprueft und zugeordnet stehen immer da - auch mit Null. "Nichts gefunden"
 * ist die haeufigste und wichtigste Antwort: sie erklaert, warum sich an der
 * Liste gerade nichts geaendert hat. Die drei Sonderfaelle erscheinen nur, wenn
 * sie eingetreten sind, damit die Meldung nicht aus lauter Nullen besteht.
 */
function describeRescanResult(result: ContractRescanResult): string {
  const segments = [
    `${contractCountLabel(result.contracts_examined)} geprüft`,
    `${result.matched_count} Zahlung(en) zugeordnet`,
  ];
  if (result.price_change_count > 0) {
    segments.push(`${result.price_change_count} Preisänderung(en) erkannt`);
  }
  if (result.missing_count > 0) {
    segments.push(`${result.missing_count} Fälligkeit(en) ausgeblieben`);
  }
  if (result.ended_count > 0) {
    segments.push(`${contractCountLabel(result.ended_count)} automatisch beendet`);
  }
  return `${segments.join(" · ")}.`;
}

/** Begruendung fuer das Warn-Badge: seit wann nichts mehr kam. */
function missedPaymentHint(contract: Contract, formatDate: (v: string) => string): string {
  const since = contract.last_seen_date
    ? `Letzte passende Buchung am ${formatDate(contract.last_seen_date)}`
    : "Bisher keine passende Buchung gefunden";
  return `${since}; ${contract.missed_periods} Fälligkeit(en) ohne Zahlung.`;
}

/**
 * Dringlichkeit eines Countdowns.
 *
 * Die Kante liegt bei 30 Tagen, weil dort auch der Server sein erstes
 * 'term_ending'-Ereignis erzeugt (contract_service.scan_term_deadlines) - eine
 * andere Grenze in der Liste hiesse, dass die Zeile ruhig aussieht, waehrend
 * die Benachrichtigung schon raus ist.
 */
function countdownTone(daysLeft: number): "warning" | "highlight" | "neutral" {
  if (daysLeft <= 30) return "warning";
  if (daysLeft <= 60) return "highlight";
  return "neutral";
}

function countdownLabel(daysLeft: number): string {
  if (daysLeft <= 0) return "heute";
  if (daysLeft === 1) return "morgen";
  return `noch ${daysLeft} Tage`;
}

function deadlineTitle(deadline: ContractTermDeadline, formatDate: (v: string) => string): string {
  return `Kündigung muss bis zum ${formatDate(deadline.deadline_on)} beim Anbieter sein, wirksam zum ${formatDate(deadline.term_ends_on)}.`;
}

/**
 * Laufzeit-Spalte: "läuft bis" schlägt die Kündigungsfrist (Plan I4 + Nutzerhinweis).
 *
 * `ends_on` ist eine feste Nutzerangabe - genau wie `compute_term_deadline`
 * auf dem Server (services/api/app/services/contract_service.py) sie ueber die
 * berechnete Frist stellt, sobald sie eingetragen ist. Ohne diesen Vorrang
 * blieb die Spalte fuer Vertraege ohne Mindestlaufzeit/Kuendigungsfrist leer,
 * selbst wenn ein konkretes Enddatum eingetragen war (z. B. direkt
 * "Läuft bis" gesetzt, ohne die Frist-Felder auszufuellen) - der Vertrag
 * wirkte dann so, als wuerde das Enddatum nirgends ankommen, obwohl Budget,
 * Prognose und der naechtliche Abgleich es laengst beruecksichtigen.
 */
function TermDeadlineCell({
  endsOn,
  cancelledOn,
  deadline,
}: {
  endsOn: string | null;
  cancelledOn: string | null;
  deadline: ContractTermDeadline | null | undefined;
}) {
  const { formatDate } = useFormatDate();
  if (endsOn) {
    return (
      <>
        <span className="txn-note">Läuft bis</span>
        <br />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>
          {formatDate(endsOn)}
        </span>
        {cancelledOn && <span className="txn-note">gekündigt am {formatDate(cancelledOn)}</span>}
      </>
    );
  }
  if (!deadline) return <span className="txn-td--empty">—</span>;
  if (deadline.kind === "open_ended") {
    return (
      <span
        className="txn-note"
        title={`Frühestens wirksam zum ${formatDate(deadline.term_ends_on)}.`}
      >
        jederzeit kündbar
      </span>
    );
  }
  return (
    <>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>
        {formatDate(deadline.deadline_on)}
      </span>
      <br />
      <span
        className={`status-badge status-badge--${countdownTone(deadline.days_left)}`}
        title={deadlineTitle(deadline, formatDate)}
      >
        {countdownLabel(deadline.days_left)}
      </span>
    </>
  );
}

/** Ziel einer Zusammenfuehrung ist der Vertrag mit der spaetesten Faelligkeit -
 * sein Betrag ist der aktuell gueltige. */
function mergeTarget(contracts: Contract[], selectedIds: string[]): string | null {
  const candidates = contracts.filter((contract) => selectedIds.includes(contract.id));
  if (candidates.length < 2) return null;
  return candidates.reduce((latest, contract) =>
    contract.next_due_date > latest.next_due_date ? contract : latest,
  ).id;
}

/** Nur `fixed_term` hat eine ablaufende Frist; alles andere sortiert ans Ende. */
function deadlineRank(contract: Contract): number {
  const deadline = contract.term_deadline;
  if (!deadline || deadline.kind !== "fixed_term") return Number.MAX_SAFE_INTEGER;
  return deadline.days_left;
}

function sortContracts(
  contracts: Contract[],
  sortKey: SortKey,
  categoryIndex: Map<string, ResolvedCategory>,
): Contract[] {
  const sorted = [...contracts];
  switch (sortKey) {
    case "yearly":
      // Absteigend: die Frage hinter dieser Sortierung ist "was kostet mich am
      // meisten", nicht "was ist am billigsten".
      return sorted.sort((a, b) => yearlyMinor(b) - yearlyMinor(a));
    case "deadline":
      return sorted.sort((a, b) => deadlineRank(a) - deadlineRank(b));
    case "name":
      return sorted.sort((a, b) => a.name.localeCompare(b.name, "de"));
    case "category":
      // Unkategorisiert faellt ans Ende, statt sich alphabetisch irgendwo
      // zwischen echten Kategorien einzureihen ("Nicht kategorisiert" faengt
      // zufaellig mit N an - das waere Zufall, keine Absicht).
      return sorted.sort((a, b) => {
        const catA = a.category_id ? categoryIndex.get(a.category_id) : undefined;
        const catB = b.category_id ? categoryIndex.get(b.category_id) : undefined;
        if (catA && catB) {
          return catA.path.localeCompare(catB.path, "de") || a.name.localeCompare(b.name, "de");
        }
        if (catA && !catB) return -1;
        if (!catA && catB) return 1;
        return a.name.localeCompare(b.name, "de");
      });
    default:
      return sorted.sort((a, b) => a.next_due_date.localeCompare(b.next_due_date));
  }
}

// ---------------------------------------------------------------------------
// Formular
// ---------------------------------------------------------------------------

interface ContractFormValues {
  name: string;
  providerLabel: string;
  contractType: ContractType;
  direction: ContractDirection;
  amountEuro: string;
  frequency: ContractFrequency;
  nextDueDate: string;
  categoryId: string;
  status: ContractStatus;
  cancelledOn: string;
  endsOn: string;
  cancellationPeriodDays: string;
  minTermMonths: string;
  providerTemplateKey: string;
  note: string;
  reserveEnabled: boolean;
  reserveStartingEuro: string;
  reserveStartedOn: string;
  /** Nur bei direction === "income" (Plan Stufe 6). */
  incomeKind: string;
  personId: string;
}

function toFormValues(contract: Contract | null, direction: ContractDirection): ContractFormValues {
  if (!contract) {
    return {
      name: "",
      providerLabel: "",
      contractType: direction === "income" ? "income" : "other",
      direction,
      amountEuro: "",
      frequency: "monthly",
      nextDueDate: today(),
      categoryId: "",
      status: "active",
      cancelledOn: "",
      endsOn: "",
      cancellationPeriodDays: "",
      minTermMonths: "",
      providerTemplateKey: "",
      note: "",
      reserveEnabled: false,
      reserveStartingEuro: "0,00",
      reserveStartedOn: today(),
      incomeKind: direction === "income" ? "salary" : "",
      personId: "",
    };
  }
  return {
    name: contract.name,
    providerLabel: contract.provider_label ?? "",
    contractType: contract.contract_type,
    direction: contract.direction,
    amountEuro: (contract.amount_minor / 100).toFixed(2).replace(".", ","),
    frequency: contract.frequency,
    nextDueDate: contract.next_due_date,
    categoryId: contract.category_id ?? "",
    status: contract.status,
    cancelledOn: contract.cancelled_on ?? "",
    endsOn: contract.ends_on ?? "",
    cancellationPeriodDays:
      contract.cancellation_period_days === null ? "" : String(contract.cancellation_period_days),
    minTermMonths: contract.min_term_months === null ? "" : String(contract.min_term_months),
    providerTemplateKey: contract.provider_template_key ?? "",
    note: contract.note ?? "",
    incomeKind: contract.income_kind ?? "",
    personId: contract.person_id ?? "",
    reserveEnabled: contract.reserve_enabled,
    reserveStartingEuro: (contract.reserve_starting_amount_minor / 100)
      .toFixed(2)
      .replace(".", ","),
    reserveStartedOn: contract.reserve_started_on ?? today(),
  };
}

/**
 * Register des Formulars selbst - Basis/Laufzeit/Notiz statt einer einzigen
 * langen Seite. Die Anbieter- und Buchungssuche stehen bewusst am ENDE des
 * Basis-Tabs, nicht direkt unter dem Namensfeld: beide sind Hilfsmittel fuer
 * die Felder darueber (Anbieter, Betrag), keine eigenstaendigen Schritte, die
 * vor dem Ausfuellen kommen muessten.
 */
type FormTab = "basis" | "laufzeit" | "notiz";

const FORM_TABS: TabDefinition<FormTab>[] = [
  { key: "basis", label: "Basis" },
  { key: "laufzeit", label: "Laufzeit & Kündigung" },
  { key: "notiz", label: "Notiz" },
];

interface ContractFormProps {
  contract: Contract | null;
  defaultDirection: ContractDirection;
  /** Nach erfolgreichem Speichern. Beim Neuanlegen mit dem frisch erzeugten
   *  Vertrag - der Aufrufer nutzt ihn, um direkt ins Register zu springen, in
   *  dem sich Buchungen zuordnen lassen (das braucht eine Vertrags-ID, die es
   *  vor dem Speichern noch nicht gibt). Beim Bearbeiten ohne Argument. */
  onDone: (created?: Contract) => void;
  onCancel: () => void;
}

/**
 * Das Formular ohne Dialograhmen.
 *
 * Seit der Detaildialog ein Register "Bearbeiten" hat, gibt es zwei Orte fuer
 * dieselben Felder: den Anlegen-Dialog und dieses Register. Zwei Kopien waeren
 * die naechste Stelle, an der ein neues Feld nur an einer von beiden ankommt -
 * deshalb ist der Rahmen aus dem Formular herausgezogen und nicht das Formular
 * verdoppelt.
 */
/** Nutzersprache fuer `contracts.income_kind` (Plan Stufe 6). */
const INCOME_KIND_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "— keine —" },
  { value: "salary", label: "Gehalt" },
  { value: "self_employment", label: "Selbständigkeit" },
  { value: "pension", label: "Rente" },
  { value: "benefit", label: "Staatliche Leistung" },
  { value: "rental", label: "Vermietung" },
  { value: "capital", label: "Kapitalertrag" },
  { value: "support", label: "Unterhalt" },
  { value: "other", label: "Sonstiges" },
];

function ContractForm({ contract, defaultDirection, onDone, onCancel }: ContractFormProps) {
  const people = usePeople().data ?? [];
  const isEdit = contract !== null;
  const { formatDate } = useFormatDate();
  const [values, setValues] = useState<ContractFormValues>(() =>
    toFormValues(contract, defaultDirection),
  );
  const [formError, setFormError] = useState<string | null>(null);
  const [formTab, setFormTab] = useState<FormTab>("basis");
  // Der Anbieterkatalog wird beim Verlassen eines Feldes befragt, nicht bei
  // jedem Tastendruck - derselbe Ausloeser wie bei der Dublettenpruefung.
  const [providerQuery, setProviderQuery] = useState("");
  const { data: categoryGroups } = useCategoryTree();
  const createMutation = useCreateContract();
  const updateMutation = useUpdateContract();
  const duplicateCheck = useContractDuplicateCheck();
  // Beim Bearbeiten bleibt der Katalog stumm: ein Klick wuerde dort gepflegte
  // Angaben ueberschreiben, ohne dass jemand danach gefragt hat.
  const providerSuggestions = useContractProviderSuggestions(isEdit ? "" : providerQuery);

  const set = <K extends keyof ContractFormValues>(key: K, value: ContractFormValues[K]) =>
    setValues((previous) => ({ ...previous, [key]: value }));

  const handleNameBlur = () => {
    const amountMinor = parseAmountToMinorUnits(values.amountEuro);
    if (!values.name.trim()) return;
    duplicateCheck.mutate({
      name: values.name.trim(),
      amount_minor: amountMinor && amountMinor > 0 ? amountMinor : undefined,
      frequency: values.frequency,
      exclude_id: contract?.id,
    });
    setProviderQuery(values.providerLabel.trim() || values.name.trim());
  };

  /**
   * Vorschlag uebernehmen.
   *
   * Nur Felder, zu denen die Vorlage etwas zu sagen hat - `??` statt eines
   * pauschalen Ueberschreibens, sonst loeschte ein Anbieter ohne typische
   * Mindestlaufzeit die vom Nutzer bereits eingetragene.
   */
  const applySuggestion = (suggestion: ContractProviderSuggestion) => {
    setValues((previous) => ({
      ...previous,
      providerLabel: suggestion.provider_name,
      contractType: suggestion.contract_type,
      frequency: suggestion.typical_frequency ?? previous.frequency,
      categoryId: suggestion.category_id ?? previous.categoryId,
      minTermMonths:
        suggestion.typical_min_term_months === null
          ? previous.minTermMonths
          : String(suggestion.typical_min_term_months),
      cancellationPeriodDays:
        suggestion.typical_cancellation_days === null
          ? previous.cancellationPeriodDays
          : String(suggestion.typical_cancellation_days),
      providerTemplateKey: suggestion.semantic_key,
    }));
  };

  /** Betrag aus einer nachgeschlagenen Buchung uebernehmen - dieselbe
   *  Umrechnung wie beim Vorbelegen aus einem bestehenden Vertrag
   *  (toFormValues oben). */
  const applyLookedUpAmount = (amountMinor: number) => {
    set("amountEuro", (amountMinor / 100).toFixed(2).replace(".", ","));
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);

    const amountMinor = parseAmountToMinorUnits(values.amountEuro);
    if (amountMinor === null || amountMinor === 0) {
      setFormError("Bitte einen Betrag ungleich null eintragen.");
      return;
    }
    // Der Betrag wird immer positiv gespeichert; die Richtung steht getrennt.
    const positiveAmount = Math.abs(amountMinor);
    const reserveStartingMinor = parseAmountToMinorUnits(values.reserveStartingEuro);
    if (reserveStartingMinor === null || reserveStartingMinor < 0) {
      setFormError("Bitte eine gültige, nicht negative Fixkosten-Rücklage eintragen.");
      return;
    }
    const reserveEligible = isReserveEligible(values.direction, values.frequency);

    const shared = {
      name: values.name.trim(),
      provider_label: values.providerLabel.trim() || null,
      contract_type: values.contractType,
      direction: values.direction,
      amount_minor: positiveAmount,
      frequency: values.frequency,
      next_due_date: values.nextDueDate,
      category_id: values.categoryId || null,
      status: values.status,
      cancelled_on: values.cancelledOn || null,
      ends_on: values.endsOn || null,
      cancellation_period_days: values.cancellationPeriodDays
        ? Number(values.cancellationPeriodDays)
        : null,
      min_term_months: values.minTermMonths ? Number(values.minTermMonths) : null,
      provider_template_key: values.providerTemplateKey || null,
      note: values.note.trim() || null,
      reserve_enabled: reserveEligible && values.reserveEnabled,
      reserve_started_on: reserveEligible ? values.reserveStartedOn : null,
      reserve_starting_amount_minor: reserveEligible ? reserveStartingMinor : 0,
      // Einnahmeart gibt es nur an Einnahmen - der Server prueft dieselbe
      // Regel noch einmal, hier verhindert sie eine unnoetige Ablehnung.
      income_kind:
        values.direction === "income" && values.incomeKind
          ? (values.incomeKind as NonNullable<Contract["income_kind"]>)
          : null,
      person_id: values.personId || null,
    };

    if (isEdit) {
      // null heisst "nicht angefasst"; ein Feld wirklich zu leeren verlangt
      // seinen Namen in clear_fields.
      const clearFields: NonNullable<ContractUpdateInput["clear_fields"]> = [];
      if (!shared.provider_label) clearFields.push("provider_label");
      if (!shared.category_id) clearFields.push("category_id");
      if (!shared.cancelled_on) clearFields.push("cancelled_on");
      if (!shared.ends_on) clearFields.push("ends_on");
      if (!shared.cancellation_period_days) clearFields.push("cancellation_period_days");
      if (!shared.min_term_months) clearFields.push("min_term_months");
      if (!shared.provider_template_key) clearFields.push("provider_template_key");
      if (!shared.note) clearFields.push("note");

      updateMutation.mutate(
        {
          contractId: contract.id,
          version: contract.version,
          input: { ...shared, clear_fields: clearFields },
        },
        {
          onSuccess: onDone,
          onError: (error) => {
            const detail = apiErrorDetail(error);
            setFormError(
              detail === "version_conflict"
                ? "Der Vertrag wurde zwischenzeitlich geändert. Bitte Ansicht neu laden."
                : errorText(error, "Speichern ist fehlgeschlagen."),
            );
          },
        },
      );
      return;
    }

    createMutation.mutate(shared, {
      onSuccess: (created) => onDone(created),
      onError: (error) => setFormError(errorText(error, "Anlegen ist fehlgeschlagen.")),
    });
  };

  const duplicates = duplicateCheck.data ?? [];
  const suggestions = providerSuggestions.data ?? [];

  return (
    <form onSubmit={handleSubmit} className="contract-form">
      <div className="form-field">
        <label className="form-label" htmlFor="contract-name">
          Name *
        </label>
        <input
          id="contract-name"
          type="text"
          className="form-input"
          value={values.name}
          onChange={(event) => set("name", event.target.value)}
          onBlur={handleNameBlur}
          placeholder="z. B. Musterfunk Festnetz"
          required
        />
      </div>

      {duplicates.length > 0 && (
        <div className="operation-preview" role="status">
          <h3 className="card-heading">Möglicherweise schon vorhanden</h3>
          <ul>
            {duplicates.map((candidate) => (
              <li key={candidate.id}>
                {candidate.name} · {formatMinorUnitsAsEuro(candidate.amount_minor)} ·{" "}
                {FREQUENCY_LABEL[candidate.frequency]}
              </li>
            ))}
          </ul>
          <p className="text-sm">
            Wenn das derselbe Vertrag ist, den bestehenden bearbeiten statt einen zweiten anzulegen
            — sonst zählt er doppelt in der Monatssumme.
          </p>
        </div>
      )}

      <Tabs
        idPrefix="contract-form"
        label="Bereiche des Formulars"
        tabs={FORM_TABS}
        active={formTab}
        onChange={setFormTab}
      />

      <TabPanel idPrefix="contract-form" tabKey="basis" active={formTab}>
        <div className="form-grid-2">
          <div className="form-field">
            <label className="form-label" htmlFor="contract-provider">
              Anbieter
            </label>
            <input
              id="contract-provider"
              type="text"
              className="form-input"
              value={values.providerLabel}
              onChange={(event) => set("providerLabel", event.target.value)}
              // Nur fragen, nie loeschen: der Klick auf einen Vorschlag
              // erzeugt zuerst das blur dieses Feldes. Wuerde ein leeres Feld
              // die Suche zuruecksetzen, verschwaende der Vorschlag genau in
              // dem Moment, in dem er angeklickt wird - der Klick liefe ins
              // Leere.
              onBlur={() => {
                const query = values.providerLabel.trim();
                if (query.length >= 2) setProviderQuery(query);
              }}
              placeholder="z. B. Musterfunk Deutschland GmbH"
            />
            {values.providerTemplateKey && (
              <span className="txn-note">Aus dem Anbieterkatalog übernommen.</span>
            )}
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="contract-type">
              Vertragsart
            </label>
            <select
              id="contract-type"
              className="form-input"
              value={values.contractType}
              onChange={(event) => set("contractType", event.target.value as ContractType)}
            >
              {(Object.keys(TYPE_LABEL) as ContractType[]).map((key) => (
                <option key={key} value={key}>
                  {TYPE_LABEL[key]}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="form-grid-3">
          <div className="form-field">
            <label className="form-label" htmlFor="contract-direction">
              Richtung
            </label>
            <select
              id="contract-direction"
              className="form-input"
              value={values.direction}
              onChange={(event) => {
                const direction = event.target.value as ContractDirection;
                setValues((previous) => ({
                  ...previous,
                  direction,
                  reserveEnabled:
                    isReserveEligible(direction, previous.frequency) && previous.reserveEnabled,
                }));
              }}
            >
              <option value="expense">Ausgabe</option>
              <option value="income">Einnahme</option>
            </select>
          </div>
          {values.direction === "income" ? (
            <>
              <div className="form-field">
                <label className="form-label" htmlFor="contract-income-kind">
                  Einnahmeart
                </label>
                <select
                  id="contract-income-kind"
                  className="form-input"
                  value={values.incomeKind}
                  onChange={(event) =>
                    setValues((previous) => ({ ...previous, incomeKind: event.target.value }))
                  }
                >
                  {INCOME_KIND_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-field">
                <label className="form-label" htmlFor="contract-person">
                  Person
                </label>
                <select
                  id="contract-person"
                  className="form-input"
                  value={values.personId}
                  onChange={(event) =>
                    setValues((previous) => ({ ...previous, personId: event.target.value }))
                  }
                >
                  <option value="">— keine —</option>
                  {people.map((person) => (
                    <option key={person.id} value={person.id}>
                      {person.name}
                    </option>
                  ))}
                </select>
              </div>
            </>
          ) : null}
          <div className="form-field">
            <label className="form-label" htmlFor="contract-amount">
              Betrag (€) *
            </label>
            <input
              id="contract-amount"
              type="text"
              className="form-input form-input--mono"
              value={values.amountEuro}
              onChange={(event) => set("amountEuro", event.target.value)}
              placeholder="39,95"
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="contract-frequency">
              Rhythmus
            </label>
            <select
              id="contract-frequency"
              className="form-input"
              value={values.frequency}
              onChange={(event) => {
                const frequency = event.target.value as ContractFrequency;
                setValues((previous) => ({
                  ...previous,
                  frequency,
                  reserveEnabled: isReserveEligible(previous.direction, frequency),
                }));
              }}
            >
              {(Object.keys(FREQUENCY_LABEL) as ContractFrequency[]).map((key) => (
                <option key={key} value={key}>
                  {FREQUENCY_LABEL[key]}
                </option>
              ))}
            </select>
          </div>
        </div>

        {isReserveEligible(values.direction, values.frequency) && (
          <fieldset className="contract-reserve-panel">
            <legend>Fixkosten-Rücklage</legend>
            <label className="toggle-row" htmlFor="contract-reserve-enabled">
              <input
                id="contract-reserve-enabled"
                type="checkbox"
                checked={values.reserveEnabled}
                onChange={(event) => set("reserveEnabled", event.target.checked)}
              />
              <span>
                <strong>Kosten über die Monate verteilen</strong>
                <small>
                  Rein kalkulatorisch – es wird weder Geld verschoben noch eine Buchung erzeugt.
                </small>
              </span>
            </label>
            {values.reserveEnabled && (
              <div className="form-grid-2">
                <div className="form-field">
                  <label className="form-label" htmlFor="contract-reserve-starting">
                    Bereits für diese Zahlung zurückgelegt
                  </label>
                  <input
                    id="contract-reserve-starting"
                    className="form-input form-input--mono"
                    inputMode="decimal"
                    value={values.reserveStartingEuro}
                    onChange={(event) => set("reserveStartingEuro", event.target.value)}
                  />
                </div>
                <div className="form-field">
                  <label className="form-label" htmlFor="contract-reserve-started-on">
                    Rücklagenplanung ab
                  </label>
                  <input
                    id="contract-reserve-started-on"
                    type="date"
                    className="form-input form-input--mono"
                    value={values.reserveStartedOn}
                    onChange={(event) => set("reserveStartedOn", event.target.value)}
                    required
                  />
                </div>
              </div>
            )}
            <div className="contract-reserve-panel__preview" aria-label="Rücklagenvorschau">
              <span>
                <small>Periodenbetrag</small>
                <strong>
                  {formatMinorUnitsAsEuro(positiveAmountForPreview(values.amountEuro))} ·{" "}
                  {FREQUENCY_LABEL[values.frequency].toLowerCase()}
                </strong>
              </span>
              <span>
                <small>Nächste Fälligkeit</small>
                <strong>
                  {values.nextDueDate ? formatDate(values.nextDueDate) : "Noch offen"}
                </strong>
              </span>
              <span>
                <small>Erste Monatsrate</small>
                <strong>
                  {firstReserveMonth(values.reserveStartedOn)
                    ? `ab ${formatDate(firstReserveMonth(values.reserveStartedOn)!)}`
                    : "Nach Startdatum"}
                </strong>
              </span>
              <p>Planungsannahme ab dem Folgemonat – kein tatsächlicher Kontostand.</p>
            </div>
          </fieldset>
        )}

        <div className="form-grid-2">
          <div className="form-field">
            <label className="form-label" htmlFor="contract-next-due">
              Nächste Fälligkeit *
            </label>
            <input
              id="contract-next-due"
              type="date"
              className="form-input form-input--mono"
              value={values.nextDueDate}
              onChange={(event) => set("nextDueDate", event.target.value)}
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="contract-category">
              Kategorie
            </label>
            <CategoryPicker
              id="contract-category"
              categoryGroups={categoryGroups}
              value={values.categoryId}
              onChange={(categoryId) => set("categoryId", categoryId)}
              noneLabel="-- Nicht kategorisiert --"
            />
          </div>
        </div>

        {/* Suchhilfen fuer die Felder oben, deshalb am Ende des Tabs statt am
            Anfang: beide sind ein "falls du nicht weiterweisst"-Angebot, kein
            Schritt, der vor dem Ausfuellen kommen muesste. Sichtbar sobald
            tatsaechlich gesucht wurde - vorher zeigte diese Box sich nur bei
            Treffern und blieb bei einer Nichttreffer-Suche (z. B. ein Anbieter,
            den der Katalog nicht kennt) spurlos aus. Ohne Rueckmeldung war die
            Suche von "gibt es nicht" nicht zu unterscheiden. */}
        {!isEdit && providerQuery.trim().length >= 2 && (
          <div className="operation-preview" role="status">
            <h3 className="card-heading">Anbietersuche</h3>
            {providerSuggestions.isFetching ? (
              <p className="text-sm">Anbieterkatalog wird durchsucht…</p>
            ) : suggestions.length > 0 ? (
              <>
                <p className="text-sm">
                  Übernimmt Anbietername, Vertragsart, Kategorie und die typische Kündigungsfrist.
                  Danach bleibt alles änderbar — die Vorlage ist ein Vorschlag, keine Vorgabe.
                </p>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion.semantic_key}
                      type="button"
                      className="btn-secondary"
                      onClick={() => applySuggestion(suggestion)}
                    >
                      {suggestion.provider_name} · {TYPE_LABEL[suggestion.contract_type]}
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-sm">
                Kein bekannter Anbieter gefunden — der Katalog kennt bisher nur einen Teil der
                großen Anbieter. Name, Betrag und die übrigen Felder trägst du wie gewohnt selbst
                ein.
              </p>
            )}
          </div>
        )}

        {!isEdit && <AmountLookupPanel onApplyAmount={applyLookedUpAmount} />}
      </TabPanel>

      <TabPanel idPrefix="contract-form" tabKey="laufzeit" active={formTab}>
        <p className="text-sm">
          Eine Kündigung ist hier direkt eintragbar. Gekündigt heißt nicht beendet: bis zum Enddatum
          zählt der Vertrag weiter mit.
        </p>
        <div className="form-grid-3">
          <div className="form-field">
            <label className="form-label" htmlFor="contract-status">
              Status
            </label>
            <select
              id="contract-status"
              className="form-input"
              value={values.status}
              onChange={(event) => set("status", event.target.value as ContractStatus)}
            >
              {(Object.keys(STATUS_LABEL) as ContractStatus[]).map((key) => (
                <option key={key} value={key}>
                  {STATUS_LABEL[key]}
                </option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="contract-cancelled-on">
              Gekündigt am
            </label>
            <input
              id="contract-cancelled-on"
              type="date"
              className="form-input form-input--mono"
              value={values.cancelledOn}
              onChange={(event) => set("cancelledOn", event.target.value)}
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="contract-ends-on">
              Läuft bis
            </label>
            <input
              id="contract-ends-on"
              type="date"
              className="form-input form-input--mono"
              value={values.endsOn}
              onChange={(event) => set("endsOn", event.target.value)}
            />
          </div>
        </div>
        <div className="form-grid-2">
          <div className="form-field">
            <label className="form-label" htmlFor="contract-min-term">
              Mindestlaufzeit (Monate)
            </label>
            <input
              id="contract-min-term"
              type="number"
              min={1}
              className="form-input form-input--mono"
              value={values.minTermMonths}
              onChange={(event) => set("minTermMonths", event.target.value)}
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="contract-cancel-days">
              Kündigungsfrist (Tage)
            </label>
            <input
              id="contract-cancel-days"
              type="number"
              min={0}
              className="form-input form-input--mono"
              value={values.cancellationPeriodDays}
              onChange={(event) => set("cancellationPeriodDays", event.target.value)}
            />
          </div>
        </div>
      </TabPanel>

      <TabPanel idPrefix="contract-form" tabKey="notiz" active={formTab}>
        <div className="form-field">
          <label className="form-label" htmlFor="contract-note">
            Notiz
          </label>
          <input
            id="contract-note"
            type="text"
            className="form-input"
            value={values.note}
            onChange={(event) => set("note", event.target.value)}
          />
        </div>
      </TabPanel>

      {formError && (
        <p role="alert" className="status-error">
          {formError}
        </p>
      )}

      <div className="modal-actions">
        <button type="button" className="btn-secondary" onClick={onCancel}>
          Abbrechen
        </button>
        <button
          type="submit"
          className="btn-primary"
          disabled={createMutation.isPending || updateMutation.isPending}
        >
          {createMutation.isPending || updateMutation.isPending ? "Wird gespeichert…" : "Speichern"}
        </button>
      </div>
    </form>
  );
}

/**
 * Anlegen-Dialog. Das Bearbeiten hat seit den Registern keinen eigenen Dialog
 * mehr, sondern steht als Register im Detaildialog.
 *
 * `onCreated` greift nur beim Neuanlegen: statt schlicht zu schliessen,
 * springt der Aufrufer direkt in den Betragsverlauf des frisch erzeugten
 * Vertrags, mit bereits geoeffnetem "Fehlende Buchungen nachtragen" - genau
 * der Schritt, den man nach dem Anlegen erfahrungsgemaess als naechstes
 * braucht (die passende Buchung suchen, statt den Betrag zu raten).
 */
function ContractFormModal({
  contract,
  defaultDirection,
  onClose,
  onCreated,
}: {
  contract: Contract | null;
  defaultDirection: ContractDirection;
  onClose: () => void;
  onCreated?: (created: Contract) => void;
}) {
  return (
    <Modal title={contract ? "Vertrag bearbeiten" : "Neuer Vertrag"} onClose={onClose} size="wide">
      <ContractForm
        contract={contract}
        defaultDirection={defaultDirection}
        onDone={(created) => (created && onCreated ? onCreated(created) : onClose())}
        onCancel={onClose}
      />
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Detailansicht
// ---------------------------------------------------------------------------

/**
 * Register des Detaildialogs (Plan 19.7).
 *
 * Vorher stand alles untereinander in einem Dialog: Kennzahlen, Verlauf,
 * Faelligkeiten, Ereignisse - und das Bearbeiten und die Zeilenaktionen lagen
 * ganz ausserhalb, im Punktmenue der Liste. Mit den Belegen je Preisstufe und
 * dem Nachtragen waere diese Stapelung endgueltig unlesbar geworden.
 */
type DetailTab = "overview" | "amounts" | "occurrences" | "events" | "edit";

const DETAIL_TABS: TabDefinition<DetailTab>[] = [
  { key: "overview", label: "Übersicht" },
  { key: "amounts", label: "Betragsverlauf" },
  { key: "occurrences", label: "Fälligkeiten" },
  { key: "events", label: "Ereignisse" },
  { key: "edit", label: "Bearbeiten" },
];

interface ContractDetailModalProps {
  contract: Contract;
  initialTab?: DetailTab;
  onClose: () => void;
  /** Oeffnet den Betragsdialog. Bewusst ein Rueckruf statt eines zweiten
   *  Dialogs im Dialog: gestapelte Modals sind mit der Tastatur kaum zu
   *  verlassen. */
  onCorrectAmount: () => void;
  onDelete: () => void;
}

function ContractDetailModal({
  contract,
  initialTab = "overview",
  onClose,
  onCorrectAmount,
  onDelete,
}: ContractDetailModalProps) {
  const { canEdit } = useContractContext();
  const { formatDate } = useFormatDate();
  const [tab, setTab] = useState<DetailTab>(initialTab);
  const occurrences = useContractOccurrences(contract.id);
  const paid = paidSoFar(occurrences.data);
  const events = useContractEvents(contract.id);
  const statusMutation = useSetContractStatus();
  const [actionError, setActionError] = useState<string | null>(null);

  const changeStatus = (status: ContractStatus) => {
    setActionError(null);
    statusMutation.mutate(
      { contractId: contract.id, input: { status } },
      {
        onError: (error) =>
          setActionError(errorText(error, "Der Status konnte nicht geändert werden.")),
      },
    );
  };

  return (
    <Modal title={contract.name} onClose={onClose} size="wide">
      <div className="contract-detail">
        {/* Die Aktionen aus dem Punktmenue der Liste, jetzt am Vertrag selbst:
            wer den Vertrag offen hat, musste ihn bisher schliessen, um ihn zu
            pausieren oder zu beenden. */}
        {canEdit && (
          <div className="page-header" style={{ marginBottom: "var(--space-3)" }}>
            <div>
              <span className={`status-badge ${STATUS_CLASS[contract.status]}`}>
                {STATUS_LABEL[contract.status]}
              </span>{" "}
              <span className="txn-note">
                {formatMinorUnitsAsEuro(signedForDisplay(contract, contract.amount_minor))} ·{" "}
                {FREQUENCY_LABEL[contract.frequency]}
              </span>
            </div>
            <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
              <button type="button" className="btn-secondary" onClick={onCorrectAmount}>
                Betrag korrigieren
              </button>
              {contract.status === "active" && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={statusMutation.isPending}
                  onClick={() => changeStatus("paused")}
                >
                  Pausieren
                </button>
              )}
              {contract.status !== "ended" && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={statusMutation.isPending}
                  onClick={() => changeStatus("ended")}
                >
                  Als beendet markieren
                </button>
              )}
              {(contract.status === "ended" || contract.status === "not_a_contract") && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={statusMutation.isPending}
                  onClick={() => changeStatus("active")}
                >
                  Wieder aktivieren
                </button>
              )}
              {contract.status !== "not_a_contract" && contract.status !== "ended" && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={statusMutation.isPending}
                  onClick={() => changeStatus("not_a_contract")}
                >
                  Kein Vertrag
                </button>
              )}
              <button type="button" className="btn-secondary" onClick={onDelete}>
                Löschen
              </button>
            </div>
          </div>
        )}
        {actionError && (
          <p role="alert" className="status-error">
            {actionError}
          </p>
        )}

        <Tabs
          idPrefix="contract-detail"
          label="Bereiche des Vertrags"
          tabs={DETAIL_TABS}
          active={tab}
          onChange={setTab}
        />

        <TabPanel idPrefix="contract-detail" tabKey="overview" active={tab}>
          <dl className="contract-detail__summary">
            <div className="contract-detail__metric">
              <dt>Anbieter</dt>
              <dd>{contract.provider_label ?? "—"}</dd>
            </div>
            <div className="contract-detail__metric">
              <dt>Vertragsart</dt>
              <dd>{TYPE_LABEL[contract.contract_type]}</dd>
            </div>
            {/* Bei mehreren gleichnamigen Vertraegen zum selben Anbieter (z. B.
              Mobilfunk, DSL und TV bei demselben Anbieter) ist die Notiz die
              einzige Stelle, die sagt, welcher Vertrag hier gerade offen ist -
              Betrag und Fälligkeit allein unterscheiden das nicht zuverlässig. */}
            {contract.note && (
              <div className="contract-detail__metric">
                <dt>Notiz</dt>
                <dd>{contract.note}</dd>
              </div>
            )}
            <div className="contract-detail__metric">
              <dt>Rhythmus</dt>
              <dd>{FREQUENCY_LABEL[contract.frequency]}</dd>
            </div>
            <div className="contract-detail__metric">
              <dt>Pro Monat</dt>
              <dd>{formatMinorUnitsAsEuro(signedForDisplay(contract, monthlyMinor(contract)))}</dd>
            </div>
            {contract.reserve_enabled && (contract.reserve_monthly_minor ?? 0) > 0 && (
              <div className="contract-detail__metric contract-detail__metric--reserve">
                <dt>
                  <span className="status-badge status-badge--highlight">Fixkosten-Rücklage</span>
                </dt>
                <dd>{formatMinorUnitsAsEuro(contract.reserve_monthly_minor ?? 0)} monatlich</dd>
                <p className="contract-detail__hint">
                  Kalkulatorische Planung seit{" "}
                  {contract.reserve_started_on ? formatDate(contract.reserve_started_on) : "heute"};
                  kein Kontostand.
                </p>
              </div>
            )}
            <div className="contract-detail__metric">
              <dt>Pro Jahr (hochgerechnet)</dt>
              <dd>{formatMinorUnitsAsEuro(signedForDisplay(contract, yearlyMinor(contract)))}</dd>
              <p className="contract-detail__hint">
                Heutiger Betrag mal {PERIODS_PER_YEAR[contract.frequency]}. Preisänderungen, Pausen
                und ein unterjähriger Beginn sind darin nicht enthalten.
              </p>
            </div>
            {/* Die einzige Zahl auf dieser Karte, die niemand hochrechnet: die
                Summe der tatsaechlich zugeordneten Buchungen. Bei einem Vertrag
                mit Preisaenderung ist die Hochrechnung darueber zwangslaeufig
                falsch - und ohne zugeordnete Buchungen macht dieses Feld die
                Datenluecke sichtbar, statt sie hinter einer plausibel
                aussehenden Jahreszahl verschwinden zu lassen. */}
            <div className="contract-detail__metric">
              <dt>Bisher gezahlt</dt>
              <dd>
                {paid.count === 0
                  ? "—"
                  : formatMinorUnitsAsEuro(signedForDisplay(contract, paid.totalMinor))}
              </dd>
              <p className="contract-detail__hint">
                {paid.count === 0
                  ? "Noch keine Zahlung zugeordnet."
                  : `${paid.count} ${paid.count === 1 ? "Zahlung" : "Zahlungen"} seit ${paid.since === "—" ? "—" : formatDate(paid.since)}`}
              </p>
            </div>
            <div className="contract-detail__metric">
              <dt>Status</dt>
              <dd>
                <span className={`status-badge ${STATUS_CLASS[contract.status]}`}>
                  {STATUS_LABEL[contract.status]}
                </span>
              </dd>
            </div>
            {contract.ends_on && (
              <div className="contract-detail__metric">
                <dt>Läuft bis</dt>
                <dd>{formatDate(contract.ends_on)}</dd>
              </div>
            )}
            {contract.cancellation_period_days !== null && (
              <div className="contract-detail__metric">
                <dt>Kündigungsfrist</dt>
                <dd>{contract.cancellation_period_days} Tage</dd>
              </div>
            )}
            {contract.term_deadline && (
              <div className="contract-detail__metric">
                <dt>
                  {contract.term_deadline.kind === "fixed_term"
                    ? "Kündigen spätestens bis"
                    : "Kündbar"}
                </dt>
                <dd>
                  {contract.term_deadline.kind === "fixed_term" ? (
                    <>
                      {formatDate(contract.term_deadline.deadline_on)}{" "}
                      <span
                        className={`status-badge status-badge--${countdownTone(
                          contract.term_deadline.days_left,
                        )}`}
                        title={deadlineTitle(contract.term_deadline, formatDate)}
                      >
                        {countdownLabel(contract.term_deadline.days_left)}
                      </span>
                      <span className="txn-note">
                        wirksam zum {formatDate(contract.term_deadline.term_ends_on)}
                      </span>
                    </>
                  ) : (
                    <>
                      jederzeit
                      <span className="txn-note">
                        frühestens wirksam zum {formatDate(contract.term_deadline.term_ends_on)}
                      </span>
                    </>
                  )}
                </dd>
              </div>
            )}
            {contract.missed_periods > 0 && (
              <div className="contract-detail__metric">
                <dt>Ausgebliebene Zahlungen</dt>
                <dd>
                  <span className="status-badge status-badge--warning">
                    {contract.missed_periods}
                  </span>{" "}
                  {contract.last_seen_date
                    ? `zuletzt am ${formatDate(contract.last_seen_date)}`
                    : "noch nie zugeordnet"}
                </dd>
              </div>
            )}
          </dl>
        </TabPanel>

        <TabPanel idPrefix="contract-detail" tabKey="amounts" active={tab}>
          <ContractAmountHistoryPanel
            contract={contract}
            canEdit={canEdit}
            initialAssignOpen={initialTab === "amounts"}
          />
        </TabPanel>

        <TabPanel idPrefix="contract-detail" tabKey="occurrences" active={tab}>
          <section>
            <h3 className="card-heading">Fälligkeiten</h3>
            {occurrences.isLoading && <p role="status">Fälligkeiten werden geladen…</p>}
            {!occurrences.isLoading && (occurrences.data ?? []).length === 0 && (
              <p className="placeholder-note">
                Für diesen Vertrag ist noch keine Fälligkeit erfasst. „Jetzt abgleichen“ vergleicht
                die Buchungen mit dem Vertrag — danach steht hier, welche Zahlung angekommen ist und
                welche ausgeblieben.
              </p>
            )}
            {(occurrences.data ?? []).length > 0 && (
              <ul className="contract-occurrences">
                {(occurrences.data ?? []).map((item) => (
                  <li key={item.id}>
                    <span className="contract-occurrences__date">{formatDate(item.due_date)}</span>
                    <span
                      className={`status-badge ${
                        item.status === "matched"
                          ? "status-badge--positive"
                          : "status-badge--warning"
                      }`}
                    >
                      {item.status === "matched" ? "Bezahlt" : "Ausgeblieben"}
                    </span>
                    {item.amount_minor !== null && (
                      <span className="contract-occurrences__amount">
                        {formatMinorUnitsAsEuro(item.amount_minor)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </TabPanel>

        <TabPanel idPrefix="contract-detail" tabKey="events" active={tab}>
          <section>
            <h3 className="card-heading">Ereignisse</h3>
            {events.isLoading && <p className="placeholder-note">Ereignisse werden geladen…</p>}
            {!events.isLoading && (events.data ?? []).length === 0 && (
              <p className="placeholder-note">Zu diesem Vertrag ist noch nichts passiert.</p>
            )}
            <ul className="contract-occurrences">
              {(events.data ?? []).map((event) => (
                <li key={event.id}>
                  <span className="contract-occurrences__date">
                    {formatDate(event.occurred_on)}
                  </span>
                  <span>{EVENT_KIND_LABEL[event.kind] ?? event.kind}</span>
                  {event.kind === "merged" && typeof event.payload.merge_id === "string" && (
                    <UndoMergeButton mergeId={event.payload.merge_id} />
                  )}
                </li>
              ))}
            </ul>
          </section>
        </TabPanel>

        <TabPanel idPrefix="contract-detail" tabKey="edit" active={tab}>
          {canEdit ? (
            <ContractForm
              contract={contract}
              defaultDirection={contract.direction}
              onDone={onClose}
              onCancel={onClose}
            />
          ) : (
            <p className="placeholder-note">
              Zum Bearbeiten braucht es die Rolle Editor oder Besitzer.
            </p>
          )}
        </TabPanel>
      </div>
    </Modal>
  );
}

const EVENT_KIND_LABEL: Record<string, string> = {
  created: "Angelegt",
  price_change: "Preisänderung",
  payment_missing: "Zahlung ausgeblieben",
  payment_resumed: "Zahlung wieder eingegangen",
  cancelled: "Gekündigt",
  ended: "Beendet",
  reactivated: "Reaktiviert",
  merged: "Zusammengeführt",
  merge_undone: "Zusammenführung rückgängig gemacht",
  dismissed: "Als eigenständig markiert",
  installment_completed: "Rate abgeschlossen",
  term_ending: "Kündigungsfrist läuft ab",
};

/**
 * Bleibt am `merged`-Ereignis dauerhaft klickbar - "unmittelbar nach dem
 * Merge sowie später im Ereignisverlauf erreichbar" (Abschnitt 19.7).
 *
 * Beide Stellen benutzen dieselbe Komponente und damit denselben Weg über
 * `undo-preview` + `undo`; nur die Beschriftung unterscheidet sich. Direkt
 * nach dem Zusammenführen steht die Aktion für sich allein und braucht das
 * Objekt im Namen ("Zusammenführung rückgängig machen"), im Ereignisverlauf
 * steht sie hinter der Zeile "Zusammengeführt" und wäre dort doppelt gemoppelt.
 */
function UndoMergeButton({
  mergeId,
  label = "Rückgängig machen",
  onUndone,
}: {
  mergeId: string;
  label?: string;
  onUndone?: () => void;
}) {
  const { canEdit } = useContractContext();
  const preview = useContractMergeUndoPreview(mergeId);
  const undoMutation = useUndoContractMerge();
  const [error, setError] = useState<string | null>(null);

  if (!canEdit) return null;
  if (preview.data?.already_undone) {
    return <span className="txn-note">bereits rückgängig gemacht</span>;
  }

  return (
    <span className="contract-occurrences__amount">
      <button
        type="button"
        className="btn-secondary"
        // Waehrend des Laufs gesperrt, unabhaengig davon, ob die Vorschau
        // schon da ist - ein zweiter Klick loeste sonst denselben Undo erneut
        // aus, nur um am Server idempotent zu verpuffen.
        disabled={undoMutation.isPending || (preview.data ? !preview.data.can_undo : false)}
        title={
          preview.data && !preview.data.can_undo
            ? "Seit dem Zusammenführen ist Neues hinzugekommen - bitte den Verlauf prüfen."
            : undefined
        }
        onClick={() =>
          undoMutation.mutate(mergeId, {
            onSuccess: () => onUndone?.(),
            onError: (err) => setError(errorText(err, "Rückgängig machen ist fehlgeschlagen.")),
          })
        }
      >
        {undoMutation.isPending ? "Wird rückgängig gemacht…" : label}
      </button>
      {error && (
        <span role="alert" className="status-error">
          {error}
        </span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Zusammenfuehren
// ---------------------------------------------------------------------------

/**
 * Was nach dem Zusammenführen zum Vertragsschlüssel zu sagen ist.
 *
 * `null` heißt hier ausdrücklich "keine eindeutige Anbieterreferenz
 * ableitbar", nicht "nicht geprüft" - der Merge leitet den Schlüssel seit
 * Abschnitt 19.11 deterministisch aus den beteiligten Buchungen ab. Ohne
 * Schlüssel bleibt der Abgleich bei `identity_score` 0,60 stehen und lehnt
 * eine spätere Preisänderung ab; das ist genau der Fall, den der Nutzer hier
 * noch einmal ansehen soll.
 */
function mergeKeyNote(contract: Contract): string {
  if (!contract.contract_key) {
    return "Kein gemeinsamer Vertragsschlüssel ableitbar — die Zusammenführung bitte noch einmal prüfen.";
  }
  const origin =
    contract.contract_key_source === "mandate"
      ? "aus der Mandatsreferenz"
      : contract.contract_key_source === "purpose_pattern"
        ? "aus dem Verwendungszweck"
        : "von Hand hinterlegt";
  return `Vertragsschlüssel übernommen (${origin}): ${contract.contract_key}`;
}

function MergeModal({
  targetId,
  sourceIds,
  onClose,
}: {
  targetId: string;
  sourceIds: string[];
  onClose: () => void;
}) {
  const preview = useContractMergePreview(targetId, sourceIds);
  const applyMutation = useApplyContractMerge();
  const [formError, setFormError] = useState<string | null>(null);
  const [requestId] = useState(() => newRequestId());
  /**
   * Das Ergebnis bleibt stehen, statt den Dialog sofort zu schließen.
   *
   * Abschnitt 19.7 verlangt "Nach dem Merge erscheint unmittelbar
   * 'Zusammenführung rückgängig machen'" - bis Phase 5 stand die Aktion nur
   * tief im Ereignisverlauf des Zielvertrags, wo sie im Zweifelsfall niemand
   * sucht. Das Ergebnis kommt aus der Mutation und nicht aus einer erneuten
   * Abfrage, weil `merge_id` nur hier steht.
   */
  const [result, setResult] = useState<ContractMergeResult | null>(null);
  const [undone, setUndone] = useState(false);

  const data = preview.data;

  if (result) {
    return (
      <Modal
        title={undone ? "Zusammenführung rückgängig gemacht" : "Zusammengeführt"}
        onClose={onClose}
        size="wide"
      >
        <div className="operation-preview" role="status">
          {undone ? (
            <p className="card-body">
              Die Quellverträge stehen mit ihren Beträgen, Preisstufen und Fälligkeiten wieder
              einzeln in der Liste. Die Fixkostensumme entspricht wieder dem Stand vor dem
              Zusammenführen.
            </p>
          ) : (
            <>
              <h3 className="card-heading">{result.contract.name}</h3>
              <ul>
                <li>{contractCountLabel(result.merged_count)} zusammengeführt</li>
                <li>{result.moved_history_entries} Preisstufe(n) im Verlauf</li>
                <li>{result.moved_occurrences} Fälligkeit(en) übernommen</li>
                <li>
                  Aktueller Betrag:{" "}
                  {formatMinorUnitsAsEuro(
                    signedForDisplay(result.contract, result.contract.amount_minor),
                  )}
                </li>
              </ul>
              <p className="text-sm">{mergeKeyNote(result.contract)}</p>
            </>
          )}
        </div>
        <div className="modal-actions">
          {!undone && result.merge_id && (
            <UndoMergeButton
              mergeId={result.merge_id}
              label="Zusammenführung rückgängig machen"
              onUndone={() => setUndone(true)}
            />
          )}
          {/* Nicht "Schließen": das × in der Kopfzeile heißt bereits so, und
              zwei gleich benannte Bedienelemente in einem Dialog sind für
              Screenreader nicht auseinanderzuhalten. */}
          <button type="button" className="btn-primary" onClick={onClose}>
            Fertig
          </button>
        </div>
        {!undone && (
          <p className="placeholder-note">
            Später ist dieselbe Aktion im Ereignisverlauf des Vertrags erreichbar.
          </p>
        )}
      </Modal>
    );
  }

  return (
    <Modal title="Verträge zusammenführen" onClose={onClose} size="wide">
      {preview.isFetching && <p role="status">Auswirkung wird berechnet…</p>}
      {preview.isError && (
        <p role="alert" className="status-error">
          {errorText(preview.error, "Vorschau ist fehlgeschlagen.")}
        </p>
      )}
      {data && (
        <div className="operation-preview" role="status">
          <h3 className="card-heading">Nach dem Zusammenführen</h3>
          <ul>
            <li>
              Ziel: <strong>{data.target_name}</strong>
            </li>
            <li>Wird zusammengeführt mit: {data.source_names.join(", ")}</li>
            <li>{data.moved_history_entries} Preisstufe(n) wandern in den Verlauf</li>
            <li>{data.moved_occurrences} Fälligkeit(en) werden übernommen</li>
            <li>Neuer Sollbetrag: {formatMinorUnitsAsEuro(data.resulting_amount_minor)}</li>
          </ul>
          {data.amount_steps.length > 1 && (
            <ol className="contract-timeline">
              {data.amount_steps.map((step) => (
                <li key={step.id} className="contract-timeline__step">
                  <span className="contract-timeline__amount">
                    {formatMinorUnitsAsEuro(step.amount_minor)}
                  </span>
                  <span className="contract-timeline__range">ab {step.valid_from}</span>
                </li>
              ))}
            </ol>
          )}
          {data.blocking_reason && (
            <p role="alert" className="status-error">
              Zusammenführen nicht möglich: Ausgaben und Einnahmen lassen sich nicht mischen.
            </p>
          )}
        </div>
      )}
      {formError && (
        <p role="alert" className="status-error">
          {formError}
        </p>
      )}
      <div className="modal-actions">
        <button type="button" className="btn-secondary" onClick={onClose}>
          Abbrechen
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={!data || Boolean(data.blocking_reason) || applyMutation.isPending}
          onClick={() =>
            applyMutation.mutate(
              { target_id: targetId, source_ids: sourceIds, request_id: requestId },
              {
                onSuccess: setResult,
                onError: (error) => {
                  setFormError(errorText(error, "Zusammenführen ist fehlgeschlagen."));
                  void preview.refetch();
                },
              },
            )
          }
        >
          {applyMutation.isPending ? "Wird zusammengeführt…" : "Zusammenführen"}
        </button>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Betragskorrektur
// ---------------------------------------------------------------------------

function AmountModal({ contract, onClose }: { contract: Contract; onClose: () => void }) {
  const [amountEuro, setAmountEuro] = useState("");
  const [validFrom, setValidFrom] = useState(today());
  const [formError, setFormError] = useState<string | null>(null);
  const mutation = useAddContractAmount();

  return (
    <Modal title="Betrag korrigieren" onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const amountMinor = parseAmountToMinorUnits(amountEuro);
          if (amountMinor === null || amountMinor === 0) {
            setFormError("Bitte einen Betrag ungleich null eintragen.");
            return;
          }
          mutation.mutate(
            {
              contractId: contract.id,
              amount_minor: Math.abs(amountMinor),
              valid_from: validFrom,
            },
            {
              onSuccess: onClose,
              onError: (error) => setFormError(errorText(error, "Speichern ist fehlgeschlagen.")),
            },
          );
        }}
        className="contract-form"
      >
        <p className="text-sm">
          Der bisherige Betrag bleibt als Preisstufe im Verlauf erhalten — so bleibt sichtbar, wann
          und um wie viel sich der Vertrag verteuert hat.
        </p>
        <div className="form-grid-2">
          <div className="form-field">
            <label className="form-label" htmlFor="amount-new">
              Neuer Betrag (€) *
            </label>
            <input
              id="amount-new"
              type="text"
              className="form-input form-input--mono"
              value={amountEuro}
              onChange={(event) => setAmountEuro(event.target.value)}
              placeholder="123,00"
              required
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="amount-valid-from">
              Gültig ab *
            </label>
            <input
              id="amount-valid-from"
              type="date"
              className="form-input form-input--mono"
              value={validFrom}
              onChange={(event) => setValidFrom(event.target.value)}
              required
            />
          </div>
        </div>
        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Abbrechen
          </button>
          <button type="submit" className="btn-primary" disabled={mutation.isPending}>
            Speichern
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Prognose und Fristen
// ---------------------------------------------------------------------------

const CASHFLOW_VIEWS: { granularity: CashflowGranularity; label: string }[] = [
  { granularity: "month", label: "Monatlich" },
  { granularity: "quarter", label: "Quartalsweise" },
];

/**
 * Cashflow-Prognose je Kalenderzeitraum (Plan I9).
 *
 * Vorgaenger dieser Karte waren rollierende 30- und 90-Tage-Fenster. Das war
 * kein Darstellungsproblem, sondern ein Rechenfehler: in "die naechsten 30
 * Tage" fiel ein monatlicher Vertrag je nach Faelligkeitstag ein- oder
 * zweimal, in "die naechsten 90 Tage" drei- oder viermal. Die ausgewiesene
 * Summe schwankte mit dem Kalender und stimmte nie mit der Fixkostensumme
 * ueber dieser Karte oder der Fixkostenquote im Budget ueberein.
 *
 * Zwei weitere Altlasten sind damit erledigt: die Spalte "Kumuliert" addierte
 * Ausgaben und Einnahmen quer durch den Kalender zu einer Zahl, die mit nichts
 * vergleichbar war - jetzt laeuft die Kumulation ueber die PERIODEN. Und der
 * Zeilendeckel von zwoelf Faelligkeiten liess den Rest lautlos verschwinden;
 * die Einzelfaelligkeiten stehen jetzt vollstaendig im aufklappbaren Detail
 * einer Periode.
 */
function CashflowCard() {
  const { formatDate } = useFormatDate();
  const [granularity, setGranularity] = useState<CashflowGranularity>("month");
  const [expanded, setExpanded] = useState<string | null>(null);
  // Sechs Monate beziehungsweise vier Quartale: beide Male knapp ueber ein
  // Jahr Vorlauf, ohne dass eine Jahrespraemie zweimal auftaucht.
  const periodCount = granularity === "month" ? 6 : 4;
  const cashflow = useContractCashflow(granularity, periodCount);

  // Eigenes useMemo statt `?? []` direkt in der Abhaengigkeit: ein frisches
  // Literal je Renderdurchlauf wuerde die Memoisierung darunter jedes Mal
  // verwerfen.
  const periods = useMemo(() => cashflow.data?.periods ?? [], [cashflow.data]);
  // Bezugsgroesse der Balken ist der groesste Bruttowert ueber alle Perioden -
  // nicht der jeweils eigene. Sonst saehe jede Periode gleich voll aus und der
  // Vergleich zwischen ihnen, der eigentliche Zweck der Karte, waere weg.
  const scale = useMemo(
    () =>
      Math.max(1, ...periods.map((period) => Math.max(period.expense_minor, period.income_minor))),
    [periods],
  );

  return (
    <div className="card">
      <div className="page-header" style={{ marginBottom: "var(--space-3)" }}>
        <div>
          <h2 className="card-heading">Cashflow-Prognose</h2>
          <p className="placeholder-note">
            Je Kalenderzeitraum — der laufende {granularity === "month" ? "Monat" : "Quartal"}{" "}
            enthält auch bereits abgebuchte Fälligkeiten.
          </p>
        </div>
        <div className="direction-toggle">
          {CASHFLOW_VIEWS.map((item) => (
            <button
              key={item.granularity}
              type="button"
              className={`direction-btn ${
                granularity === item.granularity ? "direction-btn--active" : ""
              }`}
              aria-pressed={granularity === item.granularity}
              onClick={() => {
                setGranularity(item.granularity);
                setExpanded(null);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {/* Ohne role="status": auf dieser Seite meldet role="status" das Ergebnis
          des Abgleichs. Zwei gleichzeitige Statusmeldungen ergaeben fuer einen
          Screenreader zwei konkurrierende Ansagen. */}
      {cashflow.isLoading && <p className="placeholder-note">Prognose wird berechnet…</p>}

      {!cashflow.isLoading && periods.every((period) => period.items.length === 0) && (
        <p className="placeholder-note">In diesem Zeitraum ist keine Fälligkeit erwartet.</p>
      )}

      {periods.some((period) => period.items.length > 0) && (
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th">Zeitraum</th>
                <th className="txn-th txn-th--amount">Ausgaben</th>
                <th className="txn-th txn-th--amount">Einnahmen</th>
                <th className="txn-th txn-th--amount">Saldo</th>
                <th className="txn-th txn-th--amount">Kumuliert</th>
              </tr>
            </thead>
            <tbody>
              {periods.map((period) => (
                <Fragment key={period.period_start}>
                  <tr className="txn-tr">
                    <td className="txn-td">
                      <button
                        type="button"
                        className="period-toggle"
                        aria-expanded={expanded === period.period_start}
                        onClick={() =>
                          setExpanded((current) =>
                            current === period.period_start ? null : period.period_start,
                          )
                        }
                      >
                        {period.label}
                      </button>
                      <span className="txn-note">
                        {period.items.length === 0
                          ? "keine Fälligkeit"
                          : `${period.items.length} Fälligkeit(en)`}
                        {period.remaining_expense_minor !== period.expense_minor &&
                          ` · noch offen ${formatMinorUnitsAsEuro(-period.remaining_expense_minor)}`}
                      </span>
                      <span className="period-bar" aria-hidden="true">
                        <span
                          className="period-bar__fill"
                          style={{ width: `${Math.round((period.expense_minor / scale) * 100)}%` }}
                        />
                      </span>
                    </td>
                    <td className="txn-td txn-td--amount transaction-amount--negative">
                      {formatMinorUnitsAsEuro(-period.expense_minor)}
                    </td>
                    <td className="txn-td txn-td--amount transaction-amount--positive">
                      {formatMinorUnitsAsEuro(period.income_minor)}
                    </td>
                    <td
                      className={`txn-td txn-td--amount ${
                        period.net_minor >= 0
                          ? "transaction-amount--positive"
                          : "transaction-amount--negative"
                      }`}
                    >
                      {formatMinorUnitsAsEuro(period.net_minor)}
                    </td>
                    <td
                      className={`txn-td txn-td--amount ${
                        period.cumulative_net_minor >= 0
                          ? "transaction-amount--positive"
                          : "transaction-amount--negative"
                      }`}
                    >
                      {formatMinorUnitsAsEuro(period.cumulative_net_minor)}
                    </td>
                  </tr>
                  {expanded === period.period_start &&
                    period.items.map((item) => (
                      <tr key={`${item.contract_id}-${item.due_date}`} className="txn-tr">
                        <td className="txn-td" style={{ paddingLeft: "var(--space-5)" }}>
                          <span className="txn-payee">{item.name}</span>
                          <span className="txn-note">
                            {formatDate(item.due_date)} · {TYPE_LABEL[item.contract_type]}
                          </span>
                        </td>
                        <td className="txn-td txn-td--amount transaction-amount--negative">
                          {item.direction === "expense"
                            ? formatMinorUnitsAsEuro(-item.amount_minor)
                            : "—"}
                        </td>
                        <td className="txn-td txn-td--amount transaction-amount--positive">
                          {item.direction === "income"
                            ? formatMinorUnitsAsEuro(item.amount_minor)
                            : "—"}
                        </td>
                        <td className="txn-td" />
                        <td className="txn-td" />
                      </tr>
                    ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/**
 * Kuendigungsfristen, die in den naechsten Monaten ablaufen (Plan I4).
 *
 * Die Liste kommt fertig gefiltert aus der Zusammenfassung - sie enthaelt nur
 * Vertraege mit ablaufender Frist, nicht die jederzeit kuendbaren.
 */
function TermDeadlinesCard({ deadlines }: { deadlines: ContractTermSoon[] }) {
  const { formatDate } = useFormatDate();
  return (
    <div className="card">
      <h2 className="card-heading">Kündigungsfristen</h2>
      {deadlines.length === 0 ? (
        <p className="placeholder-note">
          Kein Vertrag mit ablaufender Frist. Mindestlaufzeit und Kündigungsfrist lassen sich beim
          Bearbeiten eintragen — erst dann kann hier etwas stehen.
        </p>
      ) : (
        <ul className="contract-occurrences">
          {deadlines.map((entry) => (
            <li key={entry.contract_id}>
              <span className="contract-occurrences__date">{formatDate(entry.deadline_on)}</span>
              <span className="txn-payee">{entry.name}</span>
              <span
                className={`status-badge status-badge--${countdownTone(entry.days_left)}`}
                title={`Wirksam zum ${formatDate(entry.term_ends_on)}.`}
              >
                {countdownLabel(entry.days_left)}
              </span>
              <span className="contract-occurrences__amount">
                {formatMinorUnitsAsEuro(entry.amount_minor)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Schluesselbewusste Duplikatvorschlaege (Abschnitt 19.10)
// ---------------------------------------------------------------------------

const REASON_LABELS: Record<string, string> = {
  same_contract_key: "gleicher Vertragsschlüssel",
  same_payee: "gleicher Zahlungsempfänger",
  non_overlapping_periods: "zeitlich nicht überlappend",
  same_frequency: "gleicher Rhythmus",
  matching_due_day: "gleicher Fälligkeitstag",
  amount_transition: "Betragswechsel",
};

/** Ein Paar ist ungeordnet: dasselbe Paar darf nach einem Rollentausch von
 * `contract_a`/`contract_b` nicht als zweiter Eintrag gelten. */
function pairKey(item: ContractAICrosscheckItem): string {
  return [item.contract_a.id, item.contract_b.id].sort().join(":");
}

/**
 * Ziel und Quelle eines vom Modell beurteilten Paares.
 *
 * Bewusst über `mergeTarget` und damit über exakt dieselbe Regel wie die
 * Auswahl in der Liste: Ziel ist der Vertrag mit der spätesten Fälligkeit,
 * sein Betrag ist der aktuell gültige. Abschnitt 19.10 schließt einen zweiten
 * Zusammenführungsmechanismus ausdrücklich aus - das Modell liefert hier nur
 * das Paar, nicht die Zielwahl.
 *
 * `null`, solange einer der beiden Verträge nicht geladen ist. Dann bleibt der
 * Knopf weg, statt ein beliebiges Ziel zu raten.
 */
function aiMergePair(
  item: ContractAICrosscheckItem,
  contractsById: Map<string, Contract>,
): { targetId: string; sourceIds: string[] } | null {
  const first = contractsById.get(item.contract_a.id);
  const second = contractsById.get(item.contract_b.id);
  if (!first || !second) return null;
  const targetId = mergeTarget([first, second], [first.id, second.id]);
  if (!targetId) return null;
  return { targetId, sourceIds: [first.id, second.id].filter((id) => id !== targetId) };
}

/**
 * Duplikatkarte (Abschnitt 19.10) samt Gegenprüfung durch das Sprachmodell
 * (Abschnitt 19.11).
 *
 * Beide Quellen stehen bewusst in EINER Karte und führen über dieselben zwei
 * Aktionen weiter - "Zusammenführen" öffnet die vorhandene Merge-Vorschau,
 * "Gehören nicht zusammen" den vorhandenen Verwerfen-Weg. Ein eigener
 * KI-Pfad zum Schreiben ist ausgeschlossen (Leitsatz 8); das Modell schlägt
 * vor und begründet, entschieden wird hier.
 *
 * Die Karte erscheint für Bearbeiter auch ohne deterministischen Treffer: in
 * Altbestaenden fehlen `contract_key`, `first_seen_date` und
 * `last_seen_date`, weshalb die deterministische Abfrage dort nichts finden
 * KANN - und genau dort wird der Knopf gebraucht.
 */
function DuplicateSuggestionsCard({
  onMerge,
}: {
  onMerge: (targetId: string, sourceIds: string[]) => void;
}) {
  const { canEdit } = useContractContext();
  const { formatDate } = useFormatDate();
  const suggestionsQuery = useContractDuplicateSuggestions();
  const dismissMutation = useDismissContractDuplicate();
  const [searchParams] = useSearchParams();
  const linkedTaskId = searchParams.get("ai_task");
  const [crosscheckTaskId, setCrosscheckTaskId] = useState<string | null>(linkedTaskId);
  const crosscheckTask = useAiTask(crosscheckTaskId);
  const crosscheckMutation = useEnqueueAiTask();
  // Die Gegenpruefung betrachtet ALLE nicht zusammengefuehrten Vertraege des
  // Haushalts, auch beendete und pausierte. Die Liste unter dieser Karte zeigt
  // je nach Filter nur einen Ausschnitt - ohne eine eigene, vollstaendige
  // Abfrage stuende neben einem beurteilten Paar kein Knopf, nur weil der
  // Partner gerade ausgeblendet ist.
  const allContractsQuery = useContractList({ includeEnded: true });
  // Verworfene Paare verschwinden sofort. Die KI-Vorschlaege stammen aus einer
  // Mutation und nicht aus einer Abfrage - sie koennen sich nicht selbst
  // erneuern, und ein soeben verneintes Paar duerfte nicht stehen bleiben.
  const [dismissedPairs, setDismissedPairs] = useState<string[]>([]);

  const contractsById = useMemo(() => {
    const map = new Map<string, Contract>();
    for (const contract of allContractsQuery.data ?? []) map.set(contract.id, contract);
    return map;
  }, [allContractsQuery.data]);

  const items = suggestionsQuery.data?.items ?? [];
  const crosscheck =
    crosscheckTask.data?.task_type === "contract_duplicate_crosscheck" && crosscheckTask.data.result
      ? (crosscheckTask.data.result as unknown as ContractAICrosscheckResponse)
      : null;
  const crosscheckWorking = isAiTaskWorking(crosscheckTask.data);
  const aiItems = (crosscheck?.items ?? []).filter((item) => {
    if (dismissedPairs.includes(pairKey(item))) return false;
    // Nach einem Zusammenfuehren ist der Quellvertrag ausgeblendet und faellt
    // aus der Liste - dann ist das Paar erledigt und verschwindet von selbst.
    if (!allContractsQuery.data) return true;
    return contractsById.has(item.contract_a.id) && contractsById.has(item.contract_b.id);
  });

  if (suggestionsQuery.isLoading) return null;
  // Ohne Treffer und ohne Bearbeitungsrecht gibt es hier nichts zu sehen und
  // nichts zu tun.
  if (items.length === 0 && !canEdit) return null;

  const heading =
    items.length === 0
      ? "Doppelte Verträge und Preiswechsel"
      : items.length === 1
        ? "1 möglicher doppelter Vertrag / Preiswechsel"
        : `${items.length} mögliche doppelte Verträge / Preiswechsel`;
  const truncatedHint = suggestionsQuery.data?.truncated ? " (weitere vorhanden)" : "";

  return (
    <div className="card">
      <div className="page-header" style={{ marginBottom: "var(--space-3)" }}>
        <h2 className="card-heading">
          {heading}
          {truncatedHint}
        </h2>
        {canEdit && (
          <button
            type="button"
            className="btn-secondary"
            disabled={crosscheckMutation.isPending || crosscheckWorking}
            onClick={() =>
              crosscheckMutation.mutate(
                { task_type: "contract_duplicate_crosscheck" },
                { onSuccess: (task) => setCrosscheckTaskId(task.id) },
              )
            }
          >
            {crosscheckMutation.isPending
              ? "Wird eingereiht …"
              : crosscheckWorking
                ? "KI-Aufgabe läuft …"
                : "Verträge gegenprüfen"}
          </button>
        )}
      </div>

      {/* Eine einzige Statusregion fuer den ganzen Lauf: Wartehinweis,
          Sammelmeldung und Ergebniszeile loesen einander ab, statt sich als
          drei konkurrierende Ansagen zu ueberlagern. Sie entsteht erst mit dem
          Knopfdruck - eine dauerhaft leere Region konkurrierte sonst mit der
          Ergebnismeldung des Abgleichs weiter oben auf der Seite. */}
      {(crosscheckMutation.isPending ||
        crosscheckMutation.isError ||
        crosscheckTask.data?.status === "failed" ||
        crosscheckWorking ||
        crosscheck !== null) && (
        <div role="status">
          {(crosscheckMutation.isPending || crosscheckWorking) && (
            <p className="placeholder-note">
              {crosscheckTask.data?.status_message ??
                "Die Vertragsprüfung wartet in der KI-Warteschlange."}{" "}
              An den Verträgen wird dabei nichts verändert; du kannst währenddessen anderswo
              weiterarbeiten.
            </p>
          )}
          {/* Ruhig und ohne rote Fehlerbox: ein nicht erreichbares Modell ist
              kein Absturz, sondern ein abgeschalteter Zusatz. Der Server
              antwortet dafuer bewusst mit 200 und legt den Klartext in
              `meldung` - bei einem Teilergebnis samt den Gruenden des
              KI-Kerns ("(model_timeout ×2)"); der erste liefert die Aktion. */}
          {!crosscheckWorking && crosscheck?.meldung && (
            <AiReasonNotice
              className="placeholder-note"
              code={aiReasonCodeIn(crosscheck.meldung)}
              message={crosscheck.meldung}
            />
          )}
          {!crosscheckWorking && crosscheckMutation.isError && (
            <p className="placeholder-note">
              Die Gegenprüfung ließ sich nicht durchführen:{" "}
              {errorText(crosscheckMutation.error, "unbekannter Fehler")} An den Verträgen wurde
              nichts verändert.
            </p>
          )}
          {!crosscheckWorking && crosscheckTask.data?.status === "failed" && (
            <AiReasonNotice
              className="status-error"
              code={aiReasonCodeIn(crosscheckTask.data.error_message)}
              message={crosscheckTask.data.error_message ?? "Die Gegenprüfung ist fehlgeschlagen."}
            />
          )}
          {!crosscheckWorking && crosscheck && !crosscheck.meldung && (
            <p className="placeholder-note">
              {crosscheck.geprueft === 0
                ? "Das Sprachmodell hat kein Paar gefunden, zu dem es etwas sagen kann."
                : `Gegenprüfung: ${crosscheck.geprueft} Paar(e) beurteilt`}
              {crosscheck.uebersprungen > 0 && ` · ${crosscheck.uebersprungen} übersprungen`}
              {crosscheck.modell_name && ` · Modell: ${crosscheck.modell_name}`}
              {/* Niemals das interne Limit als Gesamtzahl nennen - gleiche Regel
                  wie bei den Duplikatvorschlaegen. */}
              {crosscheck.truncated &&
                " · Es gibt weitere Paare, die dieser Lauf noch nicht betrachtet hat. Nach dem Aufräumen erneut gegenprüfen."}
            </p>
          )}
        </div>
      )}

      {items.length > 0 && (
        <ul className="contract-occurrences">
          {items.map((item) => (
            <li key={`${item.target_id}:${item.source_ids.join(",")}`}>
              <div>
                <span className="txn-payee">{item.payee_label}</span>{" "}
                <span
                  className={`status-badge status-badge--${
                    item.evidence_strength === "strong" ? "positive" : "warning"
                  }`}
                  title={(item.reason_codes ?? [])
                    .map((reason) => REASON_LABELS[reason] ?? reason)
                    .join(", ")}
                >
                  {item.evidence_strength === "strong" ? "starker Beleg" : "zur Prüfung"}
                </span>
                <div className="placeholder-note">
                  {formatMinorUnitsAsEuro(item.older.amount_minor)} (
                  {item.older.last_seen_date ? formatDate(item.older.last_seen_date) : "?"}){" → "}
                  {formatMinorUnitsAsEuro(item.newer.amount_minor)} (
                  {item.newer.first_seen_date ? formatDate(item.newer.first_seen_date) : "?"})
                </div>
              </div>
              {canEdit && (
                <div className="modal-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={dismissMutation.isPending}
                    onClick={() =>
                      dismissMutation.mutate({
                        targetId: item.target_id,
                        sourceIds: item.source_ids,
                      })
                    }
                  >
                    Gehören nicht zusammen
                  </button>
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => onMerge(item.target_id, item.source_ids)}
                  >
                    Zusammenführen
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {aiItems.length > 0 && (
        <>
          <h3 className="card-heading">Gegenprüfung des Sprachmodells</h3>
          <p className="placeholder-note">
            Prüfvorschläge, keine Ergebnisse: das Modell hat weder etwas gespeichert noch etwas
            zusammengeführt. Bestätigt wird wie sonst auch über die Vorschau.
          </p>
          <ul className="contract-occurrences">
            {aiItems.map((item) => {
              const key = pairKey(item);
              const pair = aiMergePair(item, contractsById);
              return (
                <li key={key}>
                  <div>
                    <span className="txn-payee">
                      {item.contract_a.name} ↔ {item.contract_b.name}
                    </span>{" "}
                    <span
                      className="status-badge status-badge--neutral"
                      title={
                        crosscheck?.modell_name
                          ? `Beurteilt vom lokalen Sprachmodell ${crosscheck.modell_name}.`
                          : "Beurteilt vom lokalen Sprachmodell."
                      }
                    >
                      Vorschlag des Sprachmodells
                    </span>{" "}
                    <span
                      className={`status-badge status-badge--${
                        item.gehoert_zusammen ? "highlight" : "neutral"
                      }`}
                    >
                      {item.gehoert_zusammen
                        ? "gehört vermutlich zusammen"
                        : "vermutlich zwei Verträge"}
                    </span>
                    <div className="placeholder-note">
                      {formatMinorUnitsAsEuro(item.contract_a.amount_minor)}
                      {" · "}
                      {formatMinorUnitsAsEuro(item.contract_b.amount_minor)}
                    </div>
                    <p className="card-body">{item.begruendung}</p>
                  </div>
                  {canEdit && pair && (
                    <div className="modal-actions">
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={dismissMutation.isPending}
                        onClick={() =>
                          dismissMutation.mutate(pair, {
                            onSuccess: () => setDismissedPairs((previous) => [...previous, key]),
                          })
                        }
                      >
                        Gehören nicht zusammen
                      </button>
                      <button
                        type="button"
                        className={item.gehoert_zusammen ? "btn-primary" : "btn-secondary"}
                        onClick={() => onMerge(pair.targetId, pair.sourceIds)}
                      >
                        Zusammenführen
                      </button>
                    </div>
                  )}
                  {canEdit && !pair && (
                    <span className="txn-note">
                      Einer der beiden Verträge ist gerade nicht geladen — bitte die Seite neu
                      laden.
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Einblick nach Kategorie
// ---------------------------------------------------------------------------

interface CategoryInsightLeaf {
  key: string;
  label: string;
  color: string | null;
  count: number;
  monthlyMinor: number;
  yearlyMinor: number;
}

interface CategoryInsightGroup extends CategoryInsightLeaf {
  /** Bei "Nicht kategorisiert" leer - dort gibt es keine Unterebene, die
   *  Zeile ist gleichzeitig Gruppe und einziger Eintrag. */
  categories: CategoryInsightLeaf[];
}

/** Fasst `summary.by_category` (eine Zeile je Kategorie) zu Oberpunkten nach
 *  Kategorie-GRUPPE zusammen - bei vier, fuenf Kategorien derselben Gruppe
 *  (typisch bei "Versicherungen") sonst vier, fuenf Zeilen mit demselben
 *  langen Gruppennamen als Praefix statt einer Zwischensumme. */
function buildCategoryInsightGroups(
  byCategory: ContractCategorySummary[],
  categoryIndex: Map<string, ResolvedCategory>,
): CategoryInsightGroup[] {
  const groups = new Map<string, CategoryInsightGroup>();
  let uncategorized: CategoryInsightGroup | null = null;
  for (const entry of byCategory) {
    const resolved = entry.category_id ? categoryIndex.get(entry.category_id) : undefined;
    if (!resolved) {
      uncategorized = {
        key: "uncategorized",
        label: UNCATEGORIZED_LABEL,
        color: null,
        count: entry.count,
        monthlyMinor: entry.monthly_minor,
        yearlyMinor: entry.yearly_minor,
        categories: [],
      };
      continue;
    }
    let group = groups.get(resolved.groupId);
    if (!group) {
      group = {
        key: resolved.groupId,
        label: resolved.groupName,
        color: resolved.groupColor,
        count: 0,
        monthlyMinor: 0,
        yearlyMinor: 0,
        categories: [],
      };
      groups.set(resolved.groupId, group);
    }
    group.count += entry.count;
    group.monthlyMinor += entry.monthly_minor;
    group.yearlyMinor += entry.yearly_minor;
    group.categories.push({
      key: resolved.id,
      label: resolved.name,
      color: resolved.color,
      count: entry.count,
      monthlyMinor: entry.monthly_minor,
      yearlyMinor: entry.yearly_minor,
    });
  }
  const list = [...groups.values()];
  if (uncategorized) list.push(uncategorized);
  return list;
}

type CategoryInsightSortKey = "name" | "count" | "monthly" | "yearly" | "share";

function compareInsightRows(
  key: CategoryInsightSortKey,
  a: CategoryInsightLeaf,
  b: CategoryInsightLeaf,
): number {
  switch (key) {
    case "name":
      return a.label.localeCompare(b.label, "de");
    case "count":
      return a.count - b.count;
    case "yearly":
      return a.yearlyMinor - b.yearlyMinor;
    default:
      // "monthly" und "share" sind proportional zueinander - dieselbe
      // Reihenfolge, ein Anteil ist nichts anderes als der Monatsbetrag
      // normiert auf die Gesamtsumme.
      return a.monthlyMinor - b.monthlyMinor;
  }
}

/**
 * Woher die Verträge stammen - Anzahl und Kosten je Kategorie, ueber Ausgaben
 * und Einnahmen hinweg.
 *
 * Gruppiert nach der frei vergebenen Budget-Kategorie, nicht nach der festen
 * Vertragsart: die Kategorie ist die im Rest der App fuehrende Klassifizierung
 * und genauer, weil frei anpassbar. `summary.by_category` berechnet der
 * Server (contract_service.build_summary, dieselbe Zaehlschleife wie
 * by_type), die Namen/Farben loest diese Komponente gegen den geladenen
 * Kategoriebaum auf - so bleibt eine spaetere Umbenennung sofort korrekt,
 * ohne einen zweiten Namensstand im Server.
 *
 * Zwei Ebenen: Kategorie-GRUPPE als Zwischensumme (Oberpunkt), einzelne
 * Kategorien darunter nur mit ihrem eigenen Namen - die Gruppe steht schon in
 * der Kopfzeile, "Gruppe · Kategorie" waere darunter nur Wiederholung.
 * "Nicht kategorisiert" faellt immer ans Ende, unabhaengig von der
 * Sortierung (dieselbe Regel wie bei der Vertragsliste und beim
 * Fixkostenziel).
 */
function CategoryInsightPanel({
  byCategory,
  categoryIndex,
}: {
  byCategory: ContractCategorySummary[];
  categoryIndex: Map<string, ResolvedCategory>;
}) {
  const [sort, setSort] = useState<{ key: CategoryInsightSortKey; direction: "asc" | "desc" }>({
    key: "monthly",
    direction: "desc",
  });
  // Aufgeklappt ist der Startzustand: bei wenigen Gruppen soll man nicht erst
  // suchen muessen, was sich hinter einem Pfeil versteckt. Eingeklappt wird
  // gezielt, wenn eine Gruppe (z. B. "Gesundheit, Versicherung & Vorsorge"
  // mit vier Kategorien) den Blick auf die anderen Gruppen verstellt.
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const toggleGroupCollapsed = (key: string) => {
    setCollapsedGroups((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const totalMonthly = byCategory.reduce((sum, entry) => sum + entry.monthly_minor, 0);

  const groups = useMemo(
    () => buildCategoryInsightGroups(byCategory, categoryIndex),
    [byCategory, categoryIndex],
  );

  const sortedGroups = useMemo(() => {
    const factor = sort.direction === "asc" ? 1 : -1;
    const [rest, uncategorized] = [
      groups.filter((group) => group.key !== "uncategorized"),
      groups.find((group) => group.key === "uncategorized"),
    ] as const;
    const sortedRest = [...rest]
      .sort(
        (a, b) =>
          factor * compareInsightRows(sort.key, a, b) || a.label.localeCompare(b.label, "de"),
      )
      .map((group) => ({
        ...group,
        categories: [...group.categories].sort(
          (a, b) =>
            factor * compareInsightRows(sort.key, a, b) || a.label.localeCompare(b.label, "de"),
        ),
      }));
    return uncategorized ? [...sortedRest, uncategorized] : sortedRest;
  }, [groups, sort]);

  const toggleSort = (key: CategoryInsightSortKey) => {
    setSort((previous) =>
      previous.key === key
        ? { key, direction: previous.direction === "asc" ? "desc" : "asc" }
        : { key, direction: key === "name" ? "asc" : "desc" },
    );
  };

  if (byCategory.length === 0) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
        <h2 className="card-heading">Keine Verträge</h2>
        <p className="card-body">
          Sobald Verträge angelegt sind, erscheint hier die Aufschlüsselung nach Kategorie.
        </p>
      </div>
    );
  }

  const share = (monthlyMinor: number) =>
    totalMonthly > 0 ? `${Math.round((monthlyMinor / totalMonthly) * 100)} %` : "—";

  return (
    <div className="card">
      <h2 className="card-heading">Verträge nach Kategorie</h2>
      <p className="placeholder-note">
        Zeigt, aus welchen Kategorien sich die laufenden Verträge zusammensetzen — Ausgaben und
        Einnahmen gemeinsam. Verträge ohne Kategorie stehen unter „{UNCATEGORIZED_LABEL}“.
      </p>
      <div className="txn-table-wrapper">
        <table className="txn-table">
          <thead>
            <tr>
              <SortableTableHeader
                label="Kategorie"
                sortKey="name"
                active={sort.key === "name"}
                direction={sort.direction}
                onSort={toggleSort}
              />
              <SortableTableHeader
                label="Anzahl"
                sortKey="count"
                active={sort.key === "count"}
                direction={sort.direction}
                onSort={toggleSort}
                align="amount"
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
              <SortableTableHeader
                label="Anteil"
                sortKey="share"
                active={sort.key === "share"}
                direction={sort.direction}
                onSort={toggleSort}
                align="amount"
              />
            </tr>
          </thead>
          <tbody>
            {sortedGroups.map((group) => {
              const isLeafGroup = group.categories.length === 0;
              const collapsed = !isLeafGroup && collapsedGroups.has(group.key);
              return (
                <Fragment key={group.key}>
                  <tr className="txn-tr grouped-table-row">
                    <td className="txn-td">
                      {isLeafGroup ? (
                        <span className="grouped-table-spacer" aria-hidden="true" />
                      ) : (
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
                      )}
                      <CategoryDot color={group.color} />
                      {group.label}
                    </td>
                    <td className="txn-td txn-td--amount">{group.count}</td>
                    <td className="txn-td txn-td--amount">
                      {formatMinorUnitsAsEuro(group.monthlyMinor)}
                    </td>
                    <td className="txn-td txn-td--amount">
                      {formatMinorUnitsAsEuro(group.yearlyMinor)}
                    </td>
                    <td className="txn-td txn-td--amount">{share(group.monthlyMinor)}</td>
                  </tr>
                  {!collapsed &&
                    group.categories.map((category) => (
                      <tr key={category.key} className="txn-tr">
                        <td className="txn-td" style={{ paddingLeft: "var(--space-6)" }}>
                          <CategoryDot color={category.color} />
                          {category.label}
                        </td>
                        <td className="txn-td txn-td--amount">{category.count}</td>
                        <td className="txn-td txn-td--amount">
                          {formatMinorUnitsAsEuro(category.monthlyMinor)}
                        </td>
                        <td className="txn-td txn-td--amount">
                          {formatMinorUnitsAsEuro(category.yearlyMinor)}
                        </td>
                        <td className="txn-td txn-td--amount">{share(category.monthlyMinor)}</td>
                      </tr>
                    ))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Seite
// ---------------------------------------------------------------------------

export function ContractsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { canEdit } = useContractContext();
  const { formatDate } = useFormatDate();
  const [panel, setPanel] = useState<PanelTab>("list");
  // Richtung haengt an der URL, damit "/fixkosten?richtung=einnahmen" die
  // Einnahmenansicht direkt oeffnet - die Liquiditaetsvorschau und die
  // Uebersicht verlinken dorthin. Ein eigener Navigationseintrag "Einnahmen"
  // waere eine zweite Verwaltung fuer dasselbe Objekt
  // (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Abschnitt 3).
  const direction: ContractDirection =
    searchParams.get("richtung") === "einnahmen" ? "income" : "expense";
  const setDirection = (next: ContractDirection) => {
    const params = new URLSearchParams(searchParams);
    if (next === "income") {
      params.set("richtung", "einnahmen");
    } else {
      params.delete("richtung");
    }
    setSearchParams(params, { replace: true });
  };
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("active");
  const [categoryFilter, setCategoryFilter] = useState<"all" | "uncategorized" | string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("due");
  const [search, setSearch] = useState("");
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [pageError, setPageError] = useState<string | null>(null);

  const linkedContractId = searchParams.get("contract_id");
  const includeEnded =
    statusFilter === "ended" || statusFilter === "all" || Boolean(linkedContractId);
  const contractsQuery = useContractList({ includeEnded });
  const summaryQuery = useContractSummary();
  const { data: categoryGroups } = useCategoryTree();
  const categoryIndex = useMemo(() => buildCategoryIndex(categoryGroups), [categoryGroups]);
  const settingsQuery = useContractSettings();
  const settingsMutation = useUpdateContractSettings();
  const statusMutation = useSetContractStatus();
  const deleteMutation = useDeleteContract();
  const rescanMutation = useRescanContracts();

  const linkedContract = contractsQuery.data?.find((contract) => contract.id === linkedContractId);
  const activeDialog =
    dialog ??
    (linkedContract
      ? { kind: "detail" as const, contract: linkedContract, initialTab: "amounts" as const }
      : null);

  const closeDialog = () => {
    setDialog(null);
    if (searchParams.has("ai_task") || searchParams.has("contract_id")) {
      const next = new URLSearchParams(searchParams);
      next.delete("ai_task");
      next.delete("contract_id");
      setSearchParams(next, { replace: true });
    }
  };

  const contracts = useMemo(() => {
    const all = contractsQuery.data ?? [];
    const needle = search.trim().toLowerCase();
    const filtered = all
      .filter((contract) => contract.direction === direction)
      .filter((contract) => {
        if (statusFilter === "active") return contract.status === "active";
        if (statusFilter === "ending") {
          return contract.status === "cancelled" || contract.ends_on !== null;
        }
        if (statusFilter === "ended") {
          return contract.status === "ended" || contract.status === "not_a_contract";
        }
        return true;
      })
      .filter((contract) => {
        if (categoryFilter === "all") return true;
        if (categoryFilter === "uncategorized") return contract.category_id === null;
        return contract.category_id === categoryFilter;
      })
      .filter(
        (contract) =>
          !needle ||
          contract.name.toLowerCase().includes(needle) ||
          (contract.provider_label ?? "").toLowerCase().includes(needle),
      );
    return sortContracts(filtered, sortKey, categoryIndex);
  }, [
    contractsQuery.data,
    direction,
    statusFilter,
    categoryFilter,
    search,
    sortKey,
    categoryIndex,
  ]);

  const summary = summaryQuery.data;
  const monthlyTotal =
    direction === "expense" ? summary?.monthly_expense_minor : summary?.monthly_income_minor;
  const yearlyTotal =
    direction === "expense" ? summary?.yearly_expense_minor : summary?.yearly_income_minor;

  const toggleSelected = (contractId: string) =>
    setSelected((previous) =>
      previous.includes(contractId)
        ? previous.filter((id) => id !== contractId)
        : [...previous, contractId],
    );

  const runStatusChange = (contract: Contract, status: ContractStatus) => {
    setOpenMenu(null);
    setPageError(null);
    statusMutation.mutate(
      { contractId: contract.id, input: { status } },
      {
        onError: (error) =>
          setPageError(errorText(error, "Der Status konnte nicht geändert werden.")),
      },
    );
  };

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Verträge &amp; Fixkosten</h1>
          <p className="placeholder-note">
            Jeder Vertrag lässt sich bearbeiten, beenden, zusammenführen und löschen.
          </p>
        </div>
        {canEdit && (
          <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => rescanMutation.mutate()}
              disabled={rescanMutation.isPending}
            >
              {rescanMutation.isPending ? "Wird abgeglichen…" : "Jetzt abgleichen"}
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() => setDialog({ kind: "create" })}
            >
              + Vertrag
            </button>
          </div>
        )}
      </div>

      {/* Ergebnis des Abgleichs bleibt stehen, bis der naechste Lauf startet -
          die Zahlen sind die einzige Rueckmeldung darueber, was der Lauf
          getan hat, und duerfen nicht nach ein paar Sekunden verschwinden. */}
      {rescanMutation.isSuccess && (
        <p role="status" className="placeholder-note">
          Abgleich abgeschlossen: {describeRescanResult(rescanMutation.data)}
        </p>
      )}
      {rescanMutation.isError && (
        <p role="alert" className="status-error">
          {errorText(rescanMutation.error, "Der Abgleich ist fehlgeschlagen.")}
        </p>
      )}

      {/* Direkt unter "Jetzt abgleichen" und "+ Vertrag", nicht in einem der
          Register darunter: Dubletten und Preiswechsel sind ein Ergebnis
          dieser beiden Aktionen (Abgleich bzw. Neuanlage), nicht Teil einer
          bestimmten Ansicht - und sollen deshalb unabhaengig vom gewaehlten
          Register sichtbar bleiben. */}
      <DuplicateSuggestionsCard
        onMerge={(targetId, sourceIds) => setDialog({ kind: "merge", targetId, sourceIds })}
      />

      {/* Register oberhalb aller Kennzahlen: die Zielansicht rechnet gegen
          dieselbe Fixkostensumme, zeigt sie aber im Verhaeltnis zum Ziel. Beide
          Kopfzeilen gleichzeitig ergaeben zwei Kennzahlenbloecke uebereinander,
          die dasselbe Geld verschieden beschriften. */}
      <div className="filter-tabs" role="group" aria-label="Ansicht">
        {PANEL_TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`filter-tab ${panel === item.key ? "filter-tab--active" : ""}`}
            aria-pressed={panel === item.key}
            onClick={() => setPanel(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {panel === "target" && <FixedCostsTargetPanel />}

      {panel === "categories" && (
        <CategoryInsightPanel
          byCategory={summary?.by_category ?? []}
          categoryIndex={categoryIndex}
        />
      )}

      {panel === "cashflow" && <CashflowCard />}

      {panel === "deadlines" && <TermDeadlinesCard deadlines={summary?.term_deadlines ?? []} />}

      {panel === "list" && (
        <>
          <div className="metric-grid">
            <div className="card metric-card">
              <span className="metric-label">
                {direction === "expense" ? "Monatliche Fixkosten" : "Monatliche Einnahmen"}
              </span>
              <span className="metric-value">
                {monthlyTotal === undefined ? "—" : formatMinorUnitsAsEuro(monthlyTotal)}
              </span>
              <span className="metric-hint">Hochgerechnet pro Monat</span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">
                {direction === "expense" ? "Jährliche Fixkosten" : "Jährliche Einnahmen"}
              </span>
              <span className="metric-value">
                {yearlyTotal === undefined ? "—" : formatMinorUnitsAsEuro(yearlyTotal)}
              </span>
              <span className="metric-hint">Hochgerechnet pro Jahr</span>
            </div>
            {/* Bewusst NICHT "Verfuegbar nach Fixkosten": diese Zahl rechnet
            Einnahmevertraege gegen Ausgabevertraege. Die gleichnamige Kennzahl
            in Uebersicht und Budget rechnet gegen die GEBUCHTEN Einnahmen -
            beide gleich zu benennen ergaebe zwei verschiedene Zahlen unter
            einem Namen. */}
            <div className="card metric-card">
              <span className="metric-label">Vertragssaldo</span>
              <span className="metric-value">
                {summary === undefined ? "—" : formatMinorUnitsAsEuro(summary.monthly_net_minor)}
              </span>
              <span className="metric-hint">Einnahme- minus Ausgabeverträge je Monat</span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Läuft demnächst aus</span>
              <span className="metric-value">{summary?.ending_soon.length ?? 0}</span>
              <span className="metric-hint">Innerhalb der nächsten 60 Tage</span>
            </div>
          </div>

          <div className="direction-toggle">
            <button
              type="button"
              className={`direction-btn ${direction === "expense" ? "direction-btn--active" : ""}`}
              onClick={() => setDirection("expense")}
              aria-pressed={direction === "expense"}
            >
              Ausgaben
            </button>
            <button
              type="button"
              className={`direction-btn ${direction === "income" ? "direction-btn--active" : ""}`}
              onClick={() => setDirection("income")}
              aria-pressed={direction === "income"}
            >
              Einnahmen
            </button>
          </div>

          {direction === "income" ? <IncomeOverview canEdit={canEdit} /> : null}

          <div className="category-toolbar">
            <div className="filter-tabs" role="group" aria-label="Status">
              {STATUS_FILTERS.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  className={`filter-tab ${statusFilter === item.key ? "filter-tab--active" : ""}`}
                  aria-pressed={statusFilter === item.key}
                  onClick={() => setStatusFilter(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <input
              type="search"
              className="form-input"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Vertrag oder Anbieter suchen"
              aria-label="Verträge durchsuchen"
            />
            <label className="form-label" htmlFor="contract-category-filter">
              Kategorie
              <select
                id="contract-category-filter"
                className="form-input"
                value={categoryFilter}
                onChange={(event) => setCategoryFilter(event.target.value)}
              >
                <option value="all">Alle Kategorien</option>
                <option value="uncategorized">{UNCATEGORIZED_LABEL}</option>
                {(categoryGroups ?? [])
                  .filter((group) => !group.is_archived)
                  .map((group) => (
                    <optgroup key={group.id} label={group.name}>
                      {group.categories
                        .filter((category) => !category.is_archived)
                        .map((category) => (
                          <option key={category.id} value={category.id}>
                            {category.name}
                          </option>
                        ))}
                    </optgroup>
                  ))}
              </select>
            </label>
            <label className="form-label" htmlFor="contract-sort">
              Sortieren nach
              <select
                id="contract-sort"
                className="form-input"
                value={sortKey}
                onChange={(event) => setSortKey(event.target.value as SortKey)}
              >
                {SORT_OPTIONS.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            {canEdit && (
              <label className="form-label" htmlFor="contract-auto-end">
                Automatisch beenden nach
                <select
                  id="contract-auto-end"
                  className="form-input"
                  value={settingsQuery.data?.auto_end_after_missed ?? "3"}
                  onChange={(event) =>
                    settingsMutation.mutate({
                      auto_end_after_missed: event.target
                        .value as ContractSettings["auto_end_after_missed"],
                    })
                  }
                >
                  <option value="off">nie (nur Vorschlag)</option>
                  <option value="2">2 ausgebliebenen Zahlungen</option>
                  <option value="3">3 ausgebliebenen Zahlungen</option>
                  <option value="4">4 ausgebliebenen Zahlungen</option>
                </select>
              </label>
            )}
          </div>

          {selected.length > 1 && canEdit && (
            <div className="selection-bar">
              <span className="selection-bar-count">{selected.length} ausgewählt</span>
              <span className="selection-bar-message">
                Gehören diese Zeilen zu einem Vertrag? Der zuletzt gültige wird zum Ziel.
              </span>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  // Ziel ist der zuletzt gueltige Vertrag: seine Preisstufe ist die
                  // aktuelle, alle anderen wandern als aeltere Stufen in den Verlauf.
                  const target = mergeTarget(contracts, selected);
                  if (!target) return;
                  setDialog({
                    kind: "merge",
                    targetId: target,
                    sourceIds: selected.filter((id) => id !== target),
                  });
                }}
              >
                Zu einem Vertrag zusammenführen
              </button>
              <button type="button" className="btn-secondary" onClick={() => setSelected([])}>
                Auswahl aufheben
              </button>
            </div>
          )}

          {pageError && (
            <p role="alert" className="status-error">
              {pageError}
            </p>
          )}

          {contractsQuery.isLoading && <p role="status">Verträge werden geladen…</p>}

          {!contractsQuery.isLoading && contracts.length === 0 && (
            <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
              <h2 className="card-heading">Keine Verträge</h2>
              <p className="card-body">
                Miete, Versicherungen oder Abonnements hier erfassen — oder in der Review-Inbox aus
                dem Buchungsverlauf übernehmen.
              </p>
            </div>
          )}

          {contracts.length > 0 && (
            <div className="txn-table-wrapper contract-list-wrap">
              <table className="txn-table contract-list-table">
                <thead>
                  <tr>
                    {canEdit && <th className="txn-th txn-th--select" aria-label="Auswahl" />}
                    <th className="txn-th">Vertrag</th>
                    <th className="txn-th">Kategorie</th>
                    <th className="txn-th">Rhythmus</th>
                    <th className="txn-th">Nächste Fälligkeit</th>
                    <th className="txn-th">Laufzeit</th>
                    <th className="txn-th">Status</th>
                    <th className="txn-th txn-th--amount">Betrag</th>
                    <th className="txn-th txn-th--amount">Pro Jahr</th>
                    {canEdit && <th className="txn-th txn-th--actions">Aktionen</th>}
                  </tr>
                </thead>
                <tbody>
                  {contracts.map((contract) => (
                    <tr key={contract.id} className="txn-tr contract-list-card">
                      {canEdit && (
                        <td className="txn-td txn-td--select contract-list-card__select">
                          <input
                            type="checkbox"
                            checked={selected.includes(contract.id)}
                            onChange={() => toggleSelected(contract.id)}
                            aria-label={`${contract.name} auswählen`}
                          />
                        </td>
                      )}
                      <td className="txn-td contract-list-card__name" data-label="Vertrag">
                        <button
                          type="button"
                          className="cell-button"
                          onClick={() => setDialog({ kind: "detail", contract })}
                        >
                          <span className="txn-payee">{contract.name}</span>
                        </button>
                        {contract.provider_label && contract.provider_label !== contract.name && (
                          <span className="txn-note">{contract.provider_label}</span>
                        )}
                        {/* Bei gleichnamigen Vertraegen (z. B. mehrere Vertraege desselben
                        Anbieters fuer Mobilfunk, DSL, TV) ist die Notiz die einzige Stelle, an
                        der sich unterscheiden laesst, welcher welcher ist - ohne
                        sie jedes Mal ueber "Bearbeiten" nachzuschlagen. */}
                        {contract.note && <span className="txn-note">{contract.note}</span>}
                      </td>
                      <td className="txn-td contract-list-card__category" data-label="Kategorie">
                        {contract.category_id && categoryIndex.get(contract.category_id) ? (
                          <>
                            <CategoryDot
                              color={categoryIndex.get(contract.category_id)?.color ?? null}
                            />
                            {categoryIndex.get(contract.category_id)?.name}
                          </>
                        ) : (
                          <span className="txn-note">{UNCATEGORIZED_LABEL}</span>
                        )}
                      </td>
                      <td className="txn-td contract-list-card__frequency" data-label="Rhythmus">
                        <span className="status-badge status-badge--neutral">
                          {FREQUENCY_LABEL[contract.frequency]}
                        </span>
                        {contract.reserve_enabled && (contract.reserve_monthly_minor ?? 0) > 0 && (
                          <span className="contract-reserve-inline">
                            <span className="status-badge status-badge--highlight">
                              Fixkosten-Rücklage
                            </span>
                            <small>
                              {formatMinorUnitsAsEuro(contract.reserve_monthly_minor ?? 0)} / Monat
                            </small>
                          </span>
                        )}
                      </td>
                      <td
                        className="txn-td contract-list-card__due"
                        data-label="Nächste Fälligkeit"
                        style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}
                      >
                        {formatDate(contract.next_due_date)}
                      </td>
                      <td className="txn-td contract-list-card__term" data-label="Laufzeit">
                        <TermDeadlineCell
                          endsOn={contract.ends_on}
                          cancelledOn={contract.cancelled_on}
                          deadline={contract.term_deadline}
                        />
                      </td>
                      <td className="txn-td contract-list-card__status" data-label="Status">
                        <span className={`status-badge ${STATUS_CLASS[contract.status]}`}>
                          {STATUS_LABEL[contract.status]}
                        </span>
                        {contract.missed_periods > 0 && (
                          <span
                            className="status-badge status-badge--warning"
                            style={{ marginLeft: "var(--space-1)" }}
                            title={missedPaymentHint(contract, formatDate)}
                          >
                            Zahlung ausgeblieben
                          </span>
                        )}
                      </td>
                      <td
                        data-label="Betrag"
                        className={`txn-td txn-td--amount contract-list-card__amount ${
                          contract.direction === "expense"
                            ? "transaction-amount--negative"
                            : "transaction-amount--positive"
                        }`}
                      >
                        {formatMinorUnitsAsEuro(signedForDisplay(contract, contract.amount_minor))}
                      </td>
                      <td
                        className="txn-td txn-td--amount contract-list-card__yearly"
                        data-label="Pro Jahr"
                      >
                        {formatMinorUnitsAsEuro(signedForDisplay(contract, yearlyMinor(contract)))}
                      </td>
                      {canEdit && (
                        <td
                          className="txn-td txn-td--actions contract-list-card__actions"
                          data-label="Aktionen"
                        >
                          <button
                            type="button"
                            className="btn-inline"
                            onClick={() => setDialog({ kind: "edit", contract })}
                          >
                            Bearbeiten
                          </button>
                          <RowMenu
                            label={`Weitere Aktionen für ${contract.name}`}
                            open={openMenu === contract.id}
                            onToggle={() =>
                              setOpenMenu((previous) =>
                                previous === contract.id ? null : contract.id,
                              )
                            }
                          >
                            <button
                              type="button"
                              role="menuitem"
                              className="category-menu-item"
                              onClick={() => {
                                setOpenMenu(null);
                                setDialog({ kind: "amount", contract });
                              }}
                            >
                              Betrag korrigieren
                            </button>
                            {contract.status !== "ended" && (
                              <button
                                type="button"
                                role="menuitem"
                                className="category-menu-item"
                                onClick={() => runStatusChange(contract, "ended")}
                              >
                                Als beendet markieren
                              </button>
                            )}
                            {contract.status === "active" && (
                              <button
                                type="button"
                                role="menuitem"
                                className="category-menu-item"
                                onClick={() => runStatusChange(contract, "paused")}
                              >
                                Pausieren
                              </button>
                            )}
                            {(contract.status === "ended" ||
                              contract.status === "not_a_contract") && (
                              <button
                                type="button"
                                role="menuitem"
                                className="category-menu-item"
                                onClick={() => runStatusChange(contract, "active")}
                              >
                                Wieder aktivieren
                              </button>
                            )}
                            {contract.status !== "not_a_contract" &&
                              contract.status !== "ended" && (
                                <button
                                  type="button"
                                  role="menuitem"
                                  className="category-menu-item"
                                  onClick={() => runStatusChange(contract, "not_a_contract")}
                                >
                                  Kein Vertrag
                                </button>
                              )}
                            <button
                              type="button"
                              role="menuitem"
                              className="category-menu-item"
                              onClick={() => {
                                setOpenMenu(null);
                                setDialog({ kind: "delete", contract });
                              }}
                            >
                              Löschen
                            </button>
                          </RowMenu>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {activeDialog?.kind === "create" && (
        <ContractFormModal
          contract={null}
          defaultDirection={direction}
          onClose={closeDialog}
          onCreated={(created) =>
            setDialog({ kind: "detail", contract: created, initialTab: "amounts" })
          }
        />
      )}
      {activeDialog?.kind === "edit" && (
        <ContractFormModal
          contract={activeDialog.contract}
          defaultDirection={activeDialog.contract.direction}
          onClose={closeDialog}
        />
      )}
      {activeDialog?.kind === "detail" && (
        <ContractDetailModal
          contract={activeDialog.contract}
          initialTab={activeDialog.initialTab}
          onClose={closeDialog}
          // Die beiden Aktionen tauschen den Dialog aus, statt einen zweiten
          // darueberzulegen - gestapelte Modals sind mit der Tastatur kaum
          // wieder zu verlassen.
          onCorrectAmount={() => setDialog({ kind: "amount", contract: activeDialog.contract })}
          onDelete={() => setDialog({ kind: "delete", contract: activeDialog.contract })}
        />
      )}
      {activeDialog?.kind === "amount" && (
        <AmountModal contract={activeDialog.contract} onClose={closeDialog} />
      )}
      {activeDialog?.kind === "merge" && (
        <MergeModal
          targetId={activeDialog.targetId}
          sourceIds={activeDialog.sourceIds}
          onClose={() => {
            setSelected([]);
            closeDialog();
          }}
        />
      )}
      {activeDialog?.kind === "delete" && (
        <Modal title="Vertrag löschen" onClose={closeDialog}>
          <p className="card-body">
            „{activeDialog.contract.name}“ wird endgültig gelöscht. Anders als bei Kategorien geht
            dabei keine historische Summe verloren — nur der Vertrag selbst samt Verlauf.
          </p>
          <p className="text-sm">
            Soll er nur nicht mehr vorgeschlagen werden, ist „Kein Vertrag“ die bessere Wahl.
          </p>
          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={closeDialog}>
              Abbrechen
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={deleteMutation.isPending}
              onClick={() =>
                deleteMutation.mutate(activeDialog.contract.id, {
                  onSuccess: closeDialog,
                  onError: (error) => setPageError(errorText(error, "Löschen ist fehlgeschlagen.")),
                })
              }
            >
              Endgültig löschen
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
