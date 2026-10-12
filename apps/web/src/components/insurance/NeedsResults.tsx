/**
 * Befunde des Bedarfsabgleichs (docs/VERSICHERUNGEN-PLAN.md, 10.2 und 10.3; P6).
 *
 * **Diese Datei ist die Stelle, an der ein Schutzscore entstehen wuerde, wenn
 * man ihn nicht ausdruecklich verhindert.** Der Plan schliesst ihn aus (10.2:
 * kein Score, keine Note, keine Sterne, kein Gesamtbalken), und die
 * Versuchung, "3 von 7 erfuellt" oder einen gruenen Ring darueber zu setzen,
 * kommt nicht aus der API - sie kommt aus der Oberflaeche. Deshalb gilt hier:
 *
 * 1. **Keine Zahl ueber die Menge.** Weder eine Gesamtzahl noch eine Zahl je
 *    Gruppe, weder Prozent noch Quote. Die Liste ist so lang, wie sie ist; wie
 *    viele Zeilen darin stehen, sagt ueber die Absicherung eines Haushalts
 *    nichts, was nicht in den Zeilen selbst steht.
 * 2. **Keine Ampel ueber alles.** Die Befunde sind einfarbig gehalten - kein
 *    rotes Feld fuer "Luecke", kein gruenes fuer "erfuellt". Was einen Befund
 *    einordnet, ist seine Gruppe und sein Wortlaut, nicht seine Farbe. Eine
 *    Farbskala ueber sieben Ergebniszustaende WAERE die Note, nur gemalt.
 * 3. **Jeder Befund traegt seine Regel bei sich**: Regelschluessel,
 *    Begruendungssatz, Quelle mit Pruefdatum und die Angaben, auf denen er
 *    beruht. Formuliert wird hier nichts - saemtliche Saetze kommen aus
 *    `app/data/insurance_rules.py` ueber `GET /v1/insurance/needs/check`.
 * 4. **Kein Rat, kein Anbieter, kein Tarif.** Ein Befund nennt die Sparte, die
 *    fehlt. Wo man sie kauft, steht hier nicht und darf hier nicht stehen.
 *
 * Die Reihenfolge der Gruppen ist fachlich: erst was fehlt, dann was ungeklaert
 * ist, dann - eingeklappt - was steht und was weggelegt wurde. Die beiden
 * hinteren Gruppen beantworten die Frage "was habe ich uebersehen?" nicht und
 * stehen deshalb zu.
 */

import { useState } from "react";
import { useFormatDate } from "../../hooks/useProfile";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import {
  formatNeedsFactValue,
  needsFactLabel,
  policyDisplayName,
  NEED_TIER_LABEL,
  NEEDS_OUTCOME_LABEL,
  NEEDS_OUTCOME_ORDER,
  RULE_DECISION_REASON_LABEL,
  type InsurancePolicy,
  type NeedsCheckResult,
  type NeedsFinding,
  type NeedsOutcome,
  type RuleDecisionInput,
  type RuleDecisionKind,
  type RuleDecisionReason,
} from "../../lib/insuranceApi";

/**
 * Die vier Gruppen und die Ergebniszustaende, die in sie fallen.
 *
 * `overinsured` steht bewusst hinten bei den weggelegten Befunden und nicht bei
 * den Luecken: "Police vorhanden, Regel greift nicht" ist keine Fehlstelle. Weit
 * oben und in Signalfarbe gezeigt liest sich derselbe Satz als Aufforderung zu
 * kuendigen - und damit als Beratung, die dieses Werkzeug nicht erteilt
 * (Leitsatz 3 der Phase).
 */
interface OutcomeGroup {
  id: string;
  title: string;
  lead: string;
  outcomes: NeedsOutcome[];
  /** Eingeklappt heisst: als `details` gerendert, nicht versteckt. */
  collapsed: boolean;
}

const GROUPS: OutcomeGroup[] = [
  {
    id: "gaps",
    title: "Wofür kein Schutz erfasst ist",
    lead:
      "Zu diesen Regeln findet der Abgleich in Ihrem Bestand keine passende Police oder eine " +
      "unter dem geprüften Wert. Was fehlt, steht als Sparte da – nicht als Angebot.",
    outcomes: ["gap", "underinsured"],
    collapsed: false,
  },
  {
    id: "unclear",
    title: "Wofür eine Angabe fehlt",
    lead:
      "Diese Regeln konnten nicht zu Ende geprüft werden. Das ist ein Ergebnis und kein Fehler: " +
      "solange die Angabe fehlt, behauptet der Abgleich hier weder eine Lücke noch das Gegenteil.",
    outcomes: ["unclear"],
    collapsed: false,
  },
  {
    id: "fulfilled",
    title: "Was die Regeln als vorhanden gefunden haben",
    lead:
      "Geprüft wurde, ob eine Police der Sparte besteht und ob sie den im Regelsatz hinterlegten " +
      "Wert erreicht. Mehr sagt eine erfüllte Regel nicht – über die Bedingungen Ihrer Police " +
      "weiß der Abgleich nichts.",
    outcomes: ["fulfilled"],
    collapsed: true,
  },
  {
    id: "closed",
    title: "Weggelegtes und Nichtzutreffendes",
    lead:
      "Befunde, die Sie weggelegt haben, Regeln, deren Bedingung auf Sie nicht zutrifft, und " +
      "Policen, zu denen keine Regel greift. Sie stehen hier vollständig, damit nachvollziehbar " +
      "bleibt, was der Abgleich geprüft und wieder verworfen hat.",
    outcomes: ["dismissed", "overinsured", "not_applicable"],
    collapsed: true,
  },
];

/** Gruende in der Reihenfolge, in der sie zur Schaltflaeche passen: die
 *  Vorbelegung steht oben, weil der Knopf sie schon ausgesprochen hat. */
const DISMISS_REASONS: RuleDecisionReason[] = [
  "not_applicable",
  "self_insured",
  "covered_elsewhere",
  "planned",
  "other",
];

/** Wie die drei Entscheidungsarten heissen. `snoozed` ist ausdruecklich nicht
 *  "weggelegt": vertagt heisst, der Befund kommt wieder. */
const DECIDED_LEAD: Record<RuleDecisionKind, string> = {
  accepted: "Sie haben sich das vorgemerkt",
  dismissed: "Von Ihnen weggelegt",
  snoozed: "Von Ihnen vertagt",
};

const DECIDED_LEAD_PAST: Record<RuleDecisionKind, string> = {
  accepted: "vorgemerkt",
  dismissed: "weggelegt",
  snoozed: "vertagt",
};

function outcomeRank(outcome: NeedsOutcome): number {
  const index = NEEDS_OUTCOME_ORDER.indexOf(outcome);
  return index === -1 ? NEEDS_OUTCOME_ORDER.length : index;
}

/** Stabile Kennung eines Befundes. Eine Regel kann je Person einmal auftreten
 *  (R4, R7, R22), deshalb reicht der Regelschluessel allein nicht. */
function findingKey(finding: NeedsFinding): string {
  return `${finding.rule_key}-${finding.person_id ?? "household"}`;
}

/**
 * An welche Police eine Entscheidung gehaengt wird.
 *
 * Der Regelfall ist `null` - die meisten Regeln zeigen auf eine FEHLENDE Police,
 * und eine Entscheidung dazu ist regelweit (Plan 4.9). Nur wo der Befund auf
 * genau eine vorhandene Police zeigt, wird sie mitgegeben: sonst gaelte das
 * Wegdruecken einer zu niedrigen Deckungssumme auch fuer jede spaeter erfasste
 * Police derselben Sparte.
 */
function decisionPolicyId(finding: NeedsFinding): string | null {
  const pointsAtPolicy = finding.outcome === "underinsured" || finding.outcome === "overinsured";
  if (!pointsAtPolicy || finding.matched_policy_ids.length !== 1) return null;
  return finding.matched_policy_ids[0] ?? null;
}

interface DecisionControlsProps {
  finding: NeedsFinding;
  canEdit: boolean;
  busy: boolean;
  onDecide: (ruleKey: string, input: RuleDecisionInput) => void;
  onClearDecision: (ruleKey: string, policyId: string | null) => void;
}

/**
 * Die beiden Antworten auf einen Befund.
 *
 * "Stimmt, kuemmere ich mich" wird ohne Rueckfrage gespeichert - eine
 * Zustimmung braucht keine Begruendung. "Trifft auf mich nicht zu" oeffnet die
 * Nachfrage nach dem Grund, weil erst der Grund den Befund dauerhaft weglegt und
 * weil er im Zweifel dem Nutzer selbst erklaert, warum hier nichts mehr steht.
 */
function DecisionControls({
  finding,
  canEdit,
  busy,
  onDecide,
  onClearDecision,
}: DecisionControlsProps) {
  const { formatDate } = useFormatDate();
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState<RuleDecisionReason>("not_applicable");
  const [note, setNote] = useState("");
  const [remindOn, setRemindOn] = useState("");

  const policyId = decisionPolicyId(finding);
  const decision = finding.decision ?? null;
  const fieldPrefix = `needs-decision-${findingKey(finding)}`;

  // Die Wiedervorlage ist erreicht: die Entscheidung gilt nicht mehr, der
  // Befund steht wieder da. Sie bleibt sichtbar, weil sie erklaert, warum -
  // aber im Perfekt und ohne die Zusage einer Rueckkehr, die schon eingetreten
  // ist. Und die beiden Antworten muessen wieder offen stehen: sonst waere ein
  // einmal vertagter Befund nur noch loeschbar, nicht mehr beantwortbar.
  const expired = decision?.expired === true;

  if (decision && !expired) {
    return (
      <div className="insurance-finding-decided">
        <p className="insurance-finding-decided-text">
          {DECIDED_LEAD[decision.decision]}
          {decision.reason ? ` – ${RULE_DECISION_REASON_LABEL[decision.reason]}` : ""}
          {decision.decided_at ? `, am ${formatDate(decision.decided_at)}` : ""}
          {decision.reason_note ? `. Notiz: ${decision.reason_note}` : "."}
          {decision.remind_on ? ` Kommt am ${formatDate(decision.remind_on)} zurück.` : ""}
        </p>
        {/* Die Ruecknahme haengt nur an der Rolle und nicht am Ergebniszustand:
            eine einmal getroffene Entscheidung muss auch dann zuruecknehmbar
            bleiben, wenn die Regel inzwischen gar nicht mehr zutrifft. Sonst
            bliebe sie stehen, ohne dass jemand sie noch loesen koennte. */}
        {canEdit && (
          <button
            type="button"
            className="btn-secondary insurance-inline-button"
            disabled={busy}
            onClick={() => onClearDecision(finding.rule_key, decision.policy_id)}
          >
            Entscheidung zurücknehmen
          </button>
        )}
      </div>
    );
  }

  // Eine Regel, die ohnehin nicht zutrifft, braucht keine Antwort "trifft nicht
  // zu" - das hat der Abgleich bereits gesagt. Und "kuemmere ich mich" waere
  // dort ohne Gegenstand.
  if (!canEdit || finding.outcome === "not_applicable") return null;

  if (!asking) {
    return (
      <div className="insurance-finding-decision">
        {expired && decision && (
          <p className="insurance-finding-expired-text">
            {`Sie hatten das ${DECIDED_LEAD_PAST[decision.decision]}`}
            {decision.decided_at ? `, am ${formatDate(decision.decided_at)}` : ""}
            {decision.remind_on
              ? `, mit Wiedervorlage am ${formatDate(decision.remind_on)}. Der Tag ist erreicht, deshalb steht der Befund wieder hier.`
              : `. Die Entscheidung gilt nicht mehr, deshalb steht der Befund wieder hier.`}
          </p>
        )}
        <button
          type="button"
          className="btn-secondary insurance-inline-button"
          disabled={busy}
          onClick={() =>
            onDecide(finding.rule_key, {
              decision: "accepted",
              policy_id: policyId,
              reason: "planned",
            })
          }
        >
          Stimmt, kümmere ich mich
        </button>
        <button
          type="button"
          className="btn-secondary insurance-inline-button"
          disabled={busy}
          onClick={() => setAsking(true)}
        >
          Trifft auf mich nicht zu
        </button>
      </div>
    );
  }

  return (
    <div className="insurance-dismiss-form">
      <label className="insurance-field" htmlFor={`${fieldPrefix}-reason`}>
        <span>Warum trifft das nicht zu?</span>
        <select
          id={`${fieldPrefix}-reason`}
          className="form-input"
          value={reason}
          onChange={(event) => setReason(event.target.value as RuleDecisionReason)}
        >
          {DISMISS_REASONS.map((value) => (
            <option key={value} value={value}>
              {RULE_DECISION_REASON_LABEL[value]}
            </option>
          ))}
        </select>
      </label>

      <label className="insurance-field" htmlFor={`${fieldPrefix}-note`}>
        <span>Notiz (freiwillig)</span>
        <input
          id={`${fieldPrefix}-note`}
          className="form-input"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>

      <label className="insurance-field" htmlFor={`${fieldPrefix}-remind`}>
        <span>Wieder vorlegen am</span>
        <input
          id={`${fieldPrefix}-remind`}
          type="date"
          className="form-input"
          value={remindOn}
          onChange={(event) => setRemindOn(event.target.value)}
        />
      </label>

      <div className="insurance-action-row">
        <button
          type="button"
          className="btn-primary insurance-inline-button"
          disabled={busy}
          onClick={() =>
            onDecide(finding.rule_key, {
              decision: remindOn ? "snoozed" : "dismissed",
              policy_id: policyId,
              reason,
              reason_note: note.trim() === "" ? null : note.trim(),
              remind_on: remindOn === "" ? null : remindOn,
            })
          }
        >
          {busy ? "Wird gespeichert …" : "Weglegen"}
        </button>
        <button
          type="button"
          className="btn-secondary insurance-inline-button"
          disabled={busy}
          onClick={() => setAsking(false)}
        >
          Abbrechen
        </button>
      </div>
      <p className="insurance-form-help">
        Mit einem Datum kommt der Befund an diesem Tag zurück, ohne Datum bleibt er weg.
      </p>
    </div>
  );
}

interface FindingCardProps {
  finding: NeedsFinding;
  policyNames: Map<string, string>;
  canEdit: boolean;
  busy: boolean;
  onDecide: (ruleKey: string, input: RuleDecisionInput) => void;
  onClearDecision: (ruleKey: string, policyId: string | null) => void;
  onOpenPolicy: (policyId: string) => void;
}

function FindingCard({
  finding,
  policyNames,
  canEdit,
  busy,
  onDecide,
  onClearDecision,
  onOpenPolicy,
}: FindingCardProps) {
  const { formatDate } = useFormatDate();
  const facts = Object.entries(finding.triggered_by);
  const tier = finding.priority ?? finding.need_tier ?? null;

  return (
    <li className="insurance-finding">
      <div className="insurance-finding-head">
        <strong className="insurance-finding-headline">{finding.headline}</strong>
        {/* Der Zustand als Wort, nicht als Farbe. */}
        <span className="insurance-finding-outcome">{NEEDS_OUTCOME_LABEL[finding.outcome]}</span>
      </div>

      {finding.person_name && (
        <p className="insurance-finding-person">Betrifft {finding.person_name}</p>
      )}

      <p className="insurance-finding-why">{finding.rationale}</p>

      {/* Zwei verschiedene Saetze, deshalb zwei Absaetze: die Begruendung oben
          sagt, wofuer die Sparte da ist; dieser sagt, was am Bestand DIESES
          Haushalts dazu gefunden wurde. Ohne ihn stuende ein Ergebniszustand
          da, ohne dass er sich erklaert. Bei `unclear` ersetzt der Dienst
          bereits die Begruendung - dann steht hier derselbe Satz nicht
          zweimal. */}
      {finding.outcome_reason && finding.outcome_reason !== finding.rationale && (
        <p className="insurance-finding-outcome-reason">{finding.outcome_reason}</p>
      )}

      {/* Quelle und Pruefdatum stehen an JEDEM Befund. Ein Begruendungssatz ohne
          nachpruefbare Herkunft waere eine Behauptung des Werkzeugs. */}
      <p className="insurance-finding-source">
        <span className="insurance-finding-rule">Regel {finding.rule_key}</span>
        {finding.source_url ? (
          <>
            {" · Quelle: "}
            <a href={finding.source_url} target="_blank" rel="noreferrer noopener">
              {finding.source_url}
            </a>
          </>
        ) : (
          " · ohne hinterlegte Quelle"
        )}
        {finding.source_checked_on
          ? ` · geprüft am ${formatDate(finding.source_checked_on)}`
          : " · ohne Prüfdatum"}
      </p>

      <div className="insurance-finding-basis">
        <h4 className="insurance-finding-basis-title">Worauf dieser Befund beruht</h4>
        {facts.length > 0 ? (
          <dl className="insurance-finding-facts">
            {facts.map(([fact, value]) => (
              <div key={fact} className="insurance-finding-fact">
                <dt>{needsFactLabel(fact)}</dt>
                <dd>{formatNeedsFactValue(value)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="insurance-finding-fact-empty">
            Diese Regel greift unabhängig von Ihren Angaben zum Haushalt.
          </p>
        )}

        <p className="insurance-finding-policies">
          {finding.matched_policy_ids.length > 0 ? (
            <>
              Herangezogene Policen:{" "}
              {finding.matched_policy_ids.map((policyId, index) => (
                <span key={policyId}>
                  {index > 0 && ", "}
                  <button
                    type="button"
                    className="insurance-linklike"
                    onClick={() => onOpenPolicy(policyId)}
                  >
                    {policyNames.get(policyId) ?? "Police ohne Namen"}
                  </button>
                </span>
              ))}
            </>
          ) : (
            <>
              Keine Police herangezogen
              {finding.line_label ? ` – erfasst ist keine der Sparte ${finding.line_label}.` : "."}
            </>
          )}
        </p>

        {/* Betraege nur, wenn der Regelsatz sie mitliefert - eine selbst
            gerechnete Zahl waere genau die Scheingenauigkeit, die 10.2
            ausschliesst. */}
        {(finding.target_value_minor ?? null) !== null && (
          <p className="insurance-finding-values">
            Geprüft gegen {formatMinorUnitsAsEuro(finding.target_value_minor as number)}
            {(finding.gap_value_minor ?? null) !== null &&
              ` – es fehlen ${formatMinorUnitsAsEuro(finding.gap_value_minor as number)}`}
            .
          </p>
        )}
      </div>

      {/* Die Bedarfsstufe darf gezeigt werden, aber sie ist eine Einordnung des
          Katalogs und keine Aufforderung: eingeklappt, ohne Farbe, ohne
          Rangwort (Plan 10.3, Schluss). */}
      {tier && (
        <details className="insurance-finding-tier">
          <summary>Einordnung im Katalog</summary>
          <p className="insurance-form-help">
            {NEED_TIER_LABEL[tier]}
            {finding.priority && finding.need_tier && finding.priority !== finding.need_tier
              ? ` Der Katalog führt die Sparte allgemein als ${finding.need_tier}; für diesen Haushalt greift die Regel mit ${finding.priority}.`
              : ""}
          </p>
        </details>
      )}

      <DecisionControls
        finding={finding}
        canEdit={canEdit}
        busy={busy}
        onDecide={onDecide}
        onClearDecision={onClearDecision}
      />
    </li>
  );
}

interface FindingListProps {
  findings: NeedsFinding[];
  policyNames: Map<string, string>;
  canEdit: boolean;
  busyRuleKey: string | null;
  onDecide: (ruleKey: string, input: RuleDecisionInput) => void;
  onClearDecision: (ruleKey: string, policyId: string | null) => void;
  onOpenPolicy: (policyId: string) => void;
}

function FindingList({
  findings,
  policyNames,
  canEdit,
  busyRuleKey,
  onDecide,
  onClearDecision,
  onOpenPolicy,
}: FindingListProps) {
  return (
    <ul className="insurance-finding-list">
      {findings.map((finding) => (
        <FindingCard
          key={findingKey(finding)}
          finding={finding}
          policyNames={policyNames}
          canEdit={canEdit}
          busy={busyRuleKey === finding.rule_key}
          onDecide={onDecide}
          onClearDecision={onClearDecision}
          onOpenPolicy={onOpenPolicy}
        />
      ))}
    </ul>
  );
}

export interface NeedsResultsProps {
  result: NeedsCheckResult | null;
  /** Fuer die Namen der herangezogenen Policen. Der Server liefert nur ihre
   *  Kennungen; ein Befund mit einer UUID darin waere unlesbar. */
  policies: InsurancePolicy[];
  loading: boolean;
  error: string | null;
  /** Der Endpunkt fehlt (404) - ein Ausbaustand, kein Fehler. */
  unavailable?: boolean;
  canEdit: boolean;
  /** Welche Regel gerade schreibt. Je Regel, nicht global: zwei Befunde
   *  nebeneinander sollen sich nicht gegenseitig sperren. */
  busyRuleKey: string | null;
  actionError: string | null;
  onDecide: (ruleKey: string, input: RuleDecisionInput) => void;
  onClearDecision: (ruleKey: string, policyId: string | null) => void;
  onOpenPolicy: (policyId: string) => void;
}

export function NeedsResults({
  result,
  policies,
  loading,
  error,
  unavailable = false,
  canEdit,
  busyRuleKey,
  actionError,
  onDecide,
  onClearDecision,
  onOpenPolicy,
}: NeedsResultsProps) {
  const { formatDate } = useFormatDate();

  const policyNames = new Map(policies.map((policy) => [policy.id, policyDisplayName(policy)]));
  const findings = result?.findings ?? [];
  // Plan 10.2: "Ein Risikofeld ohne erfasste Police zeigt 'nichts erfasst',
  // nicht 'Luecke'." Ein vollstaendig rotes Bild bei leerer Erfassung ist so
  // falsch wie ein gruenes - deshalb traegt die erste Gruppe in diesem Zustand
  // einen Satz, der die Aussage zurueckstuft.
  const emptyRegister = policies.length === 0;

  const groups = GROUPS.map((group) => ({
    group,
    entries: findings
      .filter((finding) => group.outcomes.includes(finding.outcome))
      .sort(
        (left, right) =>
          outcomeRank(left.outcome) - outcomeRank(right.outcome) ||
          left.rule_key.localeCompare(right.rule_key, "de"),
      ),
  })).filter((entry) => entry.entries.length > 0);

  // Fehlt der Endpunkt, sagt das bereits das Bedarfsprofil - einmal und an der
  // Stelle, an der man etwas eintragen wollte. Zweimal derselbe Satz
  // untereinander liest sich wie zwei Stoerungen statt wie eine.
  if (unavailable) return null;

  return (
    <section className="insurance-block" aria-labelledby="insurance-needs-results-title">
      <div className="insurance-block-head">
        <h2 className="insurance-block-title" id="insurance-needs-results-title">
          Was die Regeln gefunden haben
        </h2>
        <span className="insurance-block-hint">
          feste Regeln, keine Bewertung, keine Empfehlung
        </span>
      </div>

      <div className="card insurance-needs-card">
        {loading && !result && <p className="card-body">Der Abgleich läuft …</p>}
        {error && !result && (
          <p className="insurance-error" role="alert">
            {error}
          </p>
        )}
        {actionError && (
          <p className="insurance-error" role="alert">
            {actionError}
          </p>
        )}

        {result && (
          <>
            {/* Der Hinweis kommt aus dem Antwortkoerper, nicht aus dem JSX -
                P6-Abnahme: "Der Disclaimer steht im API-Body, nicht nur in der
                Fusszeile." */}
            <p className="insurance-scope-note" role="note">
              {result.disclaimer}
            </p>

            {result.checked_on && (
              <p className="insurance-form-help">
                Dieser Abgleich wurde am {formatDate(result.checked_on)} gerechnet. Er wird bei
                jedem Aufruf neu gerechnet; gespeichert bleibt nur, was Sie hier entscheiden.
              </p>
            )}

            {groups.length === 0 ? (
              <p className="insurance-empty-note">
                Es gibt nichts zu zeigen. Das heißt nicht „alles in Ordnung“, sondern dass keine
                Regel des Katalogs zu Ihren Angaben und Ihrem Bestand etwas sagen konnte.
              </p>
            ) : (
              groups.map(({ group, entries }) => {
                const list = (
                  <FindingList
                    findings={entries}
                    policyNames={policyNames}
                    canEdit={canEdit}
                    busyRuleKey={busyRuleKey}
                    onDecide={onDecide}
                    onClearDecision={onClearDecision}
                    onOpenPolicy={onOpenPolicy}
                  />
                );

                if (group.collapsed) {
                  return (
                    <details key={group.id} className="insurance-finding-group">
                      <summary>{group.title}</summary>
                      <p className="insurance-finding-group-lead">{group.lead}</p>
                      {list}
                    </details>
                  );
                }

                return (
                  <div key={group.id} className="insurance-finding-group">
                    {/* Bewusst ohne Zahl in der Ueberschrift: eine Zaehlung ueber
                        die Gruppe waere der erste Schritt zur Note. */}
                    <h3 className="insurance-panel-subtitle">{group.title}</h3>
                    <p className="insurance-finding-group-lead">{group.lead}</p>
                    {group.id === "gaps" && emptyRegister && (
                      <p className="insurance-empty-note">
                        In diesem Haushalt ist noch keine einzige Police erfasst. Was hier steht,
                        ist deshalb keine Lücke, sondern der Stand der Erfassung: die Regeln haben
                        nichts gefunden, weil noch nichts da ist.
                      </p>
                    )}
                    {list}
                  </div>
                );
              })
            )}
          </>
        )}
      </div>
    </section>
  );
}
