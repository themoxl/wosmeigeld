/**
 * Zahlungen eines Schadenvorgangs (docs/VERSICHERUNGEN-PLAN.md, 4.8; P5).
 *
 * **Derselbe Weg wie im Bankabgleich der Erstattungen, und zwar absichtlich.**
 * `BankReconciliationPanel` loest dieselbe Aufgabe - Geld auf dem Konto einem
 * Vorgang zuordnen - und hat dafuer eine Form gefunden, die zwei Dinge kann:
 * eine Sammelueberweisung deckt mehrere Vorgaenge, und jede Zuordnung ist
 * zuruecknehmbar. Ein zweiter Zuordnungsmechanismus haette beides noch einmal
 * loesen muessen und waere frueher oder spaeter anders ausgefallen (Leitsatz 1).
 *
 * **Drei Wege, wie Geld zu einem Schaden gehoert - und nur einer davon ist
 * eine Ueberweisung an uns:**
 *
 * 1. **Auszahlung an uns.** Sie steht auf dem Konto, wird aus den Kandidaten
 *    gewaehlt und traegt eine Buchungskennung. Nur sie kann ein Ankerdatum
 *    belegen (`negotiations_closed_certainty = 'belegt'`).
 * 2. **Direktzahlung an die Werkstatt oder den Arzt.** Sie hat GAR KEINE
 *    Buchung - das Geld fliesst am Haushalt vorbei. `transaction_id` bleibt
 *    null. Ohne diesen Fall stuende ein vollstaendig regulierter Kaskoschaden
 *    als unbezahlt da, nur weil der Versicherer direkt an die Werkstatt
 *    ueberwiesen hat.
 * 3. **Selbstbeteiligung.** Sie geht in die andere Richtung: wir zahlen. Sie
 *    ist keine gekuerzte Entschaedigung, sondern ein eigener Betrag - deshalb
 *    `direction = 'outbound'` und nicht ein Minuszeichen an der Entschaedigung.
 *
 * **Was diese Ansicht nicht tut:** sie rechnet den regulierten Betrag nicht
 * aus den Zeilen zusammen. `settled_minor` steht am Vorgang und kommt vom
 * Server; eine zweite Summe im Browser waere die zweite Wahrheit, die dieses
 * Modul ueberall vermeidet.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { useFormatDate } from "../../hooks/useProfile";
import { toIsoDateString } from "../../lib/dates";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import {
  addClaimPayment,
  claimOutstandingMinor,
  insuranceClaimCandidatesQueryKey,
  invalidateInsuranceQueries,
  listClaimTransactionCandidates,
  removeClaimPayment,
  CLAIM_PAYMENT_DIRECTION_LABEL,
  CLAIM_PAYMENT_KIND_LABEL,
  type ClaimPayment,
  type ClaimPaymentDirection,
  type ClaimPaymentInput,
  type ClaimPaymentKind,
  type ClaimTransactionCandidate,
  type InsuranceClaimDetail,
} from "../../lib/insuranceApi";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unbekannter Fehler";
}

function minorToInput(minor: number): string {
  return (minor / 100).toFixed(2).replace(".", ",");
}

/**
 * Die Zahlungsarten mit ihrer Richtung.
 *
 * Die Richtung wird NICHT aus dem Vorzeichen der Buchung geraten: eine
 * Rueckforderung und eine Selbstbeteiligung sehen auf dem Konto gleich aus und
 * bedeuten Verschiedenes. Sie haengt deshalb an der Art, die der Nutzer waehlt.
 */
const PAYMENT_CHOICES: {
  kind: ClaimPaymentKind;
  direction: ClaimPaymentDirection;
  label: string;
}[] = [
  { kind: "settlement", direction: "inbound", label: "Entschädigung an uns" },
  { kind: "advance", direction: "inbound", label: "Abschlag an uns" },
  { kind: "deductible", direction: "outbound", label: "Selbstbeteiligung (wir zahlen)" },
  { kind: "invoice", direction: "outbound", label: "Von uns bezahlte Rechnung" },
  { kind: "recovery", direction: "outbound", label: "Rückforderung des Versicherers" },
];

function directionOf(kind: ClaimPaymentKind): ClaimPaymentDirection {
  return PAYMENT_CHOICES.find((choice) => choice.kind === kind)?.direction ?? "inbound";
}

export interface ClaimPaymentsProps {
  claim: InsuranceClaimDetail;
  canEdit: boolean;
  today?: Date;
}

export function ClaimPayments({ claim, canEdit, today }: ClaimPaymentsProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();

  const token = accessToken as string;
  const householdId = selectedHouseholdId as string;
  const enabled = Boolean(accessToken && selectedHouseholdId);

  // Nur fuer Schreibberechtigte: die Kandidatensuche ist die teuerste Abfrage
  // dieser Ansicht, und wer nichts zuordnen darf, bekommt die Liste ohnehin
  // nicht zu sehen.
  const candidatesQuery = useQuery({
    queryKey: insuranceClaimCandidatesQueryKey(householdId, claim.id),
    queryFn: () => listClaimTransactionCandidates(token, householdId, claim.id),
    enabled: enabled && canEdit,
    retry: false,
  });

  const add = useMutation({
    mutationFn: (input: ClaimPaymentInput) => addClaimPayment(token, householdId, claim.id, input),
    onSuccess: () => invalidateInsuranceQueries(queryClient, householdId),
  });

  const remove = useMutation({
    mutationFn: (paymentId: string) => removeClaimPayment(token, householdId, claim.id, paymentId),
    onSuccess: () => invalidateInsuranceQueries(queryClient, householdId),
  });

  const payments = claim.payments.slice().sort((a, b) => b.booked_on.localeCompare(a.booked_on));
  const candidates = candidatesQuery.data ?? [];
  const outstanding = claimOutstandingMinor(claim);
  const busy = add.isPending || remove.isPending;
  const actionError = add.error ?? remove.error;

  return (
    <section className="insurance-detail-section" aria-labelledby="insurance-claim-payments">
      <h3 id="insurance-claim-payments">Zahlungen</h3>

      <p className="card-body">
        {outstanding > 0
          ? `Nach den erfassten Zahlungen stehen noch ${formatMinorUnitsAsEuro(outstanding)} aus – gemessen an ${
              claim.approved_minor === null ? "der Forderung" : "dem anerkannten Betrag"
            }.`
          : "Aus den erfassten Zahlungen ergibt sich keine offene Forderung mehr."}{" "}
        Reguliert sind laut Vorgang {formatMinorUnitsAsEuro(claim.settled_minor)}.
      </p>

      {actionError && (
        <p className="insurance-error" role="alert">
          {errorMessage(actionError)}
        </p>
      )}

      {payments.length > 0 && (
        <div
          className="insurance-table-scroll"
          role="region"
          aria-label="Zugeordnete Zahlungen"
          tabIndex={0}
        >
          <table className="txn-table insurance-table">
            <thead>
              <tr>
                <th className="txn-th">Datum</th>
                <th className="txn-th">Art</th>
                <th className="txn-th">Betrag</th>
                <th className="txn-th">Buchung</th>
                <th className="txn-th">Notiz</th>
                {canEdit && <th className="txn-th">Aktion</th>}
              </tr>
            </thead>
            <tbody>
              {payments.map((payment) => (
                <PaymentRow
                  key={payment.id}
                  payment={payment}
                  canEdit={canEdit}
                  busy={busy}
                  formatDate={formatDate}
                  onRemove={() => remove.mutate(payment.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {payments.length === 0 && (
        <p className="insurance-empty-note">
          Zu diesem Vorgang ist noch keine Zahlung erfasst. Eine Auszahlung wird aus den
          Kontobewegungen zugeordnet; eine Direktzahlung an die Werkstatt hat keine eigene Buchung
          und wird von Hand erfasst.
        </p>
      )}

      {canEdit && (
        <>
          <h4 className="insurance-panel-subtitle">Passende Kontobewegungen</h4>
          {candidatesQuery.isLoading && (
            <p className="card-body">Kontobewegungen werden gesucht …</p>
          )}
          {candidatesQuery.error && (
            <p className="insurance-error" role="alert">
              {errorMessage(candidatesQuery.error)}
            </p>
          )}
          {!candidatesQuery.isLoading && !candidatesQuery.error && candidates.length === 0 && (
            <p className="insurance-empty-note">
              Keine offene Buchung gefunden, die zu diesem Vorgang passen könnte. Kommt die Zahlung
              erst noch, erscheint sie nach dem nächsten Kontoimport hier – gesucht wird ab dem
              Meldetag, über Betrag, Verwendungszweck und die Gegenseite des Versicherers.
            </p>
          )}
          {candidates.length > 0 && (
            <div
              className="insurance-table-scroll"
              role="region"
              aria-label="Vorgeschlagene Kontobewegungen"
              tabIndex={0}
            >
              <table className="txn-table insurance-table">
                <thead>
                  <tr>
                    <th className="txn-th">Datum</th>
                    <th className="txn-th">Buchung</th>
                    <th className="txn-th">Noch frei</th>
                    <th className="txn-th">Warum vorgeschlagen</th>
                    <th className="txn-th">Zuordnen</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((candidate) => (
                    <CandidateRow
                      key={candidate.transaction_id}
                      candidate={candidate}
                      outstandingMinor={outstanding}
                      busy={busy}
                      formatDate={formatDate}
                      onAllocate={(input) => add.mutate(input)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <ManualPaymentForm
            today={today}
            busy={busy}
            outstandingMinor={outstanding}
            onSubmit={(input) => add.mutate(input)}
          />
        </>
      )}
    </section>
  );
}

function PaymentRow({
  payment,
  canEdit,
  busy,
  formatDate,
  onRemove,
}: {
  payment: ClaimPayment;
  canEdit: boolean;
  busy: boolean;
  formatDate: (value: string) => string;
  onRemove: () => void;
}) {
  return (
    <tr>
      <td className="txn-td">{formatDate(payment.booked_on)}</td>
      <td className="txn-td">
        {CLAIM_PAYMENT_KIND_LABEL[payment.kind]}
        <span className="insurance-kv-origin">
          {CLAIM_PAYMENT_DIRECTION_LABEL[payment.direction]}
        </span>
      </td>
      <td className="txn-td">{formatMinorUnitsAsEuro(payment.amount_minor)}</td>
      <td className="txn-td">
        {payment.transaction_id ? (
          <>
            {payment.payee_label ?? "zugeordnete Buchung"}
            {payment.transaction_amount_minor !== null &&
              payment.transaction_amount_minor !== undefined && (
                <span className="insurance-kv-origin">
                  Buchung gesamt{" "}
                  {formatMinorUnitsAsEuro(Math.abs(payment.transaction_amount_minor))}
                </span>
              )}
          </>
        ) : (
          /* Kein Mangel, sondern der Fall selbst: das Geld ist nie ueber das
             Konto des Haushalts gelaufen. */
          <span className="insurance-claim-chip">ohne eigene Buchung</span>
        )}
      </td>
      <td className="txn-td">{payment.note ?? "—"}</td>
      {canEdit && (
        <td className="txn-td">
          <button type="button" className="btn-secondary" disabled={busy} onClick={onRemove}>
            Zuordnung entfernen
          </button>
        </td>
      )}
    </tr>
  );
}

/**
 * Eine vorgeschlagene Buchung mit ihrer Trefferbegruendung.
 *
 * Der Betrag ist mit dem kleineren von "noch frei" und "noch offen"
 * vorbelegt - mehr als offen ist waere eine Ueberzahlung, mehr als frei weist
 * der Server ohnehin ab. Der haeufige Fall ist damit ein Klick, der seltene
 * eine Korrektur.
 */
function CandidateRow({
  candidate,
  outstandingMinor,
  busy,
  formatDate,
  onAllocate,
}: {
  candidate: ClaimTransactionCandidate;
  outstandingMinor: number;
  busy: boolean;
  formatDate: (value: string) => string;
  onAllocate: (input: ClaimPaymentInput) => void;
}) {
  const suggestedMinor =
    outstandingMinor > 0
      ? Math.min(candidate.remaining_minor, outstandingMinor)
      : candidate.remaining_minor;
  const [amountInput, setAmountInput] = useState(() => minorToInput(suggestedMinor));
  const [lastSuggested, setLastSuggested] = useState(suggestedMinor);
  const [kind, setKind] = useState<ClaimPaymentKind>("settlement");

  // Beim Rendern nachziehen statt im Effect: schrumpft der freie Rest, weil
  // dieselbe Sammelueberweisung anderswo zugeordnet wurde, muss der Vorschlag
  // mitgehen - sonst stuende im Feld ein Betrag, den die Buchung nicht mehr
  // hergibt.
  if (lastSuggested !== suggestedMinor) {
    setLastSuggested(suggestedMinor);
    setAmountInput(minorToInput(suggestedMinor));
  }

  const parsed = parseAmountToMinorUnits(amountInput);
  const invalid = parsed === null || parsed <= 0 || parsed > candidate.remaining_minor;

  return (
    <tr>
      <td className="txn-td">{formatDate(candidate.booked_date)}</td>
      <td className="txn-td">{candidate.payee_label || candidate.note || "—"}</td>
      <td className="txn-td">{formatMinorUnitsAsEuro(candidate.remaining_minor)}</td>
      <td className="txn-td">
        {candidate.evidence.length > 0 ? (
          <ul className="insurance-evidence-list">
            {candidate.evidence.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        ) : (
          <span className="insurance-kv-origin">Betrag und Zeitraum passen ungefähr</span>
        )}
      </td>
      <td className="txn-td">
        <div className="insurance-action-row insurance-allocate-row">
          <label className="insurance-field">
            <span className="insurance-visually-hidden">Art der Zahlung</span>
            <select
              className="form-input"
              disabled={busy}
              value={kind}
              onChange={(event) => setKind(event.target.value as ClaimPaymentKind)}
            >
              {PAYMENT_CHOICES.map((choice) => (
                <option key={choice.kind} value={choice.kind}>
                  {choice.label}
                </option>
              ))}
            </select>
          </label>
          <label className="insurance-field">
            <span className="insurance-visually-hidden">
              Betrag für {candidate.payee_label ?? "Buchung"}
            </span>
            <input
              className="form-input"
              inputMode="decimal"
              size={8}
              disabled={busy}
              value={amountInput}
              onChange={(event) => setAmountInput(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="btn-primary"
            disabled={busy || invalid}
            onClick={() =>
              onAllocate({
                transaction_id: candidate.transaction_id,
                direction: directionOf(kind),
                kind,
                amount_minor: parsed as number,
                booked_on: candidate.booked_date,
              })
            }
          >
            Zuordnen
          </button>
        </div>
        {invalid && (
          <p className="insurance-error">
            Der Betrag muss über 0 liegen und darf die freien{" "}
            {formatMinorUnitsAsEuro(candidate.remaining_minor)} nicht überschreiten.
          </p>
        )}
      </td>
    </tr>
  );
}

/**
 * Zahlungen ohne eigene Buchung.
 *
 * Der Regelfall dahinter ist die Direktzahlung an die Werkstatt: der
 * Versicherer zahlt, der Schaden ist reguliert, und auf dem Konto des
 * Haushalts steht nichts. Genau dafuer laesst das Datenmodell
 * `transaction_id` null - und genau deshalb gibt es hier ein zweites Formular
 * neben der Kandidatenliste. Eine erfundene Buchung waere die Alternative
 * gewesen.
 */
function ManualPaymentForm({
  today,
  busy,
  outstandingMinor,
  onSubmit,
}: {
  today?: Date;
  busy: boolean;
  outstandingMinor: number;
  onSubmit: (input: ClaimPaymentInput) => void;
}) {
  const todayIso = toIsoDateString(today ?? new Date());
  const [kind, setKind] = useState<ClaimPaymentKind>("settlement");
  const suggested = outstandingMinor > 0 ? minorToInput(outstandingMinor) : "";
  const [amount, setAmount] = useState(suggested);
  const [lastOutstanding, setLastOutstanding] = useState(outstandingMinor);
  const [bookedOn, setBookedOn] = useState(todayIso);
  const [note, setNote] = useState("");
  const [problems, setProblems] = useState<string[]>([]);

  // Beim Rendern nachziehen statt im Effect - wie in `CandidateRow`. Ohne das
  // stuende nach einer erfassten Direktzahlung weiter der alte volle Rest im
  // Feld, und der zweite Klick buchte denselben Betrag ein zweites Mal. Das
  // Formular wird nach dem Absenden nicht neu gebaut.
  if (lastOutstanding !== outstandingMinor) {
    setLastOutstanding(outstandingMinor);
    setAmount(suggested);
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    const parsed = parseAmountToMinorUnits(amount);
    if (parsed === null || parsed <= 0) {
      found.push("Der Betrag muss über 0 liegen.");
    }
    if (!bookedOn) {
      found.push("Ohne Datum lässt sich die Zahlung nicht einordnen.");
    }
    setProblems(found);
    if (found.length > 0) return;

    onSubmit({
      transaction_id: null,
      direction: directionOf(kind),
      kind,
      amount_minor: parsed as number,
      booked_on: bookedOn,
      note: note.trim() ? note.trim() : null,
    });
    setNote("");
  };

  return (
    <form className="insurance-action-card" onSubmit={handleSubmit}>
      <h4 className="insurance-panel-subtitle">Zahlung ohne eigene Buchung erfassen</h4>
      <p className="insurance-form-help">
        Für Geld, das am Konto vorbeigelaufen ist: der Versicherer zahlt direkt an die Werkstatt
        oder den Dienstleister, oder die Selbstbeteiligung wurde bar beglichen. Diese Zeile trägt
        bewusst keine Buchungskennung – sie kann deshalb auch kein Ankerdatum belegen.
      </p>

      <div className="insurance-action-row">
        <label className="insurance-field">
          <span>Art</span>
          <select
            className="form-input"
            disabled={busy}
            value={kind}
            onChange={(event) => setKind(event.target.value as ClaimPaymentKind)}
          >
            {PAYMENT_CHOICES.map((choice) => (
              <option key={choice.kind} value={choice.kind}>
                {choice.label}
              </option>
            ))}
          </select>
        </label>
        <label className="insurance-field">
          <span>Betrag</span>
          <input
            type="text"
            inputMode="decimal"
            className="form-input"
            disabled={busy}
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </label>
        <label className="insurance-field insurance-field--anchor">
          <span>Am</span>
          <input
            type="date"
            className="form-input"
            disabled={busy}
            value={bookedOn}
            onChange={(event) => setBookedOn(event.target.value)}
          />
        </label>
        <label className="insurance-field">
          <span>Notiz (optional)</span>
          <input
            type="text"
            className="form-input"
            disabled={busy}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
      </div>

      {problems.length > 0 && (
        <ul className="insurance-error" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}

      <div className="insurance-action-row">
        <button type="submit" className="btn-secondary" disabled={busy}>
          {busy ? "Wird gespeichert …" : "Zahlung erfassen"}
        </button>
      </div>
    </form>
  );
}
