/**
 * Datenschicht des Versicherungsmoduls (docs/VERSICHERUNGEN-PLAN.md, P1).
 *
 * Die Formen spiegeln services/api/app/schemas/insurance.py. Zwei Angaben
 * tragen die gesamte Fachlogik der Oberflaeche und sind deshalb bewusst nicht
 * optional getippt: `main_due_source` an der Police und `certainty` an der
 * Frist. Ein Client, der sie beim Rendern uebergeht, zeigt eine Annahme wie
 * eine Tatsache - genau das verhindert der Plan in Abschnitt 2.4.
 *
 * Die Beschriftungen der geschlossenen Vokabulare stehen hier neben den Typen
 * und nicht in den Komponenten: kommt im Schema ein Wert dazu, faellt an genau
 * einer Stelle auf, dass die Uebersetzung fehlt.
 */

import type { QueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "./api";
import { parseAmountToMinorUnits } from "./money";

export type RiskField =
  "health" | "income" | "liability" | "property" | "mobility" | "retirement" | "legal" | "other";

export type NoticeAnchor =
  "term_end" | "main_due" | "period_end" | "fixed_date" | "month_end" | "per_event" | "none";

export type AnchorBasisKind = "statute" | "policy_terms" | "contract";

export type DeadlineKind =
  "kuendigungsfrist" | "widerrufsfrist" | "anfechtungsfrist" | "klagefrist" | "zahlungsfrist";

export type PolicyStatus =
  | "draft"
  | "applied"
  | "active"
  | "suspended"
  | "paid_up"
  | "dormant"
  | "cancelled"
  | "ended"
  | "entered_in_error";

export type PremiumPayer = "employer" | "self_payroll" | "partner" | "other";

export type Frequency = "weekly" | "monthly" | "quarterly" | "semiannual" | "yearly" | "single";

export type ServiceChannel = "post" | "einschreiben" | "email" | "kuendigungsbutton" | "fax";

/** Herkunft eines Datums. Nur `user_confirmed` erzeugt Countdown und Ampel. */
export type DateSource = "user_confirmed" | "catalog_suggested" | "not_computable";

export type NoticeTermsSource = "user_confirmed" | "catalog_suggested" | "derived_from_days";

export type Certainty = "confirmed" | "assumed" | "not_computable";

/**
 * Bundesland des EMPFAENGERS (Plan 4.7a, Regel 2).
 *
 * Es steht an der Police und nicht in den Einstellungen: Paragraph 193 BGB
 * stellt auf den am Erklaerungsort anerkannten Feiertag ab, und der Sitz des
 * Versicherers ist je Police ein anderer. Ein Haushaltswert waere fuer jede
 * Police ausser einer falsch.
 */
export type GermanState =
  | "BW"
  | "BY"
  | "BE"
  | "BB"
  | "HB"
  | "HH"
  | "HE"
  | "MV"
  | "NI"
  | "NW"
  | "RP"
  | "SL"
  | "SN"
  | "ST"
  | "SH"
  | "TH";

export type InsuranceTaskKind =
  | "missing_main_due"
  | "unconfirmed_main_due"
  | "unknown_line"
  | "missing_premium_source"
  | "deadline_approaching";

/**
 * Zahlweise EINES ANPASSUNGSSCHREIBENS - bewusst eine Teilmenge von
 * `Frequency` (Plan 4.7).
 *
 * `weekly` und `single` fehlen, und das ist keine Auslassung: ein
 * Anpassungsschreiben setzt eine WIEDERKEHRENDE Praemie neu fest. Ein
 * Einmalbeitrag (Restschuld, Sterbegeld, Garantieverlaengerung) kann nicht
 * angepasst werden - der Server weist ihn mit 422 ab, und das Formular bietet
 * ihn deshalb erst gar nicht an.
 */
export type PremiumNoticeFrequency = "monthly" | "quarterly" | "semiannual" | "yearly";

export type PremiumNoticeReason =
  | "claims_cost"
  | "risk_change"
  | "demographics"
  | "index"
  | "tariff_class"
  | "regional_class"
  | "sf_class"
  | "age_bracket"
  | "other";

export interface PolicyPremium {
  /** Die drei Geldfaelle des Plans (2.1) als Wert, nicht als Feldpraesenz. */
  kind: "contract" | "external" | "none";
  contract_id: string | null;
  contract_name: string | null;
  contract_amount_minor: number | null;
  contract_frequency: string | null;
  external_premium_minor: number | null;
  external_frequency: Frequency | null;
  premium_payer: PremiumPayer | null;
  premium_payer_note: string | null;
  premium_share_bp: number;
  /** Nur Fall (a). Fremdbetraege bleiben ausserhalb der Fixkosten. */
  yearly_in_fixed_costs_minor: number | null;
}

export interface PolicyDeadline {
  anchor: NoticeAnchor;
  anchor_basis_kind: AnchorBasisKind;
  deadline_kind: DeadlineKind;
  certainty: Certainty;
  term_ends_on: string | null;
  deadline_legal_on: string | null;
  safe_service_on: string | null;
  recommended_action_on: string | null;
  days_left: number | null;
  reason: string;

  /* ---- Nachweis der Frist (Plan 4.7a, `ResolvedDeadline`) ----------------
   *
   * Diese vier Angaben sind bewusst OPTIONAL getippt, obwohl der Plan sie am
   * Rechenergebnis fuehrt: sie entstehen mit dem Fristenregister, und bis der
   * Server sie mitliefert, sagt die Oberflaeche lieber nichts, als sie sich
   * auszudenken (Leitsatz 2). Insbesondere `safe_service_holiday` darf der
   * Client nicht selbst ermitteln - dazu braeuchte er einen zweiten
   * Feiertagskalender im Browser, und zwei Kalender heisst frueher oder
   * spaeter zwei Termine fuer dieselbe Police.
   */

  /** Fundstelle im Klartext, z. B. "Paragraph 11 Abs. 4 VVG". Nur Nachweis -
   *  die Beschriftung entscheidet `anchor_basis_kind`, nie dieser Freitext. */
  basis?: string;
  /** Begruendung der Paragraph-193-Behandlung, wo sie greift. */
  basis_note?: string | null;
  /** Was konkret zu tun ist. Fehlt bei Fristen, die der Versicherer wahrt. */
  action_hint?: string | null;
  /** Name des Feiertags, der den sicheren Zustelltag verschoben hat. */
  safe_service_holiday?: string | null;
}

export interface InsurancePolicy {
  id: string;
  line_key: string;
  line_label: string;
  line_purpose_short: string;
  risk_field: RiskField;
  label: string | null;
  insurer_name: string;
  policy_number: string | null;
  customer_number: string | null;
  tariff_name: string | null;
  purpose_note: string | null;
  status: PolicyStatus;
  starts_on: string | null;
  ends_on: string | null;
  main_due_on: string | null;
  main_due_source: DateSource;
  main_due_confirmed_at: string | null;
  notice_anchor: NoticeAnchor;
  anchor_basis_kind: AnchorBasisKind;
  notice_period_months: number | null;
  notice_terms_source: NoticeTermsSource;
  deadline_kind: DeadlineKind;
  min_term_months: number | null;
  renewal_term_months: number | null;
  waiting_period_days: number | null;
  sum_insured_minor: number | null;
  deductible_minor: number | null;
  broker_name: string | null;
  broker_phone: string | null;
  claims_phone: string | null;
  service_channel: ServiceChannel;
  recipient_state: string | null;
  cancelled_on: string | null;
  cancellation_reason: string | null;
  dormant_until: string | null;
  paid_up_benefit_minor: number | null;
  dynamic_agreed: boolean;
  /**
   * Gesundheitsnahe Police (P4). Der Server setzt sie aus
   * `insurance_lines.risk_field = 'health'`; an ihr haengt die verschaerfte
   * Lesepolicy (Plan 4.3, P4-Nachtrag).
   *
   * Optional getippt, weil sie mit P4 dazukommt - `isHealthScoped()` faellt
   * dann auf das Risikofeld zurueck, aus dem sie ohnehin abgeleitet ist.
   */
  is_health_scoped?: boolean;
  attrs: Record<string, unknown>;
  data_source: string;
  line_confirmed_at: string | null;
  line_deferred_at: string | null;
  note: string | null;
  premium: PolicyPremium;
  deadline: PolicyDeadline;
  completeness_filled: number;
  completeness_total: number;
  next_task: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface InsuranceTask {
  kind: InsuranceTaskKind;
  policy_id: string;
  policy_label: string;
  /** Was zu tun ist. */
  what: string;
  /** Warum es ohne den Nutzer nicht weitergeht. */
  why: string;
  due_on: string | null;
  days_left: number | null;
}

export interface InsuranceSummary {
  policy_count: number;
  active_count: number;
  yearly_in_fixed_costs_minor: number;
  yearly_paid_by_others_minor: number;
  paid_by_others_breakdown: Record<string, number>;
  no_premium_count: number;
  unknown_line_count: number;
  deferred_line_count: number;
  missing_main_due_count: number;
  unconfirmed_main_due_count: number;
  next_deadline_on: string | null;
  next_deadline_policy_id: string | null;
}

export interface InsuranceLine {
  line_key: string;
  risk_field: RiskField;
  gdv_sparte: string | null;
  label: string;
  purpose_short: string;
  purpose_long: string;
  typically_covers: string[];
  typically_not_covers: string[];
  is_placeholder: boolean;
  need_tier: string | null;
  need_reason: string | null;
  source_url: string | null;
  source_checked_on: string | null;
  default_notice_anchor: NoticeAnchor;
  default_anchor_basis_kind: AnchorBasisKind;
  default_notice_period_months: number | null;
  default_main_due_md: string | null;
  default_min_term_months: number | null;
  default_deadline_kind: DeadlineKind;
  special_law: string;
  completeness_fields: string[];
  covered_by_keys: string[];
  requires_object_kind: string | null;
  sort_order: number;
}

export interface PolicyPurpose {
  purpose_long: string;
  typically_covers: string[];
  typically_not_covers: string[];
  /** Steuert die Spaltenueberschrift: Katalogaussage oder Aussage ueber diese Police. */
  coverage_source: "catalog" | "policy";
  purpose_note: string | null;
  source_url: string | null;
  source_checked_on: string | null;
  caveat: string;
}

export interface BackfillPreviewItem {
  contract_id: string;
  contract_name: string;
  provider_label: string | null;
  amount_minor: number;
  frequency: string;
  suggested_line_key: string;
  suggested_line_label: string;
  reason: string;
}

export interface BackfillPreview {
  items: BackfillPreviewItem[];
  already_linked_count: number;
}

export interface BackfillResult {
  created: number;
  skipped: number;
  policy_ids: string[];
}

export interface ConfirmMainDueInput {
  main_due_on: string;
  notice_anchor?: NoticeAnchor;
}

export interface ConfirmNoticeTermsInput {
  notice_period_months: number;
  notice_anchor?: NoticeAnchor;
}

export interface ConfirmLineInput {
  line_key?: string;
  /** "Sparte nicht in der Liste" - nimmt die Police aus der Aufgabenliste,
   *  ohne sie falsch zuzuordnen. */
  defer?: boolean;
}

/**
 * Haushaltseinstellungen des Moduls (`insurance_settings`, Plan 4.10).
 *
 * Hier stehen ausschliesslich Vorlaufstaffeln und Schalter. Was an eine
 * einzelne Police gehoert - allen voran das Bundesland des Empfaengers -,
 * steht ausdruecklich NICHT hier: es waere ein Haushaltswert fuer eine
 * Angabe, die je Versicherer verschieden ist.
 *
 * `review_interval_months` wird von dieser Oberflaeche nicht bearbeitet, aber
 * mitgefuehrt: es steuert die Wiedervorlage des Bedarfsabgleichs, den es hier
 * noch nicht gibt. Ein PUT ohne dieses Feld duerfte es sonst auf den Default
 * zuruecksetzen.
 */
export interface InsuranceSettings {
  notify_deadline: boolean;
  notify_premium_notice: boolean;
  notify_claim_followup: boolean;
  /** Ordentliche Kuendigungsfristen, Voreinstellung 60/14/3 Tage. */
  lead_days: number[];
  /** Sonderkuendigungsfenster, Voreinstellung sofort/7/2 Tage. */
  lead_days_special: number[];
  review_interval_months?: number;
}

// ---------------------------------------------------------------------------
// Beitragsanpassung und Beitragsverlauf (P3)
// ---------------------------------------------------------------------------

/**
 * Ein erfasstes Anpassungsschreiben (`insurance_premium_notices`, Plan 4.7).
 *
 * Die beiden Daten am Kopf sind der Grund, warum es diese Tabelle ueberhaupt
 * gibt, und sie duerfen in keiner Oberflaeche verschmelzen:
 *
 * - `received_on` ist der **Fristanker**. Paragraph 40 Abs. 1 S. 1 VVG laesst
 *   die Frist ab ZUGANG laufen, nicht ab Wirksamwerden.
 * - `effective_on` ist das **Wirksamwerden** und traegt eine zweite Rolle: die
 *   Kuendigung wirkt "fruehestens jedoch zum Zeitpunkt des Wirksamwerdens der
 *   Erhoehung". Es verlaengert die Frist nicht, es begrenzt die Wirkung.
 *
 * Alle gerechneten Felder stehen gebuendelt in `special_termination` und
 * kommen vom Server. Der Client rechnet sie nicht nach - dafuer braeuchte er
 * einen zweiten Feiertagskalender im Browser, und zwei Kalender heissen
 * frueher oder spaeter zwei Termine fuer dieselbe Police.
 */
export interface PremiumNotice {
  id: string;
  policy_id: string;
  /** Zugang des Schreibens - der Fristanker. */
  received_on: string;
  /** Wirksamwerden der Erhoehung - kein Fristanker. */
  effective_on: string;
  old_amount_minor: number;
  new_amount_minor: number;
  /**
   * Kfz-Besonderheit: der auf der Beitragsrechnung ausgewiesene
   * Vergleichsbeitrag (neue SF-Klasse ohne Typ-, Regional- und
   * Tarifanpassung). Das Sonderkuendigungsrecht besteht, wenn der neue Betrag
   * ueber ihm liegt - auch wenn die Rechnung gegenueber dem Vorjahr insgesamt
   * SINKT.
   */
  comparison_amount_minor: number | null;
  frequency: PremiumNoticeFrequency;
  /** Erhoehung MIT entsprechender Leistungserweiterung: kein Paragraph-40-Fenster. */
  with_benefit_increase: boolean;
  reason: PremiumNoticeReason | null;
  delta_minor: number;
  /** Aenderung in Basispunkten, bezogen auf den bisherigen Betrag. */
  delta_bp: number;
  yearly_old_minor: number;
  yearly_new_minor: number;
  special_termination: SpecialTerminationWindow;
  /** Gesetzt, sobald der neue Betrag ueber die Vertragsseite fortgeschrieben ist. */
  amount_history_id: string | null;
  confirmed_at: string | null;
  acknowledged_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

/**
 * Zustand des Sonderkuendigungsfensters.
 *
 * `none` ist ein vollwertiges Ergebnis und kein fehlender Wert: es heisst
 * "diese Anpassung begruendet kein Sonderkuendigungsrecht". Die Oberflaeche
 * entscheidet ausschliesslich an diesem Wert - nie am Freitext `basis` und nie
 * an einem Datumsvergleich im Browser, sonst gaebe es zwei Meinungen darueber,
 * ob ein Recht noch laeuft.
 */
export type SpecialTerminationState = "none" | "open" | "expired";

export interface SpecialTerminationWindow {
  state: SpecialTerminationState;
  deadline_kind: DeadlineKind;
  /** Rechtliches Fristende. Paragraph 193 BGB schiebt es NICHT nach hinten. */
  deadline_legal_on: string | null;
  safe_service_on: string | null;
  recommended_action_on: string | null;
  /** Fruehestes Beendigungsdatum - wann die Kuendigung wirkt, nicht wann sie
   *  abgeschickt sein muss. */
  earliest_effect_on: string | null;
  /** Zaehlt auf das Handlungsdatum und steht nur bei `state = 'open'`. */
  days_left: number | null;
  basis: string;
  basis_kind: AnchorBasisKind;
}

export interface PremiumNoticeCreateInput {
  received_on: string;
  effective_on: string;
  old_amount_minor: number;
  new_amount_minor: number;
  comparison_amount_minor?: number | null;
  frequency: PremiumNoticeFrequency;
  with_benefit_increase: boolean;
  reason?: PremiumNoticeReason | null;
}

/** Woher eine Stufe der Treppe stammt. */
export type PremiumStepSource = "contract" | "premium_notice" | "external";

/**
 * Eine Stufe der Beitragstreppe (`GET /policies/{id}/premium-history`).
 *
 * `yearly_minor` ist der auf ein Jahr normalisierte Wert und die einzige
 * Groesse, die ueber Stufen hinweg vergleichbar ist: 120 Euro jaehrlich und 12
 * Euro monatlich sind dieselbe Belastung, und nur der Jahreswert zeigt das.
 *
 * `valid_from` darf fehlen - "seit Beginn, Datum unbekannt" ist eine Aussage
 * und kein erfundenes Datum. Eine solche Stufe steht in der Tabelle, aber
 * nicht auf der Zeitachse: dort haette sie nur einen geratenen Platz.
 */
export interface PremiumHistoryStep {
  policy_id: string;
  insurer_name: string;
  valid_from: string | null;
  valid_to: string | null;
  amount_minor: number;
  frequency: string;
  yearly_minor: number;
  yearly_delta_minor: number | null;
  source: PremiumStepSource;
  change_reason: string | null;
  notice_id: string | null;
  received_on: string | null;
  with_benefit_increase: boolean | null;
  notice_reason: PremiumNoticeReason | null;
  /** Erste Stufe nach einem Gesellschaftswechsel. Ohne diese Marke saehe der
   *  Sprung beim Wechsel wie eine Beitragsanpassung aus. */
  is_switch_point: boolean;
}

export interface PremiumHistory {
  /** Zusammengefuehrte Kette aus Vorgaenger- und Nachfolgepolicen, aelteste zuerst. */
  policy_ids: string[];
  steps: PremiumHistoryStep[];
  yearly_first_minor: number | null;
  yearly_current_minor: number | null;
  /** Gesamtsteigerung ueber den gezeigten Zeitraum, in Basispunkten. */
  total_increase_bp: number | null;
  /** Erfasste, noch nicht uebernommene Mitteilungen. Sie stehen ausdruecklich
   *  NICHT als Stufe in der Treppe: ein Beitrag, den niemand bestaetigt hat,
   *  ist kein Beitrag. */
  pending_notice_ids: string[];
}

/**
 * Gesellschaftswechsel (Plan 4.15 Punkt 1).
 *
 * Entweder eine bereits erfasste Nachfolgepolice oder die Angaben, aus denen
 * der Server sie anlegt - nie beides. Der Server setzt die alte Police auf
 * `ended` mit `cancellation_reason = 'switch'`, verknuepft
 * `predecessor_policy_id` und fuehrt die Beitragszeitreihe zusammen.
 *
 * `new_policy` ist serverseitig das volle Anlagemodell; die Oberflaeche
 * schickt bewusst nur die Felder, die auf dem Wechselschreiben stehen. Alles
 * Weitere - Hauptfaelligkeit, Frist, Beitrag - gehoert an die neue Police und
 * wird dort erfasst und bestaetigt, nicht hier nebenbei geraten.
 */
export interface ReplaceInsurerNewPolicy {
  line_key: string;
  insurer_name: string;
  policy_number?: string | null;
  tariff_name?: string | null;
  starts_on?: string | null;
}

export interface ReplaceInsurerInput {
  new_policy_id?: string;
  new_policy?: ReplaceInsurerNewPolicy;
  /** Letzter Tag der bisherigen Police. */
  ends_on: string;
  /** Mitgenommene Vorversicherungszeit - verkuerzt oder erledigt die Wartezeit. */
  prior_cover_since?: string | null;
}

export interface ReplaceInsurerResult {
  predecessor: InsurancePolicy;
  successor: InsurancePolicy;
  /** Der offene Punkt nach dem Wechsel - meist der weiterlaufende Vertrag der
   *  alten Police. Die Statuskopplung bleibt eine eigene, bestaetigte
   *  Handlung; der Wechsel aendert nicht still die Fixkosten. */
  hint: string;
}

/** Wohin der uebernommene Betrag geschrieben wurde. */
export interface PremiumNoticeConfirmResult {
  notice: PremiumNotice;
  target: "contract" | "external";
  contract_id: string | null;
  amount_history_id: string | null;
  valid_from: string;
  amount_minor: number;
  hint: string;
}

// ---------------------------------------------------------------------------
// Beschriftungen der geschlossenen Vokabulare
// ---------------------------------------------------------------------------

export const FREQUENCY_LABEL: Record<Frequency, string> = {
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

/** Mehrzahl fuer den Satz ueber Paragraph 193 BGB - er spricht ueber die
 *  Fristart, nicht ueber diese eine Frist. */
export const DEADLINE_KIND_PLURAL: Record<DeadlineKind, string> = {
  kuendigungsfrist: "Kündigungsfristen",
  widerrufsfrist: "Widerrufsfristen",
  anfechtungsfrist: "Anfechtungsfristen",
  klagefrist: "Klagefristen",
  zahlungsfrist: "Zahlungsfristen",
};

export const SERVICE_CHANNEL_LABEL: Record<ServiceChannel, string> = {
  post: "Brief",
  einschreiben: "Einschreiben",
  email: "E-Mail",
  kuendigungsbutton: "Kündigungsbutton",
  fax: "Fax",
};

/**
 * Die Grundlage als Satzteil (Plan 4.7a, Regel 6).
 *
 * Sie steht neben `ANCHOR_BASIS_KIND_LABEL`, weil eine Zeile das Wort braucht
 * ("Grundlage: Gesetz") und ein Satz die Wendung ("gesetzlich"). Beide kommen
 * aus demselben Wert - die Oberflaeche entscheidet nie anhand des Freitextes
 * `basis`, sonst stuende AKB H.1.7 eines Tages als Gesetz da.
 */
export const ANCHOR_BASIS_PHRASE: Record<AnchorBasisKind, string> = {
  statute: "gesetzlich",
  policy_terms: "laut Ihren Versicherungsbedingungen",
  contract: "laut Ihrem Vertrag",
};

export const GERMAN_STATE_LABEL: Record<GermanState, string> = {
  BW: "Baden-Württemberg",
  BY: "Bayern",
  BE: "Berlin",
  BB: "Brandenburg",
  HB: "Bremen",
  HH: "Hamburg",
  HE: "Hessen",
  MV: "Mecklenburg-Vorpommern",
  NI: "Niedersachsen",
  NW: "Nordrhein-Westfalen",
  RP: "Rheinland-Pfalz",
  SL: "Saarland",
  SN: "Sachsen",
  ST: "Sachsen-Anhalt",
  SH: "Schleswig-Holstein",
  TH: "Thüringen",
};

/** Bundesland in Worten. Ein unbekanntes Kuerzel wird durchgereicht statt
 *  verschluckt - es steht dann wenigstens da, was in der Police steht. */
export function germanStateLabel(state: string | null): string | null {
  if (!state) return null;
  return GERMAN_STATE_LABEL[state as GermanState] ?? state;
}

/**
 * Anlass einer Beitragsanpassung, wie ihn das Schreiben nennt.
 *
 * Die Formulierungen sind bewusst die des Versicherers und nicht die des
 * Gesetzes: der Nutzer sucht den Satz, der auf seinem Brief steht.
 */
export const PREMIUM_NOTICE_REASON_LABEL: Record<PremiumNoticeReason, string> = {
  claims_cost: "gestiegene Schadenkosten",
  risk_change: "veränderte Risikolage",
  demographics: "Bevölkerungsentwicklung / Lebenserwartung",
  index: "Anpassung an einen Index",
  tariff_class: "neue Typklasse",
  regional_class: "neue Regionalklasse",
  sf_class: "neue Schadenfreiheitsklasse",
  age_bracket: "neue Altersgruppe",
  other: "anderer Grund",
};

/** Woher eine Stufe der Beitragstreppe kommt - jede Stufe traegt ihre Herkunft,
 *  wie jedes Datum dieses Moduls. */
export const PREMIUM_STEP_SOURCE_LABEL: Record<PremiumStepSource, string> = {
  contract: "aus dem Vertrag (Preisstufe)",
  premium_notice: "aus einem Anpassungsschreiben",
  external: "Fremdbetrag, an der Police erfasst",
};

/** Die Herkunft eines Datums, wortgleich zu Plan 2.4. */
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

// ---------------------------------------------------------------------------
// Ableitungen ueber dem Vertrag
// ---------------------------------------------------------------------------

/**
 * Das Datum, das als "naechste Frist" gilt: das rechtliche Fristende.
 *
 * Bewusst ohne Rueckfall auf `term_ends_on`. Das Vertragsende ist eine andere
 * Angabe als der Kuendigungstermin - beide koennen Monate auseinanderliegen -,
 * und der Countdown daneben zaehlt auf das Handlungsdatum, das aus dem
 * Fristende abgeleitet ist. Faellt das Fristende weg, stuende die Tageszahl
 * unter einem Datum, zu dem sie nicht gehoert. Dann ist "nicht berechenbar"
 * die richtige Auskunft, nicht das naechstbeste Datum.
 */
export function nextDeadlineDate(deadline: PolicyDeadline): string | null {
  return deadline.deadline_legal_on;
}

/**
 * "JJJJ-MM-TT" als lokales Datum.
 *
 * Steht hier und nicht in einer Komponente, weil sie die einzige Stelle ist,
 * an der die Datumsform des Servers ueberhaupt bekannt ist - und weil zwei
 * Parser frueher oder spaeter zwei verschiedene Tage ergeben. `new Date(iso)`
 * ist ausdruecklich nicht gemeint: es liest die Zeichenkette als
 * UTC-Mitternacht und liefert westlich davon den Vortag.
 */
export function parseIsoDate(value: string | null): Date | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Ganze Kalendertage zwischen zwei Serverdaten. Gerundet, weil eine
 *  Zeitumstellung sonst 23 oder 25 Stunden als Bruchteil durchreicht. */
export function isoDaysBetween(from: string | null, to: string | null): number | null {
  const start = parseIsoDate(from);
  const end = parseIsoDate(to);
  if (!start || !end) return null;
  return Math.round((end.getTime() - start.getTime()) / 86_400_000);
}

/**
 * Woher die Frist stammt, als Satzteil unter dem Datum.
 *
 * Bei `assumed` reicht "angenommen (Katalog)" nicht: eine Police kann aus
 * zwei ganz verschiedenen Gruenden unsicher sein - das Ankerdatum ist geraten
 * oder die Monatszahl ist aus Kuendigungstagen umgerechnet. Wer bestaetigen
 * soll, muss wissen, was von beidem gemeint ist.
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
 * "Einsatzbereit" im Sinn von Plan 2.2, definiert statt gefuehlt: die Sparte
 * ist geklaert - zugeordnet oder ausdruecklich zurueckgestellt - und ein
 * Kuendigungstermin ist ueberhaupt berechenbar. Ob er bestaetigt ist, ist eine
 * zweite Frage; sie steht als eigene Aufgabe in "Jetzt wichtig".
 */
export function isPolicyReady(policy: InsurancePolicy): boolean {
  const lineSettled = policy.line_key !== "unknown" || policy.line_deferred_at !== null;
  return lineSettled && policy.deadline.certainty !== "not_computable";
}

// ---------------------------------------------------------------------------
// Ableitungen ueber der Beitragszeitreihe (P3)
// ---------------------------------------------------------------------------

/**
 * Eine Aenderung zwischen zwei benachbarten Stufen.
 *
 * Sie wird aus der Treppe abgeleitet und nicht als zweite Liste geholt:
 * Tabelle und Diagramm sollen dieselbe Quelle haben. Eine zweite Liste
 * ergaebe frueher oder spaeter eine Zeile, zu der es keine Stufe gibt - oder
 * eine Stufe, zu der die Tabelle schweigt.
 */
export interface PremiumChange {
  /** Tag, an dem die neue Stufe gilt. */
  on: string;
  old_yearly_minor: number;
  new_yearly_minor: number;
  /**
   * Aenderung in Basispunkten; negativ bei einer Senkung.
   *
   * `null`, wo es keine Quote GIBT: von 0,00 Euro aus hat kein Anstieg einen
   * Prozentwert (eine beitragsfrei gestellte Police, die wieder Beitrag
   * kostet). Frueher stand dort 0 - und "0,0 %" neben zwei Betraegen, die das
   * Gegenteil sagen, ist keine fehlende Angabe, sondern eine falsche.
   */
  delta_bp: number | null;
  /** Erste Stufe nach einem Gesellschaftswechsel. Dann ist der Sprung keine
   *  Beitragsanpassung, sondern die Folge des Wechsels. */
  is_switch_point: boolean;
  insurer_name: string;
  source: PremiumStepSource;
  notice_id: string | null;
  notice_reason: PremiumNoticeReason | null;
  with_benefit_increase: boolean | null;
  change_reason: string | null;
}

/**
 * Alle Aenderungen der Treppe, aeltester Eintrag zuerst.
 *
 * **Senkungen stehen mit dabei.** Eine Tabelle, die nur Steigerungen kennt,
 * widerspraeche der Treppe darueber - die Kfz-SF-Rueckstufung ist eine Stufe
 * nach unten, und wer sie in der Tabelle nicht findet, haelt das Diagramm fuer
 * falsch. Eine Senkung loest nur kein Sonderkuendigungsrecht aus; das steht in
 * der Zeile, statt sie zu unterschlagen.
 *
 * Stufen ohne `valid_from` bleiben draussen: "seit Beginn, Datum unbekannt"
 * ist als Stufe eine Aussage, als Aenderung waere es ein erfundener Tag.
 */
export function premiumChanges(steps: PremiumHistoryStep[]): PremiumChange[] {
  const ordered = orderedPremiumSteps(steps);
  const changes: PremiumChange[] = [];
  for (let index = 1; index < ordered.length; index += 1) {
    const previous = ordered[index - 1];
    const current = ordered[index];
    if (!previous || !current || current.valid_from === null) continue;
    if (current.yearly_minor === previous.yearly_minor) continue;
    changes.push({
      on: current.valid_from,
      old_yearly_minor: previous.yearly_minor,
      new_yearly_minor: current.yearly_minor,
      // Quoten als Basispunkte, wie ueberall in diesem Bestand. Der Nenner ist
      // der alte Jahresbeitrag; ist er null, gibt es keine Quote - dann steht
      // dort `null` statt einer Unendlichkeit oder, schlimmer, einer Null.
      delta_bp:
        previous.yearly_minor === 0
          ? null
          : Math.round(
              ((current.yearly_minor - previous.yearly_minor) / previous.yearly_minor) * 10000,
            ),
      is_switch_point: current.is_switch_point,
      insurer_name: current.insurer_name,
      source: current.source,
      notice_id: current.notice_id,
      notice_reason: current.notice_reason,
      with_benefit_increase: current.with_benefit_increase,
      change_reason: current.change_reason,
    });
  }
  return changes;
}

/**
 * Die Stufen in zeitlicher Reihenfolge.
 *
 * Stufen ohne `valid_from` stehen vorn: sie beginnen "seit Beginn, Datum
 * unbekannt" und damit vor jeder datierten Stufe. Sortierte man sie ans Ende,
 * saehe der aelteste Beitrag wie der neueste aus.
 */
export function orderedPremiumSteps(steps: PremiumHistoryStep[]): PremiumHistoryStep[] {
  return steps.slice().sort((a, b) => (a.valid_from ?? "").localeCompare(b.valid_from ?? ""));
}

/** Basispunkte als Prozentsatz mit Vorzeichen: 1210 -> "+12,1 %". */
export function formatBasisPointsAsPercent(basisPoints: number): string {
  const percent = basisPoints / 100;
  const sign = percent > 0 ? "+" : "";
  return `${sign}${percent.toLocaleString("de-DE", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} %`;
}

/**
 * Das Anpassungsschreiben, dessen Sonderkuendigungsfenster gerade laeuft -
 * oder `null`.
 *
 * **Ob ein Fenster laeuft, entscheidet der Server** (`special_termination
 * .state`). Der Client vergleicht keine Daten: der Nachtlauf setzt ein
 * verstrichenes Fenster auf `expired`, und ein zweiter Vergleich im Browser
 * ergaebe frueher oder spaeter eine andere Antwort als der Push.
 *
 * Zwei Ausschluesse kommen hier dazu, jeder mit einem eigenen Grund:
 *
 * 1. **`policy.dynamic_agreed`.** Eine vereinbarte Dynamik in BU, Ruerup oder
 *    Risikoleben ist keine Erhoehung im Sinne des Paragraphen 40 VVG
 *    (Plan 8.5, Filter 2). Ein Band waere hier keine Warnung, sondern ein
 *    Fehlalarm auf einer planmaessigen Stufe. Die erfasste Mitteilung bleibt
 *    im Register sichtbar - unterdrueckt wird der Alarm, nicht die Angabe.
 * 2. **`acknowledged_at`.** Zur Kenntnis genommen heisst: der Nutzer weiss es.
 *    Ab da unterdrueckt derselbe Zeitstempel auch den Push (Plan 8.3).
 *
 * Ein verfallenes Fenster taucht ausdruecklich NICHT als Band auf, bleibt aber
 * in der Liste stehen - der Nutzer soll sehen, dass ein Recht verfallen ist,
 * aber nicht zu einer Handlung gedraengt werden, die nichts mehr bewirkt
 * (Plan 8.2 Schritt 2a).
 */
export function openSpecialTermination(
  policy: InsurancePolicy,
  notices: PremiumNotice[],
): PremiumNotice | null {
  if (policy.dynamic_agreed) return null;
  const open = notices.filter(
    (notice) => notice.special_termination.state === "open" && notice.acknowledged_at === null,
  );
  if (open.length === 0) return null;
  // Das knappste Fenster zuerst: laufen zwei, ist das naeher am Verfall das,
  // ueber das jetzt entschieden werden muss.
  return (
    open.sort((a, b) =>
      (a.special_termination.deadline_legal_on ?? "").localeCompare(
        b.special_termination.deadline_legal_on ?? "",
      ),
    )[0] ?? null
  );
}

// ---------------------------------------------------------------------------
// Abfrageschluessel
// ---------------------------------------------------------------------------

export const insurancePoliciesQueryKey = (householdId: string) =>
  ["insurance-policies", householdId] as const;

export const insuranceTasksQueryKey = (householdId: string) =>
  ["insurance-tasks", householdId] as const;

export const insuranceSummaryQueryKey = (householdId: string) =>
  ["insurance-summary", householdId] as const;

export const insuranceLinesQueryKey = () => ["insurance-lines"] as const;

export const insuranceBackfillPreviewQueryKey = (householdId: string) =>
  ["insurance-backfill-preview", householdId] as const;

export const insurancePurposeQueryKey = (householdId: string, policyId: string | null) =>
  ["insurance-policy-purpose", householdId, policyId] as const;

export const insuranceSettingsQueryKey = (householdId: string) =>
  ["insurance-settings", householdId] as const;

export const insurancePremiumNoticesQueryKey = (householdId: string, policyId: string | null) =>
  ["insurance-premium-notices", householdId, policyId] as const;

/** Ohne Jahreszahl im Schluessel: die Zeitreihe wird immer mit demselben
 *  Fenster geholt, und ein zweites Fenster im Schluessel taeuschte einen
 *  Umschalter vor, den es nicht gibt. */
export const insurancePremiumHistoryQueryKey = (householdId: string, policyId: string | null) =>
  ["insurance-premium-history", householdId, policyId] as const;

/**
 * Alle Abfragen dieses Moduls auf einmal ungueltig machen.
 *
 * Sie steht hier und nicht in der Route, weil inzwischen zwei Stellen
 * schreiben: die Seite (Bestaetigungen, Uebernahme) und das Beitragsregister
 * im Detail (Anpassung erfassen, uebernehmen, Wechsel). Zwei Listen von
 * Schluesseln laufen genau so lange gleich, bis eine Phase einen Schluessel
 * dazulegt - danach steht an einer Stelle ein alter Stand. Die Vertraege
 * bleiben bewusst draussen: sie haengen an einem eigenen Invalidierer
 * (`useInvalidateContracts`), der weit mehr Schluessel kennt als dieses Modul.
 */
export function invalidateInsuranceQueries(
  queryClient: QueryClient,
  householdId: string,
): Promise<void> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: insurancePoliciesQueryKey(householdId) }),
    queryClient.invalidateQueries({ queryKey: insuranceTasksQueryKey(householdId) }),
    queryClient.invalidateQueries({ queryKey: insuranceSummaryQueryKey(householdId) }),
    queryClient.invalidateQueries({ queryKey: insuranceBackfillPreviewQueryKey(householdId) }),
    // Ohne Policenkennung: der Schluessel wird als Praefix verglichen, und ein
    // Gesellschaftswechsel aendert die Zeitreihe ZWEIER Policen. Nur die
    // gerade offene neu zu laden liesse die andere auf altem Stand stehen.
    queryClient.invalidateQueries({ queryKey: ["insurance-premium-notices", householdId] }),
    queryClient.invalidateQueries({ queryKey: ["insurance-premium-history", householdId] }),
    /* ---- P4 ---------------------------------------------------------------
     *
     * Der Zwecktext steht hier, seit es Deckungsbausteine gibt: erfasste
     * Bausteine kippen `coverage_source` von `catalog` auf `policy`, und dann
     * sagt dieselbe Karte etwas anderes ueber dieselbe Police (Plan 2.3
     * Punkt 2). Ohne diese Zeile bliebe im Register "Zweck" die
     * Katalogaussage stehen, waehrend nebenan die Bausteine schon erfasst
     * sind - genau die zwei Wahrheiten, die dieses Modul vermeidet.
     *
     * Personen und Objekte haengen mit dran, weil beide in die
     * Zusammenfassung (`by_person`) und in den Vollstaendigkeitsgrad der
     * Police einfliessen.
     */
    queryClient.invalidateQueries({ queryKey: ["insurance-policy-purpose", householdId] }),
    queryClient.invalidateQueries({ queryKey: ["insurance-policy-persons", householdId] }),
    queryClient.invalidateQueries({ queryKey: ["insurance-policy-objects", householdId] }),
    queryClient.invalidateQueries({ queryKey: ["insurance-policy-coverages", householdId] }),
    queryClient.invalidateQueries({ queryKey: ["insurance-objects", householdId] }),
    queryClient.invalidateQueries({ queryKey: ["insurance-object-registrations", householdId] }),
    /* ---- P5 ---------------------------------------------------------------
     *
     * Die Vorgaenge haengen mit dran, weil ein Statuswechsel oder eine
     * Zahlungszuordnung mehr aendert als den Vorgang selbst: der Fristanker
     * kippt das Sonderkuendigungsfenster, und der Vorgang taucht in der
     * Aufgabenliste auf oder verschwindet daraus. Ohne Policenkennung im
     * Praefix, weil ein Umhaengen (Gesellschaftswechsel) die Vorgaenge ZWEIER
     * Policen betrifft - und weil die Zahlungskandidaten einer
     * Sammelueberweisung an mehreren Vorgaengen zugleich schrumpfen.
     */
    queryClient.invalidateQueries({ queryKey: ["insurance-claims", householdId] }),
    queryClient.invalidateQueries({ queryKey: ["insurance-claim", householdId] }),
    queryClient.invalidateQueries({ queryKey: ["insurance-claim-candidates", householdId] }),
    /* ---- P6 ---------------------------------------------------------------
     *
     * Die Befunde des Bedarfsabgleichs haengen nicht nur am Bedarfsprofil,
     * sondern am BESTAND: eine bestaetigte Sparte, eine neue Police oder ein
     * erfasster Baustein entscheidet daruber, ob eine Regel als erfuellt oder
     * als Luecke endet. Ohne diese Zeile stuende nach dem Zuordnen einer
     * Sparte weiter "kein Schutz erfasst" da - der schaedlichste Befund
     * ueberhaupt, weil er einen gepflegten Bestand als ungepflegt zeigt.
     */
    invalidateNeedsQueries(queryClient, householdId),
  ]).then(() => undefined);
}

// ---------------------------------------------------------------------------
// Abrufe
// ---------------------------------------------------------------------------

export function listInsurancePolicies(
  token: string,
  householdId: string,
): Promise<InsurancePolicy[]> {
  // `include_ended` und `sort=deadline` bewusst gesetzt: die Seite filtert den
  // Status im Browser und braucht dafuer den vollen Bestand, und der Server
  // stellt bei `deadline` bestaetigte Termine vor angenommene (Plan 5.1).
  return apiFetch<InsurancePolicy[]>("/v1/insurance/policies?include_ended=true&sort=deadline", {
    token,
    householdId,
  });
}

export function listInsuranceTasks(token: string, householdId: string): Promise<InsuranceTask[]> {
  return apiFetch<InsuranceTask[]>("/v1/insurance/tasks", { token, householdId });
}

export function getInsuranceSummary(token: string, householdId: string): Promise<InsuranceSummary> {
  return apiFetch<InsuranceSummary>("/v1/insurance/summary", { token, householdId });
}

export function listInsuranceLines(token: string, householdId: string): Promise<InsuranceLine[]> {
  return apiFetch<InsuranceLine[]>("/v1/insurance/lines", { token, householdId });
}

export function getPolicyPurpose(
  token: string,
  householdId: string,
  policyId: string,
): Promise<PolicyPurpose> {
  return apiFetch<PolicyPurpose>(`/v1/insurance/policies/${policyId}/purpose`, {
    token,
    householdId,
  });
}

/**
 * Vorschau der Bestandsuebernahme.
 *
 * Der Plan fuehrt Vorschau und Uebernahme in einer POST-Zeile (5.5), die
 * Vorschau selbst ist aber ein reiner Lesevorgang. Deshalb wird zuerst GET
 * versucht und nur bei 405 auf POST gewechselt: an der Methodenwahl soll die
 * Seite nicht scheitern, und ein zweiter Aufruf kostet hier nichts, weil die
 * Vorschau nichts schreibt.
 */
export async function fetchBackfillPreview(
  token: string,
  householdId: string,
): Promise<BackfillPreview> {
  try {
    return await apiFetch<BackfillPreview>("/v1/insurance/backfill/preview", {
      token,
      householdId,
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 405) {
      return apiFetch<BackfillPreview>("/v1/insurance/backfill/preview", {
        token,
        householdId,
        method: "POST",
        body: JSON.stringify({}),
      });
    }
    throw error;
  }
}

/** Idempotenzschluessel wie in useCategoryCatalog: ein zweiter Klick auf
 *  "Übernehmen" darf keinen zweiten Bestand anlegen. */
function requestId(): string {
  const webCrypto = globalThis.crypto;
  if (webCrypto && typeof webCrypto.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  return `backfill-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function runBackfill(token: string, householdId: string): Promise<BackfillResult> {
  return apiFetch<BackfillResult>("/v1/insurance/backfill", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ request_id: requestId() }),
  });
}

/**
 * Einfeldaktion, kein Formular: der einzige Schreibweg zu
 * `main_due_source = 'user_confirmed'`.
 *
 * Ohne Rumpf bestaetigt der Endpunkt das vorgeschlagene Katalogdatum
 * unveraendert - der Ein-Klick-Fall aus der Aufgabenliste. Die Antwort traegt
 * die neu gerechnete Frist; die Oberflaeche liest sie nicht, sondern laedt
 * Liste, Aufgaben und Zusammenfassung neu, damit es keine zwei Wahrheiten
 * ueber dieselbe Police gibt.
 */
export function confirmMainDue(
  token: string,
  householdId: string,
  policyId: string,
  input?: ConfirmMainDueInput,
): Promise<void> {
  return apiFetch<void>(`/v1/insurance/policies/${policyId}/confirm-main-due`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input ?? {}),
  });
}

export function confirmNoticeTerms(
  token: string,
  householdId: string,
  policyId: string,
  input: ConfirmNoticeTermsInput,
): Promise<void> {
  return apiFetch<void>(`/v1/insurance/policies/${policyId}/confirm-notice-terms`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function confirmLine(
  token: string,
  householdId: string,
  policyId: string,
  input: ConfirmLineInput,
): Promise<void> {
  return apiFetch<void>(`/v1/insurance/policies/${policyId}/confirm-line`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getInsuranceSettings(
  token: string,
  householdId: string,
): Promise<InsuranceSettings> {
  return apiFetch<InsuranceSettings>("/v1/insurance/settings", { token, householdId });
}

/**
 * Vollstaendiges PUT, kein Teilstueck.
 *
 * Die Einstellungen sind ein einziger Satz Werte ohne Nebenlaeufigkeit; wer
 * nur die geaenderte Staffel schickte, muesste sich darauf verlassen, dass der
 * Server die uebrigen Felder unberuehrt laesst. Die Oberflaeche schickt
 * deshalb den geladenen Stand mit den Aenderungen darauf zurueck - inklusive
 * der Felder, die sie selbst nicht anbietet.
 */
export function updateInsuranceSettings(
  token: string,
  householdId: string,
  input: InsuranceSettings,
): Promise<InsuranceSettings> {
  return apiFetch<InsuranceSettings>("/v1/insurance/settings", {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

// ---------------------------------------------------------------------------
// Beitragsanpassung und Beitragsverlauf (P3)
// ---------------------------------------------------------------------------

export function listPremiumNotices(
  token: string,
  householdId: string,
  policyId: string,
): Promise<PremiumNotice[]> {
  return apiFetch<PremiumNotice[]>(`/v1/insurance/policies/${policyId}/premium-notices`, {
    token,
    householdId,
  });
}

/**
 * Ein Anpassungsschreiben erfassen.
 *
 * Der Server rechnet daraus das Fenster - Laenge, sicherer Zustelltag,
 * Handlungsdatum und fruehestes Beendigungsdatum - und gibt den fertigen
 * Vorgang zurueck. Die Oberflaeche uebernimmt diese Antwort ausdruecklich
 * nicht in ihren Zustand, sondern laedt die Liste neu: sonst stuenden nach
 * einem Fehlschlag zwei Wahrheiten ueber dieselbe Frist nebeneinander.
 */
export function createPremiumNotice(
  token: string,
  householdId: string,
  policyId: string,
  input: PremiumNoticeCreateInput,
): Promise<PremiumNotice> {
  return apiFetch<PremiumNotice>(`/v1/insurance/policies/${policyId}/premium-notices`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * Den neuen Betrag uebernehmen.
 *
 * Er wandert ueber die VERTRAGSSEITE (`contract_service.open_amount_stage`,
 * `valid_from = effective_on`) in die Preistreppe - dieses Modul fuehrt keinen
 * zweiten Betrag. Ohne verknuepften Vertrag ist die Uebernahme nur zulaessig,
 * wenn die Police einen Fremdzahler benennt; sonst antwortet der Server 422
 * `insurance_premium_notice_requires_external_payer`.
 */
export function confirmPremiumNotice(
  token: string,
  householdId: string,
  noticeId: string,
): Promise<PremiumNoticeConfirmResult> {
  return apiFetch<PremiumNoticeConfirmResult>(`/v1/insurance/premium-notices/${noticeId}/confirm`, {
    token,
    householdId,
    method: "POST",
    // Ohne Rumpf: der Endpunkt kennt ein abweichendes `amount_minor`, aber
    // die Oberflaeche uebernimmt genau den Betrag aus dem Schreiben. Wer ihn
    // korrigieren will, korrigiert das Schreiben - sonst stuenden Mitteilung
    // und Preisstufe mit zwei verschiedenen Zahlen nebeneinander.
    body: JSON.stringify({}),
  });
}

/** Frist zur Kenntnis genommen - unterdrueckt weiteren Push (Plan 8.3). */
export function ackPremiumNotice(
  token: string,
  householdId: string,
  noticeId: string,
): Promise<PremiumNotice> {
  return apiFetch<PremiumNotice>(`/v1/insurance/premium-notices/${noticeId}/ack`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** Fenster der Beitragszeitreihe in Jahren. Fuenf, weil der Plan den Endpunkt
 *  mit genau diesem Vorgabewert fuehrt und eine Treppe erst ueber mehrere
 *  Jahre eine Aussage ist. */
export const PREMIUM_HISTORY_YEARS = 5;

/**
 * Beitragszeitreihe EINER Police, ueber die Kette ihrer Vorgaenger hinweg.
 *
 * Der Endpunkt haengt an der Police und nicht am Haushalt: die Zeitreihe ist
 * eine Aussage ueber diese eine Absicherung, und der Server fuehrt dafuer die
 * Vorgaenger- und Nachfolgekette bereits zusammen (`policy_ids`).
 */
export function getPremiumHistory(
  token: string,
  householdId: string,
  policyId: string,
  years: number = PREMIUM_HISTORY_YEARS,
): Promise<PremiumHistory> {
  return apiFetch<PremiumHistory>(
    `/v1/insurance/policies/${policyId}/premium-history?years=${years}`,
    { token, householdId },
  );
}

/**
 * Gesellschaftswechsel mit Historienmitnahme.
 *
 * Eine Transaktion auf der Serverseite: die alte Police wird beendet, die neue
 * verknuepft, die Zeitreihe zusammengefuehrt. Die Oberflaeche laedt danach den
 * gesamten Bestand neu - der Wechsel aendert zwei Policen, und eine davon ist
 * nicht die geoeffnete.
 */
export function replaceInsurer(
  token: string,
  householdId: string,
  policyId: string,
  input: ReplaceInsurerInput,
): Promise<ReplaceInsurerResult> {
  return apiFetch<ReplaceInsurerResult>(`/v1/insurance/policies/${policyId}/replace-with`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

// ---------------------------------------------------------------------------
// Personen, Objekte, Kennzeichen und Deckungsbausteine (P4)
// ---------------------------------------------------------------------------

/**
 * Rollen aus GDV Anlage 7, reduziert auf das, was privat vorkommt
 * (Plan 4.4).
 *
 * `policyholder` ist je Police hoechstens einmal vergeben - das steht als
 * Unique-Index am Schema, nicht als Meinung der Oberflaeche.
 */
export type PolicyPersonRole =
  "policyholder" | "insured" | "premium_payer" | "beneficiary" | "driver" | "vehicle_keeper";

export interface PolicyPerson {
  /** Kennung der VERKNUEPFUNG, nicht der Person. */
  id: string;
  policy_id: string;
  insured_person_id: string;
  /**
   * Aufgeloest ueber `insured_person_label()` (Plan 4.4).
   *
   * `null` heisst ausdruecklich "fuer Sie nicht sichtbar" und nicht "keiner
   * hinterlegt": bei einer gesundheitsnahen Police haengt der Name an der
   * Freigabe fuer diese Person. Die Oberflaeche muss diese beiden Faelle
   * auseinanderhalten, sonst behauptet sie eine Luecke, wo eine Grenze ist.
   */
  display_name: string | null;
  role: PolicyPersonRole;
  /** Doppelt belegt: Beihilfebemessungssatz bei `insured`, Bezugsanteil bei
   *  `beneficiary` - je Rolle eine andere Bedeutung derselben Zahl. */
  share_bp: number | null;
  valid_from: string | null;
  valid_to: string | null;
  note: string | null;
  created_at: string;
}

/** Eine Person, die es im Haushalt noch nicht gibt. Der Server legt sie in
 *  derselben Anfrage an - zwei Rundlaeufe waeren zwei Fehlerfaelle. */
export interface NewInsuredPersonInput {
  display_name: string;
  birth_date?: string | null;
}

export interface PolicyPersonInput {
  insured_person_id?: string;
  new_person?: NewInsuredPersonInput;
  role: PolicyPersonRole;
  share_bp?: number | null;
  valid_from?: string | null;
  valid_to?: string | null;
  note?: string | null;
}

export type InsuredObjectKind = "vehicle" | "property" | "pet" | "device" | "bike" | "other";

export interface InsuredObject {
  id: string;
  kind: InsuredObjectKind;
  label: string;
  /** Bruecke ins Vermoegensmodul statt einer zweiten Wertfuehrung. */
  asset_id: string | null;
  address_line: string | null;
  postal_code: string | null;
  city: string | null;
  living_area_sqm: number | null;
  build_year: number | null;
  attrs: Record<string, unknown>;
  /**
   * Kennzeichen und Halter der LAUFENDEN Zulassung - abgeleitet aus der
   * Historie, keine Spalte am Objekt. Sie sind deshalb hier nur lesbar; der
   * Wechsel laeuft ueber `/objects/{id}/registrations`.
   */
  current_plate: string | null;
  current_keeper_person_id: string | null;
  current_keeper_name: string | null;
  /** An wie vielen Policen dieses Objekt haengt. Ein Auto traegt Haftpflicht
   *  und Kasko - das Loesen einer Verknuepfung loescht es deshalb nie. */
  policy_count: number;
  /**
   * Erwerb - der primaere Anker der Erwerberfrist (§ 96 Abs. 2 S. 2 VVG).
   * Immer Nutzereingabe, nie abgeleitet; deshalb traegt das Feld auch keine
   * Herkunftsspalte (Plan 4.5).
   */
  acquired_on: string | null;
  /** Veraeusserung. Erzeugt KEINE Kuendigungsfrist, sondern eine
   *  Anzeigepflicht (§ 97 Abs. 1 VVG). */
  disposed_on: string | null;
  is_active: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface InsuredObjectInput {
  kind: InsuredObjectKind;
  label: string;
  acquired_on?: string | null;
  disposed_on?: string | null;
  address_line?: string | null;
  postal_code?: string | null;
  city?: string | null;
  living_area_sqm?: number | null;
  build_year?: number | null;
  asset_id?: string | null;
  attrs?: Record<string, unknown>;
}

/**
 * Teilaenderung eines Objekts.
 *
 * `clear_fields` ist kein Beiwerk: ein `null` im Rumpf heisst "nicht
 * mitgeschickt", nicht "leeren". Ohne diese Liste liesse sich ein irrtuemlich
 * erfasstes Erwerbsdatum nie wieder entfernen - und genau daran haengt eine
 * Frist.
 */
export type InsuredObjectClearableField =
  | "asset_id"
  | "address_line"
  | "postal_code"
  | "city"
  | "living_area_sqm"
  | "build_year"
  | "acquired_on"
  | "disposed_on";

export type InsuredObjectPatch = Partial<InsuredObjectInput> & {
  is_active?: boolean;
  clear_fields?: InsuredObjectClearableField[];
};

/**
 * Ein Objekt in seiner Rolle an einer Police.
 *
 * Flach und bewusst schmal: Art, Bezeichnung, die beiden Lebenszyklusdaten und
 * das laufende Kennzeichen. Alles Weitere - Anschrift, Wohnflaeche,
 * Versionszaehler - steht am Objekt selbst und wird von dort gelesen. Dasselbe
 * Auto haengt an Haftpflicht und Kasko; zwei Kopien derselben Zeile waeren
 * frueher oder spaeter zwei verschiedene Erwerbsdaten.
 */
export interface PolicyObjectLink {
  /** Kennung der VERKNUEPFUNG, nicht des Objekts. */
  id: string;
  policy_id: string;
  insured_object_id: string;
  kind: InsuredObjectKind;
  label: string;
  acquired_on: string | null;
  disposed_on: string | null;
  current_plate: string | null;
  note: string | null;
  created_at: string;
}

/**
 * Eine Zeile der Halter- und Kennzeichenhistorie (Plan 4.5).
 *
 * Ein Kennzeichenwechsel ist eine neue Zeile und kein Ueberschreiben - sonst
 * verlieren alte Buchungen und Schaeden ihren Bezug.
 */
export interface ObjectRegistration {
  id: string;
  insured_object_id: string;
  plate: string | null;
  keeper_person_id: string | null;
  /** Aufgeloest wie `PolicyPerson.display_name`; `null` heisst "nicht
   *  sichtbar", nicht "kein Halter". */
  keeper_name: string | null;
  valid_from: string;
  /** `null` heisst: diese Zeile laeuft. Genau eine Zeile je Objekt tut das. */
  valid_to: string | null;
  note: string | null;
  created_at: string;
}

export interface ObjectRegistrationInput {
  plate?: string | null;
  keeper_person_id?: string | null;
  valid_from: string;
  note?: string | null;
}

/**
 * Ein Baustein aus dem globalen Katalog (`insurance_coverage_keys`).
 *
 * Er ist die Auswahlliste des Deckungsregisters und traegt dieselbe Rolle wie
 * der Spartenkatalog: die Beschriftungen kommen aus der Datenbank und nicht
 * aus dem JSX, damit Katalogpflege ohne Deployment moeglich bleibt.
 */
export interface InsuranceCoverageKey {
  coverage_key: string;
  label: string;
  purpose_short: string;
  /** `line_key`s, fuer die dieser Baustein in Frage kommt. */
  applies_to: string[];
  sort_order: number;
}

/**
 * Ein tatsaechlich vereinbarter Baustein dieser Police.
 *
 * Die Vierergruppe *enthalten / Selbstbeteiligung / Versicherungssumme /
 * Wartezeit* traegt Hausrat mit ueber 25 Bausteinen genauso wie Haftpflicht
 * mit 15 (Plan 4.6) - deshalb gibt es keine Tabelle je Sparte.
 *
 * `is_included = false` ist eine erfasste Aussage und nicht das Fehlen einer:
 * "Elementar ist ausdruecklich nicht mitversichert" ist etwas anderes als
 * "ueber Elementar wurde nichts erfasst". Die zweite Aussage ist das Fehlen
 * der Zeile.
 */
export interface PolicyCoverage {
  id: string;
  policy_id: string;
  coverage_key: string;
  /** Beschriftung und Kurztext kommen aus dem Katalog. Sie stehen auch dann an
   *  der Zeile, wenn der Baustein nicht (mehr) zur Sparte gehoert - sonst
   *  stuende dort eine Kennung. */
  label: string;
  purpose_short: string;
  is_included: boolean;
  sum_insured_minor: number | null;
  deductible_minor: number | null;
  benefit_rate_bp: number | null;
  waiting_until: string | null;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface PolicyCoverageInput {
  coverage_key: string;
  is_included: boolean;
  sum_insured_minor?: number | null;
  deductible_minor?: number | null;
  benefit_rate_bp?: number | null;
  waiting_until?: string | null;
  note?: string | null;
}

export const POLICY_PERSON_ROLE_LABEL: Record<PolicyPersonRole, string> = {
  policyholder: "Versicherungsnehmer",
  insured: "versicherte Person",
  premium_payer: "Beitragszahler",
  beneficiary: "bezugsberechtigt",
  driver: "Fahrer",
  vehicle_keeper: "Halter",
};

/** Was die Rolle bedeutet, in einem Satz. Sie steht neben dem Auswahlfeld,
 *  weil "Halter" und "Fahrer" im Kfz-Bereich verschiedene Pflichten tragen und
 *  die Verwechslung teuer ist. */
export const POLICY_PERSON_ROLE_HINT: Record<PolicyPersonRole, string> = {
  policyholder: "Wer den Vertrag geschlossen hat und kündigen darf. Je Police nur einmal.",
  insured: "Wessen Risiko abgesichert ist.",
  premium_payer: "Wer den Beitrag zahlt. Der Beitrag selbst bleibt am Vertrag.",
  beneficiary: "Wer im Leistungsfall das Geld bekommt.",
  driver: "Wer das Fahrzeug fährt – bei begrenztem Fahrerkreis beitragsrelevant.",
  vehicle_keeper: "Auf wen das Fahrzeug zugelassen ist.",
};

/**
 * Wofuer `share_bp` bei dieser Rolle steht - oder `null`, wenn die Zahl bei
 * ihr gar keine Bedeutung hat.
 *
 * Dieselbe Spalte traegt zwei Groessen (Plan 4.4). Ein Feld mit der
 * Beschriftung "Anteil" waere an einer beihilfeberechtigten Person schlicht
 * falsch beschriftet.
 */
export function shareLabelForRole(role: PolicyPersonRole): string | null {
  if (role === "insured") return "Beihilfebemessungssatz";
  if (role === "beneficiary") return "Bezugsanteil";
  return null;
}

export const INSURED_OBJECT_KIND_LABEL: Record<InsuredObjectKind, string> = {
  vehicle: "Fahrzeug",
  property: "Immobilie",
  pet: "Tier",
  device: "Gerät",
  bike: "Fahrrad",
  other: "Sonstiges",
};

export const INSURED_OBJECT_KINDS: InsuredObjectKind[] = [
  "vehicle",
  "property",
  "pet",
  "device",
  "bike",
  "other",
];

/**
 * Traegt dieses Objekt ein Kennzeichen?
 *
 * Nur Fahrzeuge. Mofa und Roller fahren als `vehicle` mit - das
 * Versicherungskennzeichen ist ein Kennzeichen. Ein Fahrrad hat keins; die
 * Rahmennummer gehoert in `attrs` und nicht in die Zulassungshistorie, weil
 * sie sich nie aendert.
 */
export function hasRegistrationHistory(kind: InsuredObjectKind): boolean {
  return kind === "vehicle";
}

/** Gesundheitsnah im Sinn des P4-Nachtrags. Der Rueckfall auf das Risikofeld
 *  ist keine zweite Regel, sondern dieselbe: der Server leitet die Spalte
 *  genau daraus ab. */
export function isHealthScoped(policy: InsurancePolicy): boolean {
  return policy.is_health_scoped ?? policy.risk_field === "health";
}

/** Anteil in Basispunkten als Prozentsatz ohne Vorzeichen: 7000 -> "70 %". */
export function formatShareAsPercent(basisPoints: number): string {
  return `${(basisPoints / 100).toLocaleString("de-DE", {
    maximumFractionDigits: 2,
  })} %`;
}

/**
 * Prozenteingabe in Basispunkte.
 *
 * Bewusst ueber denselben Parser wie ein Geldbetrag: "70,5" und "70.5" meinen
 * beide 7050, und ein zweiter Zahlenparser im Browser ergaebe frueher oder
 * spaeter eine andere Rundung als der erste. Werte ausserhalb von 0 bis 100
 * Prozent gibt der Parser als `null` zurueck - das Schema laesst sie nicht zu.
 */
export function parsePercentToBasisPoints(rawInput: string): number | null {
  const parsed = parseAmountToMinorUnits(rawInput);
  if (parsed === null || parsed < 0 || parsed > 10000) return null;
  return parsed;
}

// ---- Abfrageschluessel ----------------------------------------------------

export const insurancePolicyPersonsQueryKey = (householdId: string, policyId: string) =>
  ["insurance-policy-persons", householdId, policyId] as const;

export const insurancePolicyObjectsQueryKey = (householdId: string, policyId: string) =>
  ["insurance-policy-objects", householdId, policyId] as const;

export const insurancePolicyCoveragesQueryKey = (householdId: string, policyId: string) =>
  ["insurance-policy-coverages", householdId, policyId] as const;

export const insuranceObjectsQueryKey = (householdId: string) =>
  ["insurance-objects", householdId] as const;

export const insuranceObjectRegistrationsQueryKey = (householdId: string, objectId: string) =>
  ["insurance-object-registrations", householdId, objectId] as const;

/** Ohne Haushalt im Schluessel: der Bausteinkatalog ist global wie der
 *  Spartenkatalog (Plan 4.6). */
export const insuranceCoverageKeysQueryKey = (lineKey: string) =>
  ["insurance-coverage-keys", lineKey] as const;

// ---- Personen -------------------------------------------------------------

export function listPolicyPersons(
  token: string,
  householdId: string,
  policyId: string,
): Promise<PolicyPerson[]> {
  return apiFetch<PolicyPerson[]>(`/v1/insurance/policies/${policyId}/persons`, {
    token,
    householdId,
  });
}

/**
 * Person an die Police haengen - vorhandene oder neue.
 *
 * Bei einer gesundheitsnahen Police ist das kein reines Erfassen: ab der
 * ersten benannten Person entscheidet die Freigabe fuer diese Person darueber,
 * wer die Police ueberhaupt noch sieht (Plan 4.3, P4-Nachtrag). Deshalb sagt
 * die Oberflaeche das vor dem Speichern - hier steht nur der Aufruf.
 */
export function addPolicyPerson(
  token: string,
  householdId: string,
  policyId: string,
  input: PolicyPersonInput,
): Promise<PolicyPerson> {
  return apiFetch<PolicyPerson>(`/v1/insurance/policies/${policyId}/persons`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** Loest die Verknuepfung, nicht die Person: `insurance_policy_persons` haengt
 *  mit `on delete restrict` an `health_insured_persons`. */
export function removePolicyPerson(
  token: string,
  householdId: string,
  policyId: string,
  entryId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/insurance/policies/${policyId}/persons/${entryId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

// ---- Objekte --------------------------------------------------------------

/**
 * Die versicherten Sachen des Haushalts.
 *
 * `include_disposed` steht fest auf `true`: ein abgegebenes Auto haengt
 * weiterhin an seiner Police, und ohne die Zeile stuende dort eine
 * Verknuepfung ohne Objekt. Aus der Auswahlliste zum Hinzufuegen nimmt die
 * Oberflaeche es selbst heraus - das ist eine Anzeigefrage und keine
 * Ladefrage.
 */
export function listInsuredObjects(token: string, householdId: string): Promise<InsuredObject[]> {
  return apiFetch<InsuredObject[]>("/v1/insurance/objects?include_disposed=true", {
    token,
    householdId,
  });
}

export function createInsuredObject(
  token: string,
  householdId: string,
  input: InsuredObjectInput,
): Promise<InsuredObject> {
  return apiFetch<InsuredObject>("/v1/insurance/objects", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** `If-Match` traegt den Versionszaehler, Muster wie im uebrigen Bestand. Ein
 *  fremder Stand antwortet 409 statt still zu ueberschreiben. */
export function updateInsuredObject(
  token: string,
  householdId: string,
  objectId: string,
  version: number,
  patch: InsuredObjectPatch,
): Promise<InsuredObject> {
  return apiFetch<InsuredObject>(`/v1/insurance/objects/${objectId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: { "If-Match": `"${version}"` },
    body: JSON.stringify(patch),
  });
}

export function listPolicyObjects(
  token: string,
  householdId: string,
  policyId: string,
): Promise<PolicyObjectLink[]> {
  return apiFetch<PolicyObjectLink[]>(`/v1/insurance/policies/${policyId}/objects`, {
    token,
    householdId,
  });
}

/**
 * Ein Objekt an die Police haengen - ein bestehendes oder ein neues.
 *
 * Der Server nimmt beides in EINEM Aufruf entgegen. Das ist der Grund, warum
 * die Oberflaeche nicht erst anlegt und dann verknuepft: ein angelegtes, aber
 * nicht verknuepftes Objekt waere ein Fehlschlag mit halbem Ergebnis.
 */
export function linkPolicyObject(
  token: string,
  householdId: string,
  policyId: string,
  input:
    | { insured_object_id: string; note?: string | null }
    | { new_object: InsuredObjectInput; note?: string | null },
): Promise<PolicyObjectLink> {
  return apiFetch<PolicyObjectLink>(`/v1/insurance/policies/${policyId}/objects`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * Loest die Verknuepfung. Das Objekt bleibt stehen - es haengt an weiteren
 * Policen, an Buchungen und spaeter an Schaeden.
 *
 * Der Pfad traegt die Kennung des OBJEKTS, nicht die der Verknuepfungszeile:
 * das Paar (Police, Objekt) ist eindeutig, und der Aufrufer hat die
 * Objektkennung ohnehin in der Hand.
 */
export function unlinkPolicyObject(
  token: string,
  householdId: string,
  policyId: string,
  objectId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/insurance/policies/${policyId}/objects/${objectId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

// ---- Kennzeichenhistorie --------------------------------------------------

export function listObjectRegistrations(
  token: string,
  householdId: string,
  objectId: string,
): Promise<ObjectRegistration[]> {
  return apiFetch<ObjectRegistration[]>(`/v1/insurance/objects/${objectId}/registrations`, {
    token,
    householdId,
  });
}

/**
 * Neue Zulassungszeile.
 *
 * Der Server schliesst die laufende Zeile und oeffnet diese - die Oberflaeche
 * rechnet das `valid_to` der alten Zeile ausdruecklich NICHT aus und zeigt es
 * auch nicht vorab als Datum an. Sie sagt, DASS die laufende Zeile geschlossen
 * wird; welchen letzten Tag sie bekommt, steht nach dem Speichern in der
 * Antwort. Zwei Rechnungen ergaeben sonst zwei verschiedene letzte Tage
 * derselben Zulassung.
 */
export function createObjectRegistration(
  token: string,
  householdId: string,
  objectId: string,
  input: ObjectRegistrationInput,
): Promise<ObjectRegistration> {
  return apiFetch<ObjectRegistration>(`/v1/insurance/objects/${objectId}/registrations`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

// ---- Deckungsbausteine ----------------------------------------------------

export function listCoverageKeys(
  token: string,
  householdId: string,
  lineKey: string,
): Promise<InsuranceCoverageKey[]> {
  return apiFetch<InsuranceCoverageKey[]>(
    `/v1/insurance/coverage-keys?line_key=${encodeURIComponent(lineKey)}`,
    { token, householdId },
  );
}

export function listPolicyCoverages(
  token: string,
  householdId: string,
  policyId: string,
): Promise<PolicyCoverage[]> {
  return apiFetch<PolicyCoverage[]>(`/v1/insurance/policies/${policyId}/coverages`, {
    token,
    householdId,
  });
}

/**
 * Die Menge der Bausteine ersetzen - ein Rundlauf statt n.
 *
 * Rumpfform `{coverages: [...]}` wie bei den uebrigen Mengenersetzungen des
 * Bestands (`PUT /v1/debt-plans/{id}/rate-periods`). Was nicht mitgeschickt
 * wird, ist danach nicht mehr erfasst - deshalb schickt die Oberflaeche immer
 * die vollstaendige Menge und nie nur das Geaenderte.
 */
export function replacePolicyCoverages(
  token: string,
  householdId: string,
  policyId: string,
  coverages: PolicyCoverageInput[],
): Promise<PolicyCoverage[]> {
  return apiFetch<PolicyCoverage[]>(`/v1/insurance/policies/${policyId}/coverages`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify({ coverages }),
  });
}

// ---------------------------------------------------------------------------
// Schaden- und Leistungsfaelle (P5)
//
// Die Formen spiegeln `insurance_claims`, `insurance_claim_payments` und
// `insurance_claim_events` aus Plan 4.8 - Spalte fuer Spalte, damit die
// Oberflaeche keinen zweiten Begriffssatz einfuehrt.
//
// **Was hier NICHT gerechnet wird.** Das Ende des Sonderkuendigungsfensters
// ist ein Rechenergebnis des Servers und kommt fertig herein. Der Browser
// leitet aus `negotiations_closed_on` niemals selbst "plus ein Monat" ab: aus
// demselben Wert baut der Nachtlauf seinen Push, und zwei Rechnungen ergaeben
// frueher oder spaeter zwei Enden derselben Frist - dieselbe Regel wie bei den
// Beitragsfristen in P3.
//
// **Zwei Ergebnisse sind vollwertig und kein fehlender Wert.**
// `special_termination_state = 'open_undetermined'` heisst: das Recht besteht
// seit dem Schadentag, sein Ende steht noch nicht fest. `expired` heisst: es
// bestand und ist verfallen - die Oberflaeche leert es NICHT, der Nutzer soll
// sehen, dass es bestand (Plan 4.8, 8.2 Punkt 2b).
// ---------------------------------------------------------------------------

export type ClaimStatus =
  | "draft"
  | "reported"
  | "in_review"
  | "info_requested"
  | "decided"
  | "payment_expected"
  | "paid"
  | "closed"
  | "withdrawn";

export type ClaimDecision = "accepted_full" | "accepted_partial" | "rejected";

/**
 * Welche Norm den Fristanker stellt.
 *
 * Sie kommt aus der SPARTE der Police
 * (`insurance_lines.default_claim_termination_basis`) und steht am Vorgang,
 * nicht als Konstante im Browser: aus einem einzigen Kfz-Unfall entstehen an
 * einer Buendelpolice zwei Vorgaenge mit zwei verschiedenen Ankern - Kasko
 * nach Paragraph 92 VVG, Haftpflicht nach Paragraph 111 VVG.
 */
export type ClaimTerminationBasis = "vvg_92" | "vvg_111" | "contract" | "none";

export type NegotiationsClosedReason =
  | "final_rejection"
  | "full_payment"
  | "settlement_letter"
  | "withdrawal"
  | "judgment_final"
  | "liability_acknowledged"
  | "liability_refused"
  | "user_confirmed";

export type NegotiationsClosedSource = "transaction" | "manual";

/** Herkunft des Ankerdatums. `belegt` gibt es nur mit zugeordnetem
 *  Kontoumsatz - ein Datum ohne sichtbare Herkunft ist hier eine Behauptung. */
export type NegotiationsCertainty = "belegt" | "abgeleitet" | "geschaetzt" | "unbekannt";

export type ClaimSpecialTerminationState = "none" | "open_undetermined" | "open_until" | "expired";

export type ClaimPaymentDirection = "inbound" | "outbound";

export type ClaimPaymentKind = "settlement" | "advance" | "deductible" | "invoice" | "recovery";

/**
 * Zustand des Fensters als Rechenergebnis des Servers.
 *
 * Optional am Vorgang: bis der Dienst die drei Daten mitliefert, zeigt die
 * Oberflaeche das gespeicherte `special_termination_until` ohne Countdown -
 * lieber ein Datum ohne Tageszahl als eine Tageszahl aus zweiter Rechnung.
 */
export interface ClaimSpecialTermination {
  state: ClaimSpecialTerminationState;
  deadline_kind: DeadlineKind;
  /** Rechtliches Fristende. Bei Kuendigungsfristen schiebt Paragraph 193 BGB
   *  es NICHT nach hinten. */
  deadline_legal_on: string | null;
  safe_service_on: string | null;
  recommended_action_on: string | null;
  days_left: number | null;
  basis: string;
  basis_kind: AnchorBasisKind;
  /** Waehlbare Wirkung der Kuendigung. Eine Wahl, kein Datum. */
  effect_options?: string[];
}

/**
 * Vorschlag fuer den Fristanker - nie ein gesetzter Wert.
 *
 * Der Dienst schlaegt vor, der Nutzer bestaetigt (Plan 4.8): bei `rejected`
 * abgeleitet aus `decided_on`, bei `accepted_full` und `paid` belegt aus der
 * letzten Zahlungszuordnung. Bei `accepted_partial` kommt ausdruecklich KEIN
 * Vorschlag - dort ist die Entscheidung der Anfang der Verhandlung.
 */
export interface NegotiationsSuggestion {
  negotiations_closed_on: string;
  reason: NegotiationsClosedReason;
  certainty: NegotiationsCertainty;
  source: NegotiationsClosedSource;
  transaction_id: string | null;
  /** Woraus der Vorschlag stammt, im Klartext. */
  hint?: string | null;
}

export interface InsuranceClaim {
  id: string;
  /** Nullable: ein Schaden kann sich gegen eine FREMDE Versicherung richten. */
  policy_id: string | null;
  insured_object_id: string | null;
  title: string;
  claim_number: string | null;
  occurred_on: string;
  reported_on: string | null;
  location: string | null;
  description: string | null;
  police_reference: string | null;
  status: ClaimStatus;
  decision: ClaimDecision | null;
  /** Fachliches Datum der Versichererentscheidung. Es hat KEINE Fristfunktion
   *  mehr (Plan 3.12, Pruefung 3). */
  decided_on: string | null;
  reduction_reason: string | null;
  claimed_minor: number;
  approved_minor: number | null;
  deductible_minor: number;
  settled_minor: number;
  paid_to_third_party: boolean;
  next_action: string | null;
  next_action_due: string | null;
  termination_basis: ClaimTerminationBasis;
  negotiations_closed_on: string | null;
  negotiations_closed_reason: NegotiationsClosedReason | null;
  negotiations_closed_source: NegotiationsClosedSource | null;
  negotiations_closed_transaction_id: string | null;
  negotiations_closed_certainty: NegotiationsCertainty;
  special_termination_state: ClaimSpecialTerminationState;
  /** Abgeleitet aus dem Anker, sonst null. Nie per Statuswechsel geschrieben. */
  special_termination_until: string | null;
  termination_effect_options: string[];
  version: number;
  created_at: string;
  updated_at: string;

  /* ---- Rechenergebnisse des Dienstes -------------------------------------
   * Optional getippt, weil sie kein Spaltenwert sind. Fehlen sie, sagt die
   * Oberflaeche weniger - sie denkt sich nichts aus. */
  special_termination?: ClaimSpecialTermination | null;
  negotiations_suggestion?: NegotiationsSuggestion | null;
  /** Beschriftung der Police am Vorgang, wenn die Liste haushaltsweit kommt. */
  policy_label?: string | null;
}

/** Journalzeile. Unveraenderlich - sie beantwortet, WANN ein Stand erreicht
 *  war, und ist damit die Quelle des Zeitstrahls. */
export interface ClaimEvent {
  id: number;
  claim_id: string;
  from_status: ClaimStatus | null;
  to_status: ClaimStatus;
  occurred_on: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ClaimPayment {
  id: string;
  claim_id: string;
  /** Null bei Direktzahlung an Werkstatt oder Arzt: dort gibt es keine eigene
   *  Buchung, und eine erfundene waere schlimmer als keine. */
  transaction_id: string | null;
  direction: ClaimPaymentDirection;
  kind: ClaimPaymentKind;
  amount_minor: number;
  booked_on: string;
  note: string | null;
  created_at: string;

  /* Aufgeloeste Buchung, sofern eine daranhaengt. */
  payee_label?: string | null;
  transaction_amount_minor?: number | null;
}

/** Vorschlag mit Trefferbegruendung - dieselbe Form wie im Bankabgleich der
 *  Erstattungen, weil es derselbe Vorgang ist: Geld auf dem Konto einem
 *  Vorgang zuordnen. */
export interface ClaimTransactionCandidate {
  transaction_id: string;
  booked_date: string;
  amount_minor: number;
  payee_label: string | null;
  note: string | null;
  score: number;
  evidence: string[];
  /** Was von der Buchung noch frei ist - eine Sammelueberweisung deckt
   *  mehrere Vorgaenge. */
  remaining_minor: number;
}

export interface InsuranceClaimDetail extends InsuranceClaim {
  events: ClaimEvent[];
  payments: ClaimPayment[];
}

export interface ClaimCreateInput {
  policy_id: string | null;
  title: string;
  occurred_on: string;
  reported_on?: string | null;
  claim_number?: string | null;
  claimed_minor?: number;
  location?: string | null;
  description?: string | null;
}

export interface ClaimStatusInput {
  status: ClaimStatus;
  occurred_on?: string | null;
  decision?: ClaimDecision | null;
  decided_on?: string | null;
  reduction_reason?: string | null;
  note?: string | null;
}

export interface CloseNegotiationsInput {
  negotiations_closed_on: string;
  reason: NegotiationsClosedReason;
  /** Nur mit Buchung wird aus dem Anker ein belegter. */
  transaction_id?: string | null;
}

export interface ClaimPaymentInput {
  transaction_id: string | null;
  direction: ClaimPaymentDirection;
  kind: ClaimPaymentKind;
  amount_minor: number;
  booked_on: string;
  note?: string | null;
}

// ---- Beschriftungen -------------------------------------------------------

export const CLAIM_STATUS_LABEL: Record<ClaimStatus, string> = {
  draft: "Entwurf",
  reported: "gemeldet",
  in_review: "in Bearbeitung",
  info_requested: "Rückfrage offen",
  decided: "entschieden",
  payment_expected: "Zahlung erwartet",
  paid: "ausgezahlt",
  closed: "abgeschlossen",
  withdrawn: "zurückgezogen",
};

export const CLAIM_DECISION_LABEL: Record<ClaimDecision, string> = {
  accepted_full: "voll reguliert",
  accepted_partial: "teilweise reguliert",
  rejected: "abgelehnt",
};

export const CLAIM_TERMINATION_BASIS_LABEL: Record<ClaimTerminationBasis, string> = {
  vvg_92: "§ 92 VVG (Sachversicherung)",
  vvg_111: "§ 111 VVG (Haftpflicht)",
  contract: "Ihre Versicherungsbedingungen",
  none: "kein Sonderkündigungsrecht",
};

/** Woran die Frist haengt - im Wortlaut der jeweiligen Norm. Der Anker ist
 *  NICHT die Entscheidung des Versicherers; die kann frueher liegen. */
export const CLAIM_TERMINATION_ANCHOR_TEXT: Record<ClaimTerminationBasis, string> = {
  vvg_92: "dem Abschluss der Verhandlungen über die Entschädigung",
  vvg_111:
    "der Anerkennung oder Ablehnung des Freistellungsanspruchs oder der Rechtskraft des Urteils",
  contract: "dem in Ihren Versicherungsbedingungen vereinbarten Zeitpunkt",
  none: "",
};

export const NEGOTIATIONS_REASON_LABEL: Record<NegotiationsClosedReason, string> = {
  final_rejection: "endgültige Ablehnung",
  full_payment: "vollständige Zahlung",
  settlement_letter: "Abschlussschreiben",
  withdrawal: "Rücknahme des Anspruchs",
  judgment_final: "rechtskräftiges Urteil",
  liability_acknowledged: "Anerkennung des Freistellungsanspruchs",
  liability_refused: "Ablehnung des Freistellungsanspruchs",
  user_confirmed: "von Ihnen als abgeschlossen bestätigt",
};

export const NEGOTIATIONS_CERTAINTY_LABEL: Record<NegotiationsCertainty, string> = {
  belegt: "belegt durch einen Kontoumsatz",
  abgeleitet: "abgeleitet",
  geschaetzt: "von Ihnen gesetzt",
  unbekannt: "Herkunft unbekannt",
};

export const CLAIM_PAYMENT_KIND_LABEL: Record<ClaimPaymentKind, string> = {
  settlement: "Entschädigung",
  advance: "Abschlag",
  deductible: "Selbstbeteiligung",
  invoice: "Rechnung",
  recovery: "Rückforderung",
};

export const CLAIM_PAYMENT_DIRECTION_LABEL: Record<ClaimPaymentDirection, string> = {
  inbound: "Eingang",
  outbound: "Ausgang",
};

/** Wirkung der Kuendigung ist eine WAHL, kein Datum (Plan 4.8). Als
 *  Nachschlagewerk ueber `string`, damit ein neuer Wert des Servers nicht als
 *  leere Zelle erscheint. */
export const TERMINATION_EFFECT_LABEL: Record<string, string> = {
  immediate: "sofort",
  end_of_period: "zum Schluss der laufenden Versicherungsperiode",
  end_of_insurance_year: "zum Ende des Versicherungsjahres",
};

export function terminationEffectLabel(value: string): string {
  return TERMINATION_EFFECT_LABEL[value] ?? value;
}

/**
 * Der Satz, der an die Stelle eines leeren Feldes tritt.
 *
 * Ohne Ankerdatum gibt es kein Fristende - und genau das steht dann dort. Ein
 * leeres Feld liesse offen, ob die Frist fehlt oder nur nicht geladen wurde;
 * dieser Satz sagt, was fehlt und was daraus folgt.
 */
export const CLAIM_ANCHOR_MISSING_TEXT =
  "Abschluss der Verhandlungen fehlt – Frist nicht berechenbar";

/**
 * Was am Vorgang noch aussteht.
 *
 * Grundlage ist der anerkannte Betrag, solange es einen gibt - vorher der
 * geforderte. Beides sind Serverwerte; gerechnet wird hier nur die Differenz,
 * und zwar fuer genau einen Zweck: den Vorschlagsbetrag der
 * Zahlungszuordnung. Ohne ihn stuende im Betragsfeld die volle Buchung, auch
 * wenn der Vorgang nur noch einen Rest erwartet.
 */
export function claimOutstandingMinor(claim: InsuranceClaim): number {
  const expected = claim.approved_minor ?? claim.claimed_minor;
  return Math.max(expected - claim.settled_minor, 0);
}

/** Eine Stufe des Zeitstrahls. `open` heisst: noch nicht erreicht - und wird
 *  auch so gezeichnet, nicht als leere Zeile. */
export interface ClaimStage {
  key: "reported" | "in_review" | "decided" | "paid";
  label: string;
  on: string | null;
  state: "done" | "open" | "cancelled";
  /** Was an dieser Stufe zusaetzlich zu sagen ist. */
  note: string | null;
  /** Warum die Stufe entfaellt - nur bei `cancelled` gesetzt. Ohne dieses Feld
   *  stuende an einem abgelehnten Vorgang "zurueckgezogen". */
  cancelReason: string | null;
}

const CLAIM_STATUS_RANK: Record<ClaimStatus, number> = {
  draft: 0,
  reported: 1,
  in_review: 2,
  info_requested: 2,
  decided: 3,
  payment_expected: 3,
  paid: 4,
  closed: 4,
  // Ein zurueckgezogener Vorgang war gemeldet - was danach kaeme, kommt nicht
  // mehr. Die Stufen dahinter werden deshalb `cancelled`, nicht `open`.
  withdrawn: 1,
};

/**
 * Die vier Stufen des Vorgangs aus Journal und Stammdaten.
 *
 * **Das Journal hat Vorrang vor den Datumsspalten.** `reported_on` und
 * `decided_on` sind fachliche Angaben, die jederzeit korrigiert werden; die
 * Journalzeile sagt, wann der Stand tatsaechlich erreicht wurde. Fehlt die
 * Zeile - etwa bei einem aus dem Bestand uebernommenen Vorgang -, faellt die
 * Stufe auf die Spalte zurueck.
 *
 * **Eine erreichte Stufe ohne Datum bleibt erreicht.** Sie traegt dann den
 * Hinweis, dass das Datum fehlt, statt still auf `open` zurueckzufallen: der
 * Vorgang stuende sonst weiter vorn, als er ist.
 */
export function claimStages(
  claim: InsuranceClaim,
  events: ClaimEvent[],
  payments: ClaimPayment[] = [],
): ClaimStage[] {
  const firstEventOn = (statuses: ClaimStatus[]): string | null =>
    events
      .filter((event) => statuses.includes(event.to_status))
      .map((event) => event.occurred_on)
      .sort()[0] ?? null;

  const rank = CLAIM_STATUS_RANK[claim.status] ?? 0;
  const withdrawn = claim.status === "withdrawn";
  const withdrawnOn = withdrawn ? firstEventOn(["withdrawn"]) : null;

  const stageState = (reached: boolean): ClaimStage["state"] =>
    reached ? "done" : withdrawn ? "cancelled" : "open";
  const withdrawnReason = (state: ClaimStage["state"]): string | null =>
    state === "cancelled" ? "Vorgang zurückgezogen" : null;

  const reportedOn = firstEventOn(["reported"]) ?? claim.reported_on;
  const decidedLabel =
    claim.decision === null ? "reguliert oder abgelehnt" : CLAIM_DECISION_LABEL[claim.decision];

  // Die letzte zugeordnete Eingangszahlung ist der Tag, an dem Geld geflossen
  // ist - die Journalzeile nennt nur den Tag des Statuswechsels.
  const lastPaymentOn =
    payments
      .filter((payment) => payment.direction === "inbound")
      .map((payment) => payment.booked_on)
      .sort()
      .at(-1) ?? null;

  const partiallyPaid = claim.settled_minor > 0 && rank < 4;

  // `closed` allein ist KEIN Beleg dafuer, dass gezahlt wurde: ein abgelehnter
  // Vorgang wird ebenfalls geschlossen, und er traegt denselben Rang wie ein
  // ausgezahlter. Die Stufe braucht deshalb Geld - den ausdruecklichen Stand
  // `paid` oder einen abgeschlossenen Vorgang mit Summe. Eine Direktzahlung an
  // die Werkstatt zaehlt mit, sie steht ebenfalls in `settled_minor`.
  const paidReached = claim.status === "paid" || (rank >= 4 && claim.settled_minor > 0);
  // Eine Ablehnung nimmt der Auszahlung dieselbe Zukunft wie eine Ruecknahme:
  // "steht aus" waere die Ankuendigung eines Geldes, das nie kommt.
  const paidState: ClaimStage["state"] = paidReached
    ? "done"
    : withdrawn || claim.decision === "rejected"
      ? "cancelled"
      : "open";

  const stages: ClaimStage[] = [
    {
      key: "reported",
      label: "gemeldet",
      on: reportedOn,
      state: stageState(rank >= 1 || claim.reported_on !== null),
      note: rank >= 1 && reportedOn === null ? "Meldedatum nicht erfasst" : null,
      cancelReason: withdrawnReason(stageState(rank >= 1 || claim.reported_on !== null)),
    },
    {
      key: "in_review",
      label: "in Bearbeitung",
      on: firstEventOn(["in_review", "info_requested"]),
      state: stageState(rank >= 2),
      note:
        claim.status === "info_requested" ? "Der Versicherer hat eine Rückfrage gestellt." : null,
      cancelReason: withdrawnReason(stageState(rank >= 2)),
    },
    {
      key: "decided",
      label: decidedLabel,
      on: firstEventOn(["decided"]) ?? claim.decided_on,
      state: stageState(rank >= 3 || claim.decision !== null),
      note:
        claim.decision === "accepted_partial" && claim.reduction_reason
          ? `Kürzung: ${claim.reduction_reason}`
          : null,
      cancelReason: withdrawnReason(stageState(rank >= 3 || claim.decision !== null)),
    },
    {
      key: "paid",
      label: "ausgezahlt",
      on: firstEventOn(["paid"]) ?? lastPaymentOn,
      state: paidState,
      note: partiallyPaid
        ? "Ein Teilbetrag ist eingegangen, der Rest steht aus."
        : claim.paid_to_third_party
          ? "Direkt an eine dritte Stelle gezahlt – ohne eigene Buchung."
          : null,
      cancelReason:
        paidState === "cancelled"
          ? withdrawn
            ? "Vorgang zurückgezogen"
            : "Vorgang abgelehnt"
          : null,
    },
  ];

  // Die abgebrochene Stufe traegt den Tag der Ruecknahme, nicht keinen.
  return stages.map((stage) =>
    stage.state === "cancelled" && stage.on === null ? { ...stage, on: withdrawnOn } : stage,
  );
}

/** Trigger aus Leitsatz 2: ein Vorgang auf einer gesundheitsnahen Police wird
 *  abgewiesen. Die Kennung ist maschinenlesbar, damit die Oberflaeche auf die
 *  Erstattungsseite verweisen kann statt den Rohtext zu zeigen. */
export function isHealthScopedClaimError(error: unknown): boolean {
  return error instanceof ApiError && error.detail === "insurance_claim_health_scoped";
}

// ---- Abfrageschluessel ----------------------------------------------------

export const insuranceClaimsQueryKey = (householdId: string, policyId: string | null) =>
  ["insurance-claims", householdId, policyId] as const;

export const insuranceClaimQueryKey = (householdId: string, claimId: string) =>
  ["insurance-claim", householdId, claimId] as const;

export const insuranceClaimCandidatesQueryKey = (householdId: string, claimId: string) =>
  ["insurance-claim-candidates", householdId, claimId] as const;

// ---- Abrufe und Aktionen --------------------------------------------------

export function listInsuranceClaims(
  token: string,
  householdId: string,
  policyId?: string | null,
): Promise<InsuranceClaim[]> {
  const query = policyId ? `?policy_id=${encodeURIComponent(policyId)}` : "";
  return apiFetch<InsuranceClaim[]>(`/v1/insurance/claims${query}`, { token, householdId });
}

/** Der Einzelabruf traegt Journal, Zahlungen und - sofern der Dienst ihn hat -
 *  den Vorschlag fuer den Fristanker. */
export function getInsuranceClaim(
  token: string,
  householdId: string,
  claimId: string,
): Promise<InsuranceClaimDetail> {
  return apiFetch<InsuranceClaimDetail>(`/v1/insurance/claims/${claimId}`, {
    token,
    householdId,
  });
}

export function createInsuranceClaim(
  token: string,
  householdId: string,
  input: ClaimCreateInput,
): Promise<InsuranceClaim> {
  return apiFetch<InsuranceClaim>("/v1/insurance/claims", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** `If-Match` traegt den Versionszaehler, Muster wie im uebrigen Modul. */
export function updateInsuranceClaim(
  token: string,
  householdId: string,
  claimId: string,
  version: number,
  patch: Record<string, unknown>,
): Promise<InsuranceClaim> {
  return apiFetch<InsuranceClaim>(`/v1/insurance/claims/${claimId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: { "If-Match": `"${version}"` },
    body: JSON.stringify(patch),
  });
}

/**
 * Statuswechsel als Einfeldaktion.
 *
 * Er schreibt das Journal und setzt AUSDRUECKLICH KEIN Fristdatum: beim
 * Uebergang nach `reported` entsteht `open_undetermined`, nicht ein gerechnetes
 * Ende (Plan 5.4). Die alte Regel "decided_on plus ein Monat" ist ersatzlos
 * entfallen.
 */
export function setInsuranceClaimStatus(
  token: string,
  householdId: string,
  claimId: string,
  input: ClaimStatusInput,
): Promise<InsuranceClaim> {
  return apiFetch<InsuranceClaim>(`/v1/insurance/claims/${claimId}/status`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** Setzt den Fristanker - mit Grund und, wo vorhanden, mit Beleg. Erst danach
 *  wechselt der Zustand auf `open_until`. */
export function closeClaimNegotiations(
  token: string,
  householdId: string,
  claimId: string,
  input: CloseNegotiationsInput,
): Promise<InsuranceClaim> {
  return apiFetch<InsuranceClaim>(`/v1/insurance/claims/${claimId}/close-negotiations`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listClaimTransactionCandidates(
  token: string,
  householdId: string,
  claimId: string,
): Promise<ClaimTransactionCandidate[]> {
  return apiFetch<ClaimTransactionCandidate[]>(
    `/v1/insurance/claims/${claimId}/transaction-candidates`,
    { token, householdId },
  );
}

export function addClaimPayment(
  token: string,
  householdId: string,
  claimId: string,
  input: ClaimPaymentInput,
): Promise<ClaimPayment> {
  return apiFetch<ClaimPayment>(`/v1/insurance/claims/${claimId}/payments`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function removeClaimPayment(
  token: string,
  householdId: string,
  claimId: string,
  paymentId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/insurance/claims/${claimId}/payments/${paymentId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Bedarfsabgleich und Deckungsluecken (P6, Plan 4.9, 5.5, 10.1 bis 10.4)
//
// **Drei Leitsaetze dieses Abschnitts stehen hier und nicht nur in den
// Komponenten**, weil sie die Typen formen:
//
// 1. **Jeder Befund traegt seine Regel bei sich.** `rule_key`, `rationale`,
//    `triggered_by`, `source_url` und `source_checked_on` sind deshalb nicht
//    optional: ein Befund ohne Begruendung und ohne Quelle darf gar nicht erst
//    darstellbar sein. Formuliert wird nichts im Browser - die Saetze kommen
//    aus `app/data/insurance_rules.py`.
// 2. **Es gibt keinen Score.** Darum gibt es hier auch keinen Typ, in den einer
//    passen wuerde: keine Note, kein Prozentwert, keine Gesamtzahl. Was der
//    Server liefert, ist eine Liste einzelner Befunde und ein Disclaimer.
// 3. **"Keine Angabe" ist ein Wert, kein fehlender Wert.** Alle Profilfelder
//    sind `| null` getippt, auch die drei Ja/Nein-Fragen (`has_oil_tank`,
//    `side_business`, `volunteering`) und `pets`. Die Oberflaeche muss
//    "nicht beantwortet" von "nein" unterscheiden koennen - sonst behauptet
//    sie fuer jeden unausgefuellten Haushalt "kein Oeltank, kein Nebenerwerb,
//    keine Tiere" und erzeugt daraus Befunde, die niemand bestaetigt hat
//    (Plan 10.2 und Leitsatz 4 der Phase).
// ---------------------------------------------------------------------------

export type HousingForm = "rent" | "owner_occupied" | "owner_rented_out" | "none";

export type ElementalZone = "zurs1" | "zurs2" | "zurs3" | "zurs4" | "unknown";

export type TravelFrequency = "never" | "occasional" | "frequent";

export type EmploymentStatus =
  | "employed"
  | "civil_servant"
  | "self_employed"
  | "apprentice"
  | "student"
  | "retired"
  | "not_employed"
  | "child";

export type HealthCoverage = "gkv" | "pkv" | "pkv_beihilfe" | "other";

/** Bedarfsstufe des Katalogs bzw. der Regel (Plan 3, 10.3). Eine Einordnung,
 *  keine Bewertung - und ausdruecklich keine Ampel. */
export type NeedTier = "S0" | "S1" | "S2" | "S3" | "S4";

/** Die sieben Ergebniszustaende aus Plan 10.2 - vollstaendig, weil jeder von
 *  ihnen in der Oberflaeche eine eigene Gruppe hat. */
export type NeedsOutcome =
  "gap" | "underinsured" | "unclear" | "fulfilled" | "dismissed" | "overinsured" | "not_applicable";

export type RuleDecisionKind = "dismissed" | "accepted" | "snoozed";

export type RuleDecisionReason =
  "self_insured" | "covered_elsewhere" | "not_applicable" | "planned" | "other";

export type DuplicateKind = "same_line" | "contained_in";

/** Tierarten, die im Regelkatalog vorkommen (R14, R14a). Hund und Pferd loesen
 *  die Tierhalterhaftpflicht aus, Katze und Kleintier ausdruecklich nicht. */
export type PetKind = "dog" | "horse" | "cat" | "small_animal";

export interface NeedsPersonProfile {
  insured_person_id: string;
  display_name: string;
  employment_status: EmploymentStatus | null;
  net_income_minor: number | null;
  health_coverage: HealthCoverage | null;
  beihilfe_rate_bp: number | null;
  has_dependents: boolean | null;
  education_until: string | null;
  moved_out_on: string | null;
  /** Der Server entscheidet ueber `health_can_manage_person`, wer schreiben
   *  darf. Die Oberflaeche baut daraus KEIN zweites Rechtesystem, sie liest
   *  nur ab (Plan 4.9). Fehlt die Angabe, gilt der Datensatz als lesend. */
  can_manage?: boolean;
  version?: number;
}

export interface NeedsProfile {
  housing_form: HousingForm | null;
  living_area_sqm: number | null;
  elemental_zone: ElementalZone | null;
  has_oil_tank: boolean | null;
  liquid_reserve_minor: number | null;
  /** `null` heisst "nicht beantwortet", `[]` heisst "keine Tiere". Zwei
   *  verschiedene Aussagen: nur die zweite darf R14a ausloesen. */
  pets: PetKind[] | null;
  travel_frequency: TravelFrequency | null;
  vehicle_count: number | null;
  side_business: boolean | null;
  volunteering: boolean | null;
  last_checked_on: string | null;
  version?: number;
  /**
   * Personenbezogene Angaben, soweit der Aufrufer sie ueberhaupt sehen darf.
   *
   * Optional getippt und nicht etwa leer geliefert: wer keine
   * Gesundheitsfreigabe hat, liest hier gar nichts - und die Oberflaeche darf
   * das nicht als "keine Personen erfasst" darstellen.
   */
  persons?: NeedsPersonProfile[];
}

export type NeedsProfileInput = Omit<NeedsProfile, "last_checked_on" | "version" | "persons">;

export type NeedsPersonProfileInput = Omit<
  NeedsPersonProfile,
  "insured_person_id" | "display_name" | "can_manage" | "version"
>;

/** Eine Angabe, die fehlt, und die Regeln, die deshalb nicht ausgewertet
 *  werden koennen (Plan 5.5, `GET /needs/completeness`). */
export interface NeedsCompletenessItem {
  field: string;
  label?: string | null;
  scope?: "household" | "person";
  person_id?: string | null;
  person_name?: string | null;
  blocks_rule_keys: string[];
}

export interface NeedsCompleteness {
  missing: NeedsCompletenessItem[];
}

export interface NeedsFindingDecision {
  decision: RuleDecisionKind;
  policy_id: string | null;
  reason: RuleDecisionReason | null;
  reason_note: string | null;
  remind_on: string | null;
  decided_at: string | null;
  /**
   * Wiedervorlage erreicht: die Entscheidung gilt nicht mehr, der Befund ist
   * zurueck. Der Server laesst sie trotzdem in der Antwort stehen, damit die
   * Oberflaeche erklaeren kann, WARUM er wieder da ist. Ohne das Feld haette
   * sie "kommt am ... zurueck" ueber ein Datum in der Vergangenheit gesagt.
   */
  expired?: boolean;
}

/**
 * Ein Befund.
 *
 * `triggered_by` traegt die ausloesenden Merkmale im Klartext - Schluessel ist
 * der Profilfeldname, Wert die Antwort des Haushalts. Mehr als diese vier
 * Grundtypen kann eine Profilangabe nicht sein; ein verschachteltes Objekt
 * waere ein Hinweis darauf, dass der Server rechnet statt zu zitieren.
 */
export interface NeedsFinding {
  rule_key: string;
  risk_field: RiskField;
  line_key: string | null;
  line_label?: string | null;
  outcome: NeedsOutcome;
  /** Stufe der REGEL. Sie schlaegt die Katalogvorgabe (Plan 10.3, Schluss). */
  priority: NeedTier | null;
  /** Allgemeine Katalogvorgabe der Sparte, nur zur Einordnung. */
  need_tier?: NeedTier | null;
  headline: string;
  rationale: string;
  /**
   * Warum GENAU dieses Ergebnis herauskam - der Satz, den die Oberflaeche
   * unter die Begruendung setzt. Die `rationale` sagt, wofuer die Sparte da
   * ist; erst dieser Satz sagt, was am Bestand dieses Haushalts dazu gefunden
   * wurde ("Keine laufende Police dieser Sparte erfasst.").
   */
  outcome_reason?: string;
  triggered_by: Record<string, string | number | boolean | null>;
  source_url: string | null;
  source_checked_on: string | null;
  matched_policy_ids: string[];
  gap_value_minor?: number | null;
  target_value_minor?: number | null;
  person_id?: string | null;
  person_name?: string | null;
  decision?: NeedsFindingDecision | null;
}

export interface NeedsCheckResult {
  findings: NeedsFinding[];
  /** Plan P6-Abnahme: "Der Disclaimer steht im API-Body, nicht nur in der
   *  Fusszeile." Die Oberflaeche zeigt ihn deshalb aus der Antwort. */
  disclaimer: string;
  checked_on?: string | null;
}

export interface RuleDecisionInput {
  decision: RuleDecisionKind;
  policy_id?: string | null;
  reason?: RuleDecisionReason | null;
  reason_note?: string | null;
  remind_on?: string | null;
}

/**
 * Mutmassliche Doppelung (Plan 10.4).
 *
 * `certainty` traegt die Entscheidung aus 10.4: ein `contained_in`-Befund wird
 * erst als solcher ausgespielt, wenn die Bausteine BEIDER Policen erfasst
 * sind. Vorher lautet er ehrlich "koennte doppelt sein, ich weiss es aber
 * nicht" - und die Oberflaeche zeigt statt des Befundes die Aufgabe, die
 * Bausteine zu erfassen.
 */
export interface DuplicateFinding {
  kind: DuplicateKind;
  policy_id: string;
  other_policy_id: string;
  policy_label?: string | null;
  other_policy_label?: string | null;
  line_key?: string | null;
  line_label?: string | null;
  coverage_key?: string | null;
  coverage_label?: string | null;
  yearly_minor: number | null;
  rationale: string;
  certainty?: "established" | "unclear";
  /** Policen, deren Bausteine fuer einen belastbaren Befund fehlen. */
  missing_coverage_policy_ids?: string[];
  /** Die Aufgabe statt einer Behauptung, vom Server formuliert: er weiss,
   *  welche der beiden Policen ihre Bausteine vermissen laesst. */
  hint?: string | null;
}

// ---------------------------------------------------------------------------
// Beschriftungen
// ---------------------------------------------------------------------------

export const HOUSING_FORM_LABEL: Record<HousingForm, string> = {
  rent: "zur Miete",
  owner_occupied: "Eigentum, selbst bewohnt",
  owner_rented_out: "Eigentum, vermietet",
  none: "keine eigene Wohnung",
};

export const ELEMENTAL_ZONE_LABEL: Record<ElementalZone, string> = {
  zurs1: "ZÜRS 1 – sehr selten Hochwasser",
  zurs2: "ZÜRS 2 – selten Hochwasser",
  zurs3: "ZÜRS 3 – gelegentlich Hochwasser",
  zurs4: "ZÜRS 4 – häufig Hochwasser",
  unknown: "nachgesehen, aber unbekannt",
};

export const TRAVEL_FREQUENCY_LABEL: Record<TravelFrequency, string> = {
  never: "nie",
  occasional: "gelegentlich",
  frequent: "häufig",
};

export const EMPLOYMENT_STATUS_LABEL: Record<EmploymentStatus, string> = {
  employed: "angestellt",
  civil_servant: "verbeamtet",
  self_employed: "selbstständig",
  apprentice: "in Ausbildung",
  student: "in Studium oder Schule",
  retired: "im Ruhestand",
  not_employed: "nicht erwerbstätig",
  child: "Kind",
};

export const HEALTH_COVERAGE_LABEL: Record<HealthCoverage, string> = {
  gkv: "gesetzlich versichert",
  pkv: "privat versichert",
  pkv_beihilfe: "privat versichert mit Beihilfe",
  other: "anders abgesichert",
};

export const PET_KIND_LABEL: Record<PetKind, string> = {
  dog: "Hund",
  horse: "Pferd",
  cat: "Katze",
  small_animal: "Kleintier",
};

/**
 * Die Bedarfsstufe als Satz, nicht als Note.
 *
 * Bewusst ohne "dringend", ohne Rangwort und ohne Aufforderung: die Stufe
 * ordnet ein, welche Art Schaden eine Sparte abdeckt. Sie bewertet nicht, wie
 * gut jemand abgesichert ist - das waere der Score, den der Plan ausschliesst.
 */
export const NEED_TIER_LABEL: Record<NeedTier, string> = {
  S0: "S0 – gesetzlich vorgeschrieben",
  S1: "S1 – deckt existenzbedrohende Schäden",
  S2: "S2 – deckt Schäden oberhalb der eigenen Rücklage",
  S3: "S3 – deckt kalkulierbare Kosten",
  S4: "S4 – im Katalog als meist entbehrlich geführt",
};

export const NEEDS_OUTCOME_LABEL: Record<NeedsOutcome, string> = {
  gap: "kein Schutz erfasst",
  underinsured: "unter dem geprüften Wert",
  unclear: "Angabe fehlt",
  fulfilled: "Police vorhanden, geprüfter Wert erreicht",
  dismissed: "von Ihnen weggelegt",
  overinsured: "Police vorhanden, Regel greift nicht",
  not_applicable: "trifft nicht zu",
};

export const RULE_DECISION_REASON_LABEL: Record<RuleDecisionReason, string> = {
  self_insured: "trage ich selbst",
  covered_elsewhere: "anderweitig abgedeckt",
  not_applicable: "trifft auf mich nicht zu",
  planned: "ist geplant",
  other: "anderer Grund",
};

/**
 * Klartextnamen der Profilfelder fuer die Zeile "worauf dieser Befund beruht".
 *
 * Sie stehen hier neben den Typen und nicht im JSX: kommt im Regelkatalog ein
 * Merkmal dazu, faellt an genau einer Stelle auf, dass die Uebersetzung fehlt -
 * und bis dahin steht dort der rohe Feldname statt eines leeren Feldes.
 */
export const NEEDS_FACT_LABEL: Record<string, string> = {
  housing_form: "Wohnform",
  living_area_sqm: "Wohnfläche",
  elemental_zone: "Hochwasserzone",
  has_oil_tank: "Öltank",
  liquid_reserve_minor: "verfügbare Rücklage",
  pets: "Tiere im Haushalt",
  travel_frequency: "Reisen ins Ausland",
  vehicle_count: "Fahrzeuge",
  side_business: "Nebenerwerb",
  volunteering: "Ehrenamt",
  employment_status: "Erwerbsstatus",
  net_income_minor: "Nettoeinkommen",
  health_coverage: "Krankenversicherung",
  beihilfe_rate_bp: "Beihilfebemessungssatz",
  has_dependents: "unterhaltsberechtigte Angehörige",
  education_until: "Erstausbildung bis",
  moved_out_on: "ausgezogen am",
  died_on: "verstorben am",
  open_debt_minor: "offene Darlehen",
  child_count: "Kinder im Haushalt",
  policy_status: "Status der Police",
};

export function needsFactLabel(fact: string): string {
  return NEEDS_FACT_LABEL[fact] ?? fact;
}

/**
 * Ein ausloesendes Merkmal als Text.
 *
 * `null` wird zu "keine Angabe" und nicht zu "nein" - dieselbe Unterscheidung
 * wie im Formular. Sonst liesse sich einem Befund nicht mehr ansehen, ob er
 * auf einer Antwort beruht oder auf einer ausgebliebenen.
 */
export function formatNeedsFactValue(value: string | number | boolean | null): string {
  if (value === null) return "keine Angabe";
  if (value === true) return "ja";
  if (value === false) return "nein";
  if (typeof value === "number") return value.toLocaleString("de-DE");
  return value;
}

/** Anzeigename einer Police, wie in Liste und Zeitleiste. */
export function policyDisplayName(policy: InsurancePolicy): string {
  return policy.label ?? policy.line_label;
}

/**
 * Reihenfolge der Ergebnisgruppen (Plan 10.2).
 *
 * Sie ist fachlich und keine Geschmacksfrage: erst was fehlt, dann was
 * ungeklaert ist, dann was steht. Erfuelltes, Weggelegtes, Entbehrliches und
 * Nichtzutreffendes stehen hinten und eingeklappt - sie beantworten die Frage
 * "was habe ich uebersehen?" nicht.
 */
export const NEEDS_OUTCOME_ORDER: NeedsOutcome[] = [
  "gap",
  "underinsured",
  "unclear",
  "fulfilled",
  "dismissed",
  "overinsured",
  "not_applicable",
];

/**
 * Fehlt der Endpunkt (404), ist der Bedarfsabgleich auf diesem Server noch
 * nicht eingerichtet - das ist kein Fehler des Nutzers und bekommt deshalb
 * keine Fehlerfarbe, sondern einen Satz.
 */
export function isNeedsEndpointMissing(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

// ---------------------------------------------------------------------------
// Abfrageschluessel und Abrufe
// ---------------------------------------------------------------------------

export const insuranceNeedsProfileQueryKey = (householdId: string) =>
  ["insurance-needs-profile", householdId] as const;

export const insuranceNeedsCheckQueryKey = (householdId: string) =>
  ["insurance-needs-check", householdId] as const;

export const insuranceNeedsCompletenessQueryKey = (householdId: string) =>
  ["insurance-needs-completeness", householdId] as const;

export const insuranceDuplicatesQueryKey = (householdId: string) =>
  ["insurance-duplicates", householdId] as const;

/**
 * Die Abfragen des Bedarfsabgleichs.
 *
 * Sie haengen an ZWEI Quellen zugleich: am Profil und am Bestand. Deshalb ruft
 * `invalidateInsuranceQueries` diese Funktion mit auf - eine bestaetigte
 * Sparte, eine neue Police oder ein erfasster Baustein aendert die Befunde
 * ebenso wie eine geaenderte Antwort im Formular.
 */
export function invalidateNeedsQueries(
  queryClient: QueryClient,
  householdId: string,
): Promise<void> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: insuranceNeedsProfileQueryKey(householdId) }),
    queryClient.invalidateQueries({ queryKey: insuranceNeedsCheckQueryKey(householdId) }),
    queryClient.invalidateQueries({ queryKey: insuranceNeedsCompletenessQueryKey(householdId) }),
    queryClient.invalidateQueries({ queryKey: insuranceDuplicatesQueryKey(householdId) }),
  ]).then(() => undefined);
}

export function getNeedsProfile(token: string, householdId: string): Promise<NeedsProfile> {
  return apiFetch<NeedsProfile>("/v1/insurance/needs/profile", { token, householdId });
}

export function updateNeedsProfile(
  token: string,
  householdId: string,
  input: NeedsProfileInput,
): Promise<NeedsProfile> {
  return apiFetch<NeedsProfile>("/v1/insurance/needs/profile", {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function updateNeedsPersonProfile(
  token: string,
  householdId: string,
  personId: string,
  input: NeedsPersonProfileInput,
): Promise<NeedsPersonProfile> {
  return apiFetch<NeedsPersonProfile>(`/v1/insurance/needs/profile/persons/${personId}`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function getNeedsCompleteness(
  token: string,
  householdId: string,
): Promise<NeedsCompleteness> {
  return apiFetch<NeedsCompleteness>("/v1/insurance/needs/completeness", { token, householdId });
}

export function getNeedsCheck(token: string, householdId: string): Promise<NeedsCheckResult> {
  return apiFetch<NeedsCheckResult>("/v1/insurance/needs/check", { token, householdId });
}

/**
 * Die Entscheidung des Nutzers zu einer Regel.
 *
 * Ein Endpunkt fuer beide Antworten, weil beide dieselbe Zeile schreiben
 * (`insurance_rule_decisions.decision`): "kuemmere ich mich" ist `accepted`,
 * "trifft nicht zu" ist `dismissed`. `policy_id` bleibt im Regelfall leer -
 * die meisten Regeln zeigen auf eine FEHLENDE Police (Plan 4.9).
 */
export function saveRuleDecision(
  token: string,
  householdId: string,
  ruleKey: string,
  input: RuleDecisionInput,
): Promise<void> {
  return apiFetch<void>(`/v1/insurance/needs/${encodeURIComponent(ruleKey)}/dismiss`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function clearRuleDecision(
  token: string,
  householdId: string,
  ruleKey: string,
  policyId: string | null = null,
): Promise<void> {
  const query = policyId ? `?policy_id=${encodeURIComponent(policyId)}` : "";
  return apiFetch<void>(`/v1/insurance/needs/${encodeURIComponent(ruleKey)}/dismiss${query}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function listDuplicateFindings(
  token: string,
  householdId: string,
): Promise<DuplicateFinding[]> {
  return apiFetch<DuplicateFinding[]>("/v1/insurance/duplicates", { token, householdId });
}
