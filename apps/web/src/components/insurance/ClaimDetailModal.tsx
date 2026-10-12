/**
 * Ein Schadenvorgang mit Zeitstrahl (docs/VERSICHERUNGEN-PLAN.md, 4.8; P5).
 *
 * **Der Zeitstrahl zeigt vier Stufen, und die offenen sind als offen zu
 * erkennen.** Ein Vorgang, dessen Stufen nur dann erscheinen, wenn sie erreicht
 * sind, beantwortet die eigentliche Frage nicht: was fehlt noch. Deshalb steht
 * jede Stufe da - erreichte mit ihrem Datum, offene mit dem Wort "steht aus".
 *
 * **Das Sonderkuendigungsband steht oben und nicht in einem Unterbereich.**
 * Eine laufende Frist erreicht niemanden, der gerade die Zahlungen ansieht.
 *
 * **Und wo der Anker fehlt, steht kein leeres Feld, sondern ein Satz.** Genau
 * hier lag der Fehler der zweiten Planfassung: sie rechnete
 * `decided_on + 1 Monat` und meldete damit in zwei von drei Faellen ein
 * bestehendes Recht als verfallen (Plan 3.12, Pruefung 3). Diese Oberflaeche
 * rechnet gar nichts. Sie zeigt entweder ein belegtes Ende mit der Angabe,
 * woraus es stammt, oder sie sagt, dass das Recht besteht und sein Ende noch
 * nicht feststeht.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { useFormatDate } from "../../hooks/useProfile";
import { toIsoDateString } from "../../lib/dates";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import { Modal } from "../Modal";
import { countdownLabel, countdownTone } from "./countdown";
import { ClaimPayments } from "./ClaimPayments";
import {
  claimStages,
  closeClaimNegotiations,
  getInsuranceClaim,
  insuranceClaimQueryKey,
  invalidateInsuranceQueries,
  setInsuranceClaimStatus,
  terminationEffectLabel,
  ANCHOR_BASIS_PHRASE,
  CLAIM_ANCHOR_MISSING_TEXT,
  CLAIM_DECISION_LABEL,
  CLAIM_STATUS_LABEL,
  CLAIM_TERMINATION_ANCHOR_TEXT,
  CLAIM_TERMINATION_BASIS_LABEL,
  NEGOTIATIONS_CERTAINTY_LABEL,
  NEGOTIATIONS_REASON_LABEL,
  type ClaimDecision,
  type ClaimStatus,
  type ClaimStatusInput,
  type ClaimTerminationBasis,
  type CloseNegotiationsInput,
  type InsuranceClaim,
  type InsuranceClaimDetail,
  type InsurancePolicy,
  type NegotiationsClosedReason,
} from "../../lib/insuranceApi";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unbekannter Fehler";
}

/** Die Staende, die der Nutzer selbst setzt. `draft` fehlt: ein Vorgang geht
 *  nicht in den Entwurf zurueck. */
const SELECTABLE_STATUSES: ClaimStatus[] = [
  "reported",
  "in_review",
  "info_requested",
  "decided",
  "payment_expected",
  "paid",
  "closed",
  "withdrawn",
];

/**
 * Welche Gruende zu welcher Norm passen.
 *
 * Paragraph 92 VVG knuepft an den Abschluss der VERHANDLUNGEN, Paragraph 111
 * VVG an Anerkennung, Ablehnung oder Rechtskraft. Eine gemeinsame Liste haette
 * an der Haftpflichtpolice "vollstaendige Zahlung" angeboten - einen Grund,
 * den diese Norm nicht kennt.
 */
const REASONS_BY_BASIS: Record<ClaimTerminationBasis, NegotiationsClosedReason[]> = {
  vvg_92: ["full_payment", "settlement_letter", "final_rejection", "withdrawal", "user_confirmed"],
  vvg_111: [
    "liability_acknowledged",
    "liability_refused",
    "judgment_final",
    "final_rejection",
    "user_confirmed",
  ],
  contract: [
    "full_payment",
    "settlement_letter",
    "final_rejection",
    "liability_acknowledged",
    "liability_refused",
    "judgment_final",
    "withdrawal",
    "user_confirmed",
  ],
  none: [],
};

export interface ClaimDetailModalProps {
  claimId: string;
  policy: InsurancePolicy;
  canEdit: boolean;
  onClose: () => void;
  today?: Date;
}

export function ClaimDetailModal({
  claimId,
  policy,
  canEdit,
  onClose,
  today,
}: ClaimDetailModalProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();

  const token = accessToken as string;
  const householdId = selectedHouseholdId as string;
  const enabled = Boolean(accessToken && selectedHouseholdId);

  const claimQuery = useQuery({
    queryKey: insuranceClaimQueryKey(householdId, claimId),
    queryFn: () => getInsuranceClaim(token, householdId, claimId),
    enabled,
  });

  const status = useMutation({
    mutationFn: (input: ClaimStatusInput) =>
      setInsuranceClaimStatus(token, householdId, claimId, input),
    onSuccess: () => invalidateInsuranceQueries(queryClient, householdId),
  });

  const closeNegotiations = useMutation({
    mutationFn: (input: CloseNegotiationsInput) =>
      closeClaimNegotiations(token, householdId, claimId, input),
    onSuccess: () => invalidateInsuranceQueries(queryClient, householdId),
  });

  const claim = claimQuery.data ?? null;
  const actionError = status.error ?? closeNegotiations.error;
  const busy = status.isPending || closeNegotiations.isPending;

  return (
    <Modal
      title={claim ? claim.title : "Vorgang"}
      size="wide"
      onClose={onClose}
      className="insurance-detail-modal"
    >
      <div className="insurance-detail">
        {claimQuery.isLoading && <p className="card-body">Der Vorgang wird geladen …</p>}
        {claimQuery.error && (
          <p className="insurance-error" role="alert">
            {errorMessage(claimQuery.error)}
          </p>
        )}

        {claim && (
          <>
            <ClaimTerminationBand claim={claim} policy={policy} />

            {actionError && (
              <p className="insurance-error" role="alert">
                {errorMessage(actionError)}
              </p>
            )}

            <section className="insurance-detail-section" aria-labelledby="insurance-claim-stages">
              <h3 id="insurance-claim-stages">Stand des Vorgangs</h3>
              <ClaimTimeline claim={claim} />
            </section>

            <section className="insurance-detail-section" aria-labelledby="insurance-claim-facts">
              <h3 id="insurance-claim-facts">Der Vorgang</h3>
              <dl className="insurance-kv">
                <Row label="Schadentag" value={formatDate(claim.occurred_on)} />
                <Row
                  label="Gemeldet am"
                  value={claim.reported_on ? formatDate(claim.reported_on) : "nicht erfasst"}
                />
                <Row label="Vorgangsnummer" value={claim.claim_number ?? "nicht erfasst"} />
                <Row label="Police" value={`${policy.line_label} · ${policy.insurer_name}`} />
                {claim.location && <Row label="Ort" value={claim.location} />}
                {claim.police_reference && (
                  <Row label="Polizeiliches Aktenzeichen" value={claim.police_reference} />
                )}
                <Row label="Gefordert" value={formatMinorUnitsAsEuro(claim.claimed_minor)} />
                <Row
                  label="Anerkannt"
                  value={
                    claim.approved_minor === null
                      ? "noch keine Entscheidung"
                      : formatMinorUnitsAsEuro(claim.approved_minor)
                  }
                  origin={
                    claim.decision ? CLAIM_DECISION_LABEL[claim.decision] : "Entscheidung offen"
                  }
                />
                <Row
                  label="Selbstbeteiligung"
                  value={formatMinorUnitsAsEuro(claim.deductible_minor)}
                />
                <Row
                  label="Reguliert"
                  value={formatMinorUnitsAsEuro(claim.settled_minor)}
                  origin={
                    claim.paid_to_third_party
                      ? "einschließlich Direktzahlung an eine dritte Stelle"
                      : null
                  }
                />
                {claim.reduction_reason && (
                  <Row label="Grund der Kürzung" value={claim.reduction_reason} />
                )}
                {claim.next_action && (
                  <Row
                    label="Nächste Handlung"
                    value={claim.next_action}
                    origin={
                      claim.next_action_due ? `fällig ${formatDate(claim.next_action_due)}` : null
                    }
                  />
                )}
                {claim.description && <Row label="Beschreibung" value={claim.description} />}
              </dl>
            </section>

            {canEdit && (
              <StatusForm
                claim={claim}
                today={today}
                busy={busy}
                onSubmit={(input) => status.mutate(input)}
              />
            )}

            {canEdit && claim.termination_basis !== "none" && (
              <NegotiationsForm
                claim={claim}
                today={today}
                busy={busy}
                onSubmit={(input) => closeNegotiations.mutate(input)}
              />
            )}

            <ClaimPayments claim={claim} canEdit={canEdit} today={today} />
          </>
        )}
      </div>
    </Modal>
  );
}

function Row({ label, value, origin }: { label: string; value: string; origin?: string | null }) {
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

// ---------------------------------------------------------------------------
// Zeitstrahl
// ---------------------------------------------------------------------------

/**
 * Vier Stufen, immer alle vier.
 *
 * Eine erreichte Stufe traegt ihr Datum, eine offene das Wort "steht aus", eine
 * nach der Ruecknahme das Wort "entfaellt". Die Stufen kommen aus
 * `claimStages` und damit aus dem Journal - nicht aus dem Status allein, denn
 * der sagt nur, wo der Vorgang JETZT steht, und nicht, wann er dorthin kam.
 */
function ClaimTimeline({ claim }: { claim: InsuranceClaimDetail }) {
  const { formatDate } = useFormatDate();
  const stages = claimStages(claim, claim.events, claim.payments);

  return (
    <>
      <ol className="insurance-claim-stages">
        {stages.map((stage) => (
          <li
            key={stage.key}
            className={`insurance-claim-stage insurance-claim-stage--${stage.state}`}
          >
            <span className="insurance-claim-stage-dot" aria-hidden="true" />
            <div className="insurance-claim-stage-body">
              <p className="insurance-claim-stage-label">{stage.label}</p>
              <p className="insurance-claim-stage-date">
                {stage.state === "cancelled"
                  ? stage.on
                    ? `entfällt – ${stage.cancelReason} am ${formatDate(stage.on)}`
                    : `entfällt – ${stage.cancelReason}`
                  : stage.on
                    ? formatDate(stage.on)
                    : stage.state === "done"
                      ? "erreicht, Datum nicht erfasst"
                      : "steht aus"}
              </p>
              {stage.note && <p className="insurance-claim-stage-note">{stage.note}</p>}
            </div>
          </li>
        ))}
      </ol>
      <p className="insurance-note">
        Aktueller Stand: {CLAIM_STATUS_LABEL[claim.status]}
        {claim.decision ? ` · ${CLAIM_DECISION_LABEL[claim.decision]}` : ""}. Die Stufen stammen aus
        dem Journal des Vorgangs; es wird geschrieben, nicht geändert.
      </p>
    </>
  );
}

// ---------------------------------------------------------------------------
// Sonderkuendigungsband nach dem Schaden
// ---------------------------------------------------------------------------

export interface ClaimTerminationBandProps {
  claim: InsuranceClaim;
  policy: InsurancePolicy;
}

/**
 * Der Streifen ueber dem Vorgang - in allen vier Zustaenden, auch im
 * verfallenen.
 *
 * `expired` wird ausdruecklich nicht ausgeblendet: ein verfallenes Recht wird
 * nicht stillschweigend geleert, der Nutzer soll sehen, dass es bestand (Plan
 * 4.8). Und `open_undetermined` bekommt statt eines leeren Fristfeldes den
 * Satz, dass der Anker fehlt - das ist die Aussage, um die es geht.
 *
 * Die Tageszahl kommt ausschliesslich aus `special_termination.days_left`.
 * Fehlt das Rechenergebnis, steht das Datum ohne Countdown da: eine zweite
 * Rechnung im Browser ergaebe frueher oder spaeter eine andere Zahl als die,
 * mit der der Push arbeitet.
 */
export function ClaimTerminationBand({ claim, policy }: ClaimTerminationBandProps) {
  const { formatDate } = useFormatDate();
  const state = claim.special_termination_state;
  // Nicht `window` nennen: der Name gehoert dem Browser, und ein
  // verschattetes `window` ist genau die Art Fehler, die erst im dritten
  // Bearbeitungsschritt auffaellt.
  const resolved = claim.special_termination ?? null;
  const basis = claim.termination_basis;

  if (basis === "none") {
    return (
      <p className="insurance-note" role="note">
        Für die Sparte „{policy.line_label}“ ist kein Sonderkündigungsrecht nach einem Schaden
        hinterlegt. Es entsteht deshalb auch keine Frist – ein Fenster, das es nicht gibt, wird hier
        nicht angedeutet.
      </p>
    );
  }

  if (state === "none") {
    return (
      <p className="insurance-note" role="note">
        Solange der Vorgang nicht gemeldet ist, weist das Werkzeug kein Sonderkündigungsrecht aus.
        Es entsteht mit der Meldung – und zwar rückwirkend zum Schadentag.
      </p>
    );
  }

  const legalOn = resolved?.deadline_legal_on ?? claim.special_termination_until;
  const actionOn = resolved?.recommended_action_on ?? null;
  const daysLeft = resolved?.days_left ?? null;
  const effects = claim.termination_effect_options.map(terminationEffectLabel).join(" oder ");

  return (
    <aside className={`insurance-special-band insurance-special-band--${state}`} role="note">
      <div className="insurance-special-band-main">
        <p className="insurance-special-band-title">
          {state === "open_undetermined" && "Sonderkündigungsrecht besteht"}
          {state === "open_until" && "Sonderkündigungsrecht läuft"}
          {state === "expired" && "Sonderkündigungsrecht ist verfallen"}
        </p>
        <p className="insurance-special-band-reason">
          {policy.line_label} · {CLAIM_TERMINATION_BASIS_LABEL[basis]}
          {resolved?.basis
            ? ` · ${resolved.basis} (${ANCHOR_BASIS_PHRASE[resolved.basis_kind]})`
            : ""}
        </p>

        {state === "open_undetermined" && (
          <>
            <p className="insurance-special-band-reason">
              Sonderkündigungsrecht besteht seit dem Schadentag ({formatDate(claim.occurred_on)}).
              Es endet einen Monat nach {CLAIM_TERMINATION_ANCHOR_TEXT[basis]} – dieses Datum steht
              noch nicht fest.
            </p>
            <dl className="insurance-special-band-dates">
              <div>
                <dt>Fristende</dt>
                {/* Kein leeres Feld: der Satz sagt, was fehlt und was daraus
                    folgt. */}
                <dd className="insurance-special-band-primary insurance-special-band-missing">
                  {CLAIM_ANCHOR_MISSING_TEXT}
                </dd>
              </div>
              <div>
                <dt>Kündigung wirkt</dt>
                <dd>{effects || "nach Ihren Versicherungsbedingungen"}</dd>
              </div>
            </dl>
            <p className="insurance-special-band-note">
              Das Werkzeug erklärt dieses Recht nicht von sich aus für verfallen. Sobald die
              Verhandlungen abgeschlossen sind, tragen Sie den Tag unten ein – erst dann entsteht
              ein Enddatum, und es wird gerechnet, nicht geschätzt.
            </p>
          </>
        )}

        {state === "open_until" && (
          <>
            <dl className="insurance-special-band-dates">
              <div>
                <dt>Empfohlen absenden bis</dt>
                <dd className="insurance-special-band-primary">
                  {actionOn
                    ? formatDate(actionOn)
                    : legalOn
                      ? formatDate(legalOn)
                      : "nicht berechenbar"}
                  {daysLeft !== null && (
                    <span
                      className={`insurance-countdown insurance-countdown--${countdownTone(daysLeft)}`}
                    >
                      {countdownLabel(daysLeft)}
                    </span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Rechtliches Fristende</dt>
                <dd>{legalOn ? formatDate(legalOn) : CLAIM_ANCHOR_MISSING_TEXT}</dd>
              </div>
              <div>
                <dt>Kündigung wirkt</dt>
                <dd>{effects || "nach Ihren Versicherungsbedingungen"}</dd>
              </div>
            </dl>
            <p className="insurance-special-band-note">
              <AnchorSentence claim={claim} />
              {resolved?.safe_service_on
                ? ` Der letzte sichere Zustelltag ist ${formatDate(resolved.safe_service_on)} – eine Kündigung wirkt erst mit Zugang beim Versicherer.`
                : ""}
            </p>
          </>
        )}

        {state === "expired" && (
          <>
            <dl className="insurance-special-band-dates">
              <div>
                <dt>Fenster endete am</dt>
                <dd className="insurance-special-band-primary">
                  {legalOn ? formatDate(legalOn) : "Datum nicht erfasst"}
                </dd>
              </div>
            </dl>
            <p className="insurance-special-band-note">
              <AnchorSentence claim={claim} /> Das Fenster bleibt sichtbar, damit erkennbar bleibt,
              dass dieses Recht bestand.
            </p>
          </>
        )}
      </div>
    </aside>
  );
}

/** Woher der Anker stammt - in einem Satz, der die Herkunft nennt. Ein Datum
 *  ohne sichtbare Herkunft ist in diesem Feld eine Behauptung. */
function AnchorSentence({ claim }: { claim: InsuranceClaim }) {
  const { formatDate } = useFormatDate();
  if (!claim.negotiations_closed_on) {
    return <>{CLAIM_ANCHOR_MISSING_TEXT}.</>;
  }
  const reason = claim.negotiations_closed_reason;
  const certainty = claim.negotiations_closed_certainty;
  const anchorDate = formatDate(claim.negotiations_closed_on);

  if (certainty === "belegt") {
    return (
      <>
        Gerechnet ab dem {anchorDate}
        {reason ? ` (${NEGOTIATIONS_REASON_LABEL[reason]})` : ""} – belegt durch einen zugeordneten
        Kontoumsatz, der unten bei den Zahlungen steht.
      </>
    );
  }
  if (certainty === "geschaetzt") {
    return (
      <>
        Voraussichtlich, gerechnet ab dem {anchorDate}
        {reason ? ` (${NEGOTIATIONS_REASON_LABEL[reason]})` : ""} – dieser Anker wurde von Ihnen
        gesetzt und ist nicht belegt.
      </>
    );
  }
  return (
    <>
      Gerechnet ab dem {anchorDate}
      {reason ? ` (${NEGOTIATIONS_REASON_LABEL[reason]})` : ""} ·{" "}
      {NEGOTIATIONS_CERTAINTY_LABEL[certainty]}.
    </>
  );
}

// ---------------------------------------------------------------------------
// Aktionen
// ---------------------------------------------------------------------------

/**
 * Statuswechsel als Einfeldaktion.
 *
 * Er setzt KEIN Fristdatum - der Server schreibt das Journal, und beim
 * Uebergang nach `reported` entsteht `open_undetermined`. Genau deshalb steht
 * das Ankerdatum in einem eigenen Formular darunter und nicht als weiteres
 * Feld hier: die beiden Handlungen haben verschiedene Folgen, und eine
 * gemeinsame Maske haette sie vermischt.
 */
function StatusForm({
  claim,
  today,
  busy,
  onSubmit,
}: {
  claim: InsuranceClaim;
  today?: Date;
  busy: boolean;
  onSubmit: (input: ClaimStatusInput) => void;
}) {
  const todayIso = toIsoDateString(today ?? new Date());
  const [next, setNext] = useState<ClaimStatus | "">("");
  const [on, setOn] = useState(todayIso);
  const [decision, setDecision] = useState<ClaimDecision | "">(claim.decision ?? "");
  const [reduction, setReduction] = useState(claim.reduction_reason ?? "");
  const [problems, setProblems] = useState<string[]>([]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    if (!next) found.push("Wählen Sie den neuen Stand.");
    if (!on) found.push("Ohne Datum lässt sich der Wechsel nicht einordnen.");
    if (on && on < claim.occurred_on) {
      found.push("Ein Stand kann nicht vor dem Schadentag erreicht worden sein.");
    }
    if (next === "decided" && !decision) {
      found.push("Zu einer Entscheidung gehört, wie sie ausgefallen ist.");
    }
    if (next === "decided" && decision === "accepted_partial" && !reduction.trim()) {
      found.push(
        "Bei einer Kürzung ist der Grund Pflicht – ohne ihn ist später nicht nachvollziehbar, worüber verhandelt wurde.",
      );
    }
    setProblems(found);
    if (found.length > 0) return;

    // Nur mitschicken, was dieser Wechsel wirklich aussagt. Ein `decision:
    // null` bei jedem anderen Wechsel loeschte am Server eine bereits
    // getroffene Entscheidung - der Vorgang verloere seine Historie, weil
    // jemand ihn auf "ausgezahlt" gesetzt hat.
    const input: ClaimStatusInput = { status: next as ClaimStatus, occurred_on: on };
    if (next === "decided") {
      input.decision = decision as ClaimDecision;
      input.decided_on = on;
      if (decision === "accepted_partial") {
        input.reduction_reason = reduction.trim();
      }
    }
    onSubmit(input);
  };

  return (
    <form className="insurance-action-card" onSubmit={handleSubmit}>
      <h4 className="insurance-panel-subtitle">Stand erfassen</h4>
      <div className="insurance-action-row">
        <label className="insurance-field">
          <span>Neuer Stand</span>
          <select
            className="form-input"
            value={next}
            onChange={(event) => setNext(event.target.value as ClaimStatus | "")}
          >
            <option value="">Bitte wählen</option>
            {SELECTABLE_STATUSES.filter((entry) => entry !== claim.status).map((entry) => (
              <option key={entry} value={entry}>
                {CLAIM_STATUS_LABEL[entry]}
              </option>
            ))}
          </select>
        </label>
        <label className="insurance-field insurance-field--anchor">
          <span>An welchem Tag</span>
          <input
            type="date"
            className="form-input"
            value={on}
            onChange={(event) => setOn(event.target.value)}
          />
        </label>
        {next === "decided" && (
          <label className="insurance-field">
            <span>Wie ist entschieden worden</span>
            <select
              className="form-input"
              value={decision}
              onChange={(event) => setDecision(event.target.value as ClaimDecision | "")}
            >
              <option value="">Bitte wählen</option>
              <option value="accepted_full">{CLAIM_DECISION_LABEL.accepted_full}</option>
              <option value="accepted_partial">{CLAIM_DECISION_LABEL.accepted_partial}</option>
              <option value="rejected">{CLAIM_DECISION_LABEL.rejected}</option>
            </select>
          </label>
        )}
        {next === "decided" && decision === "accepted_partial" && (
          <label className="insurance-field">
            <span>Grund der Kürzung</span>
            <input
              type="text"
              className="form-input"
              value={reduction}
              onChange={(event) => setReduction(event.target.value)}
            />
          </label>
        )}
      </div>

      <p className="insurance-form-help">
        Der Stand setzt kein Fristdatum. Mit der Meldung entsteht das Sonderkündigungsrecht, sein
        Ende aber erst mit dem Ankerdatum darunter – die Entscheidung des Versicherers ist dafür
        ausdrücklich nicht maßgeblich.
      </p>

      {problems.length > 0 && (
        <ul className="insurance-error" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}

      <div className="insurance-action-row">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Wird gespeichert …" : "Stand speichern"}
        </button>
      </div>
    </form>
  );
}

/**
 * Der Fristanker - vorgeschlagen, nie gesetzt.
 *
 * Drei Faelle, und der dritte ist der wichtige: bei `accepted_partial` gibt es
 * KEINEN Vorschlag, weil dort die Entscheidung der Anfang der Verhandlung ist.
 * Dann steht hier ein Satz statt eines vorbelegten Datums, und der Grund
 * heisst `user_confirmed` - eine Bestaetigung des Nutzers, keine Ableitung des
 * Werkzeugs.
 *
 * Die Buchungskennung des Vorschlags wandert nur mit, solange Datum und Grund
 * unveraendert sind: "belegt" heisst, dass genau dieser Kontoumsatz genau
 * dieses Datum traegt. Wer das Datum aendert, hat keinen Beleg mehr, sondern
 * eine Einschaetzung.
 */
function NegotiationsForm({
  claim,
  today,
  busy,
  onSubmit,
}: {
  claim: InsuranceClaim;
  today?: Date;
  busy: boolean;
  onSubmit: (input: CloseNegotiationsInput) => void;
}) {
  const { formatDate } = useFormatDate();
  const todayIso = toIsoDateString(today ?? new Date());
  const suggestion = claim.negotiations_suggestion ?? null;
  const reasons = REASONS_BY_BASIS[claim.termination_basis];

  const [on, setOn] = useState(
    claim.negotiations_closed_on ?? suggestion?.negotiations_closed_on ?? todayIso,
  );
  const [reason, setReason] = useState<NegotiationsClosedReason | "">(
    claim.negotiations_closed_reason ?? suggestion?.reason ?? "",
  );
  const [problems, setProblems] = useState<string[]>([]);

  const unchangedFromSuggestion =
    suggestion !== null &&
    on === suggestion.negotiations_closed_on &&
    reason === suggestion.reason &&
    suggestion.transaction_id !== null;

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    if (!on) found.push("Ohne Datum entsteht kein Fristende.");
    if (!reason) found.push("Zu einem Ankerdatum gehört sein Grund.");
    if (on && on < claim.occurred_on) {
      found.push("Die Verhandlungen können nicht vor dem Schadentag abgeschlossen worden sein.");
    }
    setProblems(found);
    if (found.length > 0) return;

    onSubmit({
      negotiations_closed_on: on,
      reason: reason as NegotiationsClosedReason,
      transaction_id: unchangedFromSuggestion && suggestion ? suggestion.transaction_id : null,
    });
  };

  return (
    <form className="insurance-action-card" onSubmit={handleSubmit}>
      <h4 className="insurance-panel-subtitle">Abschluss der Verhandlungen</h4>

      {suggestion ? (
        <p className="insurance-form-help">
          Vorschlag des Dienstes: {formatDate(suggestion.negotiations_closed_on)} ·{" "}
          {NEGOTIATIONS_REASON_LABEL[suggestion.reason]} ·{" "}
          {NEGOTIATIONS_CERTAINTY_LABEL[suggestion.certainty]}
          {suggestion.hint ? ` – ${suggestion.hint}` : ""}. Er ist vorbelegt und wird erst mit dem
          Speichern zum Anker.
        </p>
      ) : claim.decision === "accepted_partial" ? (
        <p className="insurance-form-help">
          Bei einer teilweisen Regulierung schlägt das Werkzeug bewusst kein Datum vor: die
          Entscheidung ist hier der Anfang der Verhandlung und nicht ihr Ende. Tragen Sie den Tag
          ein, an dem die Verhandlung für Sie abgeschlossen war – er gilt dann als von Ihnen
          gesetzt, nicht als belegt.
        </p>
      ) : (
        <p className="insurance-form-help">
          Solange kein Ankerdatum feststeht, bleibt das Recht offen und ohne Ende. Die Frist endet
          einen Monat nach {CLAIM_TERMINATION_ANCHOR_TEXT[claim.termination_basis]} – nicht nach der
          Entscheidung des Versicherers, die früher liegen kann.
        </p>
      )}

      <div className="insurance-action-row">
        <label className="insurance-field insurance-field--anchor">
          <span>Abgeschlossen am</span>
          <input
            type="date"
            className="form-input"
            value={on}
            onChange={(event) => setOn(event.target.value)}
          />
        </label>
        <label className="insurance-field">
          <span>Woraus ergibt sich das</span>
          <select
            className="form-input"
            value={reason}
            onChange={(event) => setReason(event.target.value as NegotiationsClosedReason | "")}
          >
            <option value="">Bitte wählen</option>
            {reasons.map((entry) => (
              <option key={entry} value={entry}>
                {NEGOTIATIONS_REASON_LABEL[entry]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <p className="insurance-form-help">
        {unchangedFromSuggestion
          ? "Der zugeordnete Kontoumsatz wandert mit: das Ankerdatum wird dadurch belegt."
          : "Ohne zugeordneten Kontoumsatz zu diesem Datum bleibt das Ankerdatum eine Angabe von Ihnen – das Werkzeug weist es entsprechend aus."}
      </p>

      {problems.length > 0 && (
        <ul className="insurance-error" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}

      <div className="insurance-action-row">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Wird gespeichert …" : "Abschluss eintragen"}
        </button>
      </div>
    </form>
  );
}
