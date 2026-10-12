import type { Ionicons } from "@expo/vector-icons";
import { Stack, useRouter } from "expo-router";
import type { ComponentProps } from "react";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text } from "react-native";
import type { ProjectListItem, ProjectStatus, ProjectType } from "../../src/api/types";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { Badge, Card, DataRow, Money, monoTextStyle } from "../../src/components/ui";
import { useConnectivity } from "../../src/hooks/useConnectivity";
import { useProjectList } from "../../src/hooks/useProjects";
import { space, typeScale, useTheme, type AccentTone } from "../../src/theme";
import { formatDueDate } from "../../src/utils/dates";

/**
 * Vorhabenliste (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md, Abschnitt 9, P5):
 * "Mehr -> Vorhaben". Zeigt dieselben Serverkennzahlen wie
 * apps/web/src/pages/ProjectsPage.tsx (P5-Abnahme), nur als dichte Liste
 * statt Tabelle.
 */

type IconName = ComponentProps<typeof Ionicons>["name"];

const TYPE_LABELS: Record<ProjectType, string> = {
  home_build: "Hausbau",
  property_purchase: "Immobilienkauf",
  renovation: "Sanierung",
  vehicle: "Fahrzeug",
  solar: "Photovoltaik",
  education: "Ausbildung",
  business: "Gründung",
  other: "Sonstiges",
};

const TYPE_ICONS: Record<ProjectType, IconName> = {
  home_build: "hammer-outline",
  property_purchase: "home-outline",
  renovation: "construct-outline",
  vehicle: "car-outline",
  solar: "sunny-outline",
  education: "school-outline",
  business: "briefcase-outline",
  other: "ellipsis-horizontal-outline",
};

const STATUS_LABELS: Record<ProjectStatus, string> = {
  idea: "Idee",
  planning: "Planung",
  approved: "Bestätigt",
  active: "Aktiv",
  completed: "Abgeschlossen",
  cancelled: "Storniert",
  archived: "Archiviert",
};

// Der Status entscheidet, ob ein Vorhaben Geld bindet - deshalb traegt er als
// einziges Merkmal der Zeile eine Farbe, immer zusammen mit seinem Text.
const STATUS_TONES: Record<ProjectStatus, AccentTone> = {
  idea: "neutral",
  planning: "info",
  approved: "highlight",
  active: "positive",
  completed: "positive",
  cancelled: "warning",
  archived: "neutral",
};

export default function VorhabenScreen() {
  const theme = useTheme();
  const router = useRouter();
  const isConnected = useConnectivity();
  const { data, isLoading, isError, isFetching, refetch } = useProjectList();
  const projects = data ?? [];

  return (
    <>
      <Stack.Screen
        options={{
          title: "Vorhaben",
          headerRight: () => (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Neues Vorhaben anlegen"
              hitSlop={8}
              onPress={() => router.push("/vorhaben/neu")}
            >
              <Text style={[styles.headerAction, { color: theme.positive }]}>+ Neu</Text>
            </Pressable>
          ),
        }}
      />
      <ScreenContainer>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl refreshing={isFetching} onRefresh={() => void refetch()} />
          }
        >
          {!isConnected ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.offlineText, { color: theme.warning }]}>
                Offline: zuletzt geladener Stand, keine Änderungen möglich.
              </Text>
            </Card>
          ) : null}

          {isLoading ? (
            <Card>
              <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={40} />
            </Card>
          ) : isError ? (
            <Card>
              <InlineQueryState
                status="error"
                title="Vorhaben konnten nicht geladen werden"
                onRetry={() => void refetch()}
              />
            </Card>
          ) : projects.length === 0 ? (
            <Card>
              <InlineQueryState
                status="empty"
                title="Noch kein Vorhaben angelegt"
                message="Ein Vorhaben ist mehr als ein Wunsch oder ein Sparziel: Kostenpositionen, Finanzierung und Förderung gehören dazu. Mit „+ Neu“ oben rechts anlegen."
              />
            </Card>
          ) : (
            projects.map((project: ProjectListItem) => (
              <Card
                key={project.id}
                density="compact"
                onPress={() => router.push(`/vorhaben/${project.id}`)}
                accessibilityLabel={`${project.name} öffnen`}
              >
                <DataRow
                  title={project.name}
                  subtitle={
                    project.selected_scenario_name
                      ? `${TYPE_LABELS[project.project_type]} · ${project.selected_scenario_name}`
                      : TYPE_LABELS[project.project_type]
                  }
                  avatarLabel={project.name}
                  avatarIcon={TYPE_ICONS[project.project_type]}
                  titleBadge={
                    <Badge
                      label={STATUS_LABELS[project.status]}
                      tone={STATUS_TONES[project.status]}
                      size="small"
                    />
                  }
                  trailing={
                    project.project_cash_cost_minor != null ? (
                      <Money minorUnits={project.project_cash_cost_minor} tone="plain" />
                    ) : (
                      <Text style={[monoTextStyle, styles.noAmount, { color: theme.textMuted }]}>
                        —
                      </Text>
                    )
                  }
                  trailingCaption={
                    project.planned_completion_on
                      ? `Ziel: ${formatDueDate(project.planned_completion_on)}`
                      : undefined
                  }
                  divider={false}
                />
              </Card>
            ))
          )}
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  headerAction: { fontSize: typeScale.medium, fontWeight: "600" },
  content: {
    padding: space.lg,
    gap: space.md,
  },
  offlineText: {
    fontSize: typeScale.body,
  },
  noAmount: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
});
