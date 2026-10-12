import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { useMemo, useState } from "react";
import { Alert, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import type {
  Contract,
  ContractDirection,
  ContractStatus,
  ContractTermSoon,
  ContractType,
  ReviewItemContractAmountChangeCheck,
} from "../../src/api/types";
import { EmptyState } from "../../src/components/EmptyState";
import { FixedCostsRatioCard } from "../../src/components/FixedCostsRatioCard";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { SCREEN_EDGES_WITH_TOP, ScreenContainer } from "../../src/components/ScreenContainer";
import {
  SegmentedControl,
  type SegmentedControlOption,
} from "../../src/components/SegmentedControl";
import { SheetModal } from "../../src/components/SheetModal";
import {
  Badge,
  Card,
  CardLabel,
  DataRow,
  IconButton,
  Money,
  ScreenHeader,
  StatTile,
} from "../../src/components/ui";
import { useCategories } from "../../src/hooks/useCategories";
import {
  UNCATEGORIZED_SECTION_KEY,
  groupContractsByCategory,
  groupContractsByPerson,
  useContractContext,
  useContractSummary,
  useContracts,
  useDeleteContract,
  useSetContractStatus,
  type ContractSection,
} from "../../src/hooks/useContracts";
import { usePeople } from "../../src/hooks/usePeople";
import { useReviewInbox } from "../../src/hooks/useReview";
import {
  leading,
  letterSpacingLabel,
  MIN_TOUCH,
  space,
  toneColors,
  typeScale,
  useMaskedAmount,
  useTheme,
  type AccentTone,
} from "../../src/theme";
import { formatDueDate, formatShortDate, isContractNotStarted } from "../../src/utils/dates";
import { formatMinorUnits } from "../../src/utils/money";
import {
  formatContractStatus,
  formatFrequency,
  formatIncomeKind,
  toMonthlyMinor,
} from "../../src/utils/financeLabels";

/**
 * Vertragsuebersicht, seit dem Redesign eigener Bottom-Tab statt Unterpunkt
 * von Mehr (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Phase 1). Sparziele/Wunschliste,
 * die vorher an dieser Tab-Position lagen, sind unter Mehr - Sparen & Wünsche
 * erreichbar (app/mehr/sparziele.tsx).
 *
 * Aufbau nach docs/mobile_redesign_showcase.html, Abschnitt 4: eigene
 * mitscrollende Ueberschrift, dann Warnkarte fuer die naechste ablaufende
 * Kuendigungsfrist, dann Summen, Fixkostenquote und die Vertraege als
 * Kartenliste. Die Gliederung bleibt fachlich wie bisher: Ausgaben nach
 * Kategorie, Einnahmen nach Person (Liquiditaetsplan Stufe 6) - der Showcase
 * kennt nur eine flache Liste, aber "wer verdient wie viel" ist die Frage, die
 * die Einnahmenseite ueberhaupt beantwortet.
 *
 * Kritische Aktionen liegen hinter einem Aktionsmenue und nicht hinter einer
 * Wischgeste: ein versehentliches Wischen darf keinen Vertrag beenden.
 */

const DIRECTION_OPTIONS: SegmentedControlOption<ContractDirection>[] = [
  { value: "expense", label: "Ausgaben" },
  { value: "income", label: "Einnahmen" },
];

type IconName = React.ComponentProps<typeof Ionicons>["name"];

/**
 * Symbol der Avatarkachel je Vertragsart. Der Showcase setzt hier Logos und
 * Emoji; ein Symbol aus dem vorhandenen Satz kommt ohne Datenpflege aus und
 * bleibt in Graustufen lesbar. Die FARBE der Kachel kommt aus dem Namen
 * (toneForLabel in Avatar), damit derselbe Anbieter ueberall gleich aussieht.
 */
const CONTRACT_TYPE_ICON: Record<ContractType, IconName> = {
  subscription: "repeat-outline",
  insurance: "shield-checkmark-outline",
  rent: "home-outline",
  loan: "cash-outline",
  installment: "card-outline",
  utility: "flash-outline",
  telecom: "wifi-outline",
  membership: "people-outline",
  donation: "heart-outline",
  savings: "trending-up-outline",
  income: "arrow-down-circle-outline",
  other: "document-text-outline",
};

/**
 * Dringlichkeitsstufen einer Kuendigungsfrist. Die 30-Tage-Kante ist
 * dieselbe, bei der der Server sein erstes 'term_ending'-Ereignis erzeugt;
 * die 14-Tage-Kante trennt davon ab, was diese Woche auf den Postweg muss.
 */
const DEADLINE_URGENT_DAYS = 14;
const DEADLINE_SOON_DAYS = 30;

function deadlineTone(daysLeft: number): AccentTone | null {
  if (daysLeft <= DEADLINE_URGENT_DAYS) return "warning";
  if (daysLeft <= DEADLINE_SOON_DAYS) return "highlight";
  return null;
}

/**
 * Restzeit in Worten. Wortgleich zur Kuendigungskarte in app/vertrag/[id].tsx.
 * Bewusst nicht aus DeadlineBadge importiert: das haengte die Vertragsliste an
 * das gesamte Versicherungsmodul, obwohl beide nur denselben Satz brauchen.
 */
function countdownLabel(daysLeft: number): string {
  if (daysLeft <= 0) return "heute";
  if (daysLeft === 1) return "morgen";
  return `noch ${daysLeft} Tage`;
}

/**
 * Tagesdatum fuer die schmale rechte Spalte. formatShortDate() laesst das Jahr
 * weg ("01.09."), was neben einem Betrag genau richtig ist - bei einem Datum
 * ausserhalb des laufenden Jahres waere "Beginnt 01.01." aber schlicht falsch
 * zu lesen, deshalb kommt die Jahreszahl dort dazu.
 */
function formatRowDate(isoDate: string, today: Date = new Date()): string {
  const short = formatShortDate(isoDate);
  const year = isoDate.slice(0, 4);
  return year === String(today.getFullYear()) ? short : `${short}${year}`;
}

/** Nicht aktive Vertraege: laufend-aber-anders gelb, abgeschlossen grau. */
function statusTone(status: ContractStatus): AccentTone {
  return status === "paused" || status === "cancelled" ? "highlight" : "neutral";
}

type MenuState = { contract: Contract } | null;

export default function VertraegeScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { canEdit } = useContractContext();
  const [direction, setDirection] = useState<ContractDirection>("expense");
  const [menu, setMenu] = useState<MenuState>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data, isLoading, isError, error, isFetching, refetch } = useContracts();
  const summary = useContractSummary();
  const categories = useCategories();
  const people = usePeople();
  const statusMutation = useSetContractStatus();
  const deleteMutation = useDeleteContract();

  const contracts = useMemo(
    () => (data ?? []).filter((contract) => contract.direction === direction),
    [data, direction],
  );

  // Einnahmen gliedern sich nach Person, Ausgaben nach Kategorie
  // (Liquiditaetsplan Stufe 6). Bewusst nicht beides gleichzeitig: die
  // Kategorie einer Einnahme beantwortet keine Frage, die sich jemand stellt -
  // "wer verdient wie viel" schon.
  const sections = useMemo(
    () =>
      direction === "income"
        ? groupContractsByPerson(contracts, people.data ?? [])
        : groupContractsByCategory(contracts, categories.data ?? []),
    [contracts, direction, categories.data, people.data],
  );

  const monthly =
    direction === "expense"
      ? summary.data?.monthly_expense_minor
      : summary.data?.monthly_income_minor;
  const yearly =
    direction === "expense"
      ? summary.data?.yearly_expense_minor
      : summary.data?.yearly_income_minor;

  // Fristen kommen fertig gerechnet aus der Zusammenfassung (0-120 Tage
  // Restlaufzeit). Gewarnt wird nur im Ausgaben-Tab: eine Kuendigungsfrist
  // gehoert zu einem Vertrag, den man zahlt, und der Tab startet ohnehin
  // immer dort.
  const dueDeadlines = useMemo(() => {
    const all = summary.data?.term_deadlines ?? [];
    return all
      .filter((entry) => entry.days_left <= DEADLINE_SOON_DAYS)
      .sort((a, b) => a.days_left - b.days_left);
  }, [summary.data]);
  const nextDeadline: ContractTermSoon | null =
    direction === "expense" ? (dueDeadlines[0] ?? null) : null;

  // Preissprung-Banner aus derselben Quelle wie die Review-Inbox
  // (GET /v1/review, `contract_amount_change_checks`): der Abgleich hat den
  // neuen Betrag bereits uebernommen, hier steht nur die Rueckfrage. Auf
  // diesem Bildschirm ist sie am Platz, weil die Fixkostensumme darueber
  // dieselbe Zahl bereits enthaelt.
  const reviewInbox = useReviewInbox();
  const amountChanges = useMemo(() => {
    const all = reviewInbox.data?.contract_amount_change_checks ?? [];
    return all
      .filter((check) => check.direction === direction)
      .sort((a, b) => Math.abs(b.delta_minor) - Math.abs(a.delta_minor));
  }, [reviewInbox.data, direction]);
  const nextAmountChange: ReviewItemContractAmountChangeCheck | null = amountChanges[0] ?? null;

  const reportError = (message: string) => {
    setActionError(message);
    Alert.alert("Aktion fehlgeschlagen", message);
  };

  const changeStatus = (contract: Contract, status: ContractStatus, label: string) => {
    setMenu(null);
    Alert.alert(label, `„${contract.name}“ wirklich ${label.toLowerCase()}?`, [
      { text: "Abbrechen", style: "cancel" },
      {
        text: label,
        style: status === "ended" || status === "not_a_contract" ? "destructive" : "default",
        onPress: () => {
          setActionError(null);
          statusMutation.mutate(
            { contractId: contract.id, input: { status } },
            { onError: (mutationError) => reportError(mutationError.message) },
          );
        },
      },
    ]);
  };

  const confirmDelete = (contract: Contract) => {
    setMenu(null);
    Alert.alert(
      "Vertrag löschen",
      `„${contract.name}“ wird endgültig gelöscht. Soll er nur nicht mehr vorgeschlagen werden, ist „Kein Vertrag“ die bessere Wahl.`,
      [
        { text: "Abbrechen", style: "cancel" },
        {
          text: "Löschen",
          style: "destructive",
          onPress: () => {
            setActionError(null);
            deleteMutation.mutate(contract.id, {
              onError: (mutationError) => reportError(mutationError.message),
            });
          },
        },
      ],
    );
  };

  return (
    <ScreenContainer edges={SCREEN_EDGES_WITH_TOP}>
      <FlatList<ContractSection>
        data={sections}
        keyExtractor={(section) => section.key}
        refreshControl={<RefreshControl refreshing={isFetching} onRefresh={() => void refetch()} />}
        contentContainerStyle={styles.listContent}
        ListHeaderComponent={
          <View style={styles.headerBlock}>
            <ScreenHeader
              title="Verträge & Abos"
              actions={
                canEdit ? (
                  <IconButton
                    icon="add"
                    accessibilityLabel="Neuen Vertrag anlegen"
                    onPress={() => router.push("/vertrag/neu")}
                  />
                ) : undefined
              }
            />

            <View style={styles.block}>
              <SegmentedControl
                options={DIRECTION_OPTIONS}
                value={direction}
                onChange={setDirection}
                accessibilityLabel="Ausgaben oder Einnahmen"
              />

              {nextDeadline ? (
                <DeadlineBanner
                  deadline={nextDeadline}
                  furtherCount={dueDeadlines.length - 1}
                  onOpen={() => router.push(`/vertrag/${nextDeadline.contract_id}`)}
                />
              ) : null}

              {nextAmountChange ? (
                <AmountChangeBanner
                  check={nextAmountChange}
                  furtherCount={amountChanges.length - 1}
                  onOpen={() => router.push(`/vertrag/${nextAmountChange.contract_id}`)}
                />
              ) : null}

              <Card>
                <View style={styles.cardHeader}>
                  <CardLabel>
                    {direction === "expense" ? "Laufende Verträge" : "Laufende Einnahmen"}
                  </CardLabel>
                  <Text style={[styles.cardMeta, { color: theme.textMuted }]}>
                    {contracts.length === 1 ? "1 Eintrag" : `${contracts.length} Einträge`}
                  </Text>
                </View>
                <View style={styles.tileRow}>
                  <StatTile
                    label="PRO MONAT"
                    value={
                      monthly === undefined ? (
                        <Text style={[styles.tilePlaceholder, { color: theme.textSecondary }]}>
                          —
                        </Text>
                      ) : (
                        <Money
                          minorUnits={monthly}
                          size="medium"
                          tone={direction === "income" ? "positive" : "plain"}
                        />
                      )
                    }
                  />
                  <StatTile
                    label="PRO JAHR"
                    value={
                      yearly === undefined ? (
                        <Text style={[styles.tilePlaceholder, { color: theme.textSecondary }]}>
                          —
                        </Text>
                      ) : (
                        <Money
                          minorUnits={yearly}
                          size="medium"
                          tone={direction === "income" ? "positive" : "plain"}
                        />
                      )
                    }
                  />
                </View>
              </Card>
            </View>

            {/* Bringt eigene Aussenabstaende mit und steht deshalb
                ausserhalb des eingerueckten Blocks. */}
            {direction === "expense" ? <FixedCostsRatioCard /> : null}

            {actionError ? (
              <Text style={[styles.error, { color: theme.warning }]} accessibilityRole="alert">
                {actionError}
              </Text>
            ) : null}
          </View>
        }
        renderItem={({ item: section }) => (
          <View style={styles.block}>
            <Card>
              <View style={styles.cardHeader}>
                <CardLabel>{section.title}</CardLabel>
                <Text style={[styles.cardMeta, { color: theme.textMuted }]}>
                  {section.data.length === 1 ? "1 Vertrag" : `${section.data.length} Verträge`}
                </Text>
              </View>
              <View>
                {section.data.map((contract, index) => (
                  <ContractRow
                    key={contract.id}
                    contract={contract}
                    // Bei Einnahmen steht die Art an der Stelle, an der bei
                    // Ausgaben die Kategorie steht - die Person nennt bereits
                    // die Karte. "Ohne Art" wird ausdruecklich angezeigt: eine
                    // fehlende Zuordnung ist eine offene Aufgabe.
                    leadingLabel={
                      contract.direction === "income"
                        ? formatIncomeKind(contract.income_kind)
                        : section.key === UNCATEGORIZED_SECTION_KEY
                          ? null
                          : section.title
                    }
                    canEdit={canEdit}
                    divider={index < section.data.length - 1}
                    onOpen={() => router.push(`/vertrag/${contract.id}`)}
                    onOpenMenu={() => setMenu({ contract })}
                  />
                ))}
              </View>
            </Card>
          </View>
        )}
        ListEmptyComponent={
          isLoading ? (
            <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={56} />
          ) : isError ? (
            <InlineQueryState
              status="error"
              title="Verträge nicht geladen"
              message={error?.message}
              onRetry={() => void refetch()}
            />
          ) : (
            <EmptyState
              title="Keine Verträge"
              description="Miete, Versicherungen oder Abonnements hier erfassen - oder in der Review-Inbox aus dem Buchungsverlauf übernehmen."
            />
          )
        }
      />

      <SheetModal
        visible={menu !== null}
        title={menu?.contract.name ?? ""}
        onClose={() => setMenu(null)}
      >
        {menu ? (
          <View style={styles.menu}>
            <MenuItem
              label="Bearbeiten"
              icon="create-outline"
              onPress={() => {
                const id = menu.contract.id;
                setMenu(null);
                router.push(`/vertrag/${id}`);
              }}
            />
            {menu.contract.status !== "ended" ? (
              <MenuItem
                label="Als beendet markieren"
                hint="Zählt danach nicht mehr in die Fixkosten."
                icon="stop-circle-outline"
                tone="warning"
                onPress={() => changeStatus(menu.contract, "ended", "Beenden")}
              />
            ) : null}
            {menu.contract.status === "active" ? (
              <MenuItem
                label="Pausieren"
                hint="Vorübergehend ausgesetzt, bleibt erhalten."
                icon="pause-outline"
                tone="highlight"
                onPress={() => changeStatus(menu.contract, "paused", "Pausieren")}
              />
            ) : null}
            {menu.contract.status === "ended" || menu.contract.status === "not_a_contract" ? (
              <MenuItem
                label="Wieder aktivieren"
                icon="play-outline"
                tone="positive"
                onPress={() => changeStatus(menu.contract, "active", "Aktivieren")}
              />
            ) : null}
            {menu.contract.status !== "not_a_contract" && menu.contract.status !== "ended" ? (
              <MenuItem
                label="Kein Vertrag"
                hint="Wird nie wieder als Fixkosten vorgeschlagen."
                icon="close-circle-outline"
                onPress={() => changeStatus(menu.contract, "not_a_contract", "Verneinen")}
              />
            ) : null}
            <MenuItem
              label="Löschen"
              hint="Endgültig, samt Betragsverlauf."
              icon="trash-outline"
              tone="warning"
              divider={false}
              onPress={() => confirmDelete(menu.contract)}
            />
          </View>
        ) : null}
      </SheetModal>
    </ScreenContainer>
  );
}

/**
 * Warnkarte fuer die naechste ablaufende Kuendigungsfrist (Showcase
 * Abschnitt 4, "Price Jump Alert Banner").
 *
 * Sie nennt Datum, Restzeit und die Folge des Verstreichens - und fuehrt zum
 * Vertrag. Bewusst KEIN Kuendigungsassistent und keine Paragraphenangabe wie
 * im Showcase: die App verschickt nichts und darf nicht rechtlich beraten.
 */
function DeadlineBanner({
  deadline,
  furtherCount,
  onOpen,
}: {
  deadline: ContractTermSoon;
  /** Weitere Vertraege mit Frist im selben Fenster. */
  furtherCount: number;
  onOpen: () => void;
}) {
  const theme = useTheme();

  return (
    <Card tone="warning">
      <View style={styles.cardHeader}>
        <Text style={[styles.bannerLabel, { color: theme.warning }]}>Kündigungsfrist läuft ab</Text>
        <Badge tone="warning" label={countdownLabel(deadline.days_left)} />
      </View>
      <Text style={[styles.bannerText, { color: theme.textPrimary }]}>
        „{deadline.name}“ muss bis zum {formatDueDate(deadline.deadline_on)} gekündigt sein. Sonst
        läuft der Vertrag bis {formatDueDate(deadline.term_ends_on)} weiter.
      </Text>
      {furtherCount > 0 ? (
        <Text style={[styles.bannerNote, { color: theme.textSecondary }]}>
          {furtherCount === 1 ? "Ein weiterer Vertrag" : `${furtherCount} weitere Verträge`} mit
          Frist in den nächsten {DEADLINE_SOON_DAYS} Tagen.
        </Text>
      ) : null}
      <PrimaryButton variant="danger" size="compact" label="Vertrag öffnen" onPress={onOpen} />
    </Card>
  );
}

/**
 * "Preissprung erkannt" (Showcase, Abschnitt 4). Bewusst dieselbe Bauform wie
 * das Fristenbanner darueber: eine Karte, ein ausgeschriebener Satz, ein Weg
 * hinein. Der Monatsbetrag der Aenderung steht in der Pille, weil genau er
 * die Fixkostensumme dieses Bildschirms verschiebt.
 */
function AmountChangeBanner({
  check,
  furtherCount,
  onOpen,
}: {
  check: ReviewItemContractAmountChangeCheck;
  /** Weitere Vertraege mit erkannter Betragsaenderung. */
  furtherCount: number;
  onOpen: () => void;
}) {
  const theme = useTheme();
  // Betraege im Fliesstext und in der Pille: <Money> kann dort nicht stehen,
  // der Privat-Modus muss sie trotzdem verdecken.
  const mask = useMaskedAmount();
  const increased = check.delta_minor > 0;
  const tone: AccentTone = increased ? "warning" : "positive";
  const monthlyDeltaMinor = toMonthlyMinor(Math.abs(check.delta_minor), check.frequency);

  return (
    <Card tone={tone}>
      <View style={styles.cardHeader}>
        <Text style={[styles.bannerLabel, { color: toneColors(theme, tone).text }]}>
          {increased ? "Preissprung erkannt" : "Preissenkung erkannt"}
        </Text>
        <Badge
          tone={tone}
          label={`${increased ? "+" : "−"}${mask(formatMinorUnits(monthlyDeltaMinor))} / Monat`}
        />
      </View>
      <Text style={[styles.bannerText, { color: theme.textPrimary }]}>
        „{check.name}“ läuft seit {formatDueDate(check.occurred_on)} mit{" "}
        {mask(formatMinorUnits(check.new_amount_minor))} statt{" "}
        {mask(formatMinorUnits(check.old_amount_minor))} {formatFrequency(check.frequency)}.
      </Text>
      {furtherCount > 0 ? (
        <Text style={[styles.bannerNote, { color: theme.textSecondary }]}>
          {furtherCount === 1
            ? "Ein weiterer Vertrag hat sich geändert."
            : `${furtherCount} weitere Verträge haben sich geändert.`}
        </Text>
      ) : null}
      <PrimaryButton size="compact" label="Vertrag öffnen" onPress={onOpen} />
    </Card>
  );
}

/**
 * Eine Vertragszeile: Kachel, Name mit Statuspille, darunter Kategorie und
 * Rhythmus, rechts Betrag und die naechste Kante (Fristdatum, sonst
 * Faelligkeit). Das Overflow-Icon ist ein eigenstaendiges Tippziel fuer das
 * Aktionsmenue, kein verschachtelter Ersatz fuer die Zeile (RN loest
 * ueberlappende Pressables ueber den innersten Treffer auf).
 *
 * Die Frist steht farbig UND ausgeschrieben ("Frist 28.08.") - eine Farbe
 * allein waere fuer farbfehlsichtige Nutzer keine Information.
 */
function ContractRow({
  contract,
  leadingLabel,
  canEdit,
  divider,
  onOpen,
  onOpenMenu,
}: {
  contract: Contract;
  /** Kategorie bei Ausgaben, Einnahmeart bei Einnahmen. */
  leadingLabel: string | null;
  canEdit: boolean;
  divider: boolean;
  onOpen: () => void;
  onOpenMenu: () => void;
}) {
  const theme = useTheme();

  const subtitle = [leadingLabel, formatFrequency(contract.frequency)]
    .filter((part): part is string => Boolean(part))
    .join(" • ");

  const notStarted = isContractNotStarted(contract.starts_on);
  const termDeadline =
    contract.term_deadline && contract.term_deadline.kind === "fixed_term"
      ? contract.term_deadline
      : null;

  // Reihenfolge der Dringlichkeit: ein noch nicht begonnener Vertrag kostet
  // heute nichts, eine ablaufende Frist ist die Kante, die man verpassen kann,
  // sonst zaehlt die naechste Abbuchung.
  const caption = notStarted
    ? `Beginnt ${formatRowDate(contract.starts_on!)}`
    : termDeadline
      ? `Frist ${formatRowDate(termDeadline.deadline_on)}`
      : `Nächste ${formatRowDate(contract.next_due_date)}`;
  const captionTone = termDeadline && !notStarted ? deadlineTone(termDeadline.days_left) : null;
  const captionColor = captionTone ? toneColors(theme, captionTone).text : theme.textMuted;

  return (
    <DataRow
      title={contract.name}
      subtitle={subtitle}
      avatarLabel={contract.provider_label ?? contract.name}
      avatarIcon={CONTRACT_TYPE_ICON[contract.contract_type]}
      titleBadge={
        contract.status === "active" ? null : (
          <Badge
            size="small"
            tone={statusTone(contract.status)}
            label={formatContractStatus(contract.status)}
          />
        )
      }
      trailing={
        <View style={styles.rowTrailing}>
          <View style={styles.rowAmount}>
            <Money
              minorUnits={
                contract.direction === "expense" ? -contract.amount_minor : contract.amount_minor
              }
            />
            <Text style={[styles.rowCaption, { color: captionColor }]} numberOfLines={1}>
              {caption}
            </Text>
          </View>
          {canEdit ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Aktionen für ${contract.name}`}
              onPress={onOpenMenu}
              style={styles.overflowButton}
            >
              <Ionicons name="ellipsis-horizontal" size={20} color={theme.textSecondary} />
            </Pressable>
          ) : null}
        </View>
      }
      onPress={onOpen}
      accessibilityRole="link"
      accessibilityLabel={`${contract.name} öffnen`}
      divider={divider}
    />
  );
}

function MenuItem({
  label,
  hint,
  icon,
  tone,
  divider = true,
  onPress,
}: {
  label: string;
  hint?: string;
  icon: IconName;
  /** Faerbt die Kachel; ohne Angabe bleibt sie neutral. */
  tone?: AccentTone;
  divider?: boolean;
  onPress: () => void;
}) {
  return (
    <DataRow
      title={label}
      subtitle={hint}
      avatarLabel={label}
      avatarIcon={icon}
      avatarTone={tone ?? "neutral"}
      onPress={onPress}
      divider={divider}
    />
  );
}

const styles = StyleSheet.create({
  listContent: {
    paddingBottom: space.xxxl,
    gap: space.md,
  },
  headerBlock: {
    gap: space.md,
  },
  /** Seitenrand aller Karten. ScreenHeader bringt seinen eigenen mit. */
  block: {
    paddingHorizontal: space.lg,
    gap: space.md,
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  cardMeta: {
    fontSize: typeScale.micro,
    fontWeight: "700",
  },
  tileRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  tilePlaceholder: {
    fontSize: typeScale.large,
    fontWeight: "700",
  },
  bannerLabel: {
    fontSize: typeScale.micro,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: letterSpacingLabel,
    flexShrink: 1,
  },
  bannerText: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  bannerNote: {
    fontSize: typeScale.caption,
  },
  error: {
    fontSize: typeScale.caption,
    paddingHorizontal: space.lg,
  },
  rowTrailing: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.xs,
  },
  rowAmount: {
    alignItems: "flex-end",
    gap: space.hair,
  },
  rowCaption: {
    fontSize: typeScale.micro,
    fontWeight: "700",
  },
  overflowButton: {
    width: 36,
    minHeight: MIN_TOUCH,
    alignItems: "center",
    justifyContent: "center",
  },
  menu: {
    paddingHorizontal: space.xl,
    paddingBottom: space.xl,
  },
});
