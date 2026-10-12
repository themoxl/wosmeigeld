import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  acceptDuplicatesBulk,
  assessDuplicates,
  acceptReviewProposal,
  applyAIProposals,
  classifyWithAI,
  correctReviewProposal,
  getAIDecisions,
  getContractSuggestionDetail,
  getReviewHistoryMatch,
  getReviewHistoryMatches,
  getReviewInbox,
  listCategories,
  listClassificationRules,
  previewAIProposals,
  previewReviewRule,
} from "../lib/api";
import {
  isClassificationRunActive,
  useCancelClassificationRun,
  useClassificationRunCompletionEffect,
  useLatestClassificationRun,
} from "../hooks/useClassificationRun";
import { useEnqueueAiTask } from "../hooks/useAiTaskQueue";
import { useFormatDate } from "../hooks/useProfile";
import { isUnassessedVerdict } from "../lib/aiReasons";
import { formatMonthYear } from "../lib/dates";
import { formatMinorUnitsAsEuro } from "../lib/money";
import type {
  AIClassifyInput,
  AIDecisionLog,
  AIProposalBatchResult,
  AIProposalItem,
  ClassificationRule,
  ContractFrequency,
  ContractKeySource,
  ContractType,
  ReviewContractAmountStep,
  ReviewContractScore,
  ReviewContractSuggestionDetail,
  ReviewInboxResponse,
  ReviewItemContractAmountChangeCheck,
  ReviewItemContractDuplicate,
  ReviewItemContractEndedCheck,
  ReviewItemContractSuggestion,
  ReviewHistoryMatchItem,
  RuleApplyResult,
} from "../lib/types";
import { Modal } from "../components/Modal";
import { AiErrorNotice, AiReasonNotice } from "../components/AiReasonNotice";
import { BookedDuplicatesPanel } from "../components/BookedDuplicatesPanel";
import { CategoryPicker } from "../components/CategoryPicker";

type CorrectModalState =
  | {
      kind: "unclassified";
      transactionId: string;
      payee: string;
      purpose: string;
      amountMinor: number;
      accountId: string;
      initialCategoryId: string;
    }
  | { kind: "possible_duplicate"; importRowId: string; payee: string }
  | { kind: "contract_suggestion"; suggestion: ReviewItemContractSuggestion };

const FREQUENCY_LABELS: Record<ContractFrequency, string> = {
  weekly: "Wöchentlich",
  monthly: "Monatlich",
  quarterly: "Vierteljährlich",
  semiannual: "Halbjährlich",
  yearly: "Jährlich",
};

const CONTRACT_TYPE_LABELS: Record<ContractType, string> = {
  subscription: "Abonnement",
  insurance: "Versicherung",
  rent: "Miete & Wohnen",
  loan: "Kredit",
  installment: "Ratenkauf",
  utility: "Energie",
  telecom: "Telekommunikation",
  membership: "Mitgliedschaft",
  donation: "Spende",
  savings: "Sparplan",
  income: "Einnahme",
  other: "Sonstiges",
};

const CONTRACT_KEY_SOURCE_LABELS: Record<ContractKeySource, string> = {
  mandate: "Mandatsreferenz der Lastschrift",
  purpose_pattern: "Vertragsnummer aus dem Verwendungszweck",
  manual: "von Hand hinterlegt",
};

/**
 * Die fünf Bestandteile des Vertrags-Scores mit ihrem Gewicht
 * (docs/VERTRAEGE-FIXKOSTEN-PLAN.md Abschnitt 6.4).
 *
 * Gewicht und Klartext stehen bewusst mit in der Oberfläche: „71 %" allein
 * begründet nichts, und ohne Begründung kann niemand beurteilen, ob der
 * Vorschlag stimmt. Die Gewichte sind hier gespiegelt, nicht berechnet – wer
 * sie im Server ändert, muss sie hier nachziehen.
 */
const SCORE_PARTS: {
  key: Exclude<keyof ReviewContractScore, "total">;
  label: string;
  weight: number;
  explanation: string;
}[] = [
  {
    key: "date_stability",
    label: "Datumsstabilität",
    weight: 0.3,
    explanation: "Wie gleichmäßig der Zahltag liegt.",
  },
  {
    key: "amount_stability",
    label: "Betragsstabilität",
    weight: 0.2,
    explanation: "Wie wenig der Betrag schwankt.",
  },
  {
    key: "occurrence_count",
    label: "Vorkommen",
    weight: 0.15,
    explanation: "Wie viele Zahlungen belegt sind – ab sechs voll gewertet.",
  },
  {
    key: "category_affinity",
    label: "Kategorie",
    weight: 0.2,
    explanation: "Wie vertragstypisch die Kategorie ist (Miete hoch, Restaurant null).",
  },
  {
    key: "payment_method",
    label: "Zahlungsart",
    weight: 0.15,
    explanation: "Lastschrift und Dauerauftrag zählen voll, Kartenzahlung nicht.",
  },
];

const REVIEW_PAGE_SIZE = 100;

type ContractEndedAction = "ended" | "active" | "paused";

/**
 * Die drei moeglichen Antworten auf "laeuft dieser Vertrag noch?".
 *
 * Bewusst alle drei als btn-secondary und in dieser Reihenfolge: der Abgleich
 * weiss nur, dass nichts kam, und darf deshalb keine der Antworten optisch als
 * die erwartete auszeichnen. Ein hervorgehobenes "Beendet" wuerde eine bloss
 * verschobene Zahlung reihenweise zu Vertragsenden machen.
 */
const CONTRACT_ENDED_ACTIONS: { action: ContractEndedAction; label: string; title: string }[] = [
  {
    action: "ended",
    label: "Beendet",
    title: "Der Vertrag ist beendet - er zählt ab sofort nicht mehr in den Fixkosten mit.",
  },
  {
    action: "active",
    label: "Läuft weiter",
    title:
      "Der Vertrag läuft weiter, die Zahlung kam nur später oder von einem nicht importierten Konto. Der Fehlzähler wird zurückgesetzt.",
  },
  {
    action: "paused",
    label: "Pausiert",
    title:
      "Der Vertrag ruht vorübergehend, soll aber erhalten bleiben. Er zählt nicht in den Fixkosten mit.",
  },
];

/**
 * Der geänderte Betrag als ein Satz - richtungsabhängig formuliert.
 *
 * "gesunken" und "gestiegen" hängen ausschließlich am Vorzeichen von
 * `delta_minor`. Der Anlassfall ist eine SENKUNG (ein Festnetzvertrag von
 * 49,95 € → 39,95 €); ein pauschales "ist teurer geworden"
 * wäre dort schlicht falsch und hat als Push-Text genau deshalb schon einmal
 * in die Irre geführt.
 *
 * Die Beträge stehen hier unsigniert. Sie liegen wie überall in `contracts`
 * positiv in der Datenbank, und bei einem Ausgabevertrag wäre "von -49,95 €
 * auf -39,95 € gesunken" rechnerisch das Gegenteil dessen, was der Satz sagt.
 * Die Richtung steht stattdessen im Subjekt.
 */
function describeAmountChange(check: ReviewItemContractAmountChangeCheck): string {
  const subject = check.direction === "income" ? "Die Einnahme ist" : "Der Betrag ist";
  const verb = check.delta_minor < 0 ? "gesunken" : "gestiegen";
  const from = formatMinorUnitsAsEuro(check.old_amount_minor);
  const to = formatMinorUnitsAsEuro(check.new_amount_minor);
  return `${subject} von ${from} auf ${to} ${verb}`;
}

/** Vorzeichenbehaftete Prozentangabe, deutsch formatiert. Kein Konfidenzwert,
 * sondern die tatsächlich gerechnete Änderung gegenüber dem alten Betrag. */
function formatSignedPercent(value: number): string {
  const formatted = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(value);
  return `${value > 0 ? "+" : ""}${formatted} %`;
}

/**
 * Beträge liegen am Vorschlag - wie in `contracts` - IMMER positiv, die
 * Richtung steht getrennt daneben. Ohne diese Umrechnung stünde eine Ausgabe
 * hier als Einnahme.
 */
function signedAmountMinor(amountMinor: number, direction: "expense" | "income"): number {
  return direction === "expense" ? -amountMinor : amountMinor;
}

/**
 * Die erkannte Betragstreppe als ein Satz: "49,95 € bis Mai 2026, seit Juni
 * 2026 39,95 €".
 *
 * Der Satz ist der eigentliche Zweck der Anzeige: aus zwei Preisen entsteht
 * EIN Vertrag mit Betragsverlauf, nicht zwei Verträge. Wer das beim
 * Bestätigen nicht sieht, legt den zweiten von Hand noch einmal an.
 */
function describeAmountSteps(
  steps: ReviewContractAmountStep[],
  direction: "expense" | "income",
): string {
  return steps
    .map((step, index) => {
      const amount = formatMinorUnitsAsEuro(signedAmountMinor(step.amount_minor, direction));
      return index === steps.length - 1
        ? `seit ${formatMonthYear(step.first_date)} ${amount}`
        : `${amount} bis ${formatMonthYear(step.last_date)}`;
    })
    .join(", ");
}

// Maschinenlesbare Gruende des Servers in eine Erklaerung uebersetzen, die
// sagt WARUM eine Buchung nicht angefasst wurde - "uebersprungen" allein
// beantwortet die einzige Frage nicht, die der Nutzer an dieser Stelle hat.
const PROPOSAL_REASON_TEXTS: Record<string, string> = {
  below_confidence: "unter der Konfidenzschwelle",
  no_match: "kein passender Treffer der KI",
  split_transaction: "Split-Buchung – wird nie pauschal geändert",
  already_categorized: "bereits kategorisiert – bleibt unverändert",
  category_inactive: "Kategorie inzwischen archiviert",
  transaction_not_found: "Buchung nicht gefunden",
  archived_transaction: "Buchung archiviert",
  version_conflict: "zwischenzeitlich geändert",
};

function describeProposalOutcome(item: AIProposalItem): string {
  if (item.status === "applied") return "wird zugeordnet";
  const reason = item.reason ? PROPOSAL_REASON_TEXTS[item.reason] : null;
  return reason ?? item.reason ?? item.status;
}

/**
 * Konfidenz-Chip an einer Buchung.
 *
 * `source` unterscheidet den Modellvorschlag vom Vorschlag aus dem eigenen
 * Bestand (Vorfilter für bekannte Empfänger im Klassifikationslauf,
 * services/worker/worker/ai/known_payees.py). Bei letzterem lief kein Modell -
 * ein „✨ KI 95 %“ wäre dort schlicht falsch und würde dem Nutzer eine
 * Modellaussage vorspielen, die es nie gab.
 */
function AIBadge({
  confidence,
  reasonCode,
  source,
}: {
  confidence: number;
  reasonCode: string;
  source?: string;
}) {
  const percent = Math.round(confidence * 100);
  const fromHistory = source === "history";
  const label = fromHistory ? "Bekannt" : "KI";
  const icon = fromHistory ? "↩" : "✨";
  return (
    <span
      className="status-badge status-badge--positive"
      title={
        fromHistory
          ? `Aus dem eigenen Bestand übernommen, ohne Modellaufruf – Sicherheit ${percent}% (Grund: ${reasonCode})`
          : `KI-Klassifikation mit Konfidenz ${percent}% (Grund: ${reasonCode})`
      }
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "4px",
        fontSize: "12px",
        fontWeight: 600,
        backgroundColor: "var(--color-positive-muted)",
        color: "var(--color-positive)",
        borderColor: "var(--color-positive)",
        padding: "2px 8px",
        borderRadius: "var(--radius-sm)",
      }}
    >
      {icon} {label} {percent}% ({reasonCode})
    </span>
  );
}

/**
 * Warum das System glaubt, dass es ein Vertrag ist - Bestandteil für
 * Bestandteil.
 *
 * Bewusst immer sichtbar und nicht hinter einem Aufklapper: die Zahl allein
 * ist in einem Finanzprodukt wertlos, und wer erst klicken muss, prüft nicht.
 */
function ScoreBreakdown({ score }: { score: ReviewContractScore }) {
  return (
    <ul className="contract-score">
      {SCORE_PARTS.map((part) => {
        const percent = Math.round(score[part.key] * 100);
        return (
          <li key={part.key} className="contract-score__row">
            <span className="contract-score__label">
              {part.label}
              <span className="contract-score__weight">
                zählt {Math.round(part.weight * 100)} %
              </span>
            </span>
            <span className="contract-score__meter" aria-hidden="true">
              <span className="contract-score__meter-fill" style={{ width: `${percent}%` }} />
            </span>
            <span className="contract-score__value">{percent} %</span>
            <span className="contract-score__hint">{part.explanation}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function ReviewInboxPage() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();

  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  const [correctModal, setCorrectModal] = useState<CorrectModalState | null>(null);
  const [suggestionDetailSourceId, setSuggestionDetailSourceId] = useState<string | null>(null);
  const [selectedCategoryId, setSelectedCategoryId] = useState<string>("");
  const [gewaehlteDubletten, setGewaehlteDubletten] = useState<Set<string>>(new Set());
  const [bulkDuplicatesMessage, setBulkDuplicatesMessage] = useState<string | null>(null);
  // Urteile der KI je Importzeile. Bewusst nur im Seitenzustand und nicht
  // gespeichert: es ist ein Hinweis zum Ansehen, keine Entscheidung, die
  // ueberdauern soll.
  // `unassessed`: Teilergebnis - das Modell wurde zu diesem Paar nach einem
  // Abbruch gar nicht mehr gefragt. Das ist kein Urteil "unsicher".
  const [dublettenUrteile, setDublettenUrteile] = useState<
    Record<string, { verdict: string; reason: string; confidence: number; unassessed: boolean }>
  >({});
  // Ergebnis der letzten Einschaetzung: Klartext und - bei einem Ausfall oder
  // Teilergebnis - der Grund des KI-Kerns fuer die passende Aktion.
  const [kiHinweis, setKiHinweis] = useState<{ code: string | null; text: string } | null>(null);
  const [createRule, setCreateRule] = useState<boolean>(true);
  const [payeePattern, setPayeePattern] = useState<string>("");
  const [showRegexHelp, setShowRegexHelp] = useState<boolean>(false);
  const [ruleMatchPayee, setRuleMatchPayee] = useState(true);
  const [ruleMatchPurpose, setRuleMatchPurpose] = useState(false);
  const [ruleMatchExactAmount, setRuleMatchExactAmount] = useState(false);
  const [ruleMatchAccount, setRuleMatchAccount] = useState(false);
  const [rulePreview, setRulePreview] = useState<RuleApplyResult | null>(null);
  const [historyMatchesByTransaction, setHistoryMatchesByTransaction] = useState<
    Record<string, ReviewHistoryMatchItem>
  >({});
  const [unclassifiedSearchInput, setUnclassifiedSearchInput] = useState("");
  const [unclassifiedSearch, setUnclassifiedSearch] = useState("");
  const [unclassifiedOffset, setUnclassifiedOffset] = useState(0);

  // Die Eingabe bleibt lokal und damit auch waehrend eines Requests stabil.
  // Erst nach einer kurzen Tipp-Pause wird serverseitig gesucht; so feuert ein
  // Wort nicht sechs Requests ab und der Picker/die Tabelle werden nicht bei
  // jedem einzelnen Zeichen neu aufgebaut.
  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setUnclassifiedSearch(unclassifiedSearchInput.trim());
      setUnclassifiedOffset(0);
    }, 250);

    return () => window.clearTimeout(timeoutId);
  }, [unclassifiedSearchInput]);

  // Batch-KI-Kategorisierung: der Lauf passiert im Hintergrund-Worker, sein
  // Zustand liegt vollstaendig auf dem Server (siehe hooks/useClassificationRun.ts).
  // Frueher hielt diese Seite die Run-ID in lokalem State - nach einem
  // Seitenwechsel oder Reload war der laufende Lauf dadurch unauffindbar.
  const { data: classifyRun } = useLatestClassificationRun();
  const startClassificationMutation = useEnqueueAiTask();
  const cancelClassificationMutation = useCancelClassificationRun();
  useClassificationRunCompletionEffect(classifyRun);
  const isClassifyRunActive = isClassificationRunActive(classifyRun);

  const {
    data: inboxData,
    isLoading,
    isFetching,
    error,
  } = useQuery<ReviewInboxResponse>({
    queryKey: ["review-inbox", householdId, unclassifiedSearch, unclassifiedOffset],
    queryFn: () =>
      getReviewInbox(token!, householdId, {
        limit: REVIEW_PAGE_SIZE,
        offset: unclassifiedOffset,
        search: unclassifiedSearch,
      }),
    enabled: Boolean(token && householdId),
    // React Query behaelt die letzte Seite sichtbar, waehrend Filter oder
    // Seitenwechsel geladen werden. Dadurch bleibt insbesondere das
    // Suchfeld durchgehend montiert und fokussiert.
    placeholderData: (previousData) => previousData,
  });

  // Der Vorschlag ist nirgends gespeichert: das Detail wird bei jedem Aufruf
  // neu erkannt. Deshalb hängt der Query-Key an der Ankerbuchung und nicht an
  // einer Vorschlags-ID, die es nicht gibt.
  const {
    data: suggestionDetail,
    isLoading: isSuggestionDetailLoading,
    error: suggestionDetailError,
  } = useQuery<ReviewContractSuggestionDetail>({
    queryKey: ["contract-suggestion-detail", householdId, suggestionDetailSourceId],
    queryFn: () => getContractSuggestionDetail(token!, householdId, suggestionDetailSourceId!),
    enabled: Boolean(token && householdId && suggestionDetailSourceId),
  });

  const { data: categoryGroups } = useQuery({
    queryKey: ["categories", householdId],
    queryFn: () => listCategories(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  // Bestehende Regeln werden gebraucht, um eine konkurrierende Regel fuer
  // denselben Zahlungsempfaenger anzuzeigen, statt sie still zu ueberschreiben
  // (docs/KATEGORISIERUNGSPLAN.md, "Review und Import").
  const { data: existingRules } = useQuery<ClassificationRule[]>({
    queryKey: ["rules", householdId],
    queryFn: () => listClassificationRules(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const categoryNameById = useMemo(() => {
    const map = new Map<string, string>();
    categoryGroups?.forEach((group) => {
      group.categories.forEach((category) => {
        map.set(category.id, `${group.name} · ${category.name}`);
      });
    });
    return map;
  }, [categoryGroups]);

  /** Aktive Regel, die denselben Zahlungsempfaenger bereits zuordnet. */
  const findCompetingRule = (payee: string): ClassificationRule | null => {
    const needle = payee.trim().toLowerCase();
    if (!needle) {
      return null;
    }
    return (
      existingRules?.find(
        (rule) =>
          rule.is_active &&
          Boolean(rule.action_category_id) &&
          (rule.match_payee_raw ?? "").trim().toLowerCase() === needle,
      ) ?? null
    );
  };

  const competingRule =
    correctModal?.kind === "unclassified" ? findCompetingRule(payeePattern) : null;

  // KI-Entscheidungsprotokoll laden (Phase 6)
  const { data: aiDecisions } = useQuery<AIDecisionLog[]>({
    queryKey: ["ai-decisions", householdId, classifyRun?.id ?? null],
    queryFn: () => getAIDecisions(token!, householdId, undefined, classifyRun?.id ?? null),
    enabled: Boolean(token && householdId),
  });

  const aiDecisionsMap = useMemo(() => {
    const map: Record<string, AIDecisionLog> = {};
    if (aiDecisions) {
      for (const d of aiDecisions) {
        if (d.transaction_id && !map[d.transaction_id]) {
          map[d.transaction_id] = d;
        }
      }
    }
    return map;
  }, [aiDecisions]);

  const invalidateAfterAction = () => {
    void queryClient.invalidateQueries({ queryKey: ["review-inbox", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["transactions", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["rules", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["overview-report", householdId] });
    // Die Antwort auf "läuft dieser Vertrag noch?" ändert Status und
    // Fehlzähler des Vertrags - und damit die Fixkostensumme. Ohne diese
    // Invalidierung stünde auf der Vertragsseite weiter der alte Stand.
    void queryClient.invalidateQueries({ queryKey: ["contracts", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["contract-summary", householdId] });
  };

  const acceptMutation = useMutation({
    mutationFn: (input: Parameters<typeof acceptReviewProposal>[2]) =>
      acceptReviewProposal(token!, householdId, input),
    onSuccess: invalidateAfterAction,
  });

  // Sammelbestaetigung der Dubletten. Die Auswahl wird nach dem Vorgang
  // geleert: die Zeilen sind dann weg, eine stehengebliebene Auswahl zeigte auf
  // nichts mehr.
  const assessMutation = useMutation({
    mutationFn: (importRowIds: string[]) => assessDuplicates(token!, householdId, importRowIds),
    onSuccess: (ergebnis) => {
      if (!ergebnis.available) {
        // Kein Fehler, sondern der geordnete Ausgang: die Liste bleibt von
        // Hand bedienbar, der Grund steht daneben.
        setKiHinweis({ code: ergebnis.reason, text: ergebnis.message });
        return;
      }
      // Gezaehlt wird nur, was das Modell wirklich beurteilt hat. Bei einem
      // Teilergebnis (reason != "ok") nennt die Servermeldung Zahl und Grund.
      const beurteilt = ergebnis.assessments.filter(
        (urteil) => !isUnassessedVerdict(urteil.verdict, urteil.reason),
      ).length;
      setKiHinweis(
        ergebnis.reason === "ok"
          ? {
              code: null,
              text: `${beurteilt} beurteilt${ergebnis.model_name ? ` (${ergebnis.model_name})` : ""}.`,
            }
          : { code: ergebnis.reason, text: ergebnis.message },
      );
      setDublettenUrteile((bisher) => {
        const naechste = { ...bisher };
        for (const urteil of ergebnis.assessments) {
          naechste[urteil.import_row_id] = {
            verdict: urteil.verdict,
            reason: urteil.reason,
            confidence: urteil.confidence,
            unassessed: isUnassessedVerdict(urteil.verdict, urteil.reason),
          };
        }
        return naechste;
      });
    },
    onError: () => setKiHinweis(null),
  });

  const bulkDuplicatesMutation = useMutation({
    mutationFn: (importRowIds: string[]) => acceptDuplicatesBulk(token!, householdId, importRowIds),
    onSuccess: (ergebnis) => {
      const uebersprungen =
        ergebnis.skipped > 0 ? ` ${ergebnis.skipped} waren schon entschieden.` : "";
      setBulkDuplicatesMessage(`${ergebnis.confirmed} als Dublette bestätigt.${uebersprungen}`);
      setGewaehlteDubletten(new Set());
      invalidateAfterAction();
    },
  });

  // Die Regel zu einer Korrektur legt ausschliesslich das Backend an
  // (create_rule_always im Korrektur-Payload). Frueher schickte diese Seite
  // zusaetzlich ein eigenes POST /v1/rules hinterher - zusammen mit dem
  // damaligen Doppelanlegen im Backend entstanden so drei identische Regeln je
  // Standardkorrektur.
  const correctMutation = useMutation({
    mutationFn: (input: Parameters<typeof correctReviewProposal>[2]) =>
      correctReviewProposal(token!, householdId, input),
    onSuccess: () => {
      invalidateAfterAction();
      setCorrectModal(null);
    },
  });

  const rulePreviewMutation = useMutation({
    mutationFn: () => {
      if (!correctModal || correctModal.kind !== "unclassified") {
        throw new Error("Bitte zuerst eine Buchung waehlen.");
      }
      return previewReviewRule(token!, householdId, {
        transaction_id: correctModal.transactionId,
        category_id: selectedCategoryId || null,
        payee_pattern: payeePattern || null,
        match_payee: ruleMatchPayee,
        match_purpose: ruleMatchPurpose,
        match_exact_amount: ruleMatchExactAmount,
        match_account: ruleMatchAccount,
      });
    },
    onSuccess: setRulePreview,
  });

  const historyMatchMutation = useMutation({
    mutationFn: () => {
      if (!correctModal || correctModal.kind !== "unclassified") {
        throw new Error("Bitte zuerst eine Buchung wählen.");
      }
      return getReviewHistoryMatch(token!, householdId, correctModal.transactionId);
    },
    onSuccess: (result) => {
      if (result.category_id) {
        setSelectedCategoryId(result.category_id);
      }
    },
  });

  const historyMatchesMutation = useMutation({
    mutationFn: () => getReviewHistoryMatches(token!, householdId),
    onSuccess: (result) => {
      setHistoryMatchesByTransaction(
        Object.fromEntries(result.items.map((item) => [item.transaction_id, item])),
      );
    },
  });

  const matchedHistoryItems = useMemo(() => {
    if (!historyMatchesMutation.data?.items) return [];
    return historyMatchesMutation.data.items.filter(
      (item) => item.matched && Boolean(item.category_id),
    );
  }, [historyMatchesMutation.data]);

  const historyMatchedCount = matchedHistoryItems.length;

  const bulkAcceptHistoryMatchesMutation = useMutation({
    mutationFn: async () => {
      if (!token || !householdId || matchedHistoryItems.length === 0) return;
      await Promise.all(
        matchedHistoryItems.map((item) =>
          acceptReviewProposal(token!, householdId, {
            type: "unclassified",
            transaction_id: item.transaction_id,
            category_id: item.category_id!,
          }),
        ),
      );
    },
    onSuccess: () => {
      invalidateAfterAction();
      void historyMatchesMutation.mutate();
    },
  });

  // Sammeluebernahme sicherer KI-Vorschlaege. Immer zweistufig: erst die
  // servergerechnete Vorschau, dann das ausdrueckliche Anwenden. Ein Knopf, der
  // ohne Rueckfrage dutzende Buchungen umkategorisiert, waere genau die Art
  // stiller Massenaenderung, die der Kategorisierungsplan ausschliesst.
  const [proposalBatch, setProposalBatch] = useState<AIProposalBatchResult | null>(null);
  const [proposalApplied, setProposalApplied] = useState(false);
  // Eine request_id je geoeffneter Vorschau: ein zweiter Klick auf "Uebernehmen"
  // liefert dann serverseitig dasselbe Ergebnis, statt doppelt zu schreiben.
  const [proposalRequestId, setProposalRequestId] = useState<string>("");

  const proposalPreviewMutation = useMutation({
    mutationFn: () =>
      previewAIProposals(token!, householdId, {
        transaction_ids: null,
        min_confidence: null,
        classification_run_id: classifyRun?.id ?? null,
      }),
    onSuccess: (result) => {
      setProposalBatch(result);
      setProposalApplied(false);
      setProposalRequestId(crypto.randomUUID());
    },
  });

  const proposalApplyMutation = useMutation({
    mutationFn: () =>
      applyAIProposals(token!, householdId, {
        transaction_ids: null,
        min_confidence: null,
        classification_run_id: classifyRun?.id ?? null,
        request_id: proposalRequestId,
      }),
    onSuccess: (result) => {
      setProposalBatch(result);
      setProposalApplied(true);
      invalidateAfterAction();
    },
  });

  const closeProposalModal = () => {
    setProposalBatch(null);
    setProposalApplied(false);
    proposalPreviewMutation.reset();
    proposalApplyMutation.reset();
  };

  // KI-Vorschlag direkt im Korrekturdialog. Bewusst auf Knopfdruck und nicht
  // automatisch beim Oeffnen: ein Modellaufruf kostet Zeit und wird in
  // llm_calls protokolliert - das soll eine bewusste Handlung bleiben.
  const aiSuggestMutation = useMutation({
    mutationFn: (input: AIClassifyInput) => classifyWithAI(token!, householdId, input),
    onSuccess: (result) => {
      // Ein no_match (category_id === null) ist eine gueltige Antwort und darf
      // die bestehende Auswahl NICHT ueberschreiben - sonst waere "kein
      // passender Treffer" faktisch doch ein erzwungener Treffer.
      if (result.category_id) {
        setSelectedCategoryId(String(result.category_id));
      }
    },
  });

  const handleRequestAISuggestion = () => {
    if (!correctModal) return;
    aiSuggestMutation.reset();
    const contractSuggestion =
      correctModal.kind === "contract_suggestion" ? correctModal.suggestion : null;
    aiSuggestMutation.mutate({
      transaction_id:
        correctModal.kind === "unclassified"
          ? correctModal.transactionId
          : contractSuggestion?.source_transaction_id,
      payee_raw:
        correctModal.kind === "contract_suggestion"
          ? correctModal.suggestion.payee_label
          : correctModal.payee,
      purpose: correctModal.kind === "unclassified" ? correctModal.purpose || null : null,
      // Bei einem Vertrag bekommt die KI den echten letzten Betrag statt des
      // bisherigen Platzhalters 0 EUR - mit dem Vorzeichen der Richtung, sonst
      // schlüge sie für eine Ausgabe Einnahmekategorien vor.
      amount_minor: contractSuggestion
        ? signedAmountMinor(contractSuggestion.amount_minor, contractSuggestion.direction)
        : correctModal.kind === "unclassified"
          ? correctModal.amountMinor
          : 0,
    });
  };

  const handleOpenCorrectUnclassified = (
    transactionId: string,
    payee: string,
    purpose: string,
    amountMinor: number,
    accountId: string,
    currentCategoryId: string,
  ) => {
    setCorrectModal({
      kind: "unclassified",
      transactionId,
      payee,
      purpose,
      amountMinor,
      accountId,
      initialCategoryId: "",
    });
    setSelectedCategoryId(currentCategoryId);
    setPayeePattern(payee);
    setRuleMatchPayee(Boolean(payee));
    setRuleMatchPurpose(false);
    setRuleMatchExactAmount(true);
    setRuleMatchAccount(false);
    setRulePreview(null);
    rulePreviewMutation.reset();
    historyMatchMutation.reset();
    aiSuggestMutation.reset();
    // Gibt es fuer diesen Zahlungsempfaenger bereits eine aktive Regel, ist
    // "kuenftig immer" bewusst NICHT vorbelegt - die vorhandene Regel wird
    // stattdessen angezeigt und muss ausdruecklich bestaetigt werden.
    setCreateRule(findCompetingRule(payee) === null);
  };

  const handleOpenCorrectDuplicate = (importRowId: string, payee: string) => {
    setCorrectModal({ kind: "possible_duplicate", importRowId, payee });
    setSelectedCategoryId("");
    aiSuggestMutation.reset();
  };

  const handleOpenAcceptContract = (suggestion: ReviewItemContractSuggestion) => {
    setCorrectModal({ kind: "contract_suggestion", suggestion });
    setSelectedCategoryId(suggestion.suggested_category_id ?? "");
    aiSuggestMutation.reset();
  };

  /**
   * "Das ist kein Vertrag."
   *
   * Ohne diese Antwort käme derselbe Vorschlag nach jedem Import zurück -
   * besonders im unsicheren Bereich, der genau deshalb sichtbar ist.
   */
  const dismissContractSuggestion = (suggestion: ReviewItemContractSuggestion) => {
    acceptMutation.mutate({
      type: "contract_suggestion",
      source_transaction_id: suggestion.source_transaction_id,
      dismiss: true,
    });
  };

  const handleSaveCorrection = () => {
    if (!correctModal || !selectedCategoryId) return;
    if (correctModal.kind === "unclassified") {
      if (createRule && rulePreview === null) {
        rulePreviewMutation.mutate();
        return;
      }
      correctMutation.mutate({
        type: "unclassified",
        transaction_id: correctModal.transactionId,
        category_id: selectedCategoryId,
        payee_label: payeePattern || undefined,
        create_rule_always: createRule,
        rule_match_payee: ruleMatchPayee,
        rule_match_purpose: ruleMatchPurpose,
        rule_match_exact_amount: ruleMatchExactAmount,
        rule_match_account: ruleMatchAccount,
        apply_rule_to_existing: createRule && rulePreview !== null,
      });
    } else if (correctModal.kind === "possible_duplicate") {
      correctMutation.mutate({
        type: "possible_duplicate",
        import_row_id: correctModal.importRowId,
        category_id: selectedCategoryId,
      });
    } else {
      acceptMutation.mutate(
        {
          type: "contract_suggestion",
          source_transaction_id: correctModal.suggestion.source_transaction_id,
          category_id: selectedCategoryId,
        },
        { onSuccess: () => setCorrectModal(null) },
      );
    }
  };

  const unclassified = inboxData?.unclassified ?? [];
  const duplicates = inboxData?.possible_duplicates ?? [];

  const alleDublettenGewaehlt =
    duplicates.length > 0 && duplicates.every((d) => gewaehlteDubletten.has(d.import_row_id));
  const transfers = inboxData?.transfer_candidates ?? [];
  const contractEndedChecks = inboxData?.contract_ended_checks ?? [];
  const contractAmountChanges = inboxData?.contract_amount_change_checks ?? [];
  const contractDuplicates = inboxData?.contract_duplicate_suggestions ?? [];

  // Unsichere Vorschläge (0.40-0.55) ans Ende. Sie stehen bewusst nicht
  // dazwischen: zwischen bestätigungsreifen Verträgen würde ein 0.42er
  // mit durchgewinkt. sort ist stabil, die Rangfolge des Servers innerhalb
  // der beiden Gruppen bleibt erhalten.
  const contractSuggestions = useMemo(() => {
    const items = inboxData?.contract_suggestions ?? [];
    return [...items].sort((a, b) => Number(a.is_uncertain) - Number(b.is_uncertain));
  }, [inboxData]);

  const firstUncertainSuggestionId =
    contractSuggestions.find((suggestion) => suggestion.is_uncertain)?.source_transaction_id ??
    null;

  // Nur die gerade beantwortete Zeile sperren, nicht der ganze Abschnitt: die
  // Fragen sind voneinander unabhaengig, und wer drei Vertraege hintereinander
  // beantwortet, soll nicht nach jedem Klick auf alle Knoepfe warten.
  const answeringContractId =
    acceptMutation.isPending && acceptMutation.variables?.type === "contract_ended_check"
      ? acceptMutation.variables.contract_id
      : null;

  const answerContractEndedCheck = (
    check: ReviewItemContractEndedCheck,
    action: ContractEndedAction,
  ) => {
    acceptMutation.mutate({
      type: "contract_ended_check",
      contract_id: check.contract_id,
      action,
    });
  };

  // Anker ist das EREIGNIS, nicht der Vertrag: ein Vertrag kann mehrere
  // unbestaetigte Preisstufen tragen, und gesperrt wird nur die Zeile, die
  // gerade beantwortet wird.
  const answeringAmountChangeId =
    acceptMutation.isPending && acceptMutation.variables?.type === "contract_amount_change_check"
      ? acceptMutation.variables.event_id
      : null;

  const confirmAmountChange = (check: ReviewItemContractAmountChangeCheck) => {
    acceptMutation.mutate({
      type: "contract_amount_change_check",
      event_id: check.event_id,
      action: "confirmed",
    });
  };

  // Wie oben: nur die gerade beantwortete Zeile sperren, nicht den Abschnitt.
  const busySuggestionId =
    acceptMutation.isPending && acceptMutation.variables?.type === "contract_suggestion"
      ? acceptMutation.variables.source_transaction_id
      : null;
  const busyDuplicateId =
    acceptMutation.isPending && acceptMutation.variables?.type === "contract_duplicate_suggestion"
      ? acceptMutation.variables.target_id
      : null;

  /**
   * Zusammenführen oder verneinen.
   *
   * Ziel ist immer der jüngere Vertrag - er trägt den aktuellen Preis und die
   * nächste Fälligkeit; die älteren Einträge gehen mit ihren Buchungen in ihn
   * über. Das Verneinen merkt sich der Server, sonst stünde dasselbe Paar
   * nach jedem Inbox-Aufruf erneut da.
   */
  const answerContractDuplicate = (item: ReviewItemContractDuplicate, dismiss: boolean) => {
    acceptMutation.mutate({
      type: "contract_duplicate_suggestion",
      target_id: item.target_id,
      source_ids: item.source_ids,
      dismiss,
    });
  };
  // Erfolgreiche Antworten enthalten diese Werte garantiert: getReviewInbox
  // weist alte API-Antworten ohne Metadaten ab, statt die Seitengröße als
  // vermeintliche Gesamtzahl auszugeben.
  const unclassifiedOpenCount = inboxData?.unclassified_open_count ?? 0;
  const unclassifiedTotalCount = inboxData?.unclassified_total_count ?? 0;
  const unclassifiedPageEnd = Math.min(
    unclassifiedOffset + unclassified.length,
    unclassifiedTotalCount,
  );

  const totalItems = inboxData?.total_count ?? 0;

  // Schreibende Aktionen dieser Seite duerfen nicht still scheitern: jede
  // fehlgeschlagene Mutation wird sichtbar gemeldet, statt nur die Inbox
  // unveraendert stehen zu lassen.
  const actionError =
    acceptMutation.error ??
    correctMutation.error ??
    rulePreviewMutation.error ??
    historyMatchMutation.error ??
    historyMatchesMutation.error ??
    bulkAcceptHistoryMatchesMutation.error ??
    startClassificationMutation.error ??
    cancelClassificationMutation.error;

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Review-Inbox</h1>
          <p className="placeholder-note">
            Offene Fälle zur Überprüfung: Unklassifizierte Umsätze, Dubletten, Transfers und
            erkannte Verträge.
          </p>
        </div>
      </div>

      <BookedDuplicatesPanel
        key={householdId}
        items={inboxData?.booked_duplicates ?? []}
        total={inboxData?.booked_duplicate_count ?? 0}
      />

      <div className="review-control-panel">
        {/* Block 1: Bestands- & Regelabgleich */}
        <div className="review-control-card">
          <div className="review-control-card__header">
            <div className="review-control-card__title">
              <span className="review-control-card__icon">↩</span>
              <div>
                <h3>Bestand & Regeln (ohne KI)</h3>
                <p>Prüft deine vergangen Buchungen und aktive Regeln für automatische Treffer.</p>
              </div>
            </div>
            {historyMatchesMutation.data && (
              <span
                className={`status-badge ${
                  historyMatchesMutation.data.matched_count > 0
                    ? "status-badge--positive"
                    : "status-badge--neutral"
                }`}
                role="status"
              >
                {historyMatchesMutation.data.matched_count} von{" "}
                {historyMatchesMutation.data.checked_count} ohne KI gefunden
              </span>
            )}
          </div>
          <div className="review-control-card__actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => historyMatchesMutation.mutate()}
              disabled={historyMatchesMutation.isPending || !token || !householdId}
            >
              {historyMatchesMutation.isPending
                ? "Prüfe Bestand und Regeln…"
                : "Bestand & Regeln prüfen (ohne KI)"}
            </button>

            {historyMatchedCount > 0 && (
              <button
                type="button"
                className="btn-primary"
                onClick={() => bulkAcceptHistoryMatchesMutation.mutate()}
                disabled={bulkAcceptHistoryMatchesMutation.isPending || !token || !householdId}
              >
                {bulkAcceptHistoryMatchesMutation.isPending
                  ? `Übernehme ${historyMatchedCount} Vorschläge…`
                  : `✓ Alle ${historyMatchedCount} Bestandsvorschläge übernehmen`}
              </button>
            )}
          </div>
        </div>

        {/* Block 2: KI-Kategorisierung */}
        <div className="review-control-card review-control-card--ai">
          <div className="review-control-card__header">
            <div className="review-control-card__title">
              <span className="review-control-card__icon">✨</span>
              <div>
                <h3>KI-Kategorisierung (mit KI)</h3>
                <p>Nutzt KI für noch nicht zugeordnete Buchungen und Vorschläge.</p>
              </div>
            </div>
            {isClassifyRunActive && classifyRun ? (
              <div className="status-badge status-badge--neutral">
                ✨ {classifyRun.processed_count} von {classifyRun.total_count} klassifiziert…
              </div>
            ) : (
              <>
                {classifyRun?.status === "completed" && (
                  <div className="status-badge status-badge--positive">
                    ✅ Klassifizierung abgeschlossen ({classifyRun.success_count} erfolgreich)
                  </div>
                )}
                {classifyRun?.status === "failed" && (
                  <div className="status-badge status-badge--error">
                    Klassifizierung fehlgeschlagen
                    {classifyRun.error_message ? `: ${classifyRun.error_message}` : ""}
                  </div>
                )}
                {classifyRun?.status === "cancelled" && (
                  <div className="status-badge status-badge--neutral">
                    Klassifizierung abgebrochen ({classifyRun.processed_count} verarbeitet)
                  </div>
                )}
              </>
            )}
          </div>
          <div className="review-control-card__actions">
            {isClassifyRunActive && classifyRun ? (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => cancelClassificationMutation.mutate(classifyRun.id)}
                disabled={cancelClassificationMutation.isPending || classifyRun.cancel_requested}
              >
                {classifyRun.cancel_requested ? "Abbruch angefordert…" : "Lauf abbrechen"}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() =>
                    startClassificationMutation.mutate({ task_type: "transaction_classification" })
                  }
                  disabled={
                    startClassificationMutation.isPending ||
                    !token ||
                    !householdId ||
                    unclassifiedOpenCount === 0
                  }
                >
                  ✨ KI-Kategorisierung für alle offenen Buchungen starten
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => proposalPreviewMutation.mutate()}
                  disabled={proposalPreviewMutation.isPending || !token || !householdId}
                >
                  {proposalPreviewMutation.isPending
                    ? "Prüfe KI-Vorschläge…"
                    : "Vorschläge des letzten Laufs prüfen"}
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {isLoading && <p className="placeholder-note">Lade Review-Inbox...</p>}

      {error && (
        <div className="card status-error" style={{ marginBottom: "var(--space-4)" }}>
          {error instanceof Error
            ? error.message
            : "Fehler beim Laden der Review-Inbox. Bitte erneut versuchen."}
        </div>
      )}

      {actionError && (
        <div className="card status-error" style={{ marginBottom: "var(--space-4)" }} role="alert">
          {actionError instanceof Error
            ? actionError.message
            : "Die Aktion konnte nicht ausgeführt werden."}
        </div>
      )}

      {!isLoading && !error && totalItems === 0 && (
        <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
          <h2 className="card-heading">Alles erledigt! 🎉</h2>
          <p className="card-body">Es gibt aktuell keine offenen Review-Fälle.</p>
        </div>
      )}

      {!isLoading && totalItems > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
          {/* Section 1: Unklassifizierte Umsätze */}
          {unclassifiedOpenCount > 0 && (
            <div className="card">
              <h2 className="card-heading">
                Unklassifizierte Umsätze (
                {unclassifiedSearch
                  ? `${unclassifiedTotalCount} Treffer von ${unclassifiedOpenCount}`
                  : unclassifiedOpenCount}
                )
              </h2>
              <p className="card-body" style={{ marginBottom: "var(--space-3)" }}>
                Weisen Sie diesen Buchungen direkt eine Kategorie zu oder korrigieren Sie sie.
              </p>
              <div className="review-unclassified-toolbar">
                <label className="form-field review-unclassified-search">
                  <span className="form-label">Empfänger oder Verwendungszweck filtern</span>
                  <input
                    type="search"
                    className="form-input"
                    value={unclassifiedSearchInput}
                    placeholder="z. B. GitHub, Miete, Rechnung…"
                    onChange={(event) => setUnclassifiedSearchInput(event.target.value)}
                  />
                </label>
                <span className="placeholder-note" role="status" aria-live="polite">
                  {isFetching && !isLoading ? "Aktualisiere… · " : ""}
                  {unclassifiedTotalCount > 0
                    ? `${unclassifiedOffset + 1}–${unclassifiedPageEnd} von ${unclassifiedTotalCount}`
                    : "Keine Treffer"}
                </span>
              </div>

              {unclassified.length > 0 ? (
                <div className="txn-table-wrapper">
                  <table className="txn-table">
                    <thead>
                      <tr>
                        <th className="txn-th">Datum</th>
                        <th className="txn-th">Empfänger / Betreff</th>
                        <th className="txn-th txn-th--amount">Betrag</th>
                        <th className="txn-th">Kategorie-Zuordnung</th>
                        <th className="txn-th" style={{ textAlign: "right" }}>
                          Aktionen
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {unclassified.map((txn) => {
                        const aiDec = aiDecisionsMap[txn.transaction_id];
                        const historyMatch = historyMatchesByTransaction[txn.transaction_id];
                        const aiSuggestion = aiDec
                          ? {
                              category_id: aiDec.category_id,
                              confidence: aiDec.confidence,
                              reason_code: aiDec.reason_code,
                              payee_name: aiDec.payee_name ?? "",
                              source: aiDec.source,
                              model_name: aiDec.model_name,
                            }
                          : null;
                        const suggestedCategoryId =
                          historyMatch?.category_id ?? aiSuggestion?.category_id ?? "";

                        return (
                          <tr key={txn.transaction_id} className="txn-tr">
                            <td className="txn-td">{txn.booked_date}</td>
                            <td className="txn-td">
                              <div
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "var(--space-2)",
                                  flexWrap: "wrap",
                                }}
                              >
                                <div className="txn-payee">{txn.payee_label || "Unbekannt"}</div>
                                {aiSuggestion && (
                                  <AIBadge
                                    confidence={aiSuggestion.confidence}
                                    reasonCode={aiSuggestion.reason_code}
                                    source={aiSuggestion.source}
                                  />
                                )}
                                {historyMatch?.matched && (
                                  <span
                                    className="status-badge status-badge--positive"
                                    title={
                                      historyMatch.source === "rule"
                                        ? `Aktive Regel: ${historyMatch.rule_name}`
                                        : `${historyMatch.evidence_count} bestätigte Buchung(en) desselben Empfängers`
                                    }
                                  >
                                    {historyMatch.source === "rule"
                                      ? `⚙ Regel „${historyMatch.rule_name}“ · ${historyMatch.category_label}`
                                      : `↩ Bestand · ${historyMatch.category_label}`}
                                  </span>
                                )}
                              </div>
                              {(txn.purpose ?? txn.note) && (
                                <div className="txn-note">{txn.purpose ?? txn.note}</div>
                              )}
                            </td>
                            <td
                              className={`txn-td txn-td--amount ${txn.amount_minor >= 0 ? "transaction-amount--positive" : "transaction-amount--negative"}`}
                            >
                              {formatMinorUnitsAsEuro(txn.amount_minor)}
                            </td>
                            <td className="txn-td">
                              <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                                <CategoryPicker
                                  categoryGroups={categoryGroups}
                                  value={suggestedCategoryId}
                                  amountMinor={txn.amount_minor}
                                  aria-label={`Kategorie für ${txn.payee_label || "diese Buchung"}`}
                                  onChange={(catId) => {
                                    if (catId) {
                                      acceptMutation.mutate({
                                        type: "unclassified",
                                        transaction_id: txn.transaction_id,
                                        category_id: catId,
                                      });
                                    }
                                  }}
                                  noneLabel="-- Kategorie wählen --"
                                />
                                {suggestedCategoryId && (
                                  <button
                                    type="button"
                                    className="btn-secondary"
                                    style={{
                                      fontSize: "11px",
                                      padding: "2px 6px",
                                      alignSelf: "flex-start",
                                    }}
                                    onClick={() => {
                                      acceptMutation.mutate({
                                        type: "unclassified",
                                        transaction_id: txn.transaction_id,
                                        category_id: suggestedCategoryId,
                                      });
                                    }}
                                  >
                                    {historyMatch?.matched
                                      ? historyMatch.source === "rule"
                                        ? "⚙ Regelvorschlag übernehmen"
                                        : "↩ Bestandsvorschlag übernehmen"
                                      : "✨ KI-Vorschlag übernehmen"}
                                  </button>
                                )}
                              </div>
                            </td>
                            <td className="txn-td" style={{ textAlign: "right" }}>
                              <button
                                type="button"
                                className="btn-secondary"
                                onClick={() =>
                                  handleOpenCorrectUnclassified(
                                    txn.transaction_id,
                                    txn.payee_label || "",
                                    txn.purpose ?? txn.note ?? "",
                                    txn.amount_minor,
                                    txn.account_id,
                                    suggestedCategoryId,
                                  )
                                }
                              >
                                Korrigieren / Regel erstellen
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="review-unclassified-empty">
                  Keine offene Buchung passt zu „{unclassifiedSearch}“.
                </div>
              )}

              {unclassifiedTotalCount > REVIEW_PAGE_SIZE && (
                <div className="review-pagination" aria-label="Seitennavigation offene Buchungen">
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={unclassifiedOffset === 0}
                    onClick={() =>
                      setUnclassifiedOffset((current) => Math.max(0, current - REVIEW_PAGE_SIZE))
                    }
                  >
                    Vorherige 100
                  </button>
                  <span>
                    {unclassifiedOffset + 1}–{unclassifiedPageEnd} von {unclassifiedTotalCount}
                  </span>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={unclassifiedPageEnd >= unclassifiedTotalCount}
                    onClick={() => setUnclassifiedOffset((current) => current + REVIEW_PAGE_SIZE)}
                  >
                    Nächste 100
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Section 2: Mögliche Dubletten */}
          {duplicates.length > 0 && (
            <div className="card">
              <h2 className="card-heading">Mögliche Dubletten ({duplicates.length})</h2>
              <p className="card-body" style={{ marginBottom: "var(--space-3)" }}>
                Buchungen aus einem Import, die einer bereits vorhandenen Buchung stark ähneln.
                Rechts daneben steht, um welche Buchung es geht - bestätigen heißt: die neue Zeile
                wird verworfen, die vorhandene bleibt.
              </p>

              {/* Sammelaktion. Ein Import bringt Dubletten zu Dutzenden; einzeln
                  geklickt bleibt die Liste liegen, und dann ist auch die echte
                  Dublette darin verloren. */}
              <div
                className="flex flex-wrap items-center gap-3"
                style={{ marginBottom: "var(--space-3)" }}
              >
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={alleDublettenGewaehlt}
                    onChange={(event) =>
                      setGewaehlteDubletten(
                        event.target.checked
                          ? new Set(duplicates.map((d) => d.import_row_id))
                          : new Set(),
                      )
                    }
                  />
                  <span>Alle auswählen</span>
                </label>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={gewaehlteDubletten.size === 0 || bulkDuplicatesMutation.isPending}
                  onClick={() => bulkDuplicatesMutation.mutate(Array.from(gewaehlteDubletten))}
                >
                  {bulkDuplicatesMutation.isPending
                    ? "Wird bestätigt ..."
                    : `${gewaehlteDubletten.size} als Dublette bestätigen`}
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={gewaehlteDubletten.size === 0 || assessMutation.isPending}
                  onClick={() => {
                    setKiHinweis(null);
                    assessMutation.mutate(Array.from(gewaehlteDubletten).slice(0, 25));
                  }}
                  title="Fragt das lokale Modell, ob die ausgewählten Paare derselbe Vorgang sind. Es entscheidet nichts - der Hinweis steht neben der Zeile."
                >
                  {assessMutation.isPending ? "KI prüft ..." : "Von KI einschätzen lassen"}
                </button>
                {bulkDuplicatesMessage && (
                  <span className="status-success">{bulkDuplicatesMessage}</span>
                )}
                {kiHinweis && (
                  <AiReasonNotice
                    as="span"
                    className="placeholder-note"
                    role="status"
                    code={kiHinweis.code}
                    message={kiHinweis.text}
                  />
                )}
                {assessMutation.isError && (
                  <AiErrorNotice
                    as="span"
                    className="placeholder-note"
                    role="alert"
                    error={assessMutation.error}
                    prefix="Die Einschätzung konnte nicht geholt werden:"
                    suffix="Die Dubletten lassen sich unverändert von Hand entscheiden."
                  />
                )}
              </div>

              <div className="txn-table-wrapper">
                <table className="txn-table">
                  <thead>
                    <tr>
                      <th className="txn-th" style={{ width: "2rem" }} />
                      <th className="txn-th">Datum</th>
                      <th className="txn-th">Neue Zeile aus dem Import</th>
                      <th className="txn-th txn-th--amount">Betrag</th>
                      <th className="txn-th">Bereits vorhanden</th>
                      <th className="txn-th" style={{ textAlign: "right" }}>
                        Aktionen
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {duplicates.map((dup) => (
                      <tr key={dup.import_row_id} className="txn-tr">
                        <td className="txn-td">
                          <input
                            type="checkbox"
                            aria-label={`Zeile vom ${dup.booked_date ?? "unbekannt"} auswählen`}
                            checked={gewaehlteDubletten.has(dup.import_row_id)}
                            onChange={(event) =>
                              setGewaehlteDubletten((bisher) => {
                                const naechste = new Set(bisher);
                                if (event.target.checked) naechste.add(dup.import_row_id);
                                else naechste.delete(dup.import_row_id);
                                return naechste;
                              })
                            }
                          />
                        </td>
                        <td className="txn-td">{dup.booked_date ?? "–"}</td>
                        <td className="txn-td">
                          <div className="txn-payee">{dup.payee_raw || "Unbekannt"}</div>
                          {dup.purpose && <div className="txn-note">{dup.purpose}</div>}
                        </td>
                        <td className="txn-td txn-td--amount">
                          {dup.amount_minor !== null
                            ? formatMinorUnitsAsEuro(dup.amount_minor)
                            : "–"}
                        </td>
                        {/* Ohne diese Spalte war die Frage nicht zu beantworten:
                            die Liste verlangte "Dublette oder nicht", ohne zu
                            sagen, wovon. */}
                        <td className="txn-td">
                          {dup.duplicate_of ? (
                            <>
                              <div className="txn-payee">
                                {dup.duplicate_of.payee_label || "Ohne Empfänger"}
                              </div>
                              <div className="txn-note">
                                {dup.duplicate_of.booked_date}
                                {dup.duplicate_of.amount_minor !== null &&
                                dup.duplicate_of.amount_minor !== undefined
                                  ? ` · ${formatMinorUnitsAsEuro(dup.duplicate_of.amount_minor)}`
                                  : ""}
                                {dup.duplicate_of.account_name
                                  ? ` · ${dup.duplicate_of.account_name}`
                                  : ""}
                              </div>
                              {dup.duplicate_of.purpose && (
                                <div className="txn-note">{dup.duplicate_of.purpose}</div>
                              )}
                            </>
                          ) : (
                            <span className="placeholder-note">
                              Die verglichene Buchung steht nicht mehr im Bestand - hier ist nichts
                              mehr doppelt.
                            </span>
                          )}
                        </td>
                        <td className="txn-td" style={{ textAlign: "right" }}>
                          {/* Das Urteil der KI steht NEBEN der Aktion, nicht
                              anstelle: entschieden wird weiter per Knopf. */}
                          {(() => {
                            const urteil = dublettenUrteile[dup.import_row_id];
                            if (!urteil) return null;
                            const beschriftung = urteil.unassessed
                              ? "KI: nicht beurteilt"
                              : urteil.verdict === "same"
                                ? "KI: dieselbe Buchung"
                                : urteil.verdict === "different"
                                  ? "KI: verschieden"
                                  : "KI: unsicher";
                            return (
                              <div style={{ marginBottom: "var(--space-2)" }}>
                                <span
                                  className={
                                    urteil.verdict === "same"
                                      ? "status-badge status-badge--warning"
                                      : "status-badge"
                                  }
                                >
                                  {beschriftung}
                                </span>
                                <div className="txn-note">{urteil.reason}</div>
                              </div>
                            );
                          })()}
                          <div className="actions-cell" style={{ justifyContent: "flex-end" }}>
                            <button
                              type="button"
                              className="btn-primary"
                              style={{ padding: "4px 8px", fontSize: "12px" }}
                              onClick={() =>
                                acceptMutation.mutate({
                                  type: "possible_duplicate",
                                  import_row_id: dup.import_row_id,
                                })
                              }
                            >
                              Als Dublette bestätigen
                            </button>
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={() =>
                                handleOpenCorrectDuplicate(dup.import_row_id, dup.payee_raw || "")
                              }
                            >
                              Doch übernehmen
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Section 3: Transfers */}
          {transfers.length > 0 && (
            <div className="card">
              <h2 className="card-heading">
                Gefundene Umbuchungen / Transfers ({transfers.length})
              </h2>
              <p className="card-body" style={{ marginBottom: "var(--space-3)" }}>
                Diese Buchungen entsprechen vermutlich gegenseitigen Umbuchungen.
              </p>
              <div className="txn-table-wrapper">
                <table className="txn-table">
                  <thead>
                    <tr>
                      <th className="txn-th">Datum</th>
                      <th className="txn-th">Empfänger / Zweck</th>
                      <th className="txn-th txn-th--amount">Betrag</th>
                      <th className="txn-th" style={{ textAlign: "right" }}>
                        Aktionen
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {transfers.map((tr) => (
                      <tr key={tr.import_row_id} className="txn-tr">
                        <td className="txn-td">{tr.booked_date}</td>
                        <td className="txn-td">
                          <div className="txn-payee">{tr.payee_label || "Unbekannt"}</div>
                        </td>
                        <td className="txn-td txn-td--amount">
                          {formatMinorUnitsAsEuro(tr.amount_minor)}
                        </td>
                        <td className="txn-td" style={{ textAlign: "right" }}>
                          <button
                            type="button"
                            className="btn-primary"
                            style={{ padding: "4px 8px", fontSize: "12px" }}
                            onClick={() =>
                              acceptMutation.mutate({
                                type: "transfer_candidate",
                                import_row_id: tr.import_row_id,
                              })
                            }
                          >
                            Als Transfer verknüpfen
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Section 4: Erkannte Verträge */}
          {contractSuggestions.length > 0 && (
            <div className="card">
              <h2 className="card-heading">Erkannte Verträge ({contractSuggestions.length})</h2>
              <p className="card-body" style={{ marginBottom: "var(--space-3)" }}>
                Aus Empfänger, Vertragsnummer, Zahlungsrhythmus und Betragsverlauf erkannt. Zu jedem
                Vorschlag steht, warum wir ihn für einen Vertrag halten – prüfen Sie die Begründung,
                bevor Sie bestätigen.
              </p>
              <ul className="contract-suggestions">
                {contractSuggestions.map((sug) => {
                  const percent = Math.round(sug.detection_confidence * 100);
                  const isBusy = busySuggestionId === sug.source_transaction_id;
                  return (
                    <li
                      key={sug.source_transaction_id}
                      className={
                        sug.is_uncertain
                          ? "contract-suggestion contract-suggestion--uncertain"
                          : "contract-suggestion"
                      }
                    >
                      {/* Die Trennlinie steht vor dem ersten unsicheren
                          Vorschlag, damit die Kennzeichnung nicht nur an einer
                          Plakette hängt, die man beim Durchklicken übersieht. */}
                      {sug.source_transaction_id === firstUncertainSuggestionId && (
                        <p className="contract-suggestions__divider">
                          Ab hier unsicher: Erkennungsgüte zwischen 40 % und 55 %. Das kann ein
                          Vertrag sein – oder eine Reihe zufällig ähnlicher Zahlungen. Bitte einzeln
                          prüfen.
                        </p>
                      )}
                      <div className="contract-suggestion__head">
                        <div>
                          <div className="txn-payee">{sug.display_name}</div>
                          <div className="txn-note">
                            {sug.occurrences_count} Zahlungen von {formatDate(sug.first_date)} bis{" "}
                            {formatDate(sug.last_date)}
                            {sug.display_name !== sug.payee_label ? ` · ${sug.payee_label}` : ""}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="recurring-amount-button"
                          aria-label={`Details und zugehörige Buchungen für ${sug.display_name} anzeigen`}
                          title="Erkennungsdetails und zugehörige Buchungen anzeigen"
                          onClick={() => setSuggestionDetailSourceId(sug.source_transaction_id)}
                        >
                          <span className="recurring-amount-button__value">
                            {formatMinorUnitsAsEuro(
                              signedAmountMinor(sug.amount_minor, sug.direction),
                            )}
                          </span>
                          <span className="recurring-amount-button__hint">
                            {sug.amount_tolerance_minor > 0
                              ? `± ${formatMinorUnitsAsEuro(sug.amount_tolerance_minor)}`
                              : "exakter Betrag"}
                            {" · Details"}
                          </span>
                        </button>
                      </div>

                      <div className="contract-suggestion__badges">
                        <span className="status-badge status-badge--neutral">
                          {CONTRACT_TYPE_LABELS[sug.contract_type]}
                        </span>
                        <span className="status-badge status-badge--neutral">
                          {FREQUENCY_LABELS[sug.frequency]}
                        </span>
                        {sug.contract_key && sug.contract_key_source && (
                          <span
                            className="status-badge status-badge--neutral"
                            title={CONTRACT_KEY_SOURCE_LABELS[sug.contract_key_source]}
                          >
                            Vertragsnummer {sug.contract_key}
                          </span>
                        )}
                        {sug.installments_total !== null && (
                          <span className="status-badge status-badge--neutral">
                            Rate {sug.installments_paid ?? "?"} von {sug.installments_total}
                          </span>
                        )}
                        {sug.is_uncertain ? (
                          <span className="status-badge status-badge--warning">
                            Unsicher · {percent} % Erkennungsgüte
                          </span>
                        ) : (
                          <span className="status-badge status-badge--positive">
                            {percent} % Erkennungsgüte
                          </span>
                        )}
                      </div>

                      {/* Preisstufen: EIN Vertrag mit Verlauf, nicht zwei. */}
                      {sug.amount_steps.length > 1 && (
                        <p className="contract-suggestion__steps">
                          Preis hat sich geändert:{" "}
                          {describeAmountSteps(sug.amount_steps, sug.direction)}. Daraus entsteht{" "}
                          <strong>ein</strong> Vertrag mit Betragsverlauf.
                        </p>
                      )}

                      <ScoreBreakdown score={sug.score_breakdown} />

                      <div className="actions-cell">
                        <button
                          type="button"
                          className="btn-primary"
                          disabled={isBusy}
                          onClick={() => handleOpenAcceptContract(sug)}
                        >
                          Als Vertrag übernehmen
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          title="Der Vorschlag verschwindet dauerhaft und kommt beim nächsten Import nicht zurück."
                          disabled={isBusy}
                          onClick={() => dismissContractSuggestion(sug)}
                        >
                          Kein Vertrag
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {/* Section 5: Vermutlich derselbe Vertrag */}
          {contractDuplicates.length > 0 && (
            <div className="card">
              <h2 className="card-heading">
                Vermutlich derselbe Vertrag ({contractDuplicates.length})
              </h2>
              <p className="card-body" style={{ marginBottom: "var(--space-3)" }}>
                Gleicher Empfänger, gleicher Rhythmus – und der ältere Eintrag endet, bevor der
                jüngere beginnt. Das Zusammenführen überträgt Buchungen und Betragsverlauf auf den
                jüngeren Vertrag; laufen zwei Verträge desselben Anbieters wirklich parallel,
                antworten Sie mit „Sind verschieden“.
              </p>
              <div className="txn-table-wrapper">
                <table className="txn-table">
                  <thead>
                    <tr>
                      <th className="txn-th">Vertrag</th>
                      <th className="txn-th">Zeiträume</th>
                      <th className="txn-th txn-th--amount">Betragsänderung</th>
                      <th className="txn-th" style={{ textAlign: "right" }}>
                        Aktionen
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {contractDuplicates.map((item) => (
                      <tr key={item.target_id} className="txn-tr">
                        <td className="txn-td">
                          <div className="txn-payee">{item.payee_label}</div>
                          <div className="txn-note">
                            {FREQUENCY_LABELS[item.frequency]} · „{item.older.name}“ → „
                            {item.newer.name}“
                          </div>
                        </td>
                        <td className="txn-td">
                          <div className="txn-note">
                            Alt: bis{" "}
                            {item.older.last_seen_date
                              ? formatDate(item.older.last_seen_date)
                              : "unbekannt"}{" "}
                            ·{" "}
                            {formatMinorUnitsAsEuro(
                              signedAmountMinor(item.older.amount_minor, item.direction),
                            )}
                          </div>
                          <div className="txn-note">
                            Neu: seit{" "}
                            {item.newer.first_seen_date
                              ? formatDate(item.newer.first_seen_date)
                              : "unbekannt"}{" "}
                            ·{" "}
                            {formatMinorUnitsAsEuro(
                              signedAmountMinor(item.newer.amount_minor, item.direction),
                            )}
                          </div>
                          <div className="txn-note">{item.gap_days} Tage dazwischen</div>
                        </td>
                        <td className="txn-td txn-td--amount">
                          {item.amount_delta_minor === 0
                            ? "unverändert"
                            : `${item.amount_delta_minor > 0 ? "+" : ""}${formatMinorUnitsAsEuro(item.amount_delta_minor)}`}
                        </td>
                        <td className="txn-td" style={{ textAlign: "right" }}>
                          <div className="actions-cell" style={{ justifyContent: "flex-end" }}>
                            <button
                              type="button"
                              className="btn-primary"
                              style={{ padding: "4px 8px", fontSize: "12px" }}
                              disabled={busyDuplicateId === item.target_id}
                              onClick={() => answerContractDuplicate(item, false)}
                            >
                              Zusammenführen
                            </button>
                            <button
                              type="button"
                              className="btn-secondary"
                              disabled={busyDuplicateId === item.target_id}
                              onClick={() => answerContractDuplicate(item, true)}
                            >
                              Sind verschieden
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Section 6: Verträge ohne Zahlung */}
          {contractEndedChecks.length > 0 && (
            <div className="card">
              <h2 className="card-heading">
                Läuft dieser Vertrag noch? ({contractEndedChecks.length})
              </h2>
              <p className="card-body" style={{ marginBottom: "var(--space-3)" }}>
                Für diese Verträge ist mehrfach keine passende Zahlung mehr eingegangen. Der
                Abgleich kann nur zählen, dass nichts kam — ob gekündigt, gestundet oder nur
                verschoben, wissen nur Sie. Bis zur Antwort zählen sie unverändert in den Fixkosten
                mit.
              </p>
              <div className="txn-table-wrapper">
                <table className="txn-table">
                  <thead>
                    <tr>
                      <th className="txn-th">Vertrag</th>
                      <th className="txn-th">Warum wir fragen</th>
                      <th className="txn-th txn-th--amount">Betrag</th>
                      <th className="txn-th" style={{ textAlign: "right" }}>
                        Aktionen
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {contractEndedChecks.map((check) => (
                      <tr key={check.contract_id} className="txn-tr">
                        <td className="txn-td">
                          <div className="txn-payee">{check.name}</div>
                          {check.provider_label && check.provider_label !== check.name && (
                            <div className="txn-note">{check.provider_label}</div>
                          )}
                        </td>
                        <td className="txn-td">
                          <span className="status-badge status-badge--warning">
                            {check.missed_periods} Fälligkeit(en) ohne Zahlung
                          </span>
                          <div className="txn-note">
                            {check.last_seen_date
                              ? `Letzte passende Zahlung am ${formatDate(check.last_seen_date)}`
                              : "Seit Anlage des Vertrags keine passende Zahlung gefunden"}
                            {` · nächste erwartete Fälligkeit ${formatDate(check.next_due_date)}`}
                          </div>
                        </td>
                        <td className="txn-td txn-td--amount">
                          {/* Beträge liegen immer positiv in der DB, die Richtung
                              steht getrennt - Ausgaben hier negativ darstellen. */}
                          {formatMinorUnitsAsEuro(
                            check.direction === "expense"
                              ? -check.amount_minor
                              : check.amount_minor,
                          )}
                          <div className="txn-note">{FREQUENCY_LABELS[check.frequency]}</div>
                        </td>
                        <td className="txn-td" style={{ textAlign: "right" }}>
                          <div className="actions-cell" style={{ justifyContent: "flex-end" }}>
                            {CONTRACT_ENDED_ACTIONS.map((option) => (
                              <button
                                key={option.action}
                                type="button"
                                className="btn-secondary"
                                title={option.title}
                                disabled={answeringContractId === check.contract_id}
                                onClick={() => answerContractEndedCheck(check, option.action)}
                              >
                                {option.label}
                              </button>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Section 7: erheblich geänderter Betrag */}
          {contractAmountChanges.length > 0 && (
            <div className="card">
              <h2 className="card-heading">
                Stimmt dieser neue Betrag? ({contractAmountChanges.length})
              </h2>
              <p className="card-body" style={{ marginBottom: "var(--space-3)" }}>
                Der Abgleich hat den neuen Betrag bereits übernommen. Er kann aber nicht
                unterscheiden, ob der Anbieter den Preis wirklich geändert hat oder ob eine fremde
                Zahlung desselben Empfängers versehentlich auf diesen Vertrag gebucht wurde — in den
                Daten sieht beides gleich aus. Stimmt der Betrag nicht, lässt er sich auf der
                Verträge-Seite über „Betrag korrigieren“ richtigstellen.
              </p>
              <div className="txn-table-wrapper">
                <table className="txn-table">
                  <thead>
                    <tr>
                      <th className="txn-th">Vertrag</th>
                      <th className="txn-th">Was sich geändert hat</th>
                      <th className="txn-th txn-th--amount">Neuer Betrag</th>
                      <th className="txn-th" style={{ textAlign: "right" }}>
                        Aktionen
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {contractAmountChanges.map((check) => (
                      <tr key={check.event_id} className="txn-tr">
                        <td className="txn-td">
                          <div className="txn-payee">{check.name}</div>
                          {check.provider_label && check.provider_label !== check.name && (
                            <div className="txn-note">{check.provider_label}</div>
                          )}
                        </td>
                        <td className="txn-td">
                          <span
                            className={`status-badge ${
                              check.delta_minor < 0
                                ? "status-badge--positive"
                                : "status-badge--warning"
                            }`}
                          >
                            {describeAmountChange(check)}
                          </span>
                          <div className="txn-note">
                            {formatSignedPercent(check.delta_pct)} ·{" "}
                            {`erstmals am ${formatDate(check.occurred_on)} gebucht`}
                          </div>
                        </td>
                        <td className="txn-td txn-td--amount">
                          {/* Beträge liegen immer positiv in der DB, die Richtung
                              steht getrennt - Ausgaben hier negativ darstellen. */}
                          {formatMinorUnitsAsEuro(
                            check.direction === "expense"
                              ? -check.new_amount_minor
                              : check.new_amount_minor,
                          )}
                          <div className="txn-note">{FREQUENCY_LABELS[check.frequency]}</div>
                        </td>
                        <td className="txn-td" style={{ textAlign: "right" }}>
                          <div className="actions-cell" style={{ justifyContent: "flex-end" }}>
                            <button
                              type="button"
                              className="btn-secondary"
                              title="Der neue Betrag ist richtig - die Frage verschwindet und der Vertrag rechnet unverändert weiter."
                              disabled={answeringAmountChangeId === check.event_id}
                              onClick={() => confirmAmountChange(check)}
                            >
                              Stimmt so
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Sammeluebernahme sicherer KI-Vorschlaege: Vorschau -> Anwenden */}
      {proposalBatch && (
        <Modal
          title={
            proposalApplied ? "Lauf-Vorschläge übernommen" : "Sichere Lauf-Vorschläge – Vorschau"
          }
          size="wide"
          onClose={closeProposalModal}
        >
          <p className="card-body" style={{ margin: 0 }}>
            Angezeigt werden nur Vorschläge des letzten Klassifikationslaufs – keine alten
            Ergebnisse anderer Modelle. Der Lauf kann Vorschläge aus dem Modell und aus dem lokalen
            Vorfilter für bestätigte Empfänger enthalten. Berücksichtigt werden Vorschläge ab einer
            Konfidenz von <strong>{Math.round(proposalBatch.min_confidence * 100)} %</strong>.
            Bereits von Hand kategorisierte Buchungen und Split-Buchungen bleiben unangetastet, und
            wo die KI keinen passenden Treffer gefunden hat, wird nichts erzwungen.
          </p>

          <div className="review-batch-summary">
            <span className="status-badge status-badge--positive">
              {proposalApplied ? "übernommen" : "wird übernommen"}: {proposalBatch.applied}
            </span>
            <span className="status-badge">unverändert: {proposalBatch.unchanged}</span>
            <span className="status-badge">übersprungen: {proposalBatch.skipped}</span>
            {proposalBatch.conflict > 0 && (
              <span className="status-badge status-badge--warning">
                Konflikt: {proposalBatch.conflict}
              </span>
            )}
          </div>

          {proposalBatch.items.length > 0 && (
            <div className="table-scroll" style={{ maxHeight: "42vh", overflowY: "auto" }}>
              <table className="txn-table">
                <thead>
                  <tr>
                    <th className="txn-th">Buchung</th>
                    <th className="txn-th">Kategorie</th>
                    <th className="txn-th">Quelle</th>
                    <th className="txn-th">Konfidenz</th>
                    <th className="txn-th">Ergebnis</th>
                  </tr>
                </thead>
                <tbody>
                  {proposalBatch.items.map((item) => (
                    <tr className="txn-tr" key={item.transaction_id}>
                      <td className="txn-td" style={{ fontFamily: "var(--font-mono)" }}>
                        {String(item.transaction_id).slice(0, 8)}…
                      </td>
                      <td className="txn-td">{item.category_name ?? "–"}</td>
                      <td className="txn-td">
                        {item.source === "history" ? "Bekannter Empfänger (ohne KI)" : "KI"}
                      </td>
                      <td className="txn-td">{Math.round((item.confidence ?? 0) * 100)} %</td>
                      <td className="txn-td">{describeProposalOutcome(item)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {proposalBatch.items.length === 0 && (
            <p className="card-body" style={{ margin: 0 }}>
              Es gibt aktuell keine offenen KI-Vorschläge. Starte zuerst einen KI-Lauf.
            </p>
          )}

          {proposalApplyMutation.isError && (
            <p className="status-error" role="alert">
              {proposalApplyMutation.error instanceof Error
                ? proposalApplyMutation.error.message
                : "Die Vorschläge konnten nicht übernommen werden."}
            </p>
          )}

          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={closeProposalModal}>
              {proposalApplied ? "Schließen" : "Abbrechen"}
            </button>
            {!proposalApplied && (
              <button
                type="button"
                className="btn-primary"
                disabled={proposalApplyMutation.isPending || proposalBatch.applied === 0}
                onClick={() => proposalApplyMutation.mutate()}
              >
                {proposalApplyMutation.isPending
                  ? "Übernehme…"
                  : `${proposalBatch.applied} Buchung(en) übernehmen`}
              </button>
            )}
          </div>
        </Modal>
      )}

      {suggestionDetailSourceId && (
        <Modal
          title={
            suggestionDetail
              ? `Erkennungsdetails · ${suggestionDetail.suggestion.display_name}`
              : "Erkennungsdetails"
          }
          size="wide"
          onClose={() => setSuggestionDetailSourceId(null)}
        >
          {isSuggestionDetailLoading && (
            <p className="card-body" role="status">
              Zugehörige Buchungen werden geladen…
            </p>
          )}

          {suggestionDetailError && (
            <p className="status-error" role="alert">
              {suggestionDetailError instanceof Error
                ? suggestionDetailError.message
                : "Die Erkennungsdetails konnten nicht geladen werden."}
            </p>
          )}

          {/* Die recurring-detail-Klassen sind reine Layoutbausteine (Kennzahlengitter,
              scrollende Tabelle) und werden hier weiterverwendet - der Name stammt noch
              vom abgeloesten Fixkostendialog. */}
          {suggestionDetail && (
            <div className="recurring-detail">
              <div className="recurring-detail__summary">
                <div className="recurring-detail__metric">
                  <span>Aktueller Betrag</span>
                  <strong>
                    {formatMinorUnitsAsEuro(
                      signedAmountMinor(
                        suggestionDetail.suggestion.amount_minor,
                        suggestionDetail.suggestion.direction,
                      ),
                    )}
                  </strong>
                </div>
                <div className="recurring-detail__metric">
                  <span>Erkannter Rhythmus</span>
                  <strong>{FREQUENCY_LABELS[suggestionDetail.suggestion.frequency]}</strong>
                </div>
                <div className="recurring-detail__metric">
                  <span>Erkennungsgüte</span>
                  <strong>
                    {Math.round(suggestionDetail.suggestion.detection_confidence * 100)} %
                    {suggestionDetail.suggestion.is_uncertain ? " (unsicher)" : ""}
                  </strong>
                </div>
                <div className="recurring-detail__metric">
                  <span>Passende Buchungen</span>
                  <strong>{suggestionDetail.transactions.length}</strong>
                </div>
              </div>

              <ScoreBreakdown score={suggestionDetail.suggestion.score_breakdown} />

              {suggestionDetail.suggestion.amount_steps.length > 1 && (
                <p className="recurring-detail__explanation">
                  Erkannte Preisstufen:{" "}
                  {describeAmountSteps(
                    suggestionDetail.suggestion.amount_steps,
                    suggestionDetail.suggestion.direction,
                  )}
                  . Beim Bestätigen entsteht ein Vertrag, dessen Betragsverlauf diese Stufen
                  enthält.
                </p>
              )}

              <div className="txn-table-wrapper recurring-detail__table-wrapper">
                <table className="txn-table">
                  <thead>
                    <tr>
                      <th className="txn-th">Datum</th>
                      <th className="txn-th">Konto</th>
                      <th className="txn-th">Buchung</th>
                      <th className="txn-th">Preisstufe</th>
                      <th className="txn-th txn-th--amount">Betrag</th>
                    </tr>
                  </thead>
                  <tbody>
                    {suggestionDetail.transactions.map((transaction) => {
                      const step =
                        suggestionDetail.suggestion.amount_steps[transaction.amount_step_index];
                      return (
                        <tr key={transaction.transaction_id} className="txn-tr">
                          <td className="txn-td">{formatDate(transaction.booked_date)}</td>
                          <td className="txn-td">{transaction.account_name}</td>
                          <td className="txn-td">
                            <div className="txn-payee">
                              {transaction.payee_label || suggestionDetail.suggestion.payee_label}
                            </div>
                            {transaction.note && <div className="txn-note">{transaction.note}</div>}
                          </td>
                          <td className="txn-td">
                            {/* Ohne diese Spalte liesse sich nicht zeigen,
                                welche Buchungen zu welchem Preis gehörten. */}
                            {step
                              ? `${formatMinorUnitsAsEuro(signedAmountMinor(step.amount_minor, suggestionDetail.suggestion.direction))} (ab ${formatMonthYear(step.first_date)})`
                              : "–"}
                          </td>
                          <td className="txn-td txn-td--amount">
                            {formatMinorUnitsAsEuro(transaction.amount_minor)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setSuggestionDetailSourceId(null)}
                >
                  Schließen
                </button>
              </div>
            </div>
          )}
        </Modal>
      )}

      {/* Korrektur-Modal */}
      {correctModal && (
        <Modal
          title={
            correctModal.kind === "contract_suggestion"
              ? "Vertrag übernehmen"
              : "Fall korrigieren & Regel erstellen"
          }
          size="wide"
          className="review-correction-modal"
          onClose={() => setCorrectModal(null)}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            {correctModal.kind === "contract_suggestion" ? (
              <p className="card-body" style={{ margin: 0 }}>
                {`„${correctModal.suggestion.display_name}“ mit ${formatMinorUnitsAsEuro(
                  signedAmountMinor(
                    correctModal.suggestion.amount_minor,
                    correctModal.suggestion.direction,
                  ),
                )} (${FREQUENCY_LABELS[correctModal.suggestion.frequency]}, ${CONTRACT_TYPE_LABELS[correctModal.suggestion.contract_type]}) als Vertrag anlegen. Verwendet wird der letzte echte Betrag, kein Durchschnitt.`}
                {correctModal.suggestion.amount_steps.length > 1 && (
                  <>
                    {" "}
                    <strong>
                      Es entsteht ein einziger Vertrag mit Betragsverlauf:{" "}
                      {describeAmountSteps(
                        correctModal.suggestion.amount_steps,
                        correctModal.suggestion.direction,
                      )}
                      .
                    </strong>
                  </>
                )}
              </p>
            ) : (
              <p className="card-body" style={{ margin: 0 }}>
                {`Zahlungsempfänger „${correctModal.payee}“`}
              </p>
            )}
            {correctModal.kind === "unclassified" && (
              <div className="form-field">
                <div
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
                >
                  <label className="form-label" htmlFor="payee-pattern-input">
                    Händler- / Payee-Muster
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
                  id="payee-pattern-input"
                  className="form-input"
                  value={payeePattern}
                  onChange={(e) => {
                    setPayeePattern(e.target.value);
                    setRulePreview(null);
                  }}
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
                        <strong>Mehrere Händler:</strong> <code>(Rewe|Penny|Aldi)</code> matcht
                        jeden dieser Supermärkte.
                      </li>
                      <li>
                        <strong>
                          Anfang / Ende (<code>^</code> / <code>$</code>):
                        </strong>{" "}
                        <code>^musterberatung</code> (beginnt mit musterberatung) |{" "}
                        <code>GmbH$</code> (endet auf GmbH).
                      </li>
                    </ul>
                    <span
                      style={{
                        fontSize: "11px",
                        color: "var(--color-text-secondary)",
                        fontStyle: "italic",
                      }}
                    >
                      Hinweis: Groß-/Kleinschreibung wird von der Regel-Engine automatisch
                      ignoriert.
                    </span>
                  </div>
                )}
              </div>
            )}

            {correctModal.kind === "unclassified" && createRule && (
              <div className="review-rule-precheck">
                <div>
                  <strong>1. Eigenen Bestand & Regeln prüfen</strong>
                  <p className="card-body">
                    Prüft zuerst bereits kategorisierte Buchungen desselben Empfängers und danach
                    deine aktiven Regeln. Die KI wird dabei nicht aufgerufen.
                  </p>
                </div>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => historyMatchMutation.mutate()}
                  disabled={historyMatchMutation.isPending}
                >
                  {historyMatchMutation.isPending
                    ? "Prüfe Bestand und Regeln…"
                    : "Bestand & Regeln prüfen"}
                </button>
                {historyMatchMutation.data?.matched && (
                  <p className="review-history-match" role="status">
                    <span className="status-badge status-badge--positive">Ohne KI gefunden</span>{" "}
                    {historyMatchMutation.data.source === "rule" ? (
                      <>
                        Regel „{historyMatchMutation.data.rule_name}“ →{" "}
                        <strong>{historyMatchMutation.data.category_label}</strong>. Bitte prüfen.
                      </>
                    ) : (
                      <>
                        <strong>{historyMatchMutation.data.category_label}</strong> aus{" "}
                        {historyMatchMutation.data.evidence_count} bisherigen Buchung(en). Bitte
                        prüfen.
                      </>
                    )}
                  </p>
                )}
                {historyMatchMutation.data && !historyMatchMutation.data.matched && (
                  <p className="review-history-match" role="status">
                    <span className="status-badge status-badge--neutral">
                      Kein eindeutiger Treffer
                    </span>{" "}
                    {historyMatchMutation.data.ambiguous
                      ? "Der Empfänger wurde bisher mehreren Kategorien zugeordnet."
                      : "Keine aktive Regel und noch kein bestätigtes Vorbild gefunden."}
                  </p>
                )}
              </div>
            )}

            <div className="form-field">
              <div className="review-category-head">
                <label className="form-label" htmlFor="correct-category-select">
                  {correctModal.kind === "unclassified" && createRule && (
                    <span aria-hidden="true">2. </span>
                  )}
                  Ziel-Kategorie
                </label>
                <button
                  type="button"
                  className="btn-secondary btn-inline"
                  onClick={handleRequestAISuggestion}
                  disabled={aiSuggestMutation.isPending}
                >
                  {aiSuggestMutation.isPending
                    ? "KI denkt nach…"
                    : "✨ Kategorie mit KI vorschlagen"}
                </button>
              </div>
              <CategoryPicker
                id="correct-category-select"
                categoryGroups={categoryGroups}
                value={selectedCategoryId}
                onChange={(catId) => {
                  setSelectedCategoryId(catId);
                }}
                noneLabel="-- Bitte Kategorie wählen --"
              />

              {aiSuggestMutation.isError && (
                <p className="status-error" role="alert">
                  {aiSuggestMutation.error instanceof Error
                    ? aiSuggestMutation.error.message
                    : "Die KI konnte nicht befragt werden."}
                </p>
              )}

              {aiSuggestMutation.data &&
                (aiSuggestMutation.data.category_id ? (
                  <p className="review-ai-hint" role="status">
                    <AIBadge
                      confidence={aiSuggestMutation.data.confidence}
                      reasonCode={aiSuggestMutation.data.reason_code}
                    />{" "}
                    Vorschlag übernommen:{" "}
                    <strong>
                      {categoryNameById.get(String(aiSuggestMutation.data.category_id)) ??
                        "Unbekannte Kategorie"}
                    </strong>{" "}
                    – bitte prüfen und bestätigen.
                  </p>
                ) : (
                  <p className="review-ai-hint" role="status">
                    <span className="status-badge status-badge--warning">Kein Treffer</span> Die KI
                    hat keine passende Kategorie gefunden und rät bewusst nicht. Bitte von Hand
                    wählen – oder auf der Kategorienseite eine passende Kategorie anlegen.
                  </p>
                ))}
            </div>

            {correctModal.kind === "unclassified" && (
              <>
                {competingRule && (
                  <div className="review-competing-rule" role="status">
                    <span className="status-badge status-badge--warning">
                      Bestehende Regel gefunden
                    </span>
                    <p className="card-body">
                      Die aktive Regel „{competingRule.name}“ ordnet „{payeePattern}“ bereits{" "}
                      {competingRule.action_category_id
                        ? (categoryNameById.get(competingRule.action_category_id) ??
                          "einer anderen Kategorie")
                        : "einer anderen Kategorie"}{" "}
                      zu. Sie wird nicht still überschrieben –{" "}
                      <Link to="/regeln">Regel in der Regelverwaltung öffnen</Link>.
                    </p>
                  </div>
                )}

                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <input
                    type="checkbox"
                    id="create-rule-checkbox"
                    checked={createRule}
                    onChange={(e) => {
                      setCreateRule(e.target.checked);
                      setRulePreview(null);
                    }}
                  />
                  <label
                    htmlFor="create-rule-checkbox"
                    style={{ fontSize: "13px", fontWeight: 600, cursor: "pointer" }}
                  >
                    Dauerhafte Regel für künftige Buchungen speichern
                  </label>
                </div>

                {createRule && (
                  <div
                    className="card"
                    style={{
                      padding: "var(--space-3)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "var(--space-3)",
                      backgroundColor: "var(--color-surface-raised, rgba(255, 255, 255, 0.03))",
                    }}
                  >
                    <div style={{ fontSize: "12px", color: "var(--color-text-secondary)" }}>
                      Kriterien für die neue Regel:
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
                      <label
                        style={{
                          fontSize: "13px",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={ruleMatchPayee}
                          onChange={(event) => {
                            setRuleMatchPayee(event.target.checked);
                            setRulePreview(null);
                          }}
                        />{" "}
                        Empfänger: „{payeePattern || "–"}“
                      </label>
                      <label
                        style={{
                          fontSize: "13px",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={ruleMatchExactAmount}
                          onChange={(event) => {
                            setRuleMatchExactAmount(event.target.checked);
                            setRulePreview(null);
                          }}
                        />{" "}
                        Nur exakter Betrag ({formatMinorUnitsAsEuro(correctModal.amountMinor)})
                      </label>
                      <label
                        style={{
                          fontSize: "13px",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={ruleMatchPurpose}
                          disabled={!correctModal.purpose}
                          onChange={(event) => {
                            setRuleMatchPurpose(event.target.checked);
                            setRulePreview(null);
                          }}
                        />{" "}
                        Gleicher Verwendungszweck
                      </label>
                      <label
                        style={{
                          fontSize: "13px",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={ruleMatchAccount}
                          onChange={(event) => {
                            setRuleMatchAccount(event.target.checked);
                            setRulePreview(null);
                          }}
                        />{" "}
                        Nur dieses Konto
                      </label>
                    </div>

                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        marginTop: "4px",
                      }}
                    >
                      <button
                        type="button"
                        className="btn-secondary"
                        style={{ fontSize: "12px", padding: "4px 10px" }}
                        onClick={() => rulePreviewMutation.mutate()}
                        disabled={
                          rulePreviewMutation.isPending ||
                          (!ruleMatchPayee &&
                            !ruleMatchPurpose &&
                            !ruleMatchExactAmount &&
                            !ruleMatchAccount)
                        }
                      >
                        {rulePreviewMutation.isPending
                          ? "Prüfe Treffer…"
                          : "🔍 Vorschau offener Treffer prüfen"}
                      </button>

                      {rulePreview && (
                        <span
                          className="status-badge status-badge--positive"
                          style={{ fontSize: "11px" }}
                        >
                          ✓ {rulePreview.matched} offene Treffer gefunden
                        </span>
                      )}
                    </div>

                    {rulePreview && rulePreview.items.length > 0 && (
                      <div
                        className="table-scroll"
                        style={{ maxHeight: "180px", marginTop: "6px" }}
                      >
                        <table className="txn-table">
                          <thead>
                            <tr>
                              <th className="txn-th">Datum</th>
                              <th className="txn-th">Empfänger / Zweck</th>
                              <th className="txn-th txn-th--amount">Betrag</th>
                            </tr>
                          </thead>
                          <tbody>
                            {rulePreview.items.map((item) => (
                              <tr className="txn-tr" key={item.transaction_id}>
                                <td className="txn-td">{item.booked_date}</td>
                                <td className="txn-td">
                                  <div className="txn-payee">{item.payee || "Unbekannt"}</div>
                                  {item.purpose && <div className="txn-note">{item.purpose}</div>}
                                </td>
                                <td className="txn-td txn-td--amount">
                                  {formatMinorUnitsAsEuro(item.amount_minor)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}

            {actionError && (
              <p className="status-error" role="alert">
                {actionError instanceof Error
                  ? actionError.message
                  : "Die Aktion konnte nicht ausgeführt werden."}
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
              <button type="button" className="btn-secondary" onClick={() => setCorrectModal(null)}>
                Abbrechen
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={
                  correctMutation.isPending ||
                  acceptMutation.isPending ||
                  rulePreviewMutation.isPending ||
                  !selectedCategoryId ||
                  (correctModal.kind === "unclassified" &&
                    createRule &&
                    !ruleMatchPayee &&
                    !ruleMatchPurpose &&
                    !ruleMatchExactAmount &&
                    !ruleMatchAccount)
                }
                onClick={handleSaveCorrection}
              >
                {correctMutation.isPending ||
                acceptMutation.isPending ||
                rulePreviewMutation.isPending
                  ? "Speichere..."
                  : correctModal.kind === "unclassified" && createRule && rulePreview === null
                    ? "Regel prüfen"
                    : correctModal.kind === "unclassified" && createRule && rulePreview
                      ? `Regel anlegen & ${rulePreview.matched} Treffer zuordnen`
                      : "Bestätigen & Speichern"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}
