import { Stack } from "expo-router";
import { useState } from "react";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import type { ForecastDayGroup, ForecastResponse } from "../../src/api/types";
import {
  buildTrendPoints,
  resolveToneColor,
  toneToAccent,
} from "../../src/components/ForecastCard";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import {
  SegmentedControl,
  type SegmentedControlOption,
} from "../../src/components/SegmentedControl";
import { SheetModal } from "../../src/components/SheetModal";
import {
  Badge,
  Card,
  CardLabel,
  Chip,
  ChipRow,
  DataRow,
  Money,
  Numpad,
  StatTile,
  TrendChart,
  type NumpadKey,
  type TrendPoint,
} from "../../src/components/ui";
import {
  FORECAST_DAILY_WINDOW_DAYS,
  FORECAST_SCREEN_HORIZON_MONTHS,
  useForecast,
  useForecastUpcoming,
} from "../../src/hooks/useForecast";
import {
  leading,
  MIN_TOUCH,
  radii,
  space,
  toneColors,
  typeScale,
  useTheme,
  type AccentTone,
} from "../../src/theme";
import { formatDueDate, formatMonthLabel, formatShortDate } from "../../src/utils/dates";
import {
  describeForecastBasis,
  describeForecastChain,
  describeForecastFigures,
  describeForecastHints,
  describeForecastMonths,
  describeForecastQuality,
  describeForecastVerdict,
  describePaymentGroups,
  selectNextPayments,
  type ForecastVerdict,
} from "../../src/utils/forecastPresentation";
import {
  formatMinorUnits,
  formatMinorUnitsCompact,
  parseAmountToMinorUnits,
} from "../../src/utils/money";

/**
 * Liquiditaetsvorschau, lesend (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md,
 * Stufe 5). Mobile ist kein verkleinertes Web: es gibt keine Monatstabelle und
 * keine Szenariobearbeitung.
 *
 * Der Showcase stellt die Kurve nach vorn; hier steht die Aussage davor. Die
 * Kurve zeigt einen Verlauf, aber nicht, was daraus folgt - und die Frage, mit
 * der man diesen Bildschirm oeffnet, ist "wann wird es knapp", nicht "wie sah
 * der Monat aus".
 */
type Horizon = "30d" | "90d" | "12m";

const HORIZON_OPTIONS: SegmentedControlOption<Horizon>[] = [
  { value: "30d", label: "30 Tage", accessibilityLabel: "Zeitraum 30 Tage" },
  { value: "90d", label: "90 Tage", accessibilityLabel: "Zeitraum 90 Tage" },
  { value: "12m", label: "12 Monate", accessibilityLabel: "Zeitraum 12 Monate" },
];

const HORIZON_LONG_MONTHS = 12;
const HORIZON_SHORT_DAYS = 30;
const MONTH_CARD_LIMIT = 6;

export default function VorschauScreen() {
  const [horizon, setHorizon] = useState<Horizon>("90d");

  // Nur der Jahreshorizont fragt neu an: 30 und 90 Tage sind zwei Ausschnitte
  // derselben tagesgenauen Reihe und teilen sich denselben Query-Key - ein
  // Umschalten kostet dort keine Anfrage. Fuer zwoelf Monate waeren 365
  // Tageszeilen Ballast, deshalb monatsweise.
  const isLongHorizon = horizon === "12m";
  const forecastQuery = useForecast({
    horizonMonths: isLongHorizon ? HORIZON_LONG_MONTHS : FORECAST_SCREEN_HORIZON_MONTHS,
    granularity: isLongHorizon ? "month" : "day",
  });
  const upcomingQuery = useForecastUpcoming(FORECAST_DAILY_WINDOW_DAYS);

  const forecast = forecastQuery.data;
  const isRefreshing = forecastQuery.isFetching || upcomingQuery.isFetching;

  const onRefresh = () => {
    void forecastQuery.refetch();
    void upcomingQuery.refetch();
  };

  return (
    <>
      <Stack.Screen options={{ title: "Liquiditätsvorschau" }} />
      <ScreenContainer>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
        >
          <SegmentedControl
            options={HORIZON_OPTIONS}
            value={horizon}
            onChange={setHorizon}
            accessibilityLabel="Zeitraum der Vorschau"
          />

          {forecastQuery.isLoading ? (
            <InlineQueryState
              status="loading"
              skeletonRows={4}
              skeletonRowHeight={56}
              testID="vorschau-loading"
            />
          ) : null}

          {!forecastQuery.isLoading && (forecastQuery.isError || !forecast) ? (
            <InlineQueryState
              status="error"
              title="Vorschau konnte nicht berechnet werden"
              message={
                forecastQuery.error instanceof Error ? forecastQuery.error.message : undefined
              }
              onRetry={() => void forecastQuery.refetch()}
              testID="vorschau-error"
            />
          ) : null}

          {forecast ? (
            <ForecastContent
              forecast={forecast}
              horizon={horizon}
              upcoming={upcomingQuery.data ?? []}
              isUpcomingLoading={upcomingQuery.isLoading}
              isUpcomingError={upcomingQuery.isError}
              onRetryUpcoming={() => void upcomingQuery.refetch()}
            />
          ) : null}
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

interface ContentProps {
  forecast: ForecastResponse;
  horizon: Horizon;
  upcoming: ForecastDayGroup[];
  isUpcomingLoading: boolean;
  isUpcomingError: boolean;
  onRetryUpcoming: () => void;
}

function ForecastContent({
  forecast,
  horizon,
  upcoming,
  isUpcomingLoading,
  isUpcomingError,
  onRetryUpcoming,
}: ContentProps) {
  const verdict = describeForecastVerdict(forecast);

  // Ohne Einnahmequelle kennt die Vorschau nur Ausgaben. Dann erscheint der
  // Sperrhinweis STATT der Zahlen (Plan 8.3) - eine Kurve, die zwangslaeufig
  // ins Bodenlose faellt, waere keine Information, sondern eine Erschreckung.
  const showNumbers = !verdict.blocksNumbers;
  const trend = showNumbers ? buildTrend(forecast, horizon) : null;

  return (
    <>
      <VerdictCard forecast={forecast} verdict={verdict} />

      {trend ? <TrendCard forecast={forecast} trend={trend} /> : null}

      {showNumbers ? (
        <AffordabilitySimulator
          lowestMinor={forecast.metrics.lowest_balance_minor}
          lowestOnLabel={formatShortDate(forecast.metrics.lowest_balance_on)}
          bufferMinor={forecast.safety_buffer_minor}
        />
      ) : null}

      {showNumbers ? <FiguresCard forecast={forecast} /> : null}

      {showNumbers ? <ChainCard forecast={forecast} /> : null}

      {showNumbers ? (
        <PaymentsCard
          upcoming={upcoming}
          isLoading={isUpcomingLoading}
          isError={isUpcomingError}
          onRetry={onRetryUpcoming}
        />
      ) : null}

      {showNumbers ? <MonthsSection forecast={forecast} horizon={horizon} /> : null}

      <BasisCard forecast={forecast} />
    </>
  );
}

// ---------------------------------------------------------------------------
// 1. Die grosse Aussage

function VerdictCard({
  forecast,
  verdict,
}: {
  forecast: ForecastResponse;
  verdict: ForecastVerdict;
}) {
  const theme = useTheme();
  const quality = describeForecastQuality(forecast);
  const hints = describeForecastHints(forecast);

  return (
    <Card accentEdge={toneToAccent(verdict.tone)}>
      <View style={styles.cardHead}>
        <CardLabel>{verdict.title}</CardLabel>
        <Badge label={`Datenqualität: ${quality.label}`} tone={toneToAccent(quality.tone)} />
      </View>

      <View style={styles.textBlock}>
        <Text
          style={[styles.verdictHeadline, { color: resolveToneColor(verdict.tone, theme) }]}
          accessibilityLabel={verdict.accessibilityLabel}
        >
          {verdict.headline}
        </Text>
        <Text style={[styles.bodyText, { color: theme.textSecondary }]}>{verdict.detail}</Text>
      </View>

      <View style={styles.textBlock} accessibilityLabel={quality.accessibilityLabel}>
        {quality.facts.map((fact) => (
          <Text key={fact} style={[styles.footnote, { color: theme.textMuted }]}>
            {fact}
          </Text>
        ))}
      </View>

      {hints.length > 0 ? (
        <View style={styles.badgeRow}>
          {hints.map((hint) => (
            <Badge key={hint.key} label={hint.label} tone={toneToAccent(hint.tone)} />
          ))}
        </View>
      ) : null}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 2. Kontostands-Prognose

interface TrendDisplay {
  points: TrendPoint[];
  accessibilityLabel: string;
  /** Wann der markierte Tiefstand liegt, z. B. "24.08." oder "Oktober 2026". */
  lowWhen: string;
  lowMinor: number;
  /** Wann der groesste Zufluss liegt; null, wenn die Reihe nur faellt. */
  highWhen: string | null;
  highMinor: number | null;
  /** "am" fuer Tage, "im" fuer Monate - sonst stuende "Tiefstand am Oktober 2026". */
  preposition: "am" | "im";
}

interface TrendEntry {
  value: number;
  /** Kurz, fuer die sichtbare Zeile. */
  when: string;
  /** Ausgeschrieben, fuer die Vorlesehilfe. */
  spoken: string;
}

/**
 * Punkte der Verlaufskurve. Der Umschalter schneidet die Reihe nur zu und
 * rechnet nichts nach: Tiefstand und groesster Zufluss stammen aus genau den
 * Werten, die auch gezeichnet werden - sonst zeigte der Markierungspunkt
 * woandershin als die Zeile darunter.
 */
function buildTrend(forecast: ForecastResponse, horizon: Horizon): TrendDisplay | null {
  const isMonthly = horizon === "12m";
  const entries: TrendEntry[] = isMonthly
    ? forecast.monthly.map((month) => {
        const label = formatMonthLabel(new Date(month.year, month.month - 1, 1));
        return { value: month.closing_balance_minor, when: label, spoken: label };
      })
    : forecast.daily
        .slice(0, horizon === "30d" ? HORIZON_SHORT_DAYS : FORECAST_DAILY_WINDOW_DAYS)
        .map((day) => ({
          value: day.balance_minor,
          when: formatShortDate(day.on),
          spoken: formatDueDate(day.on),
        }));

  const first = entries[0];
  const last = entries[entries.length - 1];
  if (entries.length < 2 || !first || !last) return null;

  const points = buildTrendPoints(entries.map((entry) => entry.value));
  const lowEntry = entries[points.findIndex((point) => point.marker === "low")];
  const highIndex = points.findIndex((point) => point.marker === "high");
  const highEntry = highIndex < 0 ? undefined : entries[highIndex];

  const values = entries.map((entry) => entry.value);

  return {
    points,
    accessibilityLabel:
      `Verlauf über ${entries.length} ${isMonthly ? "Monate" : "Tage"}, von ${first.spoken} ` +
      `bis ${last.spoken}. Zwischen ${formatMinorUnits(Math.min(...values))} und ` +
      `${formatMinorUnits(Math.max(...values))}.`,
    lowWhen: lowEntry?.when ?? last.when,
    lowMinor: lowEntry?.value ?? last.value,
    highWhen: highEntry?.when ?? null,
    highMinor: highEntry?.value ?? null,
    preposition: isMonthly ? "im" : "am",
  };
}

/** Pufferzustand als Pille - Farbe traegt die Aussage nie allein. */
function describeBufferBadge(forecast: ForecastResponse): { label: string; tone: AccentTone } {
  if (forecast.metrics.first_negative_on) {
    return { label: "Engpass im Zeitraum", tone: "warning" };
  }
  if (forecast.metrics.first_below_buffer_on) {
    return { label: "Puffer wird unterschritten", tone: "highlight" };
  }
  if (forecast.safety_buffer_minor <= 0) {
    return { label: "Kein Puffer gepflegt", tone: "neutral" };
  }
  return { label: "Puffer gesichert", tone: "positive" };
}

function TrendCard({ forecast, trend }: { forecast: ForecastResponse; trend: TrendDisplay }) {
  const theme = useTheme();
  const buffer = describeBufferBadge(forecast);
  const hasBuffer = forecast.safety_buffer_minor > 0;

  return (
    <Card>
      <View style={styles.cardHead}>
        <CardLabel>Kontostands-Prognose</CardLabel>
        <Badge label={buffer.label} tone={buffer.tone} />
      </View>

      {/* Eine Kurve ist fuer Vorlesehilfen stumm - die Ansage sitzt deshalb am
          umschliessenden Feld. */}
      <View accessible accessibilityLabel={trend.accessibilityLabel} testID="vorschau-verlauf">
        <TrendChart
          points={trend.points}
          thresholdMinor={hasBuffer ? forecast.safety_buffer_minor : undefined}
          thresholdLabel={hasBuffer ? "Sicherheitspuffer" : undefined}
        />
      </View>

      <View style={styles.legend}>
        <LegendRow
          dotTone="highlight"
          label={`Tiefstand ${trend.preposition} ${trend.lowWhen}`}
          minorUnits={trend.lowMinor}
        />
        {trend.highWhen !== null && trend.highMinor !== null ? (
          <LegendRow
            dotTone="positive"
            label={`Größter Zufluss ${trend.preposition} ${trend.highWhen}`}
            minorUnits={trend.highMinor}
          />
        ) : null}
        {hasBuffer ? (
          <LegendRow
            dotTone="warning"
            label="Sicherheitspuffer"
            minorUnits={forecast.safety_buffer_minor}
          />
        ) : (
          <Text style={[styles.footnote, { color: theme.textMuted }]}>
            Kein Sicherheitspuffer gepflegt – gerechnet wird gegen null.
          </Text>
        )}
      </View>
    </Card>
  );
}

function LegendRow({
  dotTone,
  label,
  minorUnits,
}: {
  dotTone: AccentTone;
  label: string;
  minorUnits: number;
}) {
  const theme = useTheme();
  return (
    <View style={styles.summaryRow}>
      <View style={styles.inlineLabel}>
        <View style={[styles.legendDot, { backgroundColor: toneColors(theme, dotTone).fill }]} />
        <Text style={[styles.bodyText, { color: theme.textSecondary }]} numberOfLines={1}>
          {label}
        </Text>
      </View>
      <Money minorUnits={minorUnits} tone={minorUnits < 0 ? "warning" : "plain"} />
    </View>
  );
}

// ---------------------------------------------------------------------------
// 3. "Kann ich mir das leisten?"

const WISH_PRESETS_MINOR = [10_000, 25_000, 50_000, 100_000, 250_000];

/**
 * Rechnet ausschliesslich mit dem, was die Vorschau ohnehin liefert:
 * Wunschbetrag vom Tiefstand abziehen, mit dem Sicherheitspuffer vergleichen.
 * Bewusst keine Beratung - kein "kauf lieber spaeter", keine Rate. Die App
 * sagt, was passiert; entscheiden tut der Nutzer.
 */
function AffordabilitySimulator({
  lowestMinor,
  lowestOnLabel,
  bufferMinor,
}: {
  lowestMinor: number;
  lowestOnLabel: string;
  bufferMinor: number;
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");

  const wishMinor = parseWishInput(input);
  const verdict = describeAffordability(lowestMinor, lowestOnLabel, bufferMinor, wishMinor);

  return (
    <>
      <Card
        tone="positive"
        onPress={() => setOpen(true)}
        accessibilityRole="button"
        accessibilityLabel="Kaufbarkeit simulieren"
        testID="vorschau-simulator-oeffnen"
      >
        <View style={styles.summaryRow}>
          <View style={styles.textBlock}>
            <Text style={[styles.bannerTitle, { color: theme.textPrimary }]}>
              „Kann ich mir das leisten?“
            </Text>
            <Text style={[styles.footnote, { color: theme.textSecondary }]}>
              Wunschbetrag gegen den Tiefstand der Prognose prüfen
            </Text>
          </View>
          <Badge label="Öffnen" tone="positive" />
        </View>
      </Card>

      <SheetModal
        visible={open}
        title="„Kann ich mir das leisten?“"
        onClose={() => setOpen(false)}
        footer={<PrimaryButton label="Simulation beenden" onPress={() => setOpen(false)} />}
      >
        <ScrollView contentContainerStyle={styles.sheetBody} keyboardShouldPersistTaps="handled">
          <View style={styles.summaryRow}>
            <Text style={[styles.bodyText, { color: theme.textSecondary }]}>Geplante Ausgabe</Text>
            {/* Privat-Modus hier bewusst uebersteuert: das ist die Eingabe des
                Nutzers von gerade eben, kein Bestandswert - verdeckt waere das
                Feld unbedienbar. */}
            <Money minorUnits={wishMinor} size="display" tone="plain" isPrivacyMode={false} />
          </View>

          <ChipRow>
            {WISH_PRESETS_MINOR.map((preset) => (
              <Chip
                key={preset}
                label={formatMinorUnitsCompact(preset)}
                selected={preset === wishMinor}
                onPress={() => setInput(String(Math.round(preset / 100)))}
                accessibilityLabel={`Wunschbetrag ${formatMinorUnits(preset)}`}
              />
            ))}
          </ChipRow>

          <Numpad
            onPress={(key) => setInput((current) => applyNumpadKey(current, key))}
            onClear={() => setInput("")}
            testID="vorschau-simulator-numpad"
          />

          <Card tone={verdict.tone} testID="vorschau-simulator-ergebnis">
            <Badge label={verdict.title} tone={verdict.tone} />
            <View style={styles.summaryRow}>
              <Text style={[styles.bodyText, { color: theme.textSecondary }]}>
                Tiefstand nach der Ausgabe
              </Text>
              <Money
                minorUnits={verdict.projectedMinor}
                size="medium"
                tone={verdict.projectedMinor < 0 ? "warning" : "plain"}
              />
            </View>
            <Text style={[styles.bodyText, { color: theme.textPrimary }]}>{verdict.detail}</Text>
            <View style={styles.summaryRow}>
              <Text style={[styles.footnote, { color: theme.textMuted }]}>
                {bufferMinor > 0 ? "Sicherheitspuffer" : "Kein Puffer gepflegt – Schwelle"}
              </Text>
              <Money minorUnits={bufferMinor} tone="muted" />
            </View>
          </Card>
        </ScrollView>
      </SheetModal>
    </>
  );
}

/** Eine leere oder halbe Eingabe ist 0, kein Fehler - getippt wird Ziffer fuer Ziffer. */
function parseWishInput(raw: string): number {
  if (raw.trim().length === 0) return 0;
  try {
    return Math.max(0, parseAmountToMinorUnits(raw));
  } catch {
    return 0;
  }
}

/** Ziffernblock als Text fuehren, damit aus "0,05" nicht "0,5" wird. */
function applyNumpadKey(current: string, key: NumpadKey): string {
  if (key === "back") return current.slice(0, -1);
  if (key === ",") {
    if (current.includes(",")) return current;
    return current.length === 0 ? "0," : `${current},`;
  }
  const [whole = "", fraction] = current.split(",");
  if (fraction !== undefined && fraction.length >= 2) return current;
  if (fraction === undefined && whole.length >= 7) return current;
  if (current === "0") return key;
  return `${current}${key}`;
}

interface AffordVerdict {
  tone: AccentTone;
  title: string;
  detail: string;
  projectedMinor: number;
}

function describeAffordability(
  lowestMinor: number,
  lowestOnLabel: string,
  bufferMinor: number,
  wishMinor: number,
): AffordVerdict {
  const projectedMinor = lowestMinor - wishMinor;

  if (projectedMinor < 0) {
    return {
      tone: "warning",
      title: `Engpass am ${lowestOnLabel}`,
      detail: "Der Tiefstand der Prognose rutscht damit unter null.",
      projectedMinor,
    };
  }
  // Ohne gepflegten Puffer ist die Schwelle 0 - dieser Fall tritt dann nie ein
  // und die Oberflaeche sagt darunter ausdruecklich, wogegen gerechnet wurde.
  if (projectedMinor < bufferMinor) {
    return {
      tone: "highlight",
      title: "Knapper Puffer",
      detail: "Der Tiefstand bleibt über null, aber unter deinem Sicherheitspuffer.",
      projectedMinor,
    };
  }
  return {
    tone: "positive",
    title: "Unbedenklich",
    detail:
      bufferMinor > 0
        ? "Der Tiefstand bleibt über deinem Sicherheitspuffer."
        : "Der Tiefstand bleibt über null.",
    projectedMinor,
  };
}

// ---------------------------------------------------------------------------
// 4. Kennzahlen

/**
 * Betraege laufen zusaetzlich als Rohwert durch <Money> (Monospace,
 * Privat-Modus). Beschriftung, Hinweis und Ton bleiben beim gemeinsamen
 * Presenter - stuenden sie hier, koennten Karte und Screen fuer denselben
 * Haushalt Verschiedenes behaupten.
 */
function figureMinorUnits(forecast: ForecastResponse, key: string): number | null {
  switch (key) {
    case "lowest":
      return forecast.metrics.lowest_balance_minor;
    case "needed-negative":
      return forecast.metrics.needed_against_negative_minor;
    case "needed-buffer":
      return forecast.metrics.needed_for_buffer_minor;
    default:
      return null;
  }
}

function FiguresCard({ forecast }: { forecast: ForecastResponse }) {
  const theme = useTheme();

  return (
    <Card>
      <CardLabel>Kennzahlen</CardLabel>
      <View style={styles.tileGrid}>
        {describeForecastFigures(forecast).map((figure) => {
          const minorUnits = figureMinorUnits(forecast, figure.key);
          return (
            <View key={figure.key} style={styles.tileCell}>
              <StatTile
                label={figure.label}
                tone={toneToAccent(figure.tone)}
                caption={figure.hint}
                value={
                  minorUnits === null ? (
                    <Text style={[styles.tileValue, { color: theme.textPrimary }]}>
                      {figure.value}
                    </Text>
                  ) : (
                    <Money
                      minorUnits={minorUnits}
                      tone={figure.tone === "critical" ? "warning" : "plain"}
                    />
                  )
                }
              />
            </View>
          );
        })}
      </View>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 5. Ursachenkette

function chainMinorUnits(forecast: ForecastResponse, key: string): number | null {
  if (key === "cause") return forecast.cause?.total_minor ?? null;
  if (key === "lowest") return forecast.metrics.lowest_balance_minor;
  return null;
}

function ChainCard({ forecast }: { forecast: ForecastResponse }) {
  const theme = useTheme();
  const chain = describeForecastChain(forecast);
  if (chain.length === 0) return null;

  return (
    <Card>
      <CardLabel>Wie es dazu kommt</CardLabel>
      {chain.map((step, index) => {
        const minorUnits = chainMinorUnits(forecast, step.key);
        return (
          <View
            key={step.key}
            style={styles.chainStep}
            accessibilityLabel={step.accessibilityLabel}
          >
            <View style={styles.chainRail}>
              <View
                style={[
                  styles.chainDot,
                  { backgroundColor: toneColors(theme, toneToAccent(step.tone)).fill },
                ]}
              />
              {index < chain.length - 1 ? (
                <View style={[styles.chainLine, { backgroundColor: theme.border }]} />
              ) : null}
            </View>
            <View style={styles.chainBody}>
              <Text style={[styles.footnote, { color: theme.textSecondary }]}>{step.when}</Text>
              <Text style={[styles.rowTitle, { color: theme.textPrimary }]}>{step.what}</Text>
              {minorUnits === null ? null : (
                <Money
                  minorUnits={minorUnits}
                  tone={step.tone === "critical" ? "warning" : "plain"}
                />
              )}
            </View>
          </View>
        );
      })}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 6. Naechste Zahlungen

function PaymentsCard({
  upcoming,
  isLoading,
  isError,
  onRetry,
}: {
  upcoming: ForecastDayGroup[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
}) {
  const theme = useTheme();
  const groups = selectNextPayments(upcoming);
  const displays = describePaymentGroups(groups);

  return (
    <Card>
      <CardLabel>Nächste Zahlungen</CardLabel>

      {isLoading ? (
        <InlineQueryState
          status="loading"
          skeletonRows={3}
          skeletonRowHeight={40}
          testID="vorschau-zahlungen-loading"
        />
      ) : null}
      {!isLoading && isError ? (
        <InlineQueryState
          status="error"
          title="Zahlungen konnten nicht geladen werden"
          onRetry={onRetry}
          testID="vorschau-zahlungen-error"
        />
      ) : null}
      {!isLoading && !isError && displays.length === 0 ? (
        <Text style={[styles.bodyText, { color: theme.textSecondary }]}>
          Keine erwarteten Zahlungen im Zeitraum.
        </Text>
      ) : null}

      {/* Die Rohgruppe liefert die Betraege fuer <Money>, der Presenter die
          Texte. describePaymentGroups() bildet 1:1 ab, deshalb passt der Index. */}
      {displays.flatMap((display, index) => {
        const group = groups[index];
        if (!group) return [];
        return [
          <View key={display.key} style={styles.paymentGroup}>
            <View style={styles.summaryRow} accessibilityLabel={display.accessibilityLabel}>
              <View style={styles.inlineLabel}>
                <Text style={[styles.rowTitle, { color: theme.textPrimary }]}>
                  {display.dayLabel}
                </Text>
                {display.isHeavy ? (
                  <Badge label="Mehrere große Fälligkeiten" tone="highlight" size="small" />
                ) : null}
              </View>
              <Money minorUnits={group.total_minor} />
            </View>

            {group.flows.map((flow, flowIndex) => (
              <DataRow
                key={`${flow.label}-${flow.amount_minor}-${flowIndex}`}
                title={flow.label}
                trailing={<Money minorUnits={flow.amount_minor} />}
                divider={flowIndex < group.flows.length - 1}
              />
            ))}

            <View
              style={[styles.summaryRow, styles.paymentFooter, { borderTopColor: theme.border }]}
            >
              <Text style={[styles.bodyText, { color: theme.textSecondary }]}>
                Danach verfügbar
              </Text>
              <Money
                minorUnits={group.balance_after_minor}
                tone={group.balance_after_minor < 0 ? "warning" : "plain"}
              />
            </View>
          </View>,
        ];
      })}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 7. Monate

function MonthsSection({ forecast, horizon }: { forecast: ForecastResponse; horizon: Horizon }) {
  const theme = useTheme();
  // Sechs Spalten laufen auf dem Telefon aus dem Bild (Plan 15.1) - jede
  // Monatszeile ist deshalb eine eigene Karte mit Beschriftung je Wert.
  const limit = horizon === "12m" ? HORIZON_LONG_MONTHS : MONTH_CARD_LIMIT;
  const cards = describeForecastMonths(forecast, limit);
  if (cards.length === 0) return null;

  return (
    <View style={styles.section}>
      <CardLabel>Monate</CardLabel>
      {cards.flatMap((card, index) => {
        const month = forecast.monthly[index];
        if (!month) return [];
        return [
          <Card key={card.key} tone={card.isCritical ? "warning" : undefined} density="compact">
            <View accessible accessibilityLabel={card.accessibilityLabel} style={styles.monthBody}>
              <View style={styles.summaryRow}>
                <Text style={[styles.monthTitle, { color: theme.textPrimary }]}>{card.title}</Text>
                {card.isCritical ? <Badge label="Kritisch" tone="warning" /> : null}
              </View>
              <MonthRow label="Einnahmen" minorUnits={month.income_minor} tone="auto" />
              <MonthRow label="Fixkosten" minorUnits={month.fixed_minor} tone="plain" />
              <MonthRow label="Variabel" minorUnits={month.variable_minor} tone="plain" />
              <MonthRow
                label="Saldo"
                minorUnits={month.net_minor}
                tone={month.net_minor < 0 ? "warning" : "positive"}
              />
              <MonthRow
                label="Endstand"
                minorUnits={month.closing_balance_minor}
                tone={month.closing_balance_minor < 0 ? "warning" : "plain"}
              />
            </View>
          </Card>,
        ];
      })}
    </View>
  );
}

function MonthRow({
  label,
  minorUnits,
  tone,
}: {
  label: string;
  minorUnits: number;
  tone: "auto" | "plain" | "positive" | "warning";
}) {
  const theme = useTheme();
  return (
    <View style={styles.summaryRow}>
      <Text style={[styles.bodyText, { color: theme.textSecondary }]}>{label}</Text>
      <Money minorUnits={minorUnits} tone={tone} />
    </View>
  );
}

// ---------------------------------------------------------------------------
// 8. Berechnungsgrundlage

function basisMinorUnits(forecast: ForecastResponse, key: string): number | null {
  switch (key) {
    case "opening":
      return forecast.opening_liquidity_minor;
    case "variable":
      return forecast.metrics.variable_monthly_minor;
    case "buffer":
      return forecast.safety_buffer_minor;
    case "reserve":
      return forecast.bound_reserve_minor;
    default:
      return null;
  }
}

function BasisCard({ forecast }: { forecast: ForecastResponse }) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);

  return (
    <Card>
      <CardLabel>So wird gerechnet</CardLabel>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={
          open ? "Berechnungsgrundlage einklappen" : "Berechnungsgrundlage anzeigen"
        }
        accessibilityState={{ expanded: open }}
        onPress={() => setOpen((current) => !current)}
        style={({ pressed }) => [styles.disclosure, { opacity: pressed ? 0.7 : 1 }]}
      >
        <Text style={[styles.rowTitle, { color: theme.textPrimary }]}>
          {open ? "Grundlage ausblenden" : "Grundlage anzeigen"}
        </Text>
        <Text style={[styles.rowTitle, { color: theme.textSecondary }]}>{open ? "⌃" : "⌄"}</Text>
      </Pressable>

      {open
        ? describeForecastBasis(forecast).map((row) => {
            const minorUnits = row.isAmount ? basisMinorUnits(forecast, row.key) : null;
            return (
              <View key={row.key} style={styles.basisRow}>
                <Text style={[styles.bodyText, { color: theme.textSecondary }]}>{row.label}</Text>
                <View style={styles.basisValue}>
                  {minorUnits === null ? (
                    <Text style={[styles.rowTitle, styles.rightText, { color: theme.textPrimary }]}>
                      {row.value}
                    </Text>
                  ) : (
                    <Money minorUnits={minorUnits} tone="plain" />
                  )}
                  {row.hint ? (
                    <Text style={[styles.footnote, styles.rightText, { color: theme.textMuted }]}>
                      {row.hint}
                    </Text>
                  ) : null}
                </View>
              </View>
            );
          })
        : null}

      <Text style={[styles.footnote, { color: theme.textMuted }]}>
        Stand {formatDueDate(forecast.as_of)}. Was-wäre-wenn im Web bearbeiten.
      </Text>
    </Card>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    gap: space.md,
    flexGrow: 1,
  },
  section: {
    gap: space.sm,
  },
  cardHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  textBlock: {
    flexShrink: 1,
    gap: space.xs,
  },
  badgeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: space.sm,
  },
  verdictHeadline: {
    fontSize: typeScale.large,
    fontWeight: "700",
    lineHeight: leading(typeScale.large, "tight"),
  },
  bodyText: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
    flexShrink: 1,
  },
  rowTitle: {
    fontSize: typeScale.base,
    fontWeight: "700",
    flexShrink: 1,
  },
  footnote: {
    fontSize: typeScale.micro,
    flexShrink: 1,
  },
  summaryRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
    minHeight: 28,
  },
  inlineLabel: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    flexShrink: 1,
  },
  rightText: {
    textAlign: "right",
  },
  legend: {
    gap: space.xs,
  },
  legendDot: {
    width: 10,
    height: 10,
    borderRadius: radii.pill,
  },
  bannerTitle: {
    fontSize: typeScale.base,
    fontWeight: "800",
  },
  sheetBody: {
    padding: space.xl,
    gap: space.lg,
  },
  tileGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: space.sm,
  },
  tileCell: {
    flexBasis: "47%",
    flexGrow: 1,
    flexDirection: "row",
  },
  tileValue: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  chainStep: {
    flexDirection: "row",
    gap: space.md,
  },
  chainRail: {
    alignItems: "center",
    width: 10,
  },
  chainDot: {
    width: 10,
    height: 10,
    borderRadius: radii.pill,
    marginTop: space.xs,
  },
  chainLine: {
    width: 2,
    flex: 1,
    marginVertical: space.xs,
  },
  chainBody: {
    flex: 1,
    paddingBottom: space.md,
    gap: space.hair,
  },
  paymentGroup: {
    gap: space.xs,
  },
  paymentFooter: {
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: space.sm,
  },
  monthBody: {
    gap: space.xs,
  },
  monthTitle: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  disclosure: {
    minHeight: MIN_TOUCH,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  basisRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: space.md,
    paddingVertical: space.xs,
  },
  basisValue: {
    alignItems: "flex-end",
    flexShrink: 1,
  },
});
