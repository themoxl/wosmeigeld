/**
 * Die Wortwahl des Countdowns, an einer Stelle.
 *
 * Sie liegt bewusst nicht in der Liste und nicht in der Aufgabenliste, sondern
 * neben beiden: sobald zwei Bildschirme dieselbe Tageszahl mit eigenen Worten
 * beschreiben, driften sie auseinander - der eine schreibt "noch 1 Tage", der
 * andere "morgen", und eine verstrichene Frist erscheint an einer Stelle als
 * "heute". Genau das war der Fall, bevor beide von hier lasen.
 */

/** Tageszahl in Worte. Negative Werte sind eine verstrichene Frist - sie
 *  verschweigen heisst, den Nutzer im Glauben lassen, es sei noch Zeit. */
export function countdownLabel(daysLeft: number): string {
  if (daysLeft < 0) {
    const overdue = Math.abs(daysLeft);
    return `seit ${overdue} ${overdue === 1 ? "Tag" : "Tagen"} vorbei`;
  }
  if (daysLeft === 0) return "heute";
  if (daysLeft === 1) return "morgen";
  return `noch ${daysLeft} Tage`;
}

/** Dringlichkeit nach Restzeit. Sie faerbt nur den Countdown, nie das Datum:
 *  eine Ampel am Datum bekaeme sonst auch eine angenommene Frist. */
export function countdownTone(daysLeft: number): "warning" | "highlight" | "neutral" {
  if (daysLeft <= 30) return "warning";
  if (daysLeft <= 60) return "highlight";
  return "neutral";
}
