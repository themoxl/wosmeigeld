import { Stack, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import {
  Alert,
  FlatList,
  Linking,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { apiErrorDetail } from "../../src/api/client";
import type {
  CostItem,
  CostItemStatus,
  FinancingSourceStatus,
  FinancingSourceType,
  FundingMatch,
  FundingMatchStatus,
  ProjectScenario,
  ProjectStatus,
  TransactionCandidate,
} from "../../src/api/types";
import { EmptyState } from "../../src/components/EmptyState";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { SegmentedControl } from "../../src/components/SegmentedControl";
import { SheetModal } from "../../src/components/SheetModal";
import { TextField } from "../../src/components/TextField";
import {
  Badge,
  Card,
  CardLabel,
  Chip,
  ChipRow,
  DataRow,
  IconButton,
  Money,
  StatTile,
} from "../../src/components/ui";
import { useConnectivity } from "../../src/hooks/useConnectivity";
import {
  useActuals,
  useCostItems,
  useCreateActual,
  useFinancingSources,
  useFundingMatches,
  useFundingProgram,
  useProject,
  useProjectContext,
  useProjectScenarios,
  useScenarioCalculation,
  useTransactionCandidates,
  useUpdateCostItem,
} from "../../src/hooks/useProjects";
import {
  leading,
  MIN_TOUCH,
  radii,
  space,
  typeScale,
  useTheme,
  type AccentTone,
} from "../../src/theme";

/**
 * Vorhaben-Detail (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md, Abschnitt 9, P5).
 *
 * Vier Register wie im Plan gefordert: Uebersicht, Kosten, Finanzierung,
 * Foerderung. Alle Kennzahlen kommen unveraendert aus denselben
 * Server-Antworten wie apps/web/src/pages/ProjectDetailPage.tsx (P5-Abnahme:
 * "Web und Mobile zeigen identische Serverkennzahlen") - hier wird nichts
 * finanziell nachgerechnet, hoechstens eine reine Anzeige-Subtraktion aus zwei
 * bereits gelieferten Zahlen (Rest = Forecast - Ist, exakt wie web's
 * CostPlanTable).
 *
 * Schreibaktionen sind bewusst auf die zwei vom Plan genannten Schnellaktionen
 * beschraenkt (Kostenposition als bezahlt markieren, Banktransaktion
 * zuordnen) plus die reine Anzeige-Umschaltung des betrachteten Szenarios.
 * Finanzierungsquellen-Bearbeitung, Darlehensplaene, Foerderprofil und
 * Szenario-Auswahl/-Klon sind komplexe, mehrstufige Web-Flows und bleiben dort
 * (Plan: "kein kompletter Darlehensphasen-Editor auf Mobile").
 */

type ProjectTab = "uebersicht" | "kosten" | "finanzierung" | "foerderung";

const TAB_LABELS: { value: ProjectTab; label: string }[] = [
  { value: "uebersicht", label: "Übersicht" },
  { value: "kosten", label: "Kosten" },
  { value: "finanzierung", label: "Finanzierung" },
  { value: "foerderung", label: "Förderung" },
];

const PROJECT_STATUS_LABELS: Record<ProjectStatus, string> = {
  idea: "Idee",
  planning: "Planung",
  approved: "Bestätigt",
  active: "Aktiv",
  completed: "Abgeschlossen",
  cancelled: "Storniert",
  archived: "Archiviert",
};

const WARNING_LABELS: Record<string, string> = {
  cost_schedule_incomplete:
    "Zahlungsplan unvollständig: Die Summe der Abschläge entspricht noch nicht dem Forecast jeder Position.",
  calculation_horizon_reached:
    "Der Rechenhorizont wurde erreicht, ohne dass die Schuld getilgt ist.",
  funding_gap: "Geplante Finanzierung deckt die Projektkosten noch nicht.",
  secured_funding_gap: "Gesicherte Finanzierung deckt die Projektkosten noch nicht.",
  liquidity_reserve_breached: "Die Mindestreserve wird in mindestens einem Monat unterschritten.",
};

const COST_STATUS_LABELS: Record<CostItemStatus, string> = {
  estimated: "Geschätzt",
  quoted: "Angebot",
  committed: "Beauftragt",
  partially_paid: "Teilweise bezahlt",
  paid: "Bezahlt",
  cancelled: "Storniert",
};

const COST_STATUS_TONES: Record<CostItemStatus, AccentTone> = {
  estimated: "neutral",
  quoted: "info",
  committed: "info",
  partially_paid: "highlight",
  paid: "positive",
  cancelled: "neutral",
};

const SOURCE_TYPE_LABELS: Record<FinancingSourceType, string> = {
  cash_equity: "Eigenkapital (Konto)",
  asset_sale: "Vermögensverkauf",
  commercial_loan: "Bankdarlehen",
  subsidized_loan: "Förderkredit",
  public_grant: "Zuschuss",
  private_contribution: "Private Zuwendung",
  non_cash_equity: "Eigen-/Sachleistung (nicht Cash)",
  other: "Sonstiges",
};

const SOURCE_STATUS_LABELS: Record<FinancingSourceStatus, string> = {
  idea: "Idee",
  requested: "Beantragt",
  likely: "Wahrscheinlich",
  approved: "Genehmigt",
  contracted: "Vertraglich fixiert",
  received: "Erhalten",
  rejected: "Abgelehnt",
};

const MATCH_STATUS_LABELS: Record<FundingMatchStatus, string> = {
  eligible: "Förderfähig",
  potentially_eligible: "Möglicherweise förderfähig",
  ineligible: "Nicht förderfähig",
  unknown: "Unklar",
};

/** Der Status steht immer ausgeschrieben daneben - die Farbe ist nur Beschleuniger. */
function matchStatusTone(status: FundingMatchStatus): AccentTone {
  if (status === "eligible") return "positive";
  if (status === "potentially_eligible") return "highlight";
  if (status === "ineligible") return "warning";
  return "neutral";
}

/** Beschriftete Geldzahl in einer Kennzahlenzeile. */
function LabeledMoney({ label, minorUnits }: { label: string; minorUnits: number }) {
  const theme = useTheme();
  return (
    <View style={styles.labeledMoney}>
      <Text style={[styles.smallLabel, { color: theme.textSecondary }]}>{label}</Text>
      <Money minorUnits={minorUnits} tone="plain" />
    </View>
  );
}

export default function VorhabenDetailScreen() {
  const theme = useTheme();
  const { id } = useLocalSearchParams<{ id: string }>();
  const projectId = id ?? null;
  const isConnected = useConnectivity();
  const { canEdit } = useProjectContext();

  const [tab, setTab] = useState<ProjectTab>("uebersicht");
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null);

  const { data: project, isLoading: projectLoading, error: projectError } = useProject(projectId);
  const { data: scenarios } = useProjectScenarios(projectId);

  const activeScenarioId =
    selectedScenarioId ??
    scenarios?.find((s) => s.status === "selected")?.id ??
    scenarios?.[0]?.id ??
    null;

  const { data: calculation, isLoading: calcLoading } = useScenarioCalculation(
    projectId,
    activeScenarioId,
  );

  if (projectLoading) {
    return (
      <ScreenContainer>
        <InlineQueryState status="loading" skeletonRows={6} />
      </ScreenContainer>
    );
  }

  if (projectError || !project) {
    return (
      <>
        <Stack.Screen options={{ title: "Vorhaben" }} />
        <ScreenContainer>
          <View style={styles.pagePadding}>
            <Card tone="warning" density="compact">
              <Text style={[styles.body, { color: theme.warning }]}>
                Dieses Vorhaben existiert nicht mehr oder konnte nicht geladen werden.
              </Text>
            </Card>
          </View>
        </ScreenContainer>
      </>
    );
  }

  return (
    <>
      <Stack.Screen options={{ title: project.name }} />
      <ScreenContainer>
        <View style={styles.root}>
          {!isConnected ? (
            <View style={styles.bannerWrap}>
              <Card tone="info" density="compact">
                <Text style={[styles.body, { color: theme.info }]}>
                  Offline: zuletzt geladener Stand, keine Änderungen möglich.
                </Text>
              </Card>
            </View>
          ) : null}

          <View style={styles.header}>
            <Badge label={PROJECT_STATUS_LABELS[project.status]} tone="info" />

            {scenarios && scenarios.length > 1 ? (
              <ScenarioPicker
                scenarios={scenarios}
                activeId={activeScenarioId}
                onSelect={setSelectedScenarioId}
              />
            ) : null}

            <SegmentedControl
              options={TAB_LABELS}
              value={tab}
              onChange={setTab}
              accessibilityLabel="Bereiche des Vorhabens"
            />
          </View>

          <View style={styles.tabBody}>
            {tab === "uebersicht" ? (
              <UebersichtTab calculation={calculation} isLoading={calcLoading} />
            ) : null}
            {tab === "kosten" && activeScenarioId ? (
              <KostenTab
                projectId={project.id}
                scenarioId={activeScenarioId}
                canEdit={canEdit}
                isConnected={isConnected}
              />
            ) : null}
            {tab === "finanzierung" && activeScenarioId ? (
              <FinanzierungTab projectId={project.id} scenarioId={activeScenarioId} />
            ) : null}
            {tab === "foerderung" && activeScenarioId ? (
              <FoerderungTab projectId={project.id} scenarioId={activeScenarioId} />
            ) : null}
            {tab !== "uebersicht" && !activeScenarioId ? (
              <EmptyState
                title="Kein Szenario"
                description="Dieses Vorhaben hat noch kein Szenario. Im Web-Kostenplan lassen sich Szenarien anlegen."
              />
            ) : null}
          </View>
        </View>
      </ScreenContainer>
    </>
  );
}

function ScenarioPicker({
  scenarios,
  activeId,
  onSelect,
}: {
  scenarios: ProjectScenario[];
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <ChipRow>
      {scenarios.map((scenario) => {
        const selected = scenario.id === activeId;
        // Das Haekchen benennt das vom Haushalt gewaehlte Szenario - ohne es
        // waere "ausgewaehlt" (Anzeige) von "gewaehlt" (Plan) nicht zu trennen.
        const label = `${scenario.name}${scenario.status === "selected" ? " ✓" : ""}`;
        return (
          <Chip
            key={scenario.id}
            label={label}
            selected={selected}
            onPress={() => onSelect(scenario.id)}
            accessibilityLabel={`Szenario ${label}`}
          />
        );
      })}
    </ChipRow>
  );
}

function UebersichtTab({
  calculation,
  isLoading,
}: {
  calculation: ReturnType<typeof useScenarioCalculation>["data"];
  isLoading: boolean;
}) {
  const theme = useTheme();

  if (isLoading && !calculation) {
    return <InlineQueryState status="loading" skeletonRows={5} />;
  }

  if (!calculation) {
    return (
      <ScrollView contentContainerStyle={styles.tabContent}>
        <EmptyState
          title="Keine Berechnung verfügbar"
          description="Für dieses Szenario liegt noch keine Liveberechnung vor."
        />
      </ScrollView>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.tabContent}>
      <Card accentTop="info">
        <CardLabel>Kennzahlen</CardLabel>
        <View style={styles.metricRow}>
          <StatTile
            label="Projektkosten (Cash)"
            caption="Forecast plus Kostenpuffer"
            value={<Money minorUnits={calculation.project_cash_cost_minor} tone="plain" />}
          />
          <StatTile
            label="Oekonomische Kosten"
            caption="Inklusive Eigen-/Sachleistung"
            value={<Money minorUnits={calculation.project_economic_cost_minor} tone="plain" />}
          />
        </View>
        <View style={styles.metricRow}>
          <StatTile
            label="Kostenpuffer"
            value={<Money minorUnits={calculation.contingency_minor} tone="plain" />}
          />
          <StatTile
            label="Ist-Kosten"
            tone="highlight"
            value={<Money minorUnits={calculation.actual_cost_minor} tone="plain" />}
          />
        </View>
        <View style={styles.restRow}>
          <Text style={[styles.smallLabel, { color: theme.textSecondary }]}>
            Rest laut Forecast:
          </Text>
          <Money minorUnits={calculation.remaining_forecast_minor} tone="plain" />
        </View>
      </Card>

      {calculation.warnings.length > 0 ? (
        <Card tone="warning">
          <CardLabel>Warnungen</CardLabel>
          {calculation.warnings.map((code) => (
            <Text key={code} style={[styles.body, { color: theme.warning }]}>
              • {WARNING_LABELS[code] ?? code}
            </Text>
          ))}
        </Card>
      ) : null}
    </ScrollView>
  );
}

function KostenTab({
  projectId,
  scenarioId,
  canEdit,
  isConnected,
}: {
  projectId: string;
  scenarioId: string;
  canEdit: boolean;
  isConnected: boolean;
}) {
  const theme = useTheme();
  const { data, isLoading, isError, isFetching, refetch } = useCostItems(projectId, scenarioId);
  const items = data ?? [];

  const [menuItem, setMenuItem] = useState<CostItem | null>(null);
  const [assignItem, setAssignItem] = useState<CostItem | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const updateCostItem = useUpdateCostItem();

  const markPaid = (item: CostItem) => {
    setMenuItem(null);
    Alert.alert("Als bezahlt markieren", `„${item.name}“ als vollständig bezahlt markieren?`, [
      { text: "Abbrechen", style: "cancel" },
      {
        text: "Bestätigen",
        onPress: () => {
          setActionError(null);
          updateCostItem.mutate(
            {
              projectId,
              scenarioId,
              itemId: item.id,
              version: item.version,
              input: { status: "paid" },
            },
            { onError: (error) => setActionError(error.message) },
          );
        },
      },
    ]);
  };

  return (
    <View style={styles.fill}>
      <FlatList
        data={items}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={isFetching} onRefresh={() => void refetch()} />}
        contentContainerStyle={items.length === 0 ? styles.emptyContent : styles.tabContent}
        ListEmptyComponent={
          isLoading ? (
            <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={44} />
          ) : isError ? (
            /* Ohne diesen Zweig sah ein gescheiterter Ladevorgang aus wie ein
               Vorhaben ohne Kostenplan. */
            <InlineQueryState
              status="error"
              title="Kostenpositionen konnten nicht geladen werden"
              onRetry={() => void refetch()}
            />
          ) : (
            <EmptyState
              title="Keine Kostenpositionen"
              description="Kostenpositionen werden im Web-Kostenplan angelegt."
            />
          )
        }
        ListHeaderComponent={
          actionError ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.body, { color: theme.warning }]} accessibilityRole="alert">
                {actionError}
              </Text>
            </Card>
          ) : null
        }
        renderItem={({ item }) => {
          const restMinor = item.forecast_minor - item.actual_amount_minor;
          return (
            <Card density="compact">
              <View style={styles.itemHeader}>
                <View style={styles.itemHeaderText}>
                  <Text style={[styles.rowTitle, { color: theme.textPrimary }]} numberOfLines={2}>
                    {item.name}
                  </Text>
                  <Badge
                    label={COST_STATUS_LABELS[item.status]}
                    tone={COST_STATUS_TONES[item.status]}
                    size="small"
                  />
                </View>
                {canEdit ? (
                  <IconButton
                    icon="ellipsis-horizontal"
                    size={36}
                    accessibilityLabel={`Aktionen für ${item.name}`}
                    onPress={() => setMenuItem(item)}
                  />
                ) : null}
              </View>
              <View style={styles.amountRow}>
                <LabeledMoney label="Forecast" minorUnits={item.forecast_minor} />
                <LabeledMoney label="Ist" minorUnits={item.actual_amount_minor} />
                <LabeledMoney label="Rest" minorUnits={restMinor} />
              </View>
            </Card>
          );
        }}
      />

      <SheetModal
        visible={menuItem !== null}
        title={menuItem?.name ?? ""}
        onClose={() => setMenuItem(null)}
      >
        {menuItem ? (
          <View style={styles.menu}>
            {!isConnected ? (
              <Text style={[styles.body, styles.menuHint, { color: theme.textSecondary }]}>
                Diese Aktionen sind nur online möglich.
              </Text>
            ) : null}
            {menuItem.status !== "paid" && menuItem.status !== "cancelled" ? (
              <MenuItem
                label="Als bezahlt markieren"
                disabled={!isConnected}
                onPress={() => markPaid(menuItem)}
              />
            ) : null}
            <MenuItem
              label="Banktransaktion zuordnen"
              disabled={!isConnected}
              onPress={() => {
                const item = menuItem;
                setMenuItem(null);
                setAssignItem(item);
              }}
            />
          </View>
        ) : null}
      </SheetModal>

      {assignItem ? (
        <AssignTransactionSheet
          projectId={projectId}
          scenarioId={scenarioId}
          item={assignItem}
          isConnected={isConnected}
          onClose={() => setAssignItem(null)}
        />
      ) : null}
    </View>
  );
}

function AssignTransactionSheet({
  projectId,
  scenarioId,
  item,
  isConnected,
  onClose,
}: {
  projectId: string;
  scenarioId: string;
  item: CostItem;
  isConnected: boolean;
  onClose: () => void;
}) {
  const theme = useTheme();
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  const actualsQuery = useActuals(projectId, item.id);
  const candidatesQuery = useTransactionCandidates(projectId, {
    search: search.trim() || undefined,
  });
  const createActual = useCreateActual();
  const candidates = candidatesQuery.data ?? [];

  const handleAssign = (candidate: TransactionCandidate) => {
    if (candidate.remaining_minor <= 0) return;
    setError(null);
    createActual.mutate(
      {
        projectId,
        scenarioId,
        itemId: item.id,
        input: {
          source: "transaction",
          amount_minor: candidate.remaining_minor,
          transaction_id: candidate.transaction_id,
        },
      },
      {
        onSuccess: onClose,
        onError: (mutationError) => {
          setError(
            apiErrorDetail(mutationError) === "actuals_overbooking"
              ? "Dieser Betrag übersteigt den noch verfügbaren Betrag dieser Buchung."
              : "Zuordnung konnte nicht gespeichert werden.",
          );
        },
      },
    );
  };

  return (
    <SheetModal
      visible
      title={`Buchung zuordnen: ${item.name}`}
      onClose={onClose}
      size="full"
      header={
        <View style={styles.sheetHeader}>
          <TextField
            label="Suche (Empfänger/Notiz)"
            value={search}
            onChangeText={setSearch}
            placeholder="z. B. Baumarkt"
            editable={isConnected}
          />
          <Text style={[styles.smallLabel, { color: theme.textSecondary }]}>
            Bereits zugeordnet: {(actualsQuery.data ?? []).length} Buchung(en). Der volle verfügbare
            Betrag einer Buchung wird zugeordnet.
          </Text>
        </View>
      }
    >
      {!isConnected ? (
        <Text style={[styles.body, styles.menuHint, { color: theme.textSecondary }]}>
          Buchungen lassen sich nur online zuordnen.
        </Text>
      ) : null}
      {error ? (
        <View style={styles.sheetHeader}>
          <Card tone="warning" density="compact">
            <Text style={[styles.body, { color: theme.warning }]} accessibilityRole="alert">
              {error}
            </Text>
          </Card>
        </View>
      ) : null}
      {candidatesQuery.isLoading ? (
        <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={44} />
      ) : candidates.length === 0 ? (
        <InlineQueryState
          status="empty"
          title="Keine Treffer"
          message="Keine passenden Buchungen gefunden."
        />
      ) : (
        <FlatList
          data={candidates}
          keyExtractor={(candidate) => candidate.transaction_id}
          contentContainerStyle={styles.tabContent}
          renderItem={({ item: candidate }) => {
            const title = candidate.payee_label ?? "Ohne Empfänger";
            const blocked =
              !isConnected || createActual.isPending || candidate.remaining_minor <= 0;
            return (
              <Card
                density="compact"
                accessibilityLabel={`${title} zuordnen`}
                onPress={blocked ? undefined : () => handleAssign(candidate)}
              >
                <View style={styles.itemHeader}>
                  <Text style={[styles.rowTitle, { color: theme.textPrimary }]} numberOfLines={1}>
                    {title}
                  </Text>
                  <Text style={[styles.smallLabel, { color: theme.textSecondary }]}>
                    {candidate.booked_date}
                  </Text>
                </View>
                <View style={styles.amountRow}>
                  <LabeledMoney label="Buchung" minorUnits={candidate.amount_minor} />
                  <LabeledMoney label="Verfügbar" minorUnits={candidate.remaining_minor} />
                </View>
              </Card>
            );
          }}
        />
      )}
    </SheetModal>
  );
}

function FinanzierungTab({ projectId, scenarioId }: { projectId: string; scenarioId: string }) {
  const { data, isLoading, isError, isFetching, refetch } = useFinancingSources(
    projectId,
    scenarioId,
  );
  const sources = data ?? [];

  return (
    <FlatList
      data={sources}
      keyExtractor={(source) => source.id}
      refreshControl={<RefreshControl refreshing={isFetching} onRefresh={() => void refetch()} />}
      contentContainerStyle={sources.length === 0 ? styles.emptyContent : styles.tabContent}
      ListEmptyComponent={
        isLoading ? (
          <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={44} />
        ) : isError ? (
          <InlineQueryState
            status="error"
            title="Finanzierungsquellen konnten nicht geladen werden"
            onRetry={() => void refetch()}
          />
        ) : (
          <EmptyState
            title="Keine Finanzierungsquellen"
            description="Finanzierungsquellen werden im Web-Kostenplan angelegt. Diese Ansicht ist read-only."
          />
        )
      }
      renderItem={({ item: source }) => (
        <Card density="compact">
          <DataRow
            title={source.name}
            subtitle={`${SOURCE_TYPE_LABELS[source.source_type]} · ${SOURCE_STATUS_LABELS[source.status]}`}
            avatarLabel={source.name}
            trailing={<Money minorUnits={source.planned_amount_minor} tone="plain" />}
            trailingCaption="geplant"
            divider={false}
          />
        </Card>
      )}
    />
  );
}

function FoerderungTab({ projectId, scenarioId }: { projectId: string; scenarioId: string }) {
  const { data, isLoading, isError, isFetching, refetch } = useFundingMatches(
    projectId,
    scenarioId,
  );
  const matches = data?.matches ?? [];
  const [selectedMatch, setSelectedMatch] = useState<FundingMatch | null>(null);

  if (!isLoading && isError) {
    return (
      <ScrollView contentContainerStyle={styles.emptyContent}>
        <InlineQueryState
          status="error"
          title="Fördertreffer konnten nicht geladen werden"
          onRetry={() => void refetch()}
        />
      </ScrollView>
    );
  }

  if (!isLoading && !data) {
    return (
      <ScrollView contentContainerStyle={styles.emptyContent}>
        <EmptyState
          title="Noch kein Fördercheck durchgeführt"
          description="Ein Fördercheck lässt sich derzeit nur im Web starten. Bereits gelaufene Ergebnisse erscheinen hier."
        />
      </ScrollView>
    );
  }

  return (
    <>
      <FlatList
        data={matches}
        keyExtractor={(match) => match.id}
        refreshControl={<RefreshControl refreshing={isFetching} onRefresh={() => void refetch()} />}
        contentContainerStyle={matches.length === 0 ? styles.emptyContent : styles.tabContent}
        ListEmptyComponent={
          isLoading ? (
            <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={44} />
          ) : (
            /* Der Fehlerfall ist oben schon abgefangen und kehrt frueh zurueck -
               hier bleibt nur noch "Lauf gelaufen, nichts gefunden". */
            <EmptyState
              title="Kein passendes Programm"
              description="Der letzte Fördercheck fand kein passendes Programm für Land/Region/Vorhabenart."
            />
          )
        }
        renderItem={({ item: match }) => (
          <Card density="compact">
            <DataRow
              title={match.program_title}
              subtitle={match.provider_name}
              avatarLabel={match.provider_name}
              titleBadge={
                <Badge
                  label={MATCH_STATUS_LABELS[match.status]}
                  tone={matchStatusTone(match.status)}
                  size="small"
                />
              }
              trailing={
                match.estimated_grant_max_minor != null ? (
                  <Money minorUnits={match.estimated_grant_max_minor} tone="plain" />
                ) : undefined
              }
              trailingCaption="Zuschuss"
              onPress={() => setSelectedMatch(match)}
              accessibilityLabel={`${match.program_title}, ${MATCH_STATUS_LABELS[match.status]}`}
              showChevron
              divider={false}
            />
          </Card>
        )}
      />

      {selectedMatch ? (
        <FundingMatchDetailSheet match={selectedMatch} onClose={() => setSelectedMatch(null)} />
      ) : null}
    </>
  );
}

/** Betragsspanne "von – bis"; ohne Untergrenze steht ein Gedankenstrich. */
function MoneyRange({ min, max }: { min: number | null; max: number | null }) {
  const theme = useTheme();
  if (min == null) {
    return <Text style={[styles.body, { color: theme.textSecondary }]}>—</Text>;
  }
  return (
    <View style={styles.rangeRow}>
      <Money minorUnits={min} tone="plain" />
      <Text style={[styles.body, { color: theme.textSecondary }]}>–</Text>
      <Money minorUnits={max ?? min} tone="plain" />
    </View>
  );
}

function FundingMatchDetailSheet({ match, onClose }: { match: FundingMatch; onClose: () => void }) {
  const theme = useTheme();
  const programQuery = useFundingProgram(match.program_id);
  const program = programQuery.data;

  return (
    <SheetModal visible title={match.program_title} onClose={onClose} size="full">
      <ScrollView contentContainerStyle={styles.sheetContent}>
        <Card density="compact">
          <Badge label={MATCH_STATUS_LABELS[match.status]} tone={matchStatusTone(match.status)} />
          <Text style={[styles.body, { color: theme.textSecondary }]}>{match.provider_name}</Text>
        </Card>

        <Card density="compact">
          <CardLabel>Geschätzte Bandbreite</CardLabel>
          <View style={styles.rangeLine}>
            <Text style={[styles.smallLabel, { color: theme.textSecondary }]}>Zuschuss:</Text>
            <MoneyRange
              min={match.estimated_grant_min_minor ?? null}
              max={match.estimated_grant_max_minor ?? null}
            />
          </View>
          <View style={styles.rangeLine}>
            <Text style={[styles.smallLabel, { color: theme.textSecondary }]}>Darlehen:</Text>
            <MoneyRange
              min={match.estimated_loan_min_minor ?? null}
              max={match.estimated_loan_max_minor ?? null}
            />
          </View>
        </Card>

        {match.criteria_met.length > 0 ? (
          <Card density="compact">
            <CardLabel>Erfüllte Kriterien</CardLabel>
            {match.criteria_met.map((criterion) => (
              <Text key={criterion.key} style={[styles.body, { color: theme.positive }]}>
                • {criterion.label}
              </Text>
            ))}
          </Card>
        ) : null}

        {match.criteria_missing.length > 0 ? (
          <Card density="compact">
            <CardLabel>Fehlende Angaben</CardLabel>
            {match.criteria_missing.map((criterion) => (
              <Text key={criterion.key} style={[styles.body, { color: theme.highlight }]}>
                • {criterion.label}
              </Text>
            ))}
          </Card>
        ) : null}

        {match.criteria_violated.length > 0 ? (
          <Card density="compact">
            <CardLabel>Nicht erfüllte Kriterien</CardLabel>
            {match.criteria_violated.map((criterion) => (
              <Text key={criterion.key} style={[styles.body, { color: theme.warning }]}>
                • {criterion.label}
              </Text>
            ))}
          </Card>
        ) : null}

        {match.criteria_manual.length > 0 ? (
          <Card density="compact">
            <CardLabel>Manuell zu prüfen</CardLabel>
            {match.criteria_manual.map((criterion) => (
              <Text key={criterion.key} style={[styles.body, { color: theme.textSecondary }]}>
                • {criterion.label}
              </Text>
            ))}
          </Card>
        ) : null}

        <Card density="compact">
          <CardLabel>Datenstand</CardLabel>
          <Text style={[styles.body, { color: theme.textSecondary }]}>
            Geprüft am {new Date(match.checked_at).toLocaleDateString("de-DE")}
            {match.catalog_age_days != null ? ` • Katalogalter ${match.catalog_age_days} Tage` : ""}
          </Text>

          {programQuery.isLoading ? (
            <InlineQueryState status="loading" skeletonRows={2} />
          ) : program ? (
            <>
              {program.is_stale ? (
                <Text style={[styles.body, { color: theme.warning }]}>
                  Katalogeintrag ist fällig zur Überprüfung.
                </Text>
              ) : null}
              {program.current_version ? (
                <Text style={[styles.body, { color: theme.textSecondary }]}>
                  Quellstand abgerufen am{" "}
                  {new Date(program.current_version.retrieved_at).toLocaleDateString("de-DE")}
                  {program.current_version.valid_from
                    ? `, gültig ab ${program.current_version.valid_from}`
                    : ""}
                  {program.current_version.valid_to
                    ? ` bis ${program.current_version.valid_to}`
                    : ""}
                  .
                </Text>
              ) : null}
              <Pressable
                accessibilityRole="link"
                accessibilityLabel="Förderquelle auf der offiziellen Seite öffnen"
                onPress={() => void Linking.openURL(program.official_url)}
                style={styles.linkPress}
              >
                <Text style={[styles.linkLabel, { color: theme.positive }]}>
                  Förderquelle öffnen (offizielle Seite) →
                </Text>
              </Pressable>
            </>
          ) : null}
        </Card>

        <Text style={[styles.body, { color: theme.textSecondary }]}>
          Als Finanzierungsquelle übernehmen und Kombinationsprüfung sind komplexe
          Szenarioänderungen und bleiben dem Web-Förderregister vorbehalten.
        </Text>
      </ScrollView>
    </SheetModal>
  );
}

function MenuItem({
  label,
  disabled,
  onPress,
}: {
  label: string;
  disabled?: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.menuItem,
        {
          borderColor: theme.border,
          backgroundColor: pressed ? theme.surfaceHover : theme.surface,
          opacity: disabled ? 0.5 : 1,
        },
      ]}
    >
      <Text style={[styles.menuLabel, { color: theme.textPrimary }]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
  },
  fill: {
    flex: 1,
  },
  pagePadding: {
    padding: space.lg,
  },
  bannerWrap: {
    paddingHorizontal: space.lg,
    paddingTop: space.md,
  },
  header: {
    gap: space.sm,
    paddingHorizontal: space.lg,
    paddingTop: space.md,
    paddingBottom: space.sm,
  },
  tabBody: {
    flex: 1,
  },
  tabContent: {
    padding: space.lg,
    paddingTop: space.sm,
    gap: space.md,
    paddingBottom: space.xxl,
  },
  sheetContent: {
    padding: space.lg,
    gap: space.md,
  },
  sheetHeader: {
    paddingHorizontal: space.lg,
    paddingBottom: space.sm,
    gap: space.sm,
  },
  emptyContent: {
    flexGrow: 1,
  },
  metricRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  restRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  labeledMoney: {
    flex: 1,
    minWidth: 0,
    gap: space.hair,
  },
  smallLabel: {
    fontSize: typeScale.micro,
    fontWeight: "700",
  },
  body: {
    flexShrink: 1,
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  itemHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  itemHeaderText: {
    flex: 1,
    gap: space.xs,
    alignItems: "flex-start",
  },
  amountRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  rowTitle: {
    flexShrink: 1,
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  rangeRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.xs,
  },
  rangeLine: {
    gap: space.hair,
  },
  linkPress: {
    minHeight: MIN_TOUCH,
    justifyContent: "center",
  },
  linkLabel: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  menu: {
    gap: space.sm,
    padding: space.lg,
    paddingTop: 0,
  },
  menuHint: {
    paddingHorizontal: space.lg,
  },
  menuItem: {
    minHeight: MIN_TOUCH + 8,
    justifyContent: "center",
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
  },
  menuLabel: {
    fontSize: typeScale.base,
    fontWeight: "600",
  },
});
