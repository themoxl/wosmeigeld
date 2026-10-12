/**
 * Mutmassliche Doppelversicherungen (docs/VERSICHERUNGEN-PLAN.md, 10.4).
 *
 * **Der ganze Block ist als Vermutung formuliert, und das ist keine Hoeflichkeit
 * gegenueber dem Nutzer, sondern die Wahrheit ueber die Erkennung.** Sie liest
 * `insurance_lines.covered_by_keys` und die erfassten Bausteine - also den
 * Katalog und das, was jemand eingetippt hat. Die Bedingungen der beiden
 * Policen kennt sie nicht. Ein Satz wie "Glasbruch ist doppelt versichert"
 * waere damit eine Behauptung ueber Papier, das dieses Werkzeug nie gesehen
 * hat; "Glasbruch koennte doppelt versichert sein" ist genau so belastbar, wie
 * die Datenlage hergibt.
 *
 * Drei Folgen daraus:
 *
 * 1. **Jeder Befund nennt beide Policen mit Namen.** Ein Hinweis auf eine
 *    Doppelung, bei dem man selbst suchen muss, welche zwei Vertraege gemeint
 *    sind, ist keiner.
 * 2. **Ein Befund ohne erfasste Bausteine wird nicht als Befund ausgespielt**,
 *    sondern als die Aufgabe, die ihn belastbar machen wuerde (10.4). Ein
 *    Befund, der sich selbst entwertet, ist schlechter als keiner.
 * 3. **Angezeigt wird die Rechtsfolge, kein Rat.** Doppelte Beitraege bringen
 *    kein doppeltes Geld, und bei nachgewiesener Doppelversicherung ist der
 *    spaeter geschlossene Vertrag fristlos kuendbar - das steht im Gesetz. Was
 *    daraus zu tun ist, entscheidet der Nutzer; dieses Werkzeug empfiehlt
 *    weder Kuendigung noch Anbieter.
 */

import { formatMinorUnitsAsEuro } from "../../lib/money";
import {
  policyDisplayName,
  type DuplicateFinding,
  type InsurancePolicy,
} from "../../lib/insuranceApi";

/** Stabile Kennung: dasselbe Policenpaar kann ueber zwei Bausteine
 *  ueberlappen (Glasbruch und Fahrrad in derselben Hausratversicherung). */
function findingKey(finding: DuplicateFinding): string {
  return [
    finding.kind,
    finding.policy_id,
    finding.other_policy_id,
    finding.coverage_key ?? "-",
  ].join("|");
}

/**
 * Die Vermutung als Satz.
 *
 * Bewusst im Konjunktiv und ohne Ausrufe: "koennte" ist das staerkste Wort, das
 * die Datenlage traegt.
 */
function question(finding: DuplicateFinding): string {
  if (finding.kind === "contained_in" && finding.coverage_label) {
    return `${finding.coverage_label} könnte doppelt versichert sein`;
  }
  if (finding.line_label) {
    return `${finding.line_label} könnte doppelt bestehen`;
  }
  return "Zwei Policen könnten sich überschneiden";
}

interface PolicyChipProps {
  policyId: string;
  label: string;
  onOpenPolicy: (policyId: string) => void;
}

function PolicyChip({ policyId, label, onOpenPolicy }: PolicyChipProps) {
  return (
    <button
      type="button"
      className="insurance-duplicate-policy"
      onClick={() => onOpenPolicy(policyId)}
    >
      {label}
    </button>
  );
}

export interface DoubleInsuranceProps {
  findings: DuplicateFinding[];
  /** Fuer die Namen der beteiligten Policen, wenn der Server keine mitliefert. */
  policies: InsurancePolicy[];
  loading: boolean;
  error: string | null;
  /** Der Endpunkt fehlt (404) - ein Ausbaustand, kein Fehler. */
  unavailable?: boolean;
  onOpenPolicy: (policyId: string) => void;
}

export function DoubleInsurance({
  findings,
  policies,
  loading,
  error,
  unavailable = false,
  onOpenPolicy,
}: DoubleInsuranceProps) {
  const names = new Map(policies.map((policy) => [policy.id, policyDisplayName(policy)]));
  const nameOf = (policyId: string, given?: string | null): string =>
    given ?? names.get(policyId) ?? "Police ohne Namen";

  // Belastbar sind nur die Befunde, bei denen die Bausteine beider Policen
  // erfasst sind. Der Rest wird zur Aufgabe, nicht zum Befund (10.4).
  const established = findings.filter((finding) => finding.certainty !== "unclear");
  const pending = findings.filter((finding) => finding.certainty === "unclear");

  // Kein Befund heisst: der Block verschwindet ganz. Ein Kasten "keine Doppelung
  // gefunden" wuerde eine Pruefung behaupten, die es so nicht gab - die
  // Erkennung sieht nur erfasste Bausteine.
  if (loading || unavailable) return null;
  if (findings.length === 0 && !error) return null;

  return (
    <section className="insurance-block" aria-labelledby="insurance-duplicates-title">
      <div className="insurance-block-head">
        <h2 className="insurance-block-title" id="insurance-duplicates-title">
          Könnte doppelt versichert sein
        </h2>
        <span className="insurance-block-hint">Vermutung aus dem Katalog, keine Feststellung</span>
      </div>

      <div className="card insurance-needs-card">
        {error && (
          <p className="insurance-error" role="alert">
            {error}
          </p>
        )}

        {findings.length > 0 && (
          <p className="insurance-scope-note" role="note">
            Erkannt wird über den Spartenkatalog und die erfassten Bausteine – nicht über die
            Bedingungen Ihrer Policen. Was daran wirklich doppelt ist, steht in den beiden
            Verträgen. Die Rechtslage dazu: doppelte Beiträge bringen kein doppeltes Geld, und bei
            nachgewiesener Doppelversicherung ist der später geschlossene Vertrag fristlos kündbar.
          </p>
        )}

        {established.length > 0 && (
          <ul className="insurance-duplicate-list">
            {established.map((finding) => (
              <li key={findingKey(finding)} className="insurance-duplicate">
                <strong className="insurance-duplicate-question">{question(finding)}</strong>

                <p className="insurance-duplicate-pair">
                  Beteiligt sind{" "}
                  <PolicyChip
                    policyId={finding.policy_id}
                    label={nameOf(finding.policy_id, finding.policy_label)}
                    onOpenPolicy={onOpenPolicy}
                  />{" "}
                  und{" "}
                  <PolicyChip
                    policyId={finding.other_policy_id}
                    label={nameOf(finding.other_policy_id, finding.other_policy_label)}
                    onOpenPolicy={onOpenPolicy}
                  />
                  .
                </p>

                {/* Der Begruendungssatz kommt vom Server, damit hier keine
                    zweite Fassung derselben Aussage entsteht. */}
                <p className="insurance-duplicate-why">{finding.rationale}</p>

                {finding.yearly_minor !== null && (
                  <p className="insurance-duplicate-amount">
                    Betroffener Jahresbeitrag: {formatMinorUnitsAsEuro(finding.yearly_minor)}. Ob
                    davon etwas entfällt, hängt an den Bedingungen – die Zahl ist keine Ersparnis.
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}

        {pending.length > 0 && (
          <div className="insurance-duplicate-pending">
            <h3 className="insurance-panel-subtitle">Dafür fehlen noch Angaben</h3>
            <p className="insurance-form-help">
              Hier könnte eine Doppelung stehen – ohne die erfassten Bausteine der beteiligten
              Policen wäre sie aber geraten. Solange bleibt es bei der Aufgabe, sie zu erfassen.
            </p>
            <ul className="insurance-duplicate-list">
              {pending.map((finding) => {
                const missing =
                  finding.missing_coverage_policy_ids &&
                  finding.missing_coverage_policy_ids.length > 0
                    ? finding.missing_coverage_policy_ids
                    : [finding.policy_id, finding.other_policy_id];
                return (
                  <li key={findingKey(finding)} className="insurance-duplicate">
                    <strong className="insurance-duplicate-question">{question(finding)}</strong>
                    <p className="insurance-duplicate-why">{finding.rationale}</p>
                    {/* Die Aufgabe benennt der Server, nicht diese Komponente:
                        er weiss, WELCHE der beiden Policen ihre Bausteine
                        vermissen laesst. */}
                    {finding.hint && <p className="insurance-duplicate-why">{finding.hint}</p>}
                    <p className="insurance-duplicate-pair">
                      Bausteine erfassen bei:{" "}
                      {missing.map((policyId, index) => (
                        <span key={policyId}>
                          {index > 0 && " "}
                          <PolicyChip
                            policyId={policyId}
                            label={nameOf(policyId)}
                            onOpenPolicy={onOpenPolicy}
                          />
                        </span>
                      ))}
                    </p>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
