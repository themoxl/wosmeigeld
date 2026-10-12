import { useMemo, useState } from "react";
import { useFormatDate } from "../../hooks/useProfile";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import { countdownLabel, countdownTone } from "./countdown";
import {
  deadlineOriginLabel,
  nextDeadlineDate,
  FREQUENCY_LABEL,
  POLICY_STATUS_LABEL,
  PREMIUM_PAYER_LABEL,
  type Certainty,
  type Frequency,
  type InsurancePolicy,
  type PolicyPremium,
  type PolicyStatus,
} from "../../lib/insuranceApi";

/** Statusfilter der Liste. Der Status gehoert in den Filter, nicht in eine
 *  Spalte - "aktiv" beantwortet keine Frage (Plan 2.2, Festlegung 6). */
const STATUS_FILTERS: { value: string; label: string; matches: PolicyStatus[] }[] = [
  { value: "all", label: "Alle Policen", matches: [] },
  { value: "active", label: "Aktiv", matches: ["active", "applied", "draft"] },
  { value: "suspended", label: "Ruhend", matches: ["suspended", "dormant"] },
  { value: "paid_up", label: "Beitragsfrei", matches: ["paid_up"] },
  { value: "cancelled", label: "Gekündigt", matches: ["cancelled"] },
  { value: "ended", label: "Beendet", matches: ["ended", "entered_in_error"] },
];

function frequencyLabel(value: string | null): string {
  if (!value) return "Zahlweise unbekannt";
  return FREQUENCY_LABEL[value as Frequency] ?? value;
}

interface DeadlineValueProps {
  date: string | null;
  certainty: Certainty;
  /** Herkunft in Worten. Sie steht immer da - ein Datum ohne Herkunft wird
   *  in diesem Modul nicht angezeigt. */
  originLabel: string;
  daysLeft: number | null;
  /** Herleitung des Rechners, als Titel am Datum. */
  reason?: string;
}

/**
 * Der einzige Renderpfad fuer ein Fristdatum (Plan 6, "DeadlineCell").
 *
 * Er liegt in der Liste, weil sie seine haeufigste Verwendung ist; Detail und
 * Aufgabenliste holen ihn sich von hier. Gaebe es zwei Pfade, waere einer
 * davon frueher oder spaeter der, der ein angenommenes Datum wie ein
 * bestaetigtes aussehen laesst - und genau das ist der teuerste Fehler dieses
 * Moduls: ein Countdown auf einen Termin, den niemand geprueft hat.
 */
export function DeadlineValue({
  date,
  certainty,
  originLabel,
  daysLeft,
  reason,
}: DeadlineValueProps) {
  const { formatDate } = useFormatDate();

  // Kein Datum ist eine Aussage, kein leeres Feld.
  if (certainty === "not_computable" || !date) {
    return (
      <div className="insurance-date insurance-date--none">
        <span className="insurance-date-value">
          <span className="insurance-date-dot" aria-hidden="true" />
          nicht berechenbar
        </span>
        <span className="insurance-date-origin">
          {reason && reason.trim() ? reason : "Angabe fehlt"}
        </span>
      </div>
    );
  }

  const confirmed = certainty === "confirmed";
  return (
    <div className={`insurance-date insurance-date--${confirmed ? "confirmed" : "assumed"}`}>
      <span className="insurance-date-value" title={reason || undefined}>
        <span className="insurance-date-dot" aria-hidden="true" />
        {formatDate(date)}
      </span>
      <span className="insurance-date-origin">{originLabel}</span>
      {confirmed ? (
        daysLeft !== null && (
          <span className={`insurance-countdown insurance-countdown--${countdownTone(daysLeft)}`}>
            {countdownLabel(daysLeft)}
          </span>
        )
      ) : (
        <span className="insurance-countdown insurance-countdown--muted">
          kein Countdown, solange das Datum angenommen ist
        </span>
      )}
    </div>
  );
}

/** Der Beitrag traegt immer seine Herkunft. Ein Betrag ohne Herkunft wird
 *  nicht angezeigt (Plan 2.1). */
function PremiumValue({ premium }: { premium: PolicyPremium }) {
  if (premium.kind === "contract") {
    const amount = premium.contract_amount_minor;
    return (
      <div className="insurance-amount">
        <span className="insurance-amount-value">
          {amount === null ? "—" : formatMinorUnitsAsEuro(amount)}
        </span>
        <span className="insurance-amount-origin">
          {frequencyLabel(premium.contract_frequency)} · aus Vertrag
          {premium.contract_name ? ` „${premium.contract_name}“` : ""}
        </span>
      </div>
    );
  }
  if (premium.kind === "external") {
    const payer = premium.premium_payer;
    return (
      <div className="insurance-amount insurance-amount--external">
        <span className="insurance-amount-value">
          {premium.external_premium_minor === null
            ? "—"
            : formatMinorUnitsAsEuro(premium.external_premium_minor)}
        </span>
        <span className="insurance-amount-origin">
          {frequencyLabel(premium.external_frequency)}
          {payer ? ` · ${PREMIUM_PAYER_LABEL[payer]}` : ""} · nicht in den Fixkosten
        </span>
      </div>
    );
  }
  return (
    <div className="insurance-amount insurance-amount--none">
      <span className="insurance-amount-value">—</span>
      <span className="insurance-amount-origin">kein Beitrag hinterlegt</span>
    </div>
  );
}

export interface PolicyListProps {
  policies: InsurancePolicy[];
  selectedPolicyId: string | null;
  onSelectPolicy: (policyId: string) => void;
}

export function PolicyList({ policies, selectedPolicyId, onSelectPolicy }: PolicyListProps) {
  // Voreinstellung "alle": ein Statusfilter, der beim ersten Besuch etwas
  // ausblendet, laesst gerade uebernommene Policen verschwinden.
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");

  const visible = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("de-DE");
    const filter = STATUS_FILTERS.find((entry) => entry.value === statusFilter);
    return policies.filter((policy) => {
      if (filter && filter.matches.length > 0 && !filter.matches.includes(policy.status)) {
        return false;
      }
      if (!needle) return true;
      const haystack = [
        policy.line_label,
        policy.label ?? "",
        policy.insurer_name,
        policy.policy_number ?? "",
      ]
        .join(" ")
        .toLocaleLowerCase("de-DE");
      return haystack.includes(needle);
    });
  }, [policies, search, statusFilter]);

  return (
    <section className="insurance-block" aria-labelledby="insurance-policies-title">
      <div className="insurance-block-head">
        <h2 className="insurance-block-title" id="insurance-policies-title">
          Policen
        </h2>
        <span className="insurance-block-hint">
          Ein angenommenes Datum sieht nie aus wie ein bestätigtes
        </span>
      </div>

      <div className="insurance-toolbar">
        <label className="insurance-search">
          <span className="insurance-visually-hidden">Policen durchsuchen</span>
          <input
            type="search"
            className="form-input"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Sparte, Versicherer oder Policennummer"
          />
        </label>
        <label className="insurance-filter">
          <span>Status</span>
          <select
            className="form-input"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            {STATUS_FILTERS.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        <span className="insurance-count">
          {visible.length} von {policies.length}
        </span>
      </div>

      {policies.length === 0 ? (
        <div className="card insurance-empty">
          <strong>Noch keine Police erfasst</strong>
          <p className="card-body">
            Sobald ein Versicherungsvertrag übernommen ist, steht hier der Bestand – mit Zweck,
            Beitrag und der nächsten Frist samt ihrer Herkunft.
          </p>
        </div>
      ) : visible.length === 0 ? (
        <div className="card insurance-empty">
          <strong>Kein Treffer</strong>
          <p className="card-body">
            Für Suche und Statusfilter gibt es keine Police. {policies.length} Policen sind erfasst.
          </p>
        </div>
      ) : (
        <div className="card insurance-table-card">
          <div className="insurance-table-scroll" role="region" aria-label="Policen" tabIndex={0}>
            <table className="txn-table insurance-table">
              <thead>
                <tr>
                  <th className="txn-th">Sparte und Zweck</th>
                  <th className="txn-th">Versicherer</th>
                  <th className="txn-th">Beitrag</th>
                  <th className="txn-th">Nächste Frist</th>
                  <th className="txn-th">Nächste Aufgabe</th>
                  <th className="txn-th" aria-label="Aktion" />
                </tr>
              </thead>
              <tbody>
                {visible.map((policy) => (
                  <tr
                    key={policy.id}
                    className={`txn-tr insurance-row${
                      policy.id === selectedPolicyId ? " insurance-row--selected" : ""
                    }`}
                  >
                    <td className="txn-td">
                      <div className="insurance-line-name">
                        {policy.line_label}
                        {policy.status !== "active" && (
                          <span className="insurance-status-chip">
                            {POLICY_STATUS_LABEL[policy.status]}
                          </span>
                        )}
                      </div>
                      <p className="insurance-line-purpose">{policy.line_purpose_short}</p>
                    </td>
                    <td className="txn-td">
                      <div className="insurance-insurer">{policy.insurer_name}</div>
                      <div className="insurance-policy-number">
                        {policy.policy_number ?? "ohne Policennummer"}
                      </div>
                      {policy.label && policy.label !== policy.line_label && (
                        <div className="insurance-policy-label">{policy.label}</div>
                      )}
                    </td>
                    <td className="txn-td">
                      <PremiumValue premium={policy.premium} />
                    </td>
                    <td className="txn-td">
                      <DeadlineValue
                        date={nextDeadlineDate(policy.deadline)}
                        certainty={policy.deadline.certainty}
                        originLabel={deadlineOriginLabel(policy)}
                        daysLeft={policy.deadline.days_left}
                        reason={policy.deadline.reason}
                      />
                    </td>
                    <td className="txn-td">
                      {policy.next_task ? (
                        <span className="insurance-task-hint">{policy.next_task}</span>
                      ) : (
                        <span className="insurance-task-hint insurance-task-hint--none">
                          nichts offen
                        </span>
                      )}
                    </td>
                    <td className="txn-td insurance-row-action">
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => onSelectPolicy(policy.id)}
                        aria-label={`${policy.line_label} bei ${policy.insurer_name} öffnen`}
                      >
                        Öffnen
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
