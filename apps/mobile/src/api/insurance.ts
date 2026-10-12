/**
 * Datenschicht des Versicherungsmoduls auf Mobile
 * (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 7 und Phase P2b).
 *
 * WARUM HIER NUR FUENF ENDPUNKTE STEHEN
 *
 * Angebunden sind Liste, Detail, Spartenkatalog und Kennzahlen - und als
 * EINZIGE schreibende Aktion das Anlegen einer einfachen Police. Bewusst nicht
 * angebunden sind die Bestaetigungsaktionen (`confirm-main-due`,
 * `confirm-notice-terms`, `confirm-line`), die Bestandsuebernahme, der
 * Statuswechsel, die Vertragsverknuepfung, Deckungen und Personen.
 *
 * Der Grund ist die Frage, die dieses Modul mobil beantwortet: *"Ich stehe am
 * Unfallort und brauche Nummer, Kontakt und Frist."* Das ist Nachschlagen, und
 * Nachschlagen muss auf dem Geraet funktionieren, das man dabei hat. Pflege ist
 * das Gegenteil davon: eine Hauptfaelligkeit bestaetigt man mit dem
 * Versicherungsschein vor sich, eine Kuendigung entscheidet man neben dem
 * Vergleichsangebot, eine Sparte ordnet man mit dem Katalogtext daneben zu.
 * Diese drei Vorgaenge sind am Schreibtisch besser aufgehoben und stehen dort
 * vollstaendig zur Verfuegung (apps/web/src/lib/insuranceApi.ts).
 *
 * Die eine Ausnahme ist das Anlegen: eine neue Police entsteht typischerweise
 * genau dann, wenn man den Vertrag gerade in der Hand hat - und vier Felder
 * sind keine Pflege, sondern eine Notiz. Genau vier: Sparte, Versicherer,
 * Policennummer und der Geldfall. Alles Weitere (Fristfelder, Deckungen,
 * Bausteine) traegt man im Web nach; das Ergebnis ist eine Police mit
 * `main_due_source = 'catalog_suggested'` und einer ANGENOMMENEN Frist. Das ist
 * der haeufige Fall und kein Fehler.
 *
 * Die Beschriftungen der geschlossenen Vokabulare stehen hier neben den Typen
 * und nicht in den Komponenten - wie im Web: kommt im Schema ein Wert dazu,
 * faellt an genau einer Stelle auf, dass die Uebersetzung fehlt.
 */

import { ApiError } from "./client";
import { getApiBaseUrl } from "./config";
import type {
  AnchorBasisKind,
  Certainty,
  DateSource,
  DeadlineKind,
  InsuranceLine,
  InsurancePolicy,
  InsurancePolicyCreate,
  InsurancePolicyDetail,
  InsuranceSummary,
  NoticeAnchor,
  NoticeTermsSource,
  PolicyDeadline,
  PolicyStatus,
  PremiumFrequency,
  PremiumPayer,
  RiskField,
} from "./types";

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

/**
 * Eigener Fetch-Helfer statt `authorizedFetch` aus ./client.
 *
 * Der dortige Helfer ist modulprivat und ./client.ts gehoert nicht zu diesem
 * Arbeitspaket. Uebernommen sind deshalb sein Verhalten und seine Meldungen
 * (Bearer-Token, `X-Household-Id`, dieselbe Unerreichbarkeitsmeldung, dieselbe
 * ApiError-Klasse), NICHT sein Umfang: `If-Match` und die Konfliktbehandlung
 * fehlen hier, weil P2b keinen PATCH kennt. Ein Aufrufer kann `apiErrorDetail()`
 * aus ./client also unveraendert benutzen.
 */
type InsuranceRequestOptions = {
  method?: "GET" | "POST";
  accessToken: string;
  householdId: string;
  body?: unknown;
  signal?: AbortSignal;
};

/**
 * Stabile Fehlerkennungen des Moduls (services/api/app/services/
 * insurance_service.py) auf verstaendliche Texte abgebildet.
 *
 * Aufgefuehrt sind genau die, die das Vier-Felder-Formular ausloesen kann.
 * Geprueft wird auf die Kennung, angezeigt wird der Text - der Server
 * uebersetzt nicht.
 */
const DETAIL_MESSAGES: Record<string, string> = {
  insurance_policy_not_found: "Diese Versicherung gibt es nicht (mehr).",
  insurance_line_unknown: "Diese Sparte steht nicht (mehr) im Katalog. Bitte neu auswählen.",
  insurance_premium_source_conflict:
    "Entweder zahlt der Haushalt über einen Vertrag oder ein Dritter zahlt – nie beides.",
  insurance_premium_payer_incomplete:
    "Zu einem fremd gezahlten Beitrag gehören Betrag, Rhythmus und wer ihn zahlt.",
  insurance_contract_not_found: "Der gewählte Vertrag gehört nicht zu diesem Haushalt.",
  insurance_contract_already_linked: "Dieser Vertrag hängt bereits an einer anderen Versicherung.",
  insurance_policy_number_duplicate:
    "Zu dieser Gesellschaft ist diese Policennummer schon erfasst.",
};

const FORBIDDEN_MESSAGES: Record<string, string> = {
  household_role_readonly: "Diese Rolle darf im Haushalt nur lesen, nicht ändern.",
  household_owner_required: "Das darf nur die Besitzerin/der Besitzer des Haushalts.",
};

async function insuranceFetch<T>(path: string, options: InsuranceRequestOptions): Promise<T> {
  const { method = "GET", accessToken, householdId, body, signal } = options;

  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
    "X-Household-Id": householdId,
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (cause) {
    throw new Error(
      `API unter ${getApiBaseUrl()} nicht erreichbar. Läuft die lokale API (docker compose up)?`,
      { cause },
    );
  }

  if (!response.ok) {
    let detail = "";
    try {
      const payload: unknown = await response.json();
      if (
        payload &&
        typeof payload === "object" &&
        typeof (payload as { detail?: unknown }).detail === "string"
      ) {
        detail = (payload as { detail: string }).detail;
      }
    } catch {
      // Antwortkoerper war kein lesbares JSON - unten greift die generische Meldung.
    }

    const errorOptions = { detail: detail || null };

    if (response.status === 401) {
      throw new ApiError(
        detail || "Anmeldung abgelaufen. Bitte erneut anmelden.",
        401,
        errorOptions,
      );
    }
    if (response.status === 403) {
      throw new ApiError(
        FORBIDDEN_MESSAGES[detail] ?? "Kein Zugriff auf diesen Haushalt.",
        403,
        errorOptions,
      );
    }
    throw new ApiError(
      DETAIL_MESSAGES[detail] || detail || `Anfrage fehlgeschlagen (HTTP ${response.status}).`,
      response.status,
      errorOptions,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// Abrufe
// ---------------------------------------------------------------------------

/**
 * Der Bestand fuer die gruppierte Liste.
 *
 * `sort=line` ist ausgeschrieben, obwohl es der Serverstandard ist: die mobile
 * Liste gruppiert nach Risikofeld und verlaesst sich innerhalb einer Gruppe auf
 * die Katalogreihenfolge der Sparten. Wuerde der Standard eines Tages auf
 * `deadline` wechseln, sortierte sich die Gruppierung still um.
 *
 * `include_ended` bleibt ungesetzt - beendete und irrtuemlich erfasste Policen
 * gehoeren nicht in eine Liste, die die Frage "welchen Schutz habe ich gerade"
 * beantwortet. Die Detailseite erreicht sie weiterhin ueber ihre id, etwa aus
 * einem aelteren Push.
 */
export function listInsurancePolicies(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<InsurancePolicy[]> {
  return insuranceFetch<InsurancePolicy[]>("/v1/insurance/policies?sort=line", {
    accessToken,
    householdId,
    signal,
  });
}

/**
 * Police, Spartenblock und die noch fehlenden Pflichtfelder in einer Antwort.
 *
 * Der Zwecktext ("wofuer ist die da") kommt aus `detail.line` und braucht auf
 * Mobile deshalb keinen zweiten Aufruf von `/policies/{id}/purpose`. Der
 * Unterschied zum Web-Endpunkt ist bewusst in Kauf genommen: `purpose` liefert
 * zusaetzlich `coverage_source` ('catalog' | 'policy'), also die Unterscheidung
 * zwischen Katalogaussage und den fuer DIESE Police erfassten Bausteinen. Die
 * Bausteine entstehen erst in P4 und gibt es auf Mobile ohnehin nicht - bis
 * dahin waere `coverage_source` immer 'catalog', und ein zweiter Aufruf brachte
 * nur den Vorbehaltssatz mit (siehe POLICY_PURPOSE_CAVEAT unten).
 */
export function getInsurancePolicy(
  accessToken: string,
  householdId: string,
  policyId: string,
  signal?: AbortSignal,
): Promise<InsurancePolicyDetail> {
  return insuranceFetch<InsurancePolicyDetail>(`/v1/insurance/policies/${policyId}`, {
    accessToken,
    householdId,
    signal,
  });
}

/**
 * Spartenkatalog fuer das Anlegeformular.
 *
 * Er ist global und haushaltsunabhaengig (Migrationsgut), der Endpunkt haengt
 * aber wie alle anderen an der Haushaltssitzung - `X-Household-Id` gehoert
 * deshalb trotzdem mit.
 */
export function listInsuranceLines(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<InsuranceLine[]> {
  return insuranceFetch<InsuranceLine[]>("/v1/insurance/lines", {
    accessToken,
    householdId,
    signal,
  });
}

/** Kennzahlen der Kopfzeile. */
export function getInsuranceSummary(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<InsuranceSummary> {
  return insuranceFetch<InsuranceSummary>("/v1/insurance/summary", {
    accessToken,
    householdId,
    signal,
  });
}

/**
 * Die einzige schreibende Aktion in P2b.
 *
 * Nimmt bewusst den vollen Vertragstyp entgegen und nicht die App-Form: dieses
 * Modul ist Transport, das Zusammensetzen des Koerpers steht in
 * `toPolicyCreateBody()`. Antwort ist die angelegte Police mitsamt der frisch
 * gerechneten Frist; der Aufrufer liest sie nicht aus, sondern laedt Liste und
 * Kennzahlen neu, damit es keine zwei Wahrheiten ueber denselben Bestand gibt.
 */
export function createInsurancePolicy(
  accessToken: string,
  householdId: string,
  input: InsurancePolicyCreate,
): Promise<InsurancePolicy> {
  return insuranceFetch<InsurancePolicy>("/v1/insurance/policies", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

// ---------------------------------------------------------------------------
// Das Vier-Felder-Formular
// ---------------------------------------------------------------------------

/**
 * Der Geldfall als unterscheidbare Union statt als drei nebeneinander
 * gesetzte, optionale Felder.
 *
 * `InsurancePolicyCreate` erlaubt strukturell, `contract_id` UND `external`
 * zugleich zu schicken; der Server lehnt das mit 422
 * `insurance_premium_source_conflict` ab. Diese Union macht denselben Fehler
 * schon beim Uebersetzen unmoeglich - eine abgelehnte Anfrage, die es nie
 * geben muss, ist die schlechtere Art, dieselbe Regel durchzusetzen.
 */
export type PolicyPremiumDraft =
  | { kind: "contract"; contractId: string }
  | {
      kind: "external";
      premiumMinor: number;
      frequency: PremiumFrequency;
      payer: PremiumPayer;
      payerNote?: string | null;
    }
  | { kind: "none" };

/** Die vier Felder aus Plan Abschnitt 7 - mehr nimmt die App nicht entgegen. */
export interface SimplePolicyDraft {
  lineKey: string;
  insurerName: string;
  policyNumber?: string | null;
  premium: PolicyPremiumDraft;
}

/** Leerer Text ist keine Angabe. Sonst entstuende eine Police mit einer
 *  Policennummer "", die bei der naechsten Erfassung als Dublette gilt. */
function trimmedOrNull(value: string | null | undefined): string | null {
  const trimmed = value?.trim() ?? "";
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Baut den Anfragekoerper. Alles, was das Formular nicht kennt, wird
 * ausgelassen statt auf einen erratenen Wert gesetzt - der Server hat fuer
 * `status`, `service_channel` und `notice_anchor` eigene Voreinstellungen, und
 * ein hier mitgeschicktes Datum waere eine Behauptung ohne Grundlage.
 */
export function toPolicyCreateBody(draft: SimplePolicyDraft): InsurancePolicyCreate {
  const body: InsurancePolicyCreate = {
    line_key: draft.lineKey,
    insurer_name: draft.insurerName.trim(),
    policy_number: trimmedOrNull(draft.policyNumber),
  };

  if (draft.premium.kind === "contract") {
    return { ...body, contract_id: draft.premium.contractId };
  }
  if (draft.premium.kind === "external") {
    return {
      ...body,
      external: {
        external_premium_minor: draft.premium.premiumMinor,
        external_frequency: draft.premium.frequency,
        premium_payer: draft.premium.payer,
        premium_payer_note: trimmedOrNull(draft.premium.payerNote),
      },
    };
  }
  return body;
}

// ---------------------------------------------------------------------------
// Beschriftungen der geschlossenen Vokabulare
// ---------------------------------------------------------------------------

/**
 * Die acht Risikofelder als Ueberschriften der gruppierten Liste.
 *
 * Bewusst Alltagsworte und keine Branchenbegriffe: die Gruppierung ersetzt auf
 * Mobile die Filterleiste des Web und ist damit der Einstieg, nicht eine
 * Verfeinerung. Wer "Hab und Gut" liest, findet seine Hausratversicherung;
 * "Sachversicherung" muesste er erst uebersetzen.
 */
export const RISK_FIELD_LABEL: Record<RiskField, string> = {
  health: "Gesundheit",
  income: "Einkommen",
  liability: "Haftpflicht",
  property: "Hab und Gut",
  mobility: "Mobilität",
  retirement: "Altersvorsorge",
  legal: "Recht",
  other: "Sonstiges",
};

/**
 * Reihenfolge der Gruppen, festgelegt statt aus den Daten abgeleitet.
 *
 * Sie entspricht der Aufzaehlung in services/api/app/schemas/insurance.py. Eine
 * aus dem Bestand errechnete Reihenfolge (etwa nach Anzahl) haette dieselbe
 * Sparte je nach Haushalt an anderer Stelle - die Liste waere nicht
 * wiedererkennbar.
 */
export const RISK_FIELD_ORDER: readonly RiskField[] = [
  "health",
  "income",
  "liability",
  "property",
  "mobility",
  "retirement",
  "legal",
  "other",
];

export const POLICY_STATUS_LABEL: Record<PolicyStatus, string> = {
  draft: "Entwurf",
  applied: "beantragt",
  active: "aktiv",
  suspended: "ruhend",
  paid_up: "beitragsfrei",
  dormant: "ruhend gestellt",
  cancelled: "gekündigt",
  ended: "beendet",
  entered_in_error: "irrtümlich erfasst",
};

export const PREMIUM_FREQUENCY_LABEL: Record<PremiumFrequency, string> = {
  weekly: "wöchentlich",
  monthly: "monatlich",
  quarterly: "vierteljährlich",
  semiannual: "halbjährlich",
  yearly: "jährlich",
  single: "einmalig",
};

export const PREMIUM_PAYER_LABEL: Record<PremiumPayer, string> = {
  employer: "zahlt der Arbeitgeber",
  self_payroll: "über die Gehaltsabrechnung",
  partner: "zahlt der Partner",
  other: "zahlt ein Dritter",
};

export const NOTICE_ANCHOR_LABEL: Record<NoticeAnchor, string> = {
  term_end: "zum Ablauf des Versicherungsjahres",
  main_due: "zur Hauptfälligkeit",
  period_end: "zum Ende der Zahlperiode",
  fixed_date: "zu einem festen Datum",
  month_end: "zum Monatsende",
  per_event: "anlassbezogen",
  none: "kein Bezugspunkt hinterlegt",
};

export const ANCHOR_BASIS_KIND_LABEL: Record<AnchorBasisKind, string> = {
  statute: "Gesetz",
  policy_terms: "Versicherungsbedingungen",
  contract: "Vertrag",
};

export const DEADLINE_KIND_LABEL: Record<DeadlineKind, string> = {
  kuendigungsfrist: "Kündigungsfrist",
  widerrufsfrist: "Widerrufsfrist",
  anfechtungsfrist: "Anfechtungsfrist",
  klagefrist: "Klagefrist",
  zahlungsfrist: "Zahlungsfrist",
};

/** Herkunft eines Datums, wortgleich zu Plan 2.4 und zum Web. */
export const DATE_SOURCE_LABEL: Record<DateSource, string> = {
  user_confirmed: "bestätigt (manuell)",
  catalog_suggested: "angenommen (Katalog)",
  not_computable: "nicht berechenbar – Angabe fehlt",
};

export const NOTICE_TERMS_SOURCE_LABEL: Record<NoticeTermsSource, string> = {
  user_confirmed: "bestätigt (manuell)",
  catalog_suggested: "angenommen (Katalog)",
  derived_from_days: "aus Kündigungstagen umgerechnet",
};

export const CERTAINTY_LABEL: Record<Certainty, string> = {
  confirmed: "bestätigt (manuell)",
  assumed: "angenommen (Katalog)",
  not_computable: "nicht berechenbar – Angabe fehlt",
};

/**
 * Der Vorbehalt unter dem Zwecktext, wortgleich zum Serverdefault
 * (PolicyPurpose.caveat, services/api/app/schemas/insurance.py).
 *
 * Er steht hier als Konstante, weil Mobile den Zwecktext aus `detail.line`
 * liest und `/policies/{id}/purpose` nicht aufruft (Begruendung bei
 * getInsurancePolicy). Der Satz ist serverseitig ein fester Default und kein
 * Datum je Sparte - sollte er das eines Tages werden, muss der
 * purpose-Endpunkt angebunden und diese Konstante geloescht werden.
 */
export const POLICY_PURPOSE_CAVEAT =
  "Typischer Zweck dieser Sparte. Ihre konkrete Police kann abweichen.";

// ---------------------------------------------------------------------------
// Ableitungen ueber dem Vertrag
// ---------------------------------------------------------------------------

/**
 * Das Datum, das als "naechste Frist" gilt: das rechtliche Fristende.
 *
 * Bewusst ohne Rueckfall auf `term_ends_on`. Das Vertragsende ist eine andere
 * Angabe als der Kuendigungstermin - beide koennen Monate auseinanderliegen -,
 * und die Tageszahl daneben zaehlt auf das Handlungsdatum. Faellt das Fristende
 * weg, ist "nicht berechenbar" die richtige Auskunft, nicht das naechstbeste
 * Datum. Wortgleiche Regel wie in apps/web/src/lib/insuranceApi.ts.
 */
export function nextDeadlineDate(deadline: PolicyDeadline): string | null {
  return deadline.deadline_legal_on ?? null;
}

/**
 * Ob neben dem Datum ein Countdown stehen darf.
 *
 * Plan Abschnitt 7: "ein angenommenes Datum bekommt auch hier keinen
 * Countdown". Die Regel steht hier und nicht in der Fristenkarte, damit Liste
 * und Detail sie nicht getrennt voneinander auslegen koennen - eine Tageszahl
 * unter einem geratenen Datum ist eine erfundene Genauigkeit.
 */
export function showsCountdown(deadline: PolicyDeadline): boolean {
  return deadline.certainty === "confirmed" && typeof deadline.days_left === "number";
}

/**
 * Woher die Frist stammt, als Satzteil unter dem Datum.
 *
 * Bei `assumed` reicht "angenommen (Katalog)" nicht: eine Police kann aus zwei
 * ganz verschiedenen Gruenden unsicher sein - das Ankerdatum ist geraten oder
 * die Monatszahl ist aus Kuendigungstagen umgerechnet. Wer bestaetigen soll
 * (im Web), muss wissen, was von beidem gemeint ist. Wortgleich zum Web.
 */
export function deadlineOriginLabel(policy: InsurancePolicy): string {
  const { certainty } = policy.deadline;
  if (certainty === "not_computable") {
    return DATE_SOURCE_LABEL.not_computable;
  }
  if (certainty === "confirmed") {
    return CERTAINTY_LABEL.confirmed;
  }
  const reasons: string[] = [];
  if (policy.main_due_source === "catalog_suggested") {
    reasons.push("Hauptfälligkeit nicht bestätigt");
  }
  if (policy.notice_terms_source === "derived_from_days") {
    reasons.push("Frist aus Kündigungstagen umgerechnet");
  } else if (policy.notice_terms_source === "catalog_suggested") {
    reasons.push("Fristlänge aus dem Katalog");
  }
  return reasons.length > 0
    ? `${CERTAINTY_LABEL.assumed} · ${reasons.join(" · ")}`
    : CERTAINTY_LABEL.assumed;
}

/**
 * Die Ueberschrift einer Police in Liste und Detail.
 *
 * `label` ist die eigene Benennung ("Kfz Kombi") und schlaegt die Sparte, weil
 * ein Haushalt mit drei Policen derselben Sparte sie sonst nicht unterscheiden
 * kann. Ohne eigene Benennung steht die Sparte da - nie der Versicherer allein:
 * "HUK-COBURG" beantwortet nicht, worum es geht.
 */
export function policyTitle(policy: InsurancePolicy): string {
  return trimmedOrNull(policy.label) ?? policy.line_label;
}
