/**
 * Die naechsten zwoelf Monate als Achse (Plan 2.2, "Grafik: eine
 * 12-Monats-Zeitleiste, kein Score"; P2).
 *
 * Sie beantwortet die eine Frage, die keine Kennzahl beantwortet: *wann wird
 * es eng, und haeuft sich etwas?* Vier Termine im November sind eine
 * Information, "naechste Frist in 47 Tagen" ist es nicht.
 *
 * Drei Festlegungen tragen die Darstellung und sind keine Geschmacksfrage:
 *
 * 1. **Bestaetigt und angenommen sehen verschieden aus, und zwar in der Form,
 *    nicht nur in der Farbe.** Bestaetigt ist eine gefuellte Perle mit
 *    Countdown, angenommen eine offene Perle, blass, mit Fragezeichen am Namen
 *    und ausdruecklich ohne Countdown (Plan 2.4, Regel 1 und 3).
 * 2. **Was nicht berechenbar ist, steht nicht auf der Achse.** Eine Police
 *    ohne Hauptfaelligkeit haette dort nur einen erfundenen Platz. Sie
 *    erscheint als Zeile darunter - sichtbar, aber ohne Termin.
 * 3. **Die Achse scrollt, nicht die Seite.** Zwoelf Monatsmarken und ein
 *    Dutzend Namen brauchen Breite; ein waagerecht scrollendes Fenster ist
 *    ehrlicher als eine gestauchte Achse, auf der zwei Termine im selben
 *    Monat wie einer aussehen.
 */

import { useFormatDate } from "../../hooks/useProfile";
import { monthNameShort } from "../../lib/dates";
import { countdownLabel, countdownTone } from "./countdown";
import {
  deadlineOriginLabel,
  nextDeadlineDate,
  parseIsoDate,
  type InsurancePolicy,
  type PolicyStatus,
} from "../../lib/insuranceApi";

const MONTHS_ON_AXIS = 12;
const DAY_MS = 24 * 60 * 60 * 1000;

/** Hoehe einer Marken-Etage in Pixeln. Sie steht hier und nicht nur im CSS,
 *  weil die Achse ihre Hoehe aus der Zahl der Etagen rechnet. */
const LANE_HEIGHT = 26;
/** Abstand der Achse vom unteren Rand - darunter liegen die Monatsnamen. */
const AXIS_OFFSET = 22;

/**
 * Nur diese Zustaende koennen ueberhaupt einen ordentlichen Kuendigungstermin
 * haben - dieselbe Liste wie im Rechenkern (`insurance_deadline_service`).
 * Eine beendete Police als "ohne berechenbare Frist" zu zaehlen waere kein
 * Hinweis, sondern eine Falschmeldung: an ihr ist nichts mehr offen.
 */
const AXIS_STATUS: PolicyStatus[] = ["active", "suspended", "dormant", "paid_up"];

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function addMonths(date: Date, months: number): Date {
  return new Date(date.getFullYear(), date.getMonth() + months, 1);
}

/** Ganze Tage zwischen zwei lokalen Mitternachten. Gerundet, weil eine
 *  Zeitumstellung sonst 23 oder 25 Stunden als Bruchteil durchreicht. */
function daysBetween(from: Date, to: Date): number {
  return Math.round((to.getTime() - from.getTime()) / DAY_MS);
}

/**
 * Geschaetzte halbe Breite einer Beschriftung in Prozent der Achse.
 *
 * Die Achse ist mindestens 60rem breit (siehe `insurance.css`), ein Prozent
 * also rund 9,6 px; ein Zeichen der 10,5-px-Schrift misst etwa 6 px. Die
 * Schaetzung darf grob sein - sie entscheidet nur, ob zwei Namen dieselbe
 * Etage teilen. Ist sie zu grosszuegig, entsteht eine Etage zu viel; das ist
 * die harmlosere Richtung.
 */
function labelHalfWidthPercent(text: string): number {
  return (text.length * 0.62) / 2 + 0.6;
}

/** Wie viele Namen eine Hinweiszeile nennt, bevor sie zaehlt statt aufzuzaehlen.
 *  Zwanzig Namen in einem Satz liest niemand mehr - die Zahl schon. */
const NAMES_IN_NOTE = 6;

function policyNames(policies: InsurancePolicy[]): string {
  const names = policies.map((policy) => policy.label ?? policy.line_label);
  if (names.length <= NAMES_IN_NOTE) return names.join(", ");
  const rest = names.length - NAMES_IN_NOTE;
  return `${names.slice(0, NAMES_IN_NOTE).join(", ")} und ${rest} weitere`;
}

interface AxisMark {
  policy: InsurancePolicy;
  /** Das rechtliche Fristende - dasselbe Datum wie in der Liste. */
  on: string;
  confirmed: boolean;
  label: string;
  percent: number;
  lane: number;
}

export interface DeadlineTimelineProps {
  policies: InsurancePolicy[];
  /** Police, deren Herleitung gerade offen steht. */
  selectedPolicyId?: string | null;
  /** Fehlt der Empfaenger, sind die Marken kein Knopf, sondern Beschriftung -
   *  eine Schaltflaeche, die nichts tut, gibt es in diesem Modul nicht. */
  onSelectPolicy?: (policyId: string) => void;
  /** Ankertag der Achse. Als Eigenschaft, damit die Leiste pruefbar bleibt. */
  today?: Date;
}

export function DeadlineTimeline({
  policies,
  selectedPolicyId = null,
  onSelectPolicy,
  today,
}: DeadlineTimelineProps) {
  const { formatDate } = useFormatDate();

  const anchor = today ?? new Date();
  const windowStart = startOfMonth(anchor);
  const windowEnd = addMonths(windowStart, MONTHS_ON_AXIS);
  const windowDays = daysBetween(windowStart, windowEnd);
  const percentOf = (date: Date) => (daysBetween(windowStart, date) / windowDays) * 100;

  const marks: AxisMark[] = [];
  const withoutDeadline: InsurancePolicy[] = [];
  const beyondWindow: InsurancePolicy[] = [];
  // Dritter Eimer, und er ist kein Feinschliff: ein Termin vor dem Fensterrand
  // ist VERSTRICHEN, kein spaeterer. Steht er unter "ausserhalb dieser zwoelf
  // Monate", bekommt dieselbe verpasste Frist je nach Tag im Monat zwei
  // gegenlaeufige Auskuenfte - am 14.08. "seit 11 Tagen vorbei" auf der Achse,
  // am 01.09. eine Zeile, die nach *spaeter* klingt.
  const alreadyPassed: InsurancePolicy[] = [];

  for (const policy of policies) {
    if (!AXIS_STATUS.includes(policy.status)) continue;

    const on = nextDeadlineDate(policy.deadline);
    const date = parseIsoDate(on);
    if (policy.deadline.certainty === "not_computable" || !on || !date) {
      withoutDeadline.push(policy);
      continue;
    }
    if (date < windowStart) {
      alreadyPassed.push(policy);
      continue;
    }
    if (date >= windowEnd) {
      beyondWindow.push(policy);
      continue;
    }
    marks.push({
      policy,
      on,
      confirmed: policy.deadline.certainty === "confirmed",
      // Das Fragezeichen gehoert an den Namen und nicht in die Legende: wer
      // die Achse ueberfliegt, liest Namen, keine Legende.
      label: `${policy.label ?? policy.line_label}${policy.deadline.certainty === "confirmed" ? "" : " ?"}`,
      percent: percentOf(date),
      lane: 0,
    });
  }

  // Etagen von links nach rechts vergeben: eine Marke kommt in die unterste
  // Etage, in der ihre Beschriftung nicht auf der vorigen liegt. So bleiben
  // zwei Termine im selben Monat zwei sichtbare Termine.
  marks.sort((a, b) => a.percent - b.percent || a.label.localeCompare(b.label, "de-DE"));
  const laneRightEdge: number[] = [];
  for (const mark of marks) {
    const half = labelHalfWidthPercent(mark.label);
    let lane = 0;
    while (lane < laneRightEdge.length && (laneRightEdge[lane] ?? 0) > mark.percent - half) {
      lane += 1;
    }
    laneRightEdge[lane] = mark.percent + half;
    mark.lane = lane;
  }
  const laneCount = Math.max(laneRightEdge.length, 1);

  const months = Array.from({ length: MONTHS_ON_AXIS }, (_, index) => {
    const start = addMonths(windowStart, index);
    return {
      key: `${start.getFullYear()}-${start.getMonth()}`,
      percent: percentOf(start),
      label: monthNameShort(start.getMonth()),
      // Das Jahr nur dort, wo es wechselt - zwoelf Jahreszahlen nebeneinander
      // sind elf zu viel.
      year: index === 0 || start.getMonth() === 0 ? start.getFullYear() : null,
    };
  });

  const todayPercent = percentOf(
    new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate()),
  );

  // Ohne eine einzige Police sagt die leere Liste bereits alles; eine leere
  // Achse daneben behauptete nur, es gaebe etwas zu sehen.
  if (policies.length === 0) return null;

  const trackHeight = AXIS_OFFSET + 36 + (laneCount - 1) * LANE_HEIGHT;

  return (
    <section className="insurance-block" aria-labelledby="insurance-timeline-title">
      <div className="insurance-block-head">
        <h2 className="insurance-block-title" id="insurance-timeline-title">
          Die nächsten zwölf Monate
        </h2>
        <span className="insurance-block-hint">
          Angenommene Termine stehen blass und ohne Countdown
        </span>
      </div>

      <div className="card insurance-timeline">
        <div
          className="insurance-tl-scroll"
          role="region"
          aria-label="Fristenleiste über zwölf Monate"
          tabIndex={0}
        >
          <div className="insurance-tl-track" style={{ height: `${trackHeight}px` }}>
            <div className="insurance-tl-axis" aria-hidden="true" />
            <div
              className="insurance-tl-today"
              style={{ left: `${todayPercent}%` }}
              aria-hidden="true"
            />

            {months.map((month) => (
              <div key={month.key}>
                <span
                  className="insurance-tl-tick"
                  style={{ left: `${month.percent}%` }}
                  aria-hidden="true"
                />
                <span className="insurance-tl-month" style={{ left: `${month.percent}%` }}>
                  {month.label}
                  {month.year !== null && <span className="insurance-tl-year">{month.year}</span>}
                </span>
              </div>
            ))}

            {marks.map((mark) => {
              const daysLeft = mark.policy.deadline.days_left;
              const tone = mark.confirmed ? "confirmed" : "assumed";
              const selected = mark.policy.id === selectedPolicyId;
              const title = `${formatDate(mark.on)} · ${deadlineOriginLabel(mark.policy)}`;
              const inner = (
                <>
                  {/* Der Countdown haengt am bestaetigten Termin - und nur an
                      ihm. Eine Annahme bekommt hier keine Tageszahl, auch
                      dann nicht, wenn der Server eine liefert. */}
                  {mark.confirmed && daysLeft !== null && (
                    <span
                      className={`insurance-tl-days insurance-tl-days--${countdownTone(daysLeft)}`}
                    >
                      {countdownLabel(daysLeft)}
                    </span>
                  )}
                  <span className="insurance-tl-label">{mark.label}</span>
                  <span className="insurance-tl-bead" />
                  <span
                    className="insurance-tl-pin"
                    style={{ height: `${6 + mark.lane * LANE_HEIGHT}px` }}
                  />
                </>
              );
              const className = `insurance-tl-mark insurance-tl-mark--${tone}${
                selected ? " insurance-tl-mark--selected" : ""
              }`;
              const style = { left: `${mark.percent}%`, bottom: `${AXIS_OFFSET}px` };

              return onSelectPolicy ? (
                <button
                  key={mark.policy.id}
                  type="button"
                  className={className}
                  style={style}
                  title={title}
                  aria-pressed={selected}
                  aria-label={`${mark.policy.label ?? mark.policy.line_label}, ${formatDate(mark.on)}, ${
                    mark.confirmed ? "bestätigt" : "angenommen – kein Countdown"
                  }`}
                  onClick={() => onSelectPolicy(mark.policy.id)}
                >
                  {inner}
                </button>
              ) : (
                <span key={mark.policy.id} className={className} style={style} title={title}>
                  {inner}
                </span>
              );
            })}
          </div>
        </div>

        <div className="insurance-tl-legend">
          <span>
            <span className="insurance-tl-key insurance-tl-key--confirmed" aria-hidden="true" />
            bestätigte Frist, mit Countdown
          </span>
          <span>
            <span className="insurance-tl-key insurance-tl-key--assumed" aria-hidden="true" />
            angenommen – Fragezeichen statt Countdown
          </span>
        </div>

        {marks.length === 0 && (
          <p className="insurance-tl-note">
            In den nächsten zwölf Monaten liegt kein berechenbarer Termin.
          </p>
        )}

        {withoutDeadline.length > 0 && (
          <p className="insurance-tl-note insurance-tl-note--missing">
            <strong>
              {withoutDeadline.length}{" "}
              {withoutDeadline.length === 1 ? "Police ohne" : "Policen ohne"} berechenbare Frist
            </strong>{" "}
            – {policyNames(withoutDeadline)}.{" "}
            {withoutDeadline.length === 1 ? "Sie steht" : "Sie stehen"} nicht auf der Achse, weil
            eine Angabe fehlt; ein geschätzter Platz wäre ein erfundener Termin.
          </p>
        )}

        {alreadyPassed.length > 0 && (
          <p className="insurance-tl-note insurance-tl-note--missing">
            <strong>
              {alreadyPassed.length === 1
                ? "Ein Termin ist bereits verstrichen"
                : `${alreadyPassed.length} Termine sind bereits verstrichen`}
            </strong>{" "}
            – {policyNames(alreadyPassed)}. {alreadyPassed.length === 1 ? "Er liegt" : "Sie liegen"}{" "}
            vor diesem Fenster und steht
            {alreadyPassed.length === 1 ? "" : "en"} deshalb nicht auf der Achse.
          </p>
        )}

        {beyondWindow.length > 0 && (
          <p className="insurance-tl-note">
            {beyondWindow.length === 1
              ? "Ein weiterer Termin liegt nach diesen zwölf Monaten"
              : `${beyondWindow.length} weitere Termine liegen nach diesen zwölf Monaten`}{" "}
            – {policyNames(beyondWindow)}.
          </p>
        )}
      </div>
    </section>
  );
}
