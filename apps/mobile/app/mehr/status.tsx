import { Stack } from "expo-router";
import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { getApiBaseUrl } from "../../src/api/config";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { ServiceHealthRow } from "../../src/components/ServiceHealthRow";
import { Card, CardLabel } from "../../src/components/ui";
import { useSystemHealth } from "../../src/hooks/useSystemHealth";
import { space, typeScale, useTheme } from "../../src/theme";
import { sortServices } from "../../src/theme/serviceStatus";

/** Systemstatus, aus dem Home-Screen ausgelagert (Auftrag Punkt 1): ein
 * Betriebs-/Debug-Widget gehoert nicht auf das Haupt-Dashboard. */
export default function StatusScreen() {
  const theme = useTheme();
  const {
    data: healthData,
    error: healthError,
    isLoading: isHealthLoading,
    isFetching: isHealthFetching,
    isError: isHealthError,
    refetch: refetchHealth,
  } = useSystemHealth();

  const services = healthData ? sortServices(healthData.services) : [];

  return (
    <>
      <Stack.Screen options={{ title: "Status" }} />
      <ScreenContainer>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl
              refreshing={isHealthFetching && !isHealthLoading}
              onRefresh={() => void refetchHealth()}
            />
          }
        >
          <Card>
            <CardLabel>Systemstatus</CardLabel>

            {isHealthLoading ? <InlineQueryState status="loading" skeletonRows={5} /> : null}

            {isHealthError ? (
              <InlineQueryState
                status="error"
                title="Systemstatus nicht verfügbar"
                message={healthError instanceof Error ? healthError.message : "Unbekannter Fehler."}
                onRetry={() => void refetchHealth()}
              />
            ) : null}

            {!isHealthLoading && !isHealthError && services.length === 0 ? (
              <InlineQueryState
                status="empty"
                title="Keine Dienste gemeldet"
                message="Der Server hat keine Dienstliste zurückgegeben."
              />
            ) : null}

            <View>
              {services.map((service, index) => (
                <ServiceHealthRow
                  key={service.service}
                  service={service}
                  divider={index < services.length - 1}
                />
              ))}
            </View>
          </Card>

          <Card density="compact">
            <CardLabel>Verbindung</CardLabel>
            <Text style={[styles.detail, { color: theme.textSecondary }]} selectable>
              API-Basis-URL: {getApiBaseUrl()}
            </Text>
          </Card>
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    gap: space.lg,
  },
  detail: {
    fontSize: typeScale.body,
  },
});
