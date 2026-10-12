/**
 * Register "Beitraege" (docs/VERSICHERUNGEN-PLAN.md, 2.3 Punkt 6; P3).
 *
 * **Treppenstufen, nicht Linie.** Ein Beitrag aendert sich sprunghaft: er
 * steht zwei Jahre still und springt an einem Tag um zwoelf Prozent. Eine
 * geglaettete Linie behauptet dazwischen Werte, die es nie gab, und laesst den
 * Sprung wie eine Entwicklung aussehen. Die Stufe zeigt beides richtig - die
 * Dauer der Ruhe und die Hoehe des Sprungs.
 *
 * **Warum ECharts und kein handgezeichnetes SVG.** Der Baukasten des Projekts
 * (`components/charts/Chart.tsx`) kann Stufen von Haus aus: eine Linienserie
 * mit `step: "end"` zeichnet genau die Treppe, und sie bringt Zeitachse,
 * Tooltip, Theme, Tabellenumschalter und die Canvas-Rueckfallebene fuer jsdom
 * mit. Ein eigenes SVG muesste all das nachbauen - und es waere ein zweites
 * Diagrammsystem in einem Bestand, der bewusst genau eines hat. Zwei Dinge
 * sind hier gesetzt und keine Geschmacksfrage:
 *
 * 1. **Die Achse beginnt nicht bei null** (`scale: true`). Ein Jahresbeitrag
 *    von 300 auf 340 Euro ist auf einer Achse ab null eine gerade Linie - die
 *    Stufe, um die es geht, waere unsichtbar.
 * 2. **Verglichen wird der Jahreswert.** Eine Umstellung von monatlich auf
 *    jaehrlich ist keine Beitragserhoehung, saehe im Rohbetrag aber wie eine
 *    aus.
 */

import { useMemo, useState } from "react";
import { Chart } from "../charts/Chart";
import {
  axisStyle,
  baseOption,
  CATEGORICAL,
  formatAxisAmount,
  formatTooltipAmount,
  readPalette,
} from "../charts/chartTheme";
import { useFormatDate } from "../../hooks/useProfile";
import { toIsoDateString } from "../../lib/dates";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import { countdownLabel, countdownTone } from "./countdown";
import { PremiumNoticeModal } from "./PremiumNoticeModal";
import { ReplaceInsurerModal } from "./ReplaceInsurerModal";
import {
  formatBasisPointsAsPercent,
  orderedPremiumSteps,
  premiumChanges,
  ANCHOR_BASIS_PHRASE,
  FREQUENCY_LABEL,
  PREMIUM_NOTICE_REASON_LABEL,
  PREMIUM_STEP_SOURCE_LABEL,
  type Frequency,
  type InsurancePolicy,
  type PremiumHistoryStep,
  type PremiumNotice,
  type PremiumNoticeCreateInput,
  type ReplaceInsurerInput,
} from "../../lib/insuranceApi";

/**
 * Die Datenanbindung des Registers, als ein Buendel.
 *
 * Ein Buendel statt zwoelf Einzelfeldern, weil es durch zwei Ebenen
 * durchgereicht wird und jede zusaetzliche Eigenschaft dort zweimal
 * abgeschrieben werden muesste. Fehlt es, gibt es das Register nicht - "keines
 * wird vorab leer registriert" (Plan 2.3).
 */
export interface PremiumRegisterData {
  notices: PremiumNotice[];
  steps: PremiumHistoryStep[];
  /** Gesamtsteigerung ueber den gezeigten Zeitraum, in Basispunkten. Kommt vom
   *  Server; die Oberflaeche rechnet sie nicht aus den Stufen nach. */
  totalIncreaseBp: number | null;
  loading: boolean;
  error: string | null;
  busy: boolean;
  actionError: string | null;
  canEdit: boolean;
  /** Policen, aus denen eine Nachfolgerin gewaehlt werden kann. */
  successorCandidates: InsurancePolicy[];
  onCreateNotice: (input: PremiumNoticeCreateInput) => void;
  onConfirmNotice: (noticeId: string) => void;
  onAckNotice: (noticeId: string) => void;
  onReplaceInsurer: (input: ReplaceInsurerInput) => void;
}

// ---------------------------------------------------------------------------
// Sonderkuendigungsband
// ---------------------------------------------------------------------------

export interface SpecialTerminationBandProps {
  policy: InsurancePolicy;
  /** Der Vorgang mit laufendem Fenster. `null` heisst: kein Band. */
  notice: PremiumNotice | null;
  busy: boolean;
  canEdit: boolean;
  onAck: (noticeId: string) => void;
}

/**
 * Der Streifen ueber dem Detail, solange ein Sonderkuendigungsfenster laeuft.
 *
 * Er nennt **beide** Daten, und zwar in dieser Rangfolge: das empfohlene
 * Handlungsdatum gross, das rechtliche Fristende daneben. Ein einzelnes Feld
 * "Fristende" ist die eigentliche Fehlerquelle - es kann nicht zwischen dem
 * Tag unterscheiden, bis zu dem man handeln muss, und dem Tag, an dem das
 * Recht endet. Dazu das fruehste Beendigungsdatum: eine Kuendigung wirkt
 * "fruehestens jedoch zum Zeitpunkt des Wirksamwerdens der Erhoehung", und
 * ohne diese Zeile fragte sich der Nutzer, warum sein Schutz noch laeuft.
 *
 * Ob das Band ueberhaupt erscheint, entscheidet `openSpecialTermination` -
 * insbesondere schweigt es bei `dynamic_agreed`, weil eine vereinbarte Dynamik
 * keine Erhoehung im Sinne des Paragraphen 40 VVG ist.
 *
 * Der Countdown kommt aus `special_termination.days_left` und wird hier NICHT
 * nachgerechnet: derselbe Wert steuert den Push, und zwei Rechnungen ergaeben
 * frueher oder spaeter zwei verschiedene Tageszahlen fuer dieselbe Frist.
 */
export function SpecialTerminationBand({
  policy,
  notice,
  busy,
  canEdit,
  onAck,
}: SpecialTerminationBandProps) {
  const { formatDate } = useFormatDate();
  if (!notice) return null;

  const fenster = notice.special_termination;
  const actionOn = fenster.recommended_action_on ?? fenster.deadline_legal_on;
  const daysLeft = fenster.days_left;

  return (
    <aside className="insurance-special-band" role="note">
      <div className="insurance-special-band-main">
        <p className="insurance-special-band-title">Sonderkündigungsrecht läuft</p>
        <p className="insurance-special-band-reason">
          {formatMinorUnitsAsEuro(notice.old_amount_minor)} →{" "}
          {formatMinorUnitsAsEuro(notice.new_amount_minor)} ({FREQUENCY_LABEL[notice.frequency]}) ·{" "}
          {formatBasisPointsAsPercent(notice.delta_bp)} ohne Mehrleistung
          {notice.reason ? ` · ${PREMIUM_NOTICE_REASON_LABEL[notice.reason]}` : ""}. Schreiben
          zugegangen am {formatDate(notice.received_on)}.
        </p>
        <dl className="insurance-special-band-dates">
          <div>
            <dt>Empfohlen absenden bis</dt>
            <dd className="insurance-special-band-primary">
              {actionOn ? formatDate(actionOn) : "nicht berechenbar"}
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
            <dd>
              {fenster.deadline_legal_on
                ? formatDate(fenster.deadline_legal_on)
                : "nicht berechenbar"}
            </dd>
          </div>
          <div>
            <dt>Kündigung wirkt frühestens zum</dt>
            <dd>
              {fenster.earliest_effect_on
                ? formatDate(fenster.earliest_effect_on)
                : formatDate(notice.effective_on)}
            </dd>
          </div>
        </dl>
        <p className="insurance-special-band-note">
          Der letzte sichere Zustelltag ist{" "}
          {fenster.safe_service_on ? formatDate(fenster.safe_service_on) : "nicht berechenbar"} –
          eine Kündigung wirkt erst mit Zugang beim Versicherer. Wird der empfohlene Tag verpasst,
          kann bis zum rechtlichen Fristende ein schnellerer Weg die Frist noch wahren.
          {fenster.basis &&
            ` Grundlage: ${fenster.basis} (${ANCHOR_BASIS_PHRASE[fenster.basis_kind]}).`}
        </p>
      </div>
      {canEdit && (
        <div className="insurance-special-band-side">
          <button
            type="button"
            className="btn-secondary"
            disabled={busy}
            onClick={() => onAck(notice.id)}
          >
            Zur Kenntnis genommen
          </button>
          <span className="insurance-kv-origin">
            {policy.line_label} · beendet die Erinnerungen zu dieser Frist, nicht die Frist selbst.
          </span>
        </div>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Treppenverlauf und Steigerungstabelle
// ---------------------------------------------------------------------------

/** Punkt der Treppe: [Datum, Jahresbeitrag in Minor Units]. */
type StepPoint = [string, number];

/**
 * Die Treppe als Punktfolge.
 *
 * Stufen ohne `valid_from` bleiben draussen - auf einer Zeitachse haetten sie
 * nur einen geratenen Platz. Sie stehen dafuer in der Tabelle, mit dem Wort
 * "seit Beginn" statt eines erfundenen Tages.
 */
function buildStepPoints(steps: PremiumHistoryStep[], todayIso: string): StepPoint[] {
  const dated = steps.filter(
    (step): step is PremiumHistoryStep & { valid_from: string } => step.valid_from !== null,
  );
  const points: StepPoint[] = dated.map((step) => [step.valid_from, step.yearly_minor]);
  const last = dated[dated.length - 1];
  if (!last) return points;
  // Ohne einen Schlusspunkt endet die Treppe auf der letzten Kante und die
  // aktuell geltende Stufe waere ein Punkt statt einer Flaeche - genau die
  // Stufe, die am meisten interessiert.
  const end = last.valid_to ?? todayIso;
  if (end > last.valid_from) {
    points.push([end, last.yearly_minor]);
  }
  return points;
}

export interface PremiumHistoryProps {
  policy: InsurancePolicy;
  data: PremiumRegisterData;
  /** Ankertag des Schlusspunkts. Als Eigenschaft, damit das Register pruefbar
   *  bleibt - der letzte Tritt der Treppe reicht bis heute. */
  today?: Date;
}

export function PremiumHistory({ policy, data, today }: PremiumHistoryProps) {
  const { formatDate } = useFormatDate();
  const [openModal, setOpenModal] = useState<"notice" | "replace" | null>(null);

  const todayIso = toIsoDateString(today ?? new Date());
  const steps = useMemo(() => orderedPremiumSteps(data.steps), [data.steps]);
  const firstStep = steps[0] ?? null;
  const datedSteps = steps.filter((step) => step.valid_from !== null).length;
  // Neueste Aenderung zuerst: die letzte Erhoehung ist die, um die es geht.
  const changes = useMemo(() => premiumChanges(steps).reverse(), [steps]);
  const pendingCount = data.notices.filter((notice) => notice.confirmed_at === null).length;

  const option = useMemo(() => {
    const palette = readPalette();
    const base = baseOption(palette);
    const points = buildStepPoints(steps, todayIso);
    // Ein Wechselpunkt ist keine Beitragsaenderung, sondern ein Wechsel der
    // Gesellschaft. Er bekommt deshalb eine eigene Marke statt einer Stufe -
    // sonst laese man den Sprung als Erhoehung.
    const switches = steps
      .filter((step) => step.is_switch_point && step.valid_from !== null)
      .map((step) => ({
        xAxis: step.valid_from as string,
        lineStyle: { color: palette.highlight, width: 1, type: "dashed" as const },
        label: {
          formatter: step.insurer_name,
          position: "insideStartTop" as const,
          color: palette.highlight,
          fontSize: 11,
        },
      }));

    return {
      ...base,
      legend: { ...base.legend, show: false },
      tooltip: {
        ...base.tooltip,
        trigger: "axis" as const,
        axisPointer: { type: "line" as const },
        valueFormatter: (value: unknown) => formatTooltipAmount(Number(value)),
      },
      grid: { left: 8, right: 24, top: 24, bottom: 8, containLabel: true },
      xAxis: {
        type: "time" as const,
        ...axisStyle(palette),
        splitLine: { show: false },
        axisLabel: { color: palette.textSecondary, fontSize: 11, hideOverlap: true },
      },
      yAxis: {
        type: "value" as const,
        // Siehe Dateikopf: eine Achse ab null macht die Stufe unsichtbar.
        scale: true,
        ...axisStyle(palette),
        axisLabel: { color: palette.textSecondary, fontSize: 12, formatter: formatAxisAmount },
      },
      series: [
        {
          name: "Jahresbeitrag",
          type: "line" as const,
          // Die eine Zeile, um die es geht: horizontal halten, am naechsten
          // Datum senkrecht springen.
          step: "end" as const,
          data: points,
          showSymbol: true,
          symbolSize: 6,
          lineStyle: { width: 2, color: CATEGORICAL[0] },
          itemStyle: { color: CATEGORICAL[0] },
          areaStyle: { color: CATEGORICAL[0], opacity: 0.1 },
          markLine:
            switches.length > 0
              ? { silent: true, symbol: "none" as const, data: switches }
              : undefined,
        },
      ],
    };
  }, [steps, todayIso]);

  const stepTable = (
    <table className="txn-table insurance-table">
      <thead>
        <tr>
          <th className="txn-th">Ab</th>
          <th className="txn-th">Jahresbeitrag</th>
          <th className="txn-th">Erfasster Betrag</th>
          <th className="txn-th">Versicherer</th>
        </tr>
      </thead>
      <tbody>
        {steps.map((step, index) => (
          <tr key={`${step.policy_id}-${step.valid_from ?? "start"}-${index}`}>
            <td className="txn-td">
              {step.valid_from ? formatDate(step.valid_from) : "seit Beginn, Datum unbekannt"}
            </td>
            <td className="txn-td">{formatMinorUnitsAsEuro(step.yearly_minor)}</td>
            <td className="txn-td">
              {formatMinorUnitsAsEuro(step.amount_minor)} ·{" "}
              {FREQUENCY_LABEL[step.frequency as Frequency] ?? step.frequency}
            </td>
            <td className="txn-td">{step.insurer_name}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <div className="insurance-premium-register">
      {data.canEdit && (
        <div className="insurance-action-row">
          <button
            type="button"
            className="btn-primary"
            disabled={data.busy}
            onClick={() => setOpenModal("notice")}
          >
            Beitragsanpassung erfassen
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={data.busy}
            onClick={() => setOpenModal("replace")}
          >
            Versicherer wechseln
          </button>
        </div>
      )}

      {data.actionError && (
        <p className="insurance-error" role="alert">
          {data.actionError}
        </p>
      )}
      {data.error && (
        <p className="insurance-error" role="alert">
          {data.error}
        </p>
      )}

      <section className="insurance-detail-section" aria-labelledby="insurance-premium-steps">
        <h3 id="insurance-premium-steps">Beitragsverlauf</h3>
        {data.loading && steps.length === 0 && <p className="card-body">Wird geladen …</p>}
        {!data.loading && steps.length === 0 && (
          <p className="card-body">
            Für diese Police ist noch keine Beitragsstufe bekannt. Der Verlauf entsteht aus den
            Preisstufen des verknüpften Vertrags und aus erfassten Anpassungsschreiben – eine
            einzelne Stufe ist noch keine Treppe.
          </p>
        )}
        {firstStep && (
          <>
            {/* Ohne einen einzigen datierten Tritt gibt es keine Zeitachse,
                sondern nur einen Betrag. Dann steht die Tabelle allein da -
                ein Diagramm mit einem geratenen Datum waere schlimmer als
                keines. */}
            {datedSteps === 0 ? (
              <div
                className="insurance-table-scroll"
                role="region"
                aria-label="Beitragsstufen"
                tabIndex={0}
              >
                {stepTable}
              </div>
            ) : (
              <Chart
                option={option}
                height={240}
                description={`Jahresbeitrag von ${policy.line_label} als Treppe: ${steps.length} Stufen${
                  firstStep.valid_from ? ` seit ${formatDate(firstStep.valid_from)}` : ""
                }.`}
                tableFallback={stepTable}
              />
            )}
            <p className="insurance-note">
              Jede Stufe hält, bis die nächste beginnt – gezeichnet wird der auf ein Jahr
              umgerechnete Beitrag, damit ein Wechsel der Zahlweise nicht wie eine Erhöhung
              aussieht.
              {data.totalIncreaseBp !== null &&
                ` Über den gezeigten Zeitraum: ${formatBasisPointsAsPercent(data.totalIncreaseBp)}.`}
            </p>
            {/* Ohne diesen Satz wirkt die Treppe kaputt: wer eben ein
                Schreiben erfasst hat, sucht die neue Stufe. Sie entsteht
                bewusst erst mit der Übernahme - ein Beitrag, den niemand
                bestätigt hat, ist kein Beitrag. */}
            {pendingCount > 0 && (
              <p className="insurance-derivation-warning">
                {pendingCount === 1
                  ? "Ein erfasstes Anpassungsschreiben ist noch nicht übernommen und steht deshalb nicht als Stufe in der Treppe."
                  : `${pendingCount} erfasste Anpassungsschreiben sind noch nicht übernommen und stehen deshalb nicht als Stufen in der Treppe.`}
              </p>
            )}
          </>
        )}
      </section>

      <section className="insurance-detail-section" aria-labelledby="insurance-premium-changes">
        <h3 id="insurance-premium-changes">Beitragsänderungen</h3>
        {changes.length === 0 ? (
          <p className="card-body">Zwischen den bekannten Stufen liegt keine Änderung.</p>
        ) : (
          <>
            <div
              className="insurance-table-scroll"
              role="region"
              aria-label="Beitragsänderungen"
              tabIndex={0}
            >
              <table className="txn-table insurance-table">
                <thead>
                  <tr>
                    <th className="txn-th">Ab</th>
                    <th className="txn-th">Bisher (Jahr)</th>
                    <th className="txn-th">Neu (Jahr)</th>
                    <th className="txn-th">Steigerung</th>
                    <th className="txn-th">Anlass und Herkunft</th>
                  </tr>
                </thead>
                <tbody>
                  {changes.map((change) => (
                    <tr key={`${change.on}-${change.new_yearly_minor}`}>
                      <td className="txn-td">{formatDate(change.on)}</td>
                      <td className="txn-td">{formatMinorUnitsAsEuro(change.old_yearly_minor)}</td>
                      <td className="txn-td">{formatMinorUnitsAsEuro(change.new_yearly_minor)}</td>
                      {/* Die Farbe haengt an den beiden Betraegen, nicht an der
                          Quote: eine Erhoehung um fuenf Cent im Jahr rundet auf
                          0 bp, ist aber immer noch eine Erhoehung - und faerbte
                          sich ueber `delta_bp > 0` als Senkung. */}
                      <td className="txn-td">
                        <span
                          className={
                            change.new_yearly_minor > change.old_yearly_minor
                              ? "insurance-delta insurance-delta--up"
                              : "insurance-delta insurance-delta--down"
                          }
                        >
                          {change.delta_bp === null
                            ? "keine Quote"
                            : formatBasisPointsAsPercent(change.delta_bp)}
                        </span>
                      </td>
                      <td className="txn-td">
                        {change.is_switch_point
                          ? `Wechsel zu ${change.insurer_name}`
                          : change.notice_id
                            ? `${
                                change.notice_reason
                                  ? PREMIUM_NOTICE_REASON_LABEL[change.notice_reason]
                                  : "Anpassungsschreiben"
                              }${change.with_benefit_increase ? " · mit Mehrleistung" : ""}`
                            : PREMIUM_STEP_SOURCE_LABEL[change.source]}
                        {policy.dynamic_agreed && !change.is_switch_point && (
                          <span className="insurance-kv-origin">
                            vereinbarte Dynamik – kein Sonderkündigungsrecht nach § 40 VVG
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="insurance-note">
              Senkungen stehen mit in der Tabelle. Eine Kfz-Rückstufung ist eine Stufe nach unten –
              wer sie hier nicht fände, hielte das Diagramm für falsch. Sie löst nur kein
              Sonderkündigungsrecht aus.
            </p>
          </>
        )}
      </section>

      <section className="insurance-detail-section" aria-labelledby="insurance-premium-notices">
        <h3 id="insurance-premium-notices">Erfasste Anpassungsschreiben</h3>
        {data.notices.length === 0 ? (
          <p className="card-body">
            Noch kein Schreiben erfasst. Erst mit dem Tag des Zugangs entsteht eine Frist – ohne ihn
            bleibt es bei einer Rückfrage.
          </p>
        ) : (
          <ul className="insurance-notice-list">
            {data.notices
              .slice()
              .sort((a, b) => b.received_on.localeCompare(a.received_on))
              .map((notice) => {
                const fenster = notice.special_termination;
                return (
                  <li key={notice.id} className="insurance-notice-item">
                    <div>
                      <p className="insurance-notice-head">
                        {formatMinorUnitsAsEuro(notice.old_amount_minor)} →{" "}
                        {formatMinorUnitsAsEuro(notice.new_amount_minor)} ·{" "}
                        {FREQUENCY_LABEL[notice.frequency]} ·{" "}
                        {formatBasisPointsAsPercent(notice.delta_bp)}
                      </p>
                      <p className="insurance-kv-origin">
                        erhalten am {formatDate(notice.received_on)} · gilt ab{" "}
                        {formatDate(notice.effective_on)}
                        {notice.comparison_amount_minor !== null &&
                          ` · Vergleichsbeitrag ${formatMinorUnitsAsEuro(
                            notice.comparison_amount_minor,
                          )}`}
                      </p>
                      {/* Der Zustand kommt vom Server. Ein verfallenes Fenster
                          wird ausdruecklich genannt statt geleert - der Nutzer
                          soll sehen, dass ein Recht verfallen ist. */}
                      <p className="insurance-notice-state">
                        {policy.dynamic_agreed && fenster.state === "none"
                          ? "Vereinbarte Dynamik – planmäßige Stufe, kein Sonderkündigungsrecht."
                          : fenster.state === "none"
                            ? `Kein Sonderkündigungsrecht.${fenster.basis ? ` ${fenster.basis}` : ""}`
                            : fenster.state === "expired"
                              ? `Fenster verfallen${
                                  fenster.deadline_legal_on
                                    ? ` am ${formatDate(fenster.deadline_legal_on)}`
                                    : ""
                                }.`
                              : `Fenster läuft${
                                  fenster.deadline_legal_on
                                    ? ` bis ${formatDate(fenster.deadline_legal_on)}`
                                    : ""
                                }.`}
                        {notice.acknowledged_at &&
                          ` Zur Kenntnis genommen am ${formatDate(notice.acknowledged_at)}.`}
                        {notice.confirmed_at
                          ? ` Betrag übernommen am ${formatDate(notice.confirmed_at)}.`
                          : " Betrag noch nicht übernommen."}
                      </p>
                    </div>
                    {data.canEdit && (
                      <div className="insurance-notice-actions">
                        {notice.confirmed_at === null && (
                          <button
                            type="button"
                            className="insurance-inline-button"
                            disabled={data.busy}
                            onClick={() => data.onConfirmNotice(notice.id)}
                          >
                            Betrag übernehmen
                          </button>
                        )}
                        {!notice.acknowledged_at && fenster.state === "open" && (
                          <button
                            type="button"
                            className="insurance-inline-button"
                            disabled={data.busy}
                            onClick={() => data.onAckNotice(notice.id)}
                          >
                            Zur Kenntnis genommen
                          </button>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
          </ul>
        )}
        <p className="insurance-settings-note">
          „Betrag übernehmen“ schreibt den neuen Betrag über die Vertragsseite fort – mit dem Tag
          des Wirksamwerdens als Beginn der Preisstufe. Die Police führt keinen zweiten Betrag.
        </p>
      </section>

      {openModal === "notice" && (
        <PremiumNoticeModal
          policy={policy}
          busy={data.busy}
          error={data.actionError}
          onClose={() => setOpenModal(null)}
          onSubmit={(input) => {
            data.onCreateNotice(input);
            setOpenModal(null);
          }}
        />
      )}
      {openModal === "replace" && (
        <ReplaceInsurerModal
          policy={policy}
          candidates={data.successorCandidates}
          busy={data.busy}
          error={data.actionError}
          onClose={() => setOpenModal(null)}
          onSubmit={(input) => {
            data.onReplaceInsurer(input);
            setOpenModal(null);
          }}
        />
      )}
    </div>
  );
}
