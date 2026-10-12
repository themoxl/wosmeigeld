import { StyleSheet, Text, View } from "react-native";
import {
  ANCHOR_BASIS_KIND_LABEL,
  DATE_SOURCE_LABEL,
  DEADLINE_KIND_LABEL,
  NOTICE_ANCHOR_LABEL,
  deadlineOriginLabel,
  nextDeadlineDate,
  showsCountdown,
} from "../api/insurance";
import type { DateSource, InsurancePolicy } from "../api/types";
import { letterSpacingLabel, radii, space, typeScale, useTheme, type AccentTone } from "../theme";
import { Badge } from "./ui";

/**
 * Der EINZIGE Ort, an dem diese App ein Fristdatum zeichnet
 * (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 2.4 und 7).
 *
 * Der teuerste Fehler dieses Moduls ist ein Countdown auf einen Termin, den
 * niemand geprueft hat. Er entsteht nicht durch eine falsche Rechnung, sondern
 * durch einen zweiten Renderpfad: sobald Liste und Detail ein Datum jeweils
 * selbst hinschreiben, sieht es an einer der beiden Stellen frueher oder
 * spaeter gleich aus, egal ob bestaetigt oder geraten. Deshalb kommt hier auch
 * die Hauptfaelligkeit her (DateWithSource) - sie ist zwar keine Frist, traegt
 * aber dieselbe Herkunftsfrage.
 *
 * Die drei Zustaende unterscheiden sich in DREI Merkmalen, nie nur in der
 * Farbe: Schriftgewicht, Wortlaut und Markierungsform. Wer die App in
 * Graustufen, bei Sonnenlicht oder mit einer Vorlesehilfe benutzt, muss sie
 * trotzdem auseinanderhalten koennen.
 *
 * | certainty        | Datum            | Markierung   | Countdown            |
 * |------------------|------------------|--------------|----------------------|
 * | confirmed        | kraeftig, gross  | voller Punkt | ja, mit Dringlichkeit|
 * | assumed          | ruhig, normal    | offener Ring | NEIN, dafuer "angenommen" |
 * | not_computable   | "Angabe fehlt"   | Strich       | nein                 |
 */

/**
 * Numerisches Datum ("30.09.2026") statt des ausgeschriebenen Monats aus
 * utils/dates.ts.
 *
 * Eine Frist wird abgeglichen, nicht gelesen: sie steht neben einem Countdown
 * und in einer schmalen Spalte am rechten Zeilenrand, wo "30. September 2026"
 * zweizeilig umbraeche. Der Formatter steht hier lokal, weil dies der einzige
 * Renderpfad fuer Fristdaten ist - eine zweite Kopie kann also nicht entstehen.
 */
const deadlineDateFormatter = new Intl.DateTimeFormat("de-DE", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})/;

/**
 * "JJJJ-MM-TT" als lokaler Kalendertag. `new Date(iso)` ist ausdruecklich
 * nicht gemeint: es liest die Zeichenkette als UTC-Mitternacht und liefert
 * westlich davon den Vortag - bei einer Frist waere das ein Tag zu frueh.
 */
function formatIsoDate(value: string): string {
  const match = ISO_DATE_PATTERN.exec(value);
  if (!match) return value;
  const [, year, month, day] = match;
  return deadlineDateFormatter.format(new Date(Number(year), Number(month) - 1, Number(day)));
}

/**
 * Tageszahl in Worte. Negative Werte sind eine verstrichene Frist - sie zu
 * verschweigen hiesse, den Nutzer im Glauben zu lassen, es sei noch Zeit.
 * Wortgleich zu apps/web/src/components/insurance/countdown.ts.
 */
export function countdownLabel(daysLeft: number): string {
  if (daysLeft < 0) {
    const overdue = Math.abs(daysLeft);
    return `seit ${overdue} ${overdue === 1 ? "Tag" : "Tagen"} vorbei`;
  }
  if (daysLeft === 0) return "heute";
  if (daysLeft === 1) return "morgen";
  return `noch ${daysLeft} Tage`;
}

/** Dringlichkeit nach Restzeit. Sie faerbt NUR den Countdown, nie das Datum -
 *  eine Ampel am Datum bekaeme sonst auch eine angenommene Frist. Dieselben
 *  Schwellen wie im Web und wie der erste 'term_ending'-Lauf des Servers. */
function countdownTone(daysLeft: number): AccentTone {
  if (daysLeft <= 30) return "warning";
  if (daysLeft <= 60) return "highlight";
  return "neutral";
}

type Props = {
  policy: InsurancePolicy;
  /**
   * "compact" ist die Zeilen- und Feldform (Liste, Ernstfallblock), "full" die
   * Fristenkarte mit allen drei Daten und ihrer Herleitung.
   */
  variant?: "compact" | "full";
  /** In der Liste steht die Frist rechtsbuendig am Zeilenende. */
  align?: "start" | "end";
};

export function DeadlineBadge({ policy, variant = "compact", align = "start" }: Props) {
  const theme = useTheme();
  const { deadline } = policy;
  const date = nextDeadlineDate(deadline);
  // Das erzeugte OpenAPI-Schema fuehrt `days_left` und `reason` als optional
  // (Pydantic-Defaults). Beide werden hier einmal auf eine feste Form gebracht,
  // damit die Fallunterscheidung unten nicht dreimal dasselbe pruefen muss.
  const daysLeft = typeof deadline.days_left === "number" ? deadline.days_left : null;
  const reason = deadline.reason ?? "";
  const alignStyle = align === "end" ? styles.blockEnd : styles.blockStart;
  const textAlignStyle = align === "end" ? styles.textEnd : null;

  // Kein Datum ist eine Aussage, kein leeres Feld. `reason` traegt die
  // Begruendung des Rechners ("keine Hauptfaelligkeit erfasst") - sie steht
  // hier, weil sie genau die Angabe benennt, die fehlt.
  if (deadline.certainty === "not_computable" || !date) {
    return (
      <View style={[styles.block, alignStyle]}>
        <View style={[styles.valueRow, align === "end" ? styles.valueRowEnd : null]}>
          <View style={[styles.markerDash, { backgroundColor: theme.textSecondary }]} />
          <Text style={[styles.valueMuted, { color: theme.textSecondary }, textAlignStyle]}>
            Angabe fehlt
          </Text>
        </View>
        {variant === "full" ? (
          <Text style={[styles.note, { color: theme.textSecondary }]}>
            {reason.trim().length > 0
              ? reason
              : "Ohne Hauptfälligkeit lässt sich kein Kündigungstermin berechnen."}
          </Text>
        ) : null}
      </View>
    );
  }

  const confirmed = deadline.certainty === "confirmed";

  return (
    <View style={[styles.block, alignStyle]}>
      {variant === "full" ? (
        <Text style={[styles.caption, { color: theme.textSecondary }]}>
          {DEADLINE_KIND_LABEL[deadline.deadline_kind]}
        </Text>
      ) : null}

      <View style={[styles.valueRow, align === "end" ? styles.valueRowEnd : null]}>
        {/* Voller Punkt gegen offenen Ring: die Unterscheidung ueberlebt
            Graustufen und Sonnenlicht, eine Farbe allein nicht. */}
        {confirmed ? (
          <View style={[styles.markerDot, { backgroundColor: theme.textPrimary }]} />
        ) : (
          <View style={[styles.markerRing, { borderColor: theme.textSecondary }]} />
        )}
        <Text
          style={[
            confirmed ? styles.valueConfirmed : styles.valueAssumed,
            variant === "full" ? styles.valueLarge : null,
            { color: confirmed ? theme.textPrimary : theme.textSecondary },
            textAlignStyle,
          ]}
        >
          {formatIsoDate(date)}
        </Text>
      </View>

      {/* Der Countdown gehoert ausschliesslich zur bestaetigten Frist. Unter
          einem geratenen Datum waere eine Tageszahl erfundene Genauigkeit -
          und genau die Zahl, nach der jemand sein Handeln richtet. Als Pille,
          damit die Dringlichkeit in der Zeile sofort auffaellt; der Wortlaut
          steht ausgeschrieben darin und traegt sie auch ohne Farbe. */}
      {confirmed && daysLeft !== null && showsCountdown(deadline) ? (
        <Badge
          label={countdownLabel(daysLeft)}
          tone={countdownTone(daysLeft)}
          size="small"
          style={align === "end" ? styles.badgeEnd : null}
        />
      ) : null}

      {/* Das Wort "angenommen" steht immer da, auch in der schmalen Zeile: es
          ist der Ersatz fuer den Countdown, nicht seine Fussnote. */}
      <Text style={[styles.origin, { color: theme.textMuted }, textAlignStyle]}>
        {variant === "full" ? deadlineOriginLabel(policy) : confirmed ? "bestätigt" : "angenommen"}
      </Text>

      {variant === "full" ? (
        <View style={styles.details}>
          {!confirmed ? (
            <Text style={[styles.note, { color: theme.textSecondary }]}>
              Kein Countdown, solange das Datum angenommen ist. Bestätigen lässt es sich in der
              Webansicht unter „Versicherungen“.
            </Text>
          ) : null}

          {deadline.safe_service_on ? (
            <Text style={[styles.detailLine, { color: theme.textSecondary }]}>
              Sicher zugestellt bis {formatIsoDate(deadline.safe_service_on)}
              {deadline.safe_service_holiday
                ? ` (verschoben wegen ${deadline.safe_service_holiday})`
                : ""}
            </Text>
          ) : null}

          {deadline.recommended_action_on ? (
            <Text style={[styles.detailLine, { color: theme.textSecondary }]}>
              Empfohlen zu handeln bis {formatIsoDate(deadline.recommended_action_on)}
            </Text>
          ) : null}

          {deadline.term_ends_on ? (
            <Text style={[styles.detailLine, { color: theme.textSecondary }]}>
              Wirksam zum {formatIsoDate(deadline.term_ends_on)}
            </Text>
          ) : null}

          <Text style={[styles.detailLine, { color: theme.textSecondary }]}>
            {NOTICE_ANCHOR_LABEL[deadline.anchor]} · Grundlage:{" "}
            {ANCHOR_BASIS_KIND_LABEL[deadline.anchor_basis_kind]}
          </Text>

          {reason.trim().length > 0 ? (
            <Text style={[styles.note, { color: theme.textSecondary }]}>{reason}</Text>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

/**
 * Ein Datum, das keine Frist ist, aber dieselbe Herkunftsfrage traegt - allen
 * voran die Hauptfaelligkeit.
 *
 * Sie steht hier statt im Detailbildschirm, weil sie die Vorbedingung der
 * Frist ist: eine angenommene Hauptfaelligkeit ist der haeufigste Grund fuer
 * eine angenommene Frist. Beide duerfen deshalb nicht an verschiedenen Stellen
 * verschieden ausgezeichnet werden.
 */
export function DateWithSource({
  label,
  date,
  source,
}: {
  label: string;
  date: string | null;
  source: DateSource;
}) {
  const theme = useTheme();
  const confirmed = source === "user_confirmed";

  return (
    <View style={styles.fieldRow}>
      <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>{label}</Text>
      {date ? (
        <View style={styles.fieldValue}>
          <Text
            style={[
              confirmed ? styles.valueConfirmed : styles.valueAssumed,
              { color: confirmed ? theme.textPrimary : theme.textSecondary },
            ]}
          >
            {formatIsoDate(date)}
          </Text>
          <Text style={[styles.origin, { color: theme.textMuted }]}>
            {DATE_SOURCE_LABEL[source]}
          </Text>
        </View>
      ) : (
        <View style={styles.fieldValue}>
          <Text style={[styles.valueMuted, { color: theme.textSecondary }]}>Angabe fehlt</Text>
        </View>
      )}
    </View>
  );
}

/**
 * Frist und Herkunft als ein Satz fuer Vorlesehilfen.
 *
 * Sie liegt neben der Komponente, weil Farbe, Schriftgewicht und Markierung
 * fuer eine Vorlesehilfe nicht existieren: ohne diesen Satz klaenge eine
 * geratene Frist genau wie eine geprüfte.
 */
export function deadlineAccessibilityLabel(policy: InsurancePolicy): string {
  const { deadline } = policy;
  const date = nextDeadlineDate(deadline);
  if (deadline.certainty === "not_computable" || !date) {
    return "Nächste Frist: Angabe fehlt, nicht berechenbar";
  }
  if (deadline.certainty === "confirmed") {
    const countdown =
      typeof deadline.days_left === "number" ? `, ${countdownLabel(deadline.days_left)}` : "";
    return `Nächste Frist ${formatIsoDate(date)}, bestätigt${countdown}`;
  }
  return `Nächste Frist ${formatIsoDate(date)}, angenommen, kein Countdown`;
}

const styles = StyleSheet.create({
  block: {
    gap: space.hair,
  },
  blockStart: {
    alignItems: "flex-start",
  },
  blockEnd: {
    alignItems: "flex-end",
    // Deckel gegen die Textspalte: ohne ihn draengt ein langes Datum das
    // Label der Zeile in den Umbruch.
    maxWidth: 132,
  },
  valueRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  valueRowEnd: {
    justifyContent: "flex-end",
  },
  textEnd: {
    textAlign: "right",
  },
  // Die drei Markierungsformen teilen sich die Breite 8 - daran erkennt sie
  // auch der Component-Test wieder, unabhaengig von Farbe und Theme.
  markerDot: {
    width: 8,
    height: 8,
    borderRadius: radii.pill,
  },
  markerRing: {
    width: 8,
    height: 8,
    borderRadius: radii.pill,
    borderWidth: 1.5,
    backgroundColor: "transparent",
  },
  markerDash: {
    width: 8,
    height: 2,
  },
  badgeEnd: {
    alignSelf: "flex-end",
  },
  caption: {
    fontSize: typeScale.micro,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: letterSpacingLabel,
  },
  valueConfirmed: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  valueAssumed: {
    fontSize: typeScale.base,
    fontWeight: "400",
  },
  valueLarge: {
    fontSize: typeScale.large,
  },
  valueMuted: {
    fontSize: typeScale.base,
    fontWeight: "400",
    fontStyle: "italic",
  },
  origin: {
    fontSize: typeScale.micro,
  },
  details: {
    gap: space.hair,
    paddingTop: space.sm,
  },
  detailLine: {
    fontSize: typeScale.caption,
  },
  note: {
    fontSize: typeScale.caption,
    fontStyle: "italic",
  },
  fieldRow: {
    gap: space.hair,
  },
  fieldLabel: {
    fontSize: typeScale.caption,
  },
  fieldValue: {
    gap: 0,
  },
});
