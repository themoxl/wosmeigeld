import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Modal } from "../Modal";
import { TabPanel, Tabs, type TabDefinition } from "../Tabs";
import { useFormatDate } from "../../hooks/useProfile";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import {
  deadlineOriginLabel,
  isHealthScoped,
  nextDeadlineDate,
  openSpecialTermination,
  ANCHOR_BASIS_KIND_LABEL,
  DATE_SOURCE_LABEL,
  DEADLINE_KIND_LABEL,
  FREQUENCY_LABEL,
  NOTICE_ANCHOR_LABEL,
  NOTICE_TERMS_SOURCE_LABEL,
  POLICY_STATUS_LABEL,
  PREMIUM_PAYER_LABEL,
  type ConfirmLineInput,
  type Frequency,
  type InsuranceLine,
  type InsurancePolicy,
  type PolicyPurpose,
} from "../../lib/insuranceApi";
import { ClaimTable } from "./ClaimTable";
import { CoverageEditor } from "./CoverageEditor";
import { ObjectsPanel } from "./ObjectsPanel";
import { PersonsEditor } from "./PersonsEditor";
import { DeadlineValue } from "./PolicyList";
import { PremiumHistory, SpecialTerminationBand, type PremiumRegisterData } from "./PremiumHistory";
import { RegistrationHistoryPanel } from "./RegistrationHistoryPanel";

function frequencyLabel(value: string | null): string {
  if (!value) return "Zahlweise unbekannt";
  return FREQUENCY_LABEL[value as Frequency] ?? value;
}

/** Eine Zeile der Stammdatenliste. Fehlt der Wert, steht dort ein Satz und
 *  kein Gedankenstrich: das Werkzeug sagt, was fehlt, statt es zu verschweigen. */
function Row({
  label,
  value,
  origin,
}: {
  label: string;
  value: ReactNode;
  origin?: string | null;
}) {
  return (
    <div className="insurance-kv-row">
      <dt>{label}</dt>
      <dd>
        {value}
        {origin && <span className="insurance-kv-origin">{origin}</span>}
      </dd>
    </div>
  );
}

/**
 * Register des Detaildialogs. Sie wachsen mit den Phasen (Plan 2.3), und
 * keines wird vorab leer registriert - deshalb entsteht "Beiträge" erst, wenn
 * die Datenanbindung des Beitragsregisters uebergeben wird.
 *
 * Die vier P4-Register stehen dagegen immer. Sie haengen an keiner
 * Datenanbindung von aussen: jedes holt seinen eigenen Bestand, sobald es
 * gewaehlt wird. Insbesondere steht "Kennzeichen" auch an einer Hausratpolice
 * und sagt dort in einem Satz, dass kein Fahrzeug daranhaengt. Die Alternative
 * waere gewesen, das Register aus der Sparte zu erraten - und es genau dort zu
 * verstecken, wo es am noetigsten ist: an einer aus dem Bestand uebernommenen
 * Kfz-Police, deren Sparte noch `unknown` ist.
 *
 * **"Schaeden" (P5) ist die eine Ausnahme von dieser Regel, und zwar aus
 * fachlichem Grund.** An einer gesundheitsnahen Police wird ein Vorgang von
 * einem Trigger abgewiesen (Leitsatz 2): Krankheitskosten laufen ueber
 * `health_expense_cases` und die Erstattungsseite. Ein Register, das dort
 * stuende und jeden Anlageversuch mit einem Serverfehler beantwortete, waere
 * schlimmer als keines. Es entfaellt deshalb - und an seiner Stelle steht in
 * der Uebersicht der Verweis darauf, wo diese Vorgaenge stattdessen leben.
 */
type DetailTab =
  "overview" | "premium" | "coverage" | "persons" | "objects" | "registrations" | "claims";

const TAB_PREFIX = "insurance-detail";

export interface PolicyDetailProps {
  policy: InsurancePolicy;
  purpose: PolicyPurpose | null;
  purposeLoading: boolean;
  purposeError: string | null;
  lines: InsuranceLine[];
  canEdit: boolean;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirmMainDue: (policyId: string, mainDueOn: string) => void;
  onConfirmNoticeTerms: (policyId: string, months: number) => void;
  onConfirmLine: (policyId: string, input: ConfirmLineInput) => void;

  /* ---- Beitraege (P3) ----------------------------------------------------
   *
   * Bewusst optional und als ein Buendel: fehlt es, gibt es weder das Register
   * noch das Sonderkuendigungsband. Ein leeres Register waere schlimmer als
   * keines - es behauptete, hier stehe nichts an, wo in Wahrheit nichts
   * geladen wurde.
   */
  premiumRegister?: PremiumRegisterData;
  /** Ankertag fuer Countdown und Verfallspruefung. Als Eigenschaft, damit das
   *  Band pruefbar bleibt. */
  today?: Date;
}

export function PolicyDetail({
  policy,
  purpose,
  purposeLoading,
  purposeError,
  lines,
  canEdit,
  busy,
  error,
  onClose,
  onConfirmMainDue,
  onConfirmNoticeTerms,
  onConfirmLine,
  premiumRegister,
  today,
}: PolicyDetailProps) {
  const { formatDate } = useFormatDate();
  // Die drei Eingaben starten am aktuellen Stand der Police. Die Komponente
  // wird je Police neu gemountet (key), deshalb braucht es keinen Abgleich
  // gegen spaeter geaenderte Eigenschaften.
  const [mainDueInput, setMainDueInput] = useState(policy.main_due_on ?? "");
  const [noticeMonthsInput, setNoticeMonthsInput] = useState(
    policy.notice_period_months === null ? "" : String(policy.notice_period_months),
  );
  const [lineChoice, setLineChoice] = useState(
    policy.line_key === "unknown" ? "" : policy.line_key,
  );
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [activeTab, setActiveTab] = useState<DetailTab>("overview");

  const deadline = policy.deadline;
  const premium = policy.premium;
  const lineOpen = policy.line_key === "unknown" && policy.line_deferred_at === null;
  const selectableLines = lines
    .filter((line) => !line.is_placeholder)
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order || a.label.localeCompare(b.label, "de-DE"));
  const completenessShare =
    policy.completeness_total > 0
      ? Math.round((policy.completeness_filled / policy.completeness_total) * 100)
      : 0;

  // Die Zwischenablage steht nur in einem sicheren Kontext zur Verfuegung. Im
  // LAN-Betrieb ueber schlichtes http fehlt sie - dann darf der Knopf nicht
  // dastehen und nichts tun, sondern gar nicht erst erscheinen. Die Nummer
  // selbst ist markierbarer Text und bleibt erreichbar.
  const canCopy = typeof navigator !== "undefined" && Boolean(navigator.clipboard);

  const copyPolicyNumber = () => {
    const value = policy.policy_number;
    if (!value || !canCopy) return;
    void navigator.clipboard.writeText(value).then(
      () => setCopyState("copied"),
      () => setCopyState("failed"),
    );
  };

  /**
   * Die Ueberschrift der beiden Spalten - und damit die Reichweite der
   * Aussage.
   *
   * `coverage_source` entscheidet, nicht die Sparte und nicht ein Zaehlen im
   * Browser: sobald an der Police Bausteine erfasst sind, spricht die Karte
   * ueber DIESE Police (Plan 2.3 Punkt 2). Der Zusatz "laut erfassten
   * Bausteinen" bleibt dran, damit erkennbar bleibt, woher die Liste kommt.
   */
  const coveragesFromPolicy = purpose?.coverage_source === "policy";
  const coverageHeadings = coveragesFromPolicy
    ? [
        "In dieser Police enthalten · laut erfassten Bausteinen",
        "In dieser Police nicht enthalten · laut erfassten Bausteinen",
      ]
    : ["Typischerweise enthalten", "Typischerweise nicht enthalten"];

  // Das Band entscheidet sich an EINER Stelle, damit Detail und Aufgabenliste
  // nicht zu verschiedenen Antworten kommen. Insbesondere schweigt es bei
  // `dynamic_agreed`: eine vereinbarte Dynamik ist planmaessig und keine
  // Erhoehung im Sinne des Paragraphen 40 VVG.
  const specialTermination = premiumRegister
    ? openSpecialTermination(policy, premiumRegister.notices)
    : null;

  // Gesundheitsnah heisst hier: kein Schadenregister. Die Entscheidung faellt
  // an EINER Stelle und wird zweimal gelesen - vom Registerband und vom
  // Verweis in der Uebersicht.
  const claimsAllowed = !isHealthScoped(policy);

  const tabs: TabDefinition<DetailTab>[] = [
    { key: "overview", label: "Übersicht" },
    ...(premiumRegister ? [{ key: "premium" as const, label: "Beiträge" }] : []),
    { key: "coverage", label: "Deckung" },
    { key: "persons", label: "Personen" },
    { key: "objects", label: "Objekte" },
    { key: "registrations", label: "Kennzeichen" },
    ...(claimsAllowed ? [{ key: "claims" as const, label: "Schäden" }] : []),
  ];

  return (
    <Modal
      title={`${policy.line_label} · ${policy.insurer_name}`}
      size="wide"
      onClose={onClose}
      className="insurance-detail-modal"
    >
      <div className="insurance-detail">
        {/* Oben steht, was man im Ernstfall braucht - keine Erklaerung der
            Sparte, sondern eine Telefonnummer (Plan 2.3). */}
        <section className="insurance-emergency" aria-labelledby="insurance-emergency-title">
          <h3 className="insurance-emergency-title" id="insurance-emergency-title">
            Im Ernstfall
          </h3>
          <div className="insurance-emergency-grid">
            <div>
              <span className="insurance-field-label">Versicherer</span>
              <span className="insurance-field-value">{policy.insurer_name}</span>
            </div>
            <div>
              <span className="insurance-field-label">Policennummer</span>
              <span className="insurance-field-value insurance-field-value--mono">
                {policy.policy_number ?? "nicht erfasst"}
              </span>
              {policy.policy_number && canCopy && (
                <button type="button" className="btn-secondary" onClick={copyPolicyNumber}>
                  {copyState === "copied"
                    ? "Kopiert"
                    : copyState === "failed"
                      ? "Kopieren nicht möglich"
                      : "Nummer kopieren"}
                </button>
              )}
            </div>
            <div>
              <span className="insurance-field-label">Schaden melden</span>
              {policy.claims_phone ? (
                <a
                  className="insurance-field-value insurance-field-value--mono"
                  href={`tel:${policy.claims_phone.replaceAll(" ", "")}`}
                >
                  {policy.claims_phone}
                </a>
              ) : (
                <span className="insurance-field-value insurance-field-value--missing">
                  keine Hotline hinterlegt
                </span>
              )}
            </div>
            {(policy.broker_name || policy.broker_phone) && (
              <div>
                <span className="insurance-field-label">Vermittler</span>
                <span className="insurance-field-value">
                  {[policy.broker_name, policy.broker_phone].filter(Boolean).join(" · ")}
                </span>
              </div>
            )}
            <div>
              <span className="insurance-field-label">Status</span>
              <span className="insurance-field-value">{POLICY_STATUS_LABEL[policy.status]}</span>
            </div>
            <div>
              <span className="insurance-field-label">Nächster Termin</span>
              <DeadlineValue
                date={nextDeadlineDate(deadline)}
                certainty={deadline.certainty}
                originLabel={deadlineOriginLabel(policy)}
                daysLeft={deadline.days_left}
                reason={deadline.reason}
              />
            </div>
          </div>
        </section>

        <div className="insurance-completeness">
          <span>
            {policy.completeness_filled} von {policy.completeness_total} Angaben erfasst
          </span>
          <span className="insurance-completeness-bar" aria-hidden="true">
            <span style={{ width: `${completenessShare}%` }} />
          </span>
        </div>

        {error && (
          <p className="insurance-error" role="alert">
            {error}
          </p>
        )}

        {/* Sonderkuendigungsband: es steht oben und nicht im Register, weil
            eine laufende Frist niemanden erreicht, der gerade ein anderes
            Register offen hat (Plan 2.2, "Jetzt wichtig"). */}
        {premiumRegister && (
          <SpecialTerminationBand
            policy={policy}
            notice={specialTermination}
            busy={premiumRegister.busy}
            canEdit={premiumRegister.canEdit}
            onAck={premiumRegister.onAckNotice}
          />
        )}

        <Tabs
          idPrefix={TAB_PREFIX}
          label="Bereiche dieser Police"
          tabs={tabs}
          active={activeTab}
          onChange={setActiveTab}
        />

        <TabPanel idPrefix={TAB_PREFIX} tabKey="overview" active={activeTab}>
          {/* Zweck: der Vorbehalt steht ueber dem Text, den er einordnet, nicht
            als Fussnote darunter (Plan 3.11). */}
          <section className="insurance-detail-section" aria-labelledby="insurance-purpose-title">
            <h3 id="insurance-purpose-title">Wofür ist diese Art von Versicherung da?</h3>
            {purposeLoading && <p className="card-body">Der Zwecktext wird geladen …</p>}
            {purposeError && (
              <p className="insurance-error" role="alert">
                {purposeError}
              </p>
            )}
            {purpose && (
              <div className="insurance-purpose">
                <p className="insurance-purpose-caveat">{purpose.caveat}</p>
                <p className="insurance-purpose-text">{purpose.purpose_long}</p>
                <div className="insurance-coverage-split">
                  <div>
                    <h4>{coverageHeadings[0]}</h4>
                    {purpose.typically_covers.length === 0 ? (
                      <p className="card-body">Keine Angaben erfasst.</p>
                    ) : (
                      <ul>
                        {purpose.typically_covers.map((entry) => (
                          <li key={entry}>{entry}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <h4>{coverageHeadings[1]}</h4>
                    {purpose.typically_not_covers.length === 0 ? (
                      <p className="card-body">Keine Angaben erfasst.</p>
                    ) : (
                      <ul>
                        {purpose.typically_not_covers.map((entry) => (
                          <li key={entry}>{entry}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
                {/* Der Vorbehalt oben ordnet den Zwecktext ein - der bleibt
                    auch dann die Aussage des Katalogs, wenn die Spalten es
                    nicht mehr sind. Fuer die Spalten wird er deshalb hier
                    ausdruecklich zurueckgenommen (Plan 2.3 Punkt 2). */}
                {coveragesFromPolicy && (
                  <p className="insurance-purpose-source">
                    Diese beiden Spalten stammen aus den Bausteinen, die im Register „Deckung“ an
                    dieser Police erfasst sind – nicht aus dem Katalog. Der Vorbehalt oben gilt für
                    sie nicht.
                  </p>
                )}
                {purpose.source_url && (
                  <p className="insurance-purpose-source">
                    Quelle:{" "}
                    <a href={purpose.source_url} target="_blank" rel="noreferrer">
                      {purpose.source_url}
                    </a>
                    {purpose.source_checked_on &&
                      ` · Stand ${formatDate(purpose.source_checked_on)}`}
                  </p>
                )}
                {purpose.purpose_note && (
                  <p className="insurance-purpose-note">Ihr Zusatz: „{purpose.purpose_note}“</p>
                )}
              </div>
            )}
          </section>

          {/* Sparte: nur solange sie offen ist. Eine bestaetigte Sparte braucht
            kein Formular, sondern hoechstens eine Zeile. */}
          <section className="insurance-detail-section" aria-labelledby="insurance-line-title">
            <h3 id="insurance-line-title">Sparte</h3>
            {lineOpen ? (
              <div className="insurance-action-card">
                <p className="card-body">
                  Ohne Sparte gibt es keinen Zwecktext, keine Fristvorbelegung und keinen
                  Bedarfsabgleich. Die Police bleibt gespeichert, bis die Zuordnung erfolgt.
                </p>
                {canEdit && (
                  <div className="insurance-action-row">
                    <label className="insurance-field">
                      <span>Sparte wählen</span>
                      <select
                        className="form-input"
                        value={lineChoice}
                        onChange={(event) => setLineChoice(event.target.value)}
                      >
                        <option value="">Bitte wählen</option>
                        {selectableLines.map((line) => (
                          <option key={line.line_key} value={line.line_key}>
                            {line.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      className="btn-primary"
                      disabled={busy || !lineChoice}
                      onClick={() => onConfirmLine(policy.id, { line_key: lineChoice })}
                    >
                      Sparte bestätigen
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={busy}
                      onClick={() => onConfirmLine(policy.id, { defer: true })}
                    >
                      Sparte nicht in der Liste
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <p className="card-body">
                {policy.line_deferred_at
                  ? `Zurückgestellt am ${formatDate(policy.line_deferred_at)} – die Sparte steht nicht im Katalog.`
                  : policy.line_confirmed_at
                    ? `${policy.line_label}, bestätigt am ${formatDate(policy.line_confirmed_at)}.`
                    : `${policy.line_label} – aus der Übernahme abgeleitet.`}
              </p>
            )}
          </section>

          {/* Frist mit ihrer Herleitung. Jede Zeile traegt ihre Herkunft; die
            Bestaetigungen sind Einfeldaktionen, keine Formulare (Plan 2.4). */}
          <section className="insurance-detail-section" aria-labelledby="insurance-deadline-title">
            <h3 id="insurance-deadline-title">Frist und ihre Herkunft</h3>
            <dl className="insurance-kv">
              <Row
                label="Hauptfälligkeit"
                value={policy.main_due_on ? formatDate(policy.main_due_on) : "nicht erfasst"}
                origin={DATE_SOURCE_LABEL[policy.main_due_source]}
              />
              <Row
                label="Bezugspunkt"
                value={NOTICE_ANCHOR_LABEL[policy.notice_anchor]}
                origin={`Grundlage: ${ANCHOR_BASIS_KIND_LABEL[policy.anchor_basis_kind]}`}
              />
              <Row
                label="Kündigungsfrist"
                value={
                  policy.notice_period_months === null
                    ? "nicht hinterlegt"
                    : `${policy.notice_period_months} ${
                        policy.notice_period_months === 1 ? "Monat" : "Monate"
                      }`
                }
                origin={NOTICE_TERMS_SOURCE_LABEL[policy.notice_terms_source]}
              />
              <Row label="Fristtyp" value={DEADLINE_KIND_LABEL[deadline.deadline_kind]} />
              <Row
                label="Rechtliches Fristende"
                value={
                  deadline.deadline_legal_on
                    ? formatDate(deadline.deadline_legal_on)
                    : "nicht berechenbar – Angabe fehlt"
                }
              />
              <Row
                label="Letzter sicherer Zustelltag"
                value={
                  deadline.safe_service_on
                    ? formatDate(deadline.safe_service_on)
                    : "nicht berechenbar"
                }
              />
              <Row
                label="Empfohlen absenden bis"
                value={
                  deadline.recommended_action_on
                    ? formatDate(deadline.recommended_action_on)
                    : "nicht berechenbar"
                }
              />
              <Row
                label="Vertragsende"
                value={
                  deadline.term_ends_on ? formatDate(deadline.term_ends_on) : "nicht berechenbar"
                }
              />
            </dl>
            {deadline.reason && <p className="insurance-note">{deadline.reason}</p>}

            {canEdit && (
              <div className="insurance-action-card">
                <div className="insurance-action-row">
                  <label className="insurance-field">
                    <span>Hauptfälligkeit bestätigen</span>
                    <input
                      type="date"
                      className="form-input"
                      value={mainDueInput}
                      onChange={(event) => setMainDueInput(event.target.value)}
                    />
                  </label>
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={busy || !mainDueInput}
                    onClick={() => onConfirmMainDue(policy.id, mainDueInput)}
                  >
                    {policy.main_due_source === "user_confirmed"
                      ? "Datum ändern und bestätigen"
                      : "Datum bestätigen"}
                  </button>
                </div>
                <p className="card-body">
                  Erst mit der Bestätigung wird aus dem Datum ein Termin mit Countdown. Bis dahin
                  bleibt es eine Annahme – und Annahmen erzeugen in diesem Modul keine Erinnerung.
                </p>

                {policy.notice_terms_source !== "user_confirmed" && (
                  <>
                    <div className="insurance-action-row">
                      <label className="insurance-field">
                        <span>Kündigungsfrist in Monaten</span>
                        <input
                          type="number"
                          min={0}
                          max={36}
                          className="form-input"
                          value={noticeMonthsInput}
                          onChange={(event) => setNoticeMonthsInput(event.target.value)}
                        />
                      </label>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={busy || noticeMonthsInput.trim() === ""}
                        onClick={() =>
                          onConfirmNoticeTerms(policy.id, Number.parseInt(noticeMonthsInput, 10))
                        }
                      >
                        Fristlänge bestätigen
                      </button>
                    </div>
                    <p className="card-body">
                      {policy.notice_terms_source === "derived_from_days"
                        ? "Die Monatszahl ist aus Kündigungstagen umgerechnet. Solange sie das ist, bleibt der Termin eine Annahme – auch wenn die Hauptfälligkeit bestätigt ist."
                        : "Die Fristlänge stammt aus dem Katalog der Sparte und ist nicht an dieser Police geprüft."}
                    </p>
                  </>
                )}
              </div>
            )}
          </section>

          {/* Beitrag: die Police zeigt ihn an, sie fuehrt ihn nicht. */}
          <section className="insurance-detail-section" aria-labelledby="insurance-premium-title">
            <h3 id="insurance-premium-title">Beitrag</h3>
            {premium.kind === "contract" && (
              <div className="insurance-premium">
                <dl className="insurance-kv">
                  <Row
                    label="Beitrag"
                    value={
                      premium.contract_amount_minor === null
                        ? "nicht erfasst"
                        : formatMinorUnitsAsEuro(premium.contract_amount_minor)
                    }
                    origin={frequencyLabel(premium.contract_frequency)}
                  />
                  {premium.yearly_in_fixed_costs_minor !== null && (
                    <Row
                      label="Im Jahr"
                      value={formatMinorUnitsAsEuro(premium.yearly_in_fixed_costs_minor)}
                      origin="zählt in die Fixkosten"
                    />
                  )}
                </dl>
                <p className="card-body">
                  Der Beitrag lebt im Vertrag
                  {premium.contract_name ? ` „${premium.contract_name}“` : ""} und zählt dort in die
                  Fixkosten. Die Police zeigt ihn nur an.
                </p>
                {premium.contract_id && (
                  <Link
                    className="insurance-link-out"
                    to={`/fixkosten?contract_id=${premium.contract_id}`}
                  >
                    Zum Vertrag <span aria-hidden="true">→</span>
                  </Link>
                )}
              </div>
            )}
            {premium.kind === "external" && (
              <div className="insurance-premium">
                <dl className="insurance-kv">
                  <Row
                    label="Beitrag"
                    value={
                      premium.external_premium_minor === null
                        ? "nicht erfasst"
                        : formatMinorUnitsAsEuro(premium.external_premium_minor)
                    }
                    origin={frequencyLabel(premium.external_frequency)}
                  />
                  <Row
                    label="Wer zahlt"
                    value={
                      premium.premium_payer
                        ? PREMIUM_PAYER_LABEL[premium.premium_payer]
                        : "nicht benannt"
                    }
                    origin={premium.premium_payer_note}
                  />
                </dl>
                <p className="card-body">
                  Ein Dritter zahlt diesen Beitrag. Er ist ein Informationswert und zählt
                  ausdrücklich nicht in die Fixkosten des Haushalts.
                </p>
              </div>
            )}
            {premium.kind === "none" && (
              <p className="card-body">
                Kein Beitrag hinterlegt. Ein selbst gezahlter Beitrag gehört an einen Vertrag – nur
                dort taucht er in Budget und Vorschau auf. Das Konto muss dafür nicht angebunden
                sein.
              </p>
            )}
          </section>

          {/* Der Verweis steht dort, wo sonst das Register waere - nicht als
              Fussnote. Wer an einer PKV-Police einen Leistungsfall sucht, soll
              in einem Satz erfahren, wo er ihn findet, und nicht auf ein
              fehlendes Register schliessen muessen (Plan 9.1). */}
          {!claimsAllowed && (
            <section
              className="insurance-detail-section"
              aria-labelledby="insurance-claims-referral"
            >
              <h3 id="insurance-claims-referral">Schäden und Leistungsfälle</h3>
              <p className="card-body">
                Diese Police ist gesundheitsnah. Krankheitskosten laufen deshalb nicht über einen
                Schadenvorgang, sondern über die Erstattungsseite: dort hängen Rechnung,
                Einreichung, Träger und Zahlungseingang zusammen – auch bei mehreren Trägern und bei
                einer zurückgehaltenen Einreichung wegen der Beitragsrückerstattung. Zwei
                Vorgangsmodelle für dieselbe Rechnung würden zwei Wahrheiten ergeben.
              </p>
              <Link className="insurance-link-out" to="/erstattungen">
                Zu den Erstattungen <span aria-hidden="true">→</span>
              </Link>
            </section>
          )}
        </TabPanel>

        {/* Register "Beiträge" (P3). Der Bereich wird erst gebaut, wenn er
            gewaehlt ist - `TabPanel` rendert nichts, solange sein Register
            geschlossen ist, und der Treppenverlauf holt dann auch keine
            Zeitreihe, die niemand ansieht. */}
        {premiumRegister && (
          <TabPanel idPrefix={TAB_PREFIX} tabKey="premium" active={activeTab}>
            <PremiumHistory policy={policy} data={premiumRegister} today={today} />
          </TabPanel>
        )}

        {/* Die vier P4-Register. Sie holen ihren Bestand selbst - erst beim
            Oeffnen, weil `TabPanel` sie vorher gar nicht baut. Der Weg ueber
            durchgereichte Eigenschaften waere hier der schlechtere: Personen,
            Objekte, Zulassungen und Bausteine sind vier Abfragen, die niemand
            braucht, solange die Uebersicht offen ist. */}
        <TabPanel idPrefix={TAB_PREFIX} tabKey="coverage" active={activeTab}>
          <CoverageEditor policy={policy} canEdit={canEdit} />
        </TabPanel>
        <TabPanel idPrefix={TAB_PREFIX} tabKey="persons" active={activeTab}>
          <PersonsEditor policy={policy} canEdit={canEdit} />
        </TabPanel>
        <TabPanel idPrefix={TAB_PREFIX} tabKey="objects" active={activeTab}>
          <ObjectsPanel policy={policy} canEdit={canEdit} lines={lines} />
        </TabPanel>
        <TabPanel idPrefix={TAB_PREFIX} tabKey="registrations" active={activeTab}>
          <RegistrationHistoryPanel policy={policy} canEdit={canEdit} />
        </TabPanel>

        {/* Register "Schäden" (P5). Wie die P4-Register holt es seinen Bestand
            selbst - und es entsteht gar nicht erst, wo Vorgaenge fachlich
            nicht hingehoeren. */}
        {claimsAllowed && (
          <TabPanel idPrefix={TAB_PREFIX} tabKey="claims" active={activeTab}>
            <ClaimTable policy={policy} canEdit={canEdit} today={today} />
          </TabPanel>
        )}
      </div>
    </Modal>
  );
}
