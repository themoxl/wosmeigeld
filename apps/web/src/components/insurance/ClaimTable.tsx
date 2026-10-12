/**
 * Register "Schäden" (docs/VERSICHERUNGEN-PLAN.md, 2.3 Punkt 7 und 4.8; P5).
 *
 * **Dieses Register modelliert kein Geld - aber ein Schaden hat Zahlungen.**
 * Die Tabelle nennt deshalb zwei Betraege nebeneinander: was gefordert wurde
 * und was tatsaechlich regliert ist. Ein einzelner "Schadenbetrag" koennte den
 * haeufigsten Fall - Kuerzung, Selbstbeteiligung, Abschlag plus Restzahlung -
 * gar nicht darstellen und saehe trotzdem wie eine Aussage aus.
 *
 * **Das Register holt seinen Bestand selbst.** Wie die vier P4-Register haengt
 * es an keiner Datenanbindung von aussen: `TabPanel` baut es erst, wenn es
 * gewaehlt ist, und dann laedt es die Vorgaenge dieser Police. Der Weg ueber
 * durchgereichte Eigenschaften haette die Vorgaenge jeder geoeffneten Police
 * geladen, auch wenn niemand sie ansieht.
 *
 * **Gesundheit steht hier nicht.** Ein Vorgang auf einer gesundheitsnahen
 * Police wird von einem Trigger abgewiesen (Leitsatz 2); das Register wird
 * deshalb an solchen Policen gar nicht erst registriert - `PolicyDetail`
 * entscheidet das. Sollte der Server dennoch mit
 * `insurance_claim_health_scoped` antworten, verweist das Formular auf die
 * Erstattungsseite statt den Rohtext des Fehlers zu zeigen.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { useFormatDate } from "../../hooks/useProfile";
import { toIsoDateString } from "../../lib/dates";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import {
  claimOutstandingMinor,
  createInsuranceClaim,
  insuranceClaimsQueryKey,
  invalidateInsuranceQueries,
  isHealthScopedClaimError,
  listInsuranceClaims,
  CLAIM_DECISION_LABEL,
  CLAIM_STATUS_LABEL,
  type ClaimCreateInput,
  type InsuranceClaim,
  type InsurancePolicy,
} from "../../lib/insuranceApi";
import { ClaimDetailModal } from "./ClaimDetailModal";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unbekannter Fehler";
}

/** Offene Vorgaenge zuerst, darin der juengste Schaden oben: wer dieses
 *  Register oeffnet, sucht in aller Regel den laufenden Fall. */
const CLOSED_STATUSES = new Set(["closed", "withdrawn"]);

function orderedClaims(claims: InsuranceClaim[]): InsuranceClaim[] {
  return claims.slice().sort((a, b) => {
    const aClosed = CLOSED_STATUSES.has(a.status) ? 1 : 0;
    const bClosed = CLOSED_STATUSES.has(b.status) ? 1 : 0;
    if (aClosed !== bClosed) return aClosed - bClosed;
    return b.occurred_on.localeCompare(a.occurred_on);
  });
}

/** Laeuft an diesem Vorgang ein Sonderkuendigungsfenster? Der Zustand kommt vom
 *  Server; die Liste vergleicht ausdruecklich keine Daten selbst. */
function terminationOpen(claim: InsuranceClaim): boolean {
  return (
    claim.special_termination_state === "open_undetermined" ||
    claim.special_termination_state === "open_until"
  );
}

export interface ClaimTableProps {
  policy: InsurancePolicy;
  canEdit: boolean;
  /** Ankertag fuer die Vorbelegung des Schadentages. Als Eigenschaft, damit
   *  das Register pruefbar bleibt. */
  today?: Date;
}

export function ClaimTable({ policy, canEdit, today }: ClaimTableProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();

  const token = accessToken as string;
  const householdId = selectedHouseholdId as string;
  const enabled = Boolean(accessToken && selectedHouseholdId);

  const [openClaimId, setOpenClaimId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const claimsQuery = useQuery({
    queryKey: insuranceClaimsQueryKey(householdId, policy.id),
    queryFn: () => listInsuranceClaims(token, householdId, policy.id),
    enabled,
  });

  const create = useMutation({
    mutationFn: (input: ClaimCreateInput) => createInsuranceClaim(token, householdId, input),
    onSuccess: async (claim) => {
      await invalidateInsuranceQueries(queryClient, householdId);
      setFormOpen(false);
      // Direkt in den Vorgang: der naechste Schritt ist immer derselbe -
      // melden, Stand erfassen, Zahlungen zuordnen.
      setOpenClaimId(claim.id);
    },
  });

  const claims = orderedClaims(claimsQuery.data ?? []);
  const openCount = claims.filter((claim) => !CLOSED_STATUSES.has(claim.status)).length;

  return (
    <section className="insurance-panel" aria-labelledby="insurance-claims-title">
      <h3 id="insurance-claims-title">Schäden und Vorgänge</h3>
      <p className="card-body">
        Was passiert ist, was gefordert wurde und was davon reguliert ist. Zahlungen hängen an den
        Buchungen des Kontos – dieses Register führt keinen zweiten Geldbestand.
      </p>

      {claimsQuery.error && (
        <p className="insurance-error" role="alert">
          {errorMessage(claimsQuery.error)}
        </p>
      )}
      {claimsQuery.isLoading && <p className="card-body">Die Vorgänge werden geladen …</p>}

      {!claimsQuery.isLoading && !claimsQuery.error && claims.length === 0 && (
        <p className="insurance-empty-note">
          An dieser Police ist kein Vorgang erfasst. Ein Vorgang lohnt sich ab dem Tag der Meldung:
          an ihm hängen die Rückmeldefrist, das Sonderkündigungsrecht nach dem Schaden und die
          Zuordnung der Zahlungen.
        </p>
      )}

      {claims.length > 0 && (
        <>
          <div
            className="insurance-table-scroll"
            role="region"
            aria-label="Vorgänge dieser Police"
            tabIndex={0}
          >
            <table className="txn-table insurance-table">
              <thead>
                <tr>
                  <th className="txn-th">Schadentag</th>
                  <th className="txn-th">Vorgang</th>
                  <th className="txn-th">Nummer</th>
                  <th className="txn-th">Stand</th>
                  <th className="txn-th">Gefordert</th>
                  <th className="txn-th">Reguliert</th>
                  <th className="txn-th">Aktion</th>
                </tr>
              </thead>
              <tbody>
                {claims.map((claim) => {
                  const outstanding = claimOutstandingMinor(claim);
                  return (
                    <tr
                      key={claim.id}
                      className={openClaimId === claim.id ? "insurance-row--selected" : undefined}
                    >
                      <td className="txn-td">{formatDate(claim.occurred_on)}</td>
                      <td className="txn-td">
                        <span className="insurance-entity-title">{claim.title}</span>
                        {claim.location && (
                          <span className="insurance-kv-origin">{claim.location}</span>
                        )}
                      </td>
                      <td className="txn-td insurance-policy-number">
                        {claim.claim_number ?? "nicht erfasst"}
                      </td>
                      <td className="txn-td">
                        <span className="insurance-claim-status">
                          {CLAIM_STATUS_LABEL[claim.status]}
                        </span>
                        {claim.decision && (
                          <span className="insurance-kv-origin">
                            {CLAIM_DECISION_LABEL[claim.decision]}
                          </span>
                        )}
                        {/* Das laufende Fenster steht schon in der Liste: wer
                            es erst im Vorgang saehe, fande es womoeglich nach
                            seinem Ablauf. */}
                        {terminationOpen(claim) && (
                          <span className="insurance-claim-chip">Sonderkündigung offen</span>
                        )}
                      </td>
                      <td className="txn-td">{formatMinorUnitsAsEuro(claim.claimed_minor)}</td>
                      <td className="txn-td">
                        {formatMinorUnitsAsEuro(claim.settled_minor)}
                        {outstanding > 0 && claim.status !== "withdrawn" && (
                          <span className="insurance-kv-origin">
                            offen {formatMinorUnitsAsEuro(outstanding)}
                          </span>
                        )}
                      </td>
                      <td className="txn-td">
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => setOpenClaimId(claim.id)}
                        >
                          Vorgang öffnen
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="insurance-note">
            {openCount === 0
              ? "Kein Vorgang ist mehr offen."
              : `${openCount} ${openCount === 1 ? "Vorgang ist" : "Vorgänge sind"} noch offen. Der Nachtlauf meldet einen Vorgang ohne Rückmeldung nach 30 Tagen – er ändert ihn nicht.`}
          </p>
        </>
      )}

      {canEdit && !formOpen && (
        <div className="insurance-action-row">
          <button type="button" className="btn-primary" onClick={() => setFormOpen(true)}>
            Vorgang erfassen
          </button>
        </div>
      )}

      {canEdit && formOpen && (
        <ClaimForm
          policy={policy}
          today={today}
          busy={create.isPending}
          error={create.error}
          onCancel={() => setFormOpen(false)}
          onSubmit={(input) => create.mutate(input)}
        />
      )}

      {openClaimId && (
        <ClaimDetailModal
          key={openClaimId}
          claimId={openClaimId}
          policy={policy}
          canEdit={canEdit}
          today={today}
          onClose={() => setOpenClaimId(null)}
        />
      )}
    </section>
  );
}

/**
 * Der Vorgang beim Anlegen - vier Angaben, nicht vierzehn.
 *
 * Erfasst wird, was am Tag des Schadens bekannt ist: was passiert ist, wann,
 * wo und wie viel gefordert wird. Meldedatum, Stand, Entscheidung und
 * Zahlungen entstehen spaeter im Vorgang selbst; ein Formular, das sie hier
 * schon abfragte, verlangte Angaben, die es noch nicht gibt.
 */
function ClaimForm({
  policy,
  today,
  busy,
  error,
  onCancel,
  onSubmit,
}: {
  policy: InsurancePolicy;
  today?: Date;
  busy: boolean;
  error: unknown;
  onCancel: () => void;
  onSubmit: (input: ClaimCreateInput) => void;
}) {
  const todayIso = toIsoDateString(today ?? new Date());
  const [title, setTitle] = useState("");
  const [occurredOn, setOccurredOn] = useState(todayIso);
  const [reportedOn, setReportedOn] = useState("");
  const [claimNumber, setClaimNumber] = useState("");
  const [claimed, setClaimed] = useState("");
  const [location, setLocation] = useState("");
  const [problems, setProblems] = useState<string[]>([]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    if (!title.trim()) {
      found.push("Ohne eine kurze Bezeichnung ist der Vorgang später nicht wiederzuerkennen.");
    }
    if (!occurredOn) {
      found.push("Der Schadentag ist Pflicht – an ihm hängt das Sonderkündigungsrecht.");
    }
    if (reportedOn && occurredOn && reportedOn < occurredOn) {
      found.push("Gemeldet werden kann erst, nachdem der Schaden eingetreten ist.");
    }
    let claimedMinor: number | null = 0;
    if (claimed.trim()) {
      claimedMinor = parseAmountToMinorUnits(claimed);
      if (claimedMinor === null || claimedMinor < 0) {
        found.push("Der geforderte Betrag ist kein gültiger Betrag.");
      }
    }
    setProblems(found);
    if (found.length > 0) return;

    onSubmit({
      policy_id: policy.id,
      title: title.trim(),
      occurred_on: occurredOn,
      reported_on: reportedOn || null,
      claim_number: claimNumber.trim() ? claimNumber.trim() : null,
      claimed_minor: claimedMinor ?? 0,
      location: location.trim() ? location.trim() : null,
    });
  };

  return (
    <form className="insurance-action-card" onSubmit={handleSubmit}>
      <h4 className="insurance-panel-subtitle">Vorgang erfassen</h4>

      <div className="insurance-action-row">
        <label className="insurance-field">
          <span>Was ist passiert</span>
          <input
            type="text"
            className="form-input"
            placeholder="Leitungswasser Bad"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label className="insurance-field insurance-field--anchor">
          <span>Schadentag</span>
          <input
            type="date"
            className="form-input"
            value={occurredOn}
            onChange={(event) => setOccurredOn(event.target.value)}
          />
        </label>
        <label className="insurance-field">
          <span>Gemeldet am (optional)</span>
          <input
            type="date"
            className="form-input"
            value={reportedOn}
            onChange={(event) => setReportedOn(event.target.value)}
          />
        </label>
        <label className="insurance-field">
          <span>Vorgangsnummer (optional)</span>
          <input
            type="text"
            className="form-input"
            value={claimNumber}
            onChange={(event) => setClaimNumber(event.target.value)}
          />
        </label>
        <label className="insurance-field">
          <span>Gefordert (optional)</span>
          <input
            type="text"
            inputMode="decimal"
            className="form-input"
            value={claimed}
            onChange={(event) => setClaimed(event.target.value)}
          />
        </label>
        <label className="insurance-field">
          <span>Ort (optional)</span>
          <input
            type="text"
            className="form-input"
            value={location}
            onChange={(event) => setLocation(event.target.value)}
          />
        </label>
      </div>

      <p className="insurance-form-help">
        Das Sonderkündigungsrecht nach einem Schaden beginnt mit dem Schadentag, nicht mit der
        Meldung – deshalb ist der Schadentag Pflicht und das Meldedatum nicht. Welche Norm den
        Fristanker stellt, entscheidet die Sparte „{policy.line_label}“; das Werkzeug setzt sie
        nicht selbst.
      </p>

      {problems.length > 0 && (
        <ul className="insurance-error" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {error !== null && error !== undefined && (
        <div className="insurance-error" role="alert">
          {isHealthScopedClaimError(error) ? (
            <p>
              Diese Police ist gesundheitsnah. Krankheitskosten laufen nicht über einen Vorgang,
              sondern über die Erstattungsseite – dort werden Rechnung, Einreichung und Träger
              geführt. <Link to="/erstattungen">Zu den Erstattungen</Link>
            </p>
          ) : (
            <p>{errorMessage(error)}</p>
          )}
        </div>
      )}

      <div className="insurance-action-row">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Wird angelegt …" : "Vorgang anlegen"}
        </button>
        <button type="button" className="btn-secondary" disabled={busy} onClick={onCancel}>
          Abbrechen
        </button>
      </div>
    </form>
  );
}
