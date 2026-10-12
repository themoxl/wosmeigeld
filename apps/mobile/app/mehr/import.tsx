import * as DocumentPicker from "expo-document-picker";
import { Stack } from "expo-router";
import { useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";
import type { ImportRow, ImportRun, RowStatus } from "../../src/api/types";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { OptionList } from "../../src/components/OptionList";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { SheetModal } from "../../src/components/SheetModal";
import { Badge, Card, CardLabel, DataRow, Money, StatTile } from "../../src/components/ui";
import { useAccounts } from "../../src/hooks/useAccounts";
import {
  useCommitImportRun,
  useImportRevertPreview,
  useImportRows,
  useImportRun,
  useImportRuns,
  useRevertImportRun,
  useUploadImport,
} from "../../src/hooks/useImport";
import { leading, space, typeScale, useTheme, type AccentTone } from "../../src/theme";
import { formatFileSize, getFormatLabel, getRowStatusInfo } from "../../src/utils/importHelpers";

/**
 * Zeilenstatus als Farbton des Designsystems. Die Beschriftung kommt weiter
 * aus getRowStatusInfo, damit Web und App denselben Wortlaut zeigen; nur die
 * Farbwahl liegt hier, weil der Helfer noch Hex-Werte fuehrt.
 */
const ROW_STATUS_TONES: Record<RowStatus, AccentTone> = {
  new: "positive",
  duplicate_exact: "warning",
  duplicate_possible: "highlight",
  transfer_candidate: "info",
  committed: "positive",
  skipped: "neutral",
  error: "warning",
};

function RowItem({ row }: { row: ImportRow }) {
  const theme = useTheme();
  const statusLabel = getRowStatusInfo(row.status, theme).label;
  const dateStr = row.booked_date || row.value_date || "Kein Datum";
  const payee = row.payee_raw || row.purpose || "Unbekannte Buchung";
  const detail = row.purpose && row.payee_raw ? ` · ${row.purpose}` : "";

  return (
    <DataRow
      title={payee}
      subtitle={`Zeile #${row.row_number} · ${dateStr}${detail}`}
      avatarLabel={payee}
      titleBadge={
        <Badge label={statusLabel} tone={ROW_STATUS_TONES[row.status] ?? "neutral"} size="small" />
      }
      trailing={
        row.amount_minor !== null ? (
          <Money minorUnits={row.amount_minor} currency={row.currency || "EUR"} />
        ) : (
          <Text style={[styles.noAmount, { color: theme.textMuted }]}>—</Text>
        )
      }
    />
  );
}

export default function ImportScreen() {
  const theme = useTheme();
  const { data: accounts, isLoading: accountsLoading } = useAccounts();

  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<{
    uri: string;
    name: string;
    size: number;
    type: string;
  } | null>(null);

  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [commitSuccessMessage, setCommitSuccessMessage] = useState<string | null>(null);

  // Gesetzt heisst: der Bestaetigungsdialog fuer die Ruecknahme ist offen.
  const [revertRun, setRevertRun] = useState<ImportRun | null>(null);
  const [revertMessage, setRevertMessage] = useState<string | null>(null);

  const uploadMutation = useUploadImport();
  const commitMutation = useCommitImportRun();
  const revertMutation = useRevertImportRun();

  const { data: importRuns } = useImportRuns();
  const revertPreview = useImportRevertPreview(revertRun?.id ?? null);
  const uebernommeneLaeufe = (importRuns ?? []).filter((run) => run.status === "completed");

  const { data: activeRun, isLoading: runLoading } = useImportRun(activeRunId);
  const isRunProcessing = activeRun?.status === "pending" || activeRun?.status === "analysing";
  const { data: stagingRows, isLoading: rowsLoading } = useImportRows(
    activeRunId,
    !isRunProcessing,
  );

  const accountOptions = (accounts ?? []).map((acc) => ({
    value: acc.id,
    label: `${acc.name} (${acc.currency})`,
  }));

  async function handlePickDocument() {
    setErrorMessage(null);
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: [
          "text/csv",
          "text/plain",
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "application/vnd.ms-excel",
          "application/xml",
          "text/xml",
          "application/zip",
          "*/*",
        ],
        copyToCacheDirectory: true,
      });

      if (!result.canceled && result.assets && result.assets.length > 0 && result.assets[0]) {
        const asset = result.assets[0];
        setSelectedFile({
          uri: asset.uri,
          name: asset.name,
          size: asset.size ?? 0,
          type: asset.mimeType || "application/octet-stream",
        });
      }
    } catch (err) {
      setErrorMessage(
        "Dateiauswahl fehlgeschlagen: " + (err instanceof Error ? err.message : String(err)),
      );
    }
  }

  async function handleUpload() {
    if (!selectedFile) {
      setErrorMessage("Bitte zuerst eine Bankdatei auswählen.");
      return;
    }

    setErrorMessage(null);
    setCommitSuccessMessage(null);

    try {
      const run: ImportRun = await uploadMutation.mutateAsync({
        file: selectedFile,
        accountId: selectedAccountId ?? undefined,
      });
      setActiveRunId(run.id);
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : "Upload fehlgeschlagen. Bitte versuche es erneut.",
      );
    }
  }

  async function handleCommit() {
    if (!activeRunId) return;

    setErrorMessage(null);
    setCommitSuccessMessage(null);

    try {
      const res = await commitMutation.mutateAsync({
        runId: activeRunId,
      });
      setCommitSuccessMessage(`${res.committed} Buchungen erfolgreich übernommen!`);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Übernahme fehlgeschlagen.");
    }
  }

  /** Nimmt die Buchungen des Laufs aus dem Bestand - siehe SheetModal unten. */
  async function handleRevert() {
    const lauf = revertRun;
    if (!lauf) return;
    try {
      const res = await revertMutation.mutateAsync(lauf.id);
      setRevertRun(null);
      const behalten =
        res.kept > 0 ? ` ${res.kept} blieben stehen, weil daran inzwischen etwas hängt.` : "";
      setRevertMessage(
        `Aus „${lauf.filename}“ wurden ${res.reverted} Buchungen zurückgenommen.${behalten}`,
      );
    } catch (err) {
      setRevertRun(null);
      setRevertMessage(
        err instanceof Error ? err.message : "Der Import wurde nicht zurückgenommen.",
      );
    }
  }

  function handleReset() {
    setSelectedFile(null);
    setActiveRunId(null);
    setErrorMessage(null);
    setCommitSuccessMessage(null);
  }

  return (
    <>
      <Stack.Screen options={{ title: "Import" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
          <Card>
            <CardLabel>1. Dateiauswahl</CardLabel>
            <Text style={[styles.hintText, { color: theme.textSecondary }]}>
              Wähle eine Kontoauszugsdatei (.csv, .xlsx, .xml, .sta, .txt, .zip).
            </Text>
            {selectedFile ? (
              <>
                <DataRow
                  title={selectedFile.name}
                  subtitle={formatFileSize(selectedFile.size)}
                  avatarIcon="document-text-outline"
                  divider={false}
                />
                <PrimaryButton
                  label="Andere Datei wählen"
                  variant="secondary"
                  size="compact"
                  onPress={() => void handlePickDocument()}
                />
              </>
            ) : (
              <PrimaryButton
                label="Bankdatei auswählen"
                onPress={() => void handlePickDocument()}
                variant="secondary"
              />
            )}
          </Card>

          <Card>
            <CardLabel>2. Zielkonto (optional)</CardLabel>
            <Text style={[styles.hintText, { color: theme.textSecondary }]}>
              Zielkonto für die zu importierenden Buchungen auswählen.
            </Text>
            {accountsLoading ? (
              <InlineQueryState status="loading" skeletonRows={3} />
            ) : accountOptions.length === 0 ? (
              <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                Keine Konten vorhanden.
              </Text>
            ) : (
              <OptionList
                options={accountOptions}
                value={selectedAccountId}
                onChange={setSelectedAccountId}
              />
            )}
          </Card>

          {!activeRunId ? (
            <View style={styles.actionSection}>
              {errorMessage ? (
                <Card tone="warning" density="compact">
                  <Text
                    style={[styles.hintText, { color: theme.warning }]}
                    accessibilityRole="alert"
                  >
                    {errorMessage}
                  </Text>
                </Card>
              ) : null}
              <PrimaryButton
                label="Datei hochladen & analysieren"
                onPress={() => void handleUpload()}
                loading={uploadMutation.isPending}
                disabled={!selectedFile}
              />
            </View>
          ) : null}

          {runLoading ? (
            <Card>
              <CardLabel>Analyse läuft ...</CardLabel>
              <InlineQueryState status="loading" skeletonRows={4} />
            </Card>
          ) : activeRun ? (
            <>
              <Card accentTop="info">
                <CardLabel>3. Staging-Vorschau</CardLabel>
                <View style={styles.summaryGrid}>
                  <SummaryRow label="Dateiname" value={activeRun.filename} />
                  <SummaryRow
                    label="Erkanntes Format"
                    value={getFormatLabel(activeRun.detected_format)}
                  />
                  <SummaryRow label="Status" value={activeRun.status} />
                </View>

                <View style={styles.metricsContainer}>
                  <StatTile
                    label="Gesamt"
                    value={
                      <Text style={[styles.metricNumber, { color: theme.textPrimary }]}>
                        {activeRun.total_rows}
                      </Text>
                    }
                  />
                  <StatTile
                    label="Neu"
                    tone="positive"
                    value={
                      <Text style={[styles.metricNumber, { color: theme.positive }]}>
                        {activeRun.new_count}
                      </Text>
                    }
                  />
                  <StatTile
                    label="Dubletten"
                    tone="warning"
                    value={
                      <Text style={[styles.metricNumber, { color: theme.warning }]}>
                        {activeRun.duplicate_count}
                      </Text>
                    }
                  />
                  <StatTile
                    label="Umbuchungen"
                    tone="info"
                    value={
                      <Text style={[styles.metricNumber, { color: theme.info }]}>
                        {activeRun.transfer_candidate_count}
                      </Text>
                    }
                  />
                </View>

                {isRunProcessing ? (
                  <View style={styles.processingRow}>
                    <ActivityIndicator color={theme.textPrimary} />
                    <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                      Datei wird verarbeitet... Diese Ansicht aktualisiert sich automatisch.
                    </Text>
                  </View>
                ) : commitSuccessMessage ? (
                  <View style={styles.actionBlock}>
                    <Card tone="positive" density="compact">
                      <Text style={[styles.successText, { color: theme.positive }]}>
                        {commitSuccessMessage}
                      </Text>
                    </Card>
                    <PrimaryButton
                      label="Neuen Import starten"
                      onPress={handleReset}
                      variant="secondary"
                    />
                  </View>
                ) : (
                  <View style={styles.actionBlock}>
                    {errorMessage ? (
                      <Card tone="warning" density="compact">
                        <Text
                          style={[styles.hintText, { color: theme.warning }]}
                          accessibilityRole="alert"
                        >
                          {errorMessage}
                        </Text>
                      </Card>
                    ) : null}
                    <PrimaryButton
                      label={`${activeRun.new_count} Buchungen übernehmen`}
                      onPress={() => void handleCommit()}
                      loading={commitMutation.isPending}
                      disabled={activeRun.new_count === 0 || activeRun.status === "completed"}
                    />
                  </View>
                )}
              </Card>

              {isRunProcessing ? null : (
                <Card>
                  <CardLabel>{`Staging-Zeilen (${stagingRows?.length ?? 0})`}</CardLabel>
                  {rowsLoading ? (
                    <InlineQueryState status="loading" skeletonRows={4} />
                  ) : stagingRows && stagingRows.length > 0 ? (
                    <View>
                      {stagingRows.map((row) => (
                        <RowItem key={row.id} row={row} />
                      ))}
                    </View>
                  ) : (
                    <InlineQueryState
                      status="empty"
                      title="Keine Zeilen"
                      message="Keine Zeilen in diesem Importlauf vorhanden."
                    />
                  )}
                </Card>
              )}
            </>
          ) : null}

          {/* Import-Historie. Bis hierher konnte die App einen uebernommenen
              Import nicht mehr anfassen - sie zeigte ihn nicht einmal an. Der
              Weg zurueck existierte nur in der Weboberflaeche. */}
          {uebernommeneLaeufe.length > 0 ? (
            <Card>
              <CardLabel>Übernommene Importe</CardLabel>
              {revertMessage ? (
                <Card tone="positive" density="compact">
                  <Text style={[styles.hintText, { color: theme.positive }]}>{revertMessage}</Text>
                </Card>
              ) : null}

              {uebernommeneLaeufe.map((run) => (
                <View key={run.id} style={styles.historyRun}>
                  <DataRow
                    title={run.filename}
                    subtitle={`${run.total_rows} Zeilen · ${getFormatLabel(run.detected_format)}`}
                    avatarIcon="cloud-download-outline"
                    divider={false}
                  />
                  <PrimaryButton
                    label="Zurücknehmen"
                    variant="secondary"
                    size="compact"
                    onPress={() => {
                      setRevertMessage(null);
                      setRevertRun(run);
                    }}
                  />
                </View>
              ))}
            </Card>
          ) : null}
        </ScrollView>
      </ScreenContainer>

      <SheetModal
        visible={revertRun !== null}
        title="Import zurücknehmen"
        onClose={() => setRevertRun(null)}
        footer={
          <View style={styles.sheetFooter}>
            <PrimaryButton
              label={`${revertPreview.data?.revertable ?? 0} Buchungen zurücknehmen`}
              onPress={() => void handleRevert()}
              loading={revertMutation.isPending}
              disabled={!revertPreview.data || revertPreview.data.revertable === 0}
            />
            <PrimaryButton
              label="Abbrechen"
              variant="secondary"
              onPress={() => setRevertRun(null)}
            />
          </View>
        }
      >
        <ScrollView contentContainerStyle={styles.sheetContent}>
          <Text style={[styles.hintText, { color: theme.textSecondary }]}>
            Die Buchungen werden archiviert: sie verschwinden aus Listen, Budgets und Auswertungen,
            bleiben aber im Bestand. Der Importlauf bleibt als Herkunftsnachweis stehen, und die
            Datei lässt sich danach erneut einlesen.
          </Text>

          {revertPreview.isPending ? (
            <InlineQueryState status="loading" skeletonRows={3} />
          ) : revertPreview.isError ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.hintText, { color: theme.warning }]} accessibilityRole="alert">
                Die Vorschau konnte nicht geladen werden.
              </Text>
            </Card>
          ) : revertPreview.data ? (
            <>
              <Card density="compact">
                <View style={styles.summaryGrid}>
                  <View style={styles.summaryRow}>
                    <Text style={[styles.summaryLabel, { color: theme.textSecondary }]}>
                      Wird zurückgenommen:
                    </Text>
                    <Text style={[styles.summaryValue, { color: theme.textPrimary }]}>
                      {revertPreview.data.revertable} von {revertPreview.data.transactions_total}
                    </Text>
                  </View>
                  {revertPreview.data.from_date && revertPreview.data.to_date ? (
                    <View style={styles.summaryRow}>
                      <Text style={[styles.summaryLabel, { color: theme.textSecondary }]}>
                        Zeitraum:
                      </Text>
                      <Text style={[styles.summaryValue, { color: theme.textPrimary }]}>
                        {revertPreview.data.from_date} bis {revertPreview.data.to_date}
                      </Text>
                    </View>
                  ) : null}
                  <View style={styles.summaryRow}>
                    <Text style={[styles.summaryLabel, { color: theme.textSecondary }]}>
                      Summe:
                    </Text>
                    <Money minorUnits={revertPreview.data.sum_minor} tone="plain" />
                  </View>
                </View>
              </Card>

              {revertPreview.data.blocked.length > 0 ? (
                <Card tone="warning" density="compact">
                  <Text style={[styles.summaryValue, { color: theme.warning }]}>
                    Diese bleiben stehen:
                  </Text>
                  {revertPreview.data.blocked.map((blocker) => (
                    <Text key={blocker.reason} style={[styles.hintText, { color: theme.warning }]}>
                      {blocker.count}× {blocker.label}
                    </Text>
                  ))}
                </Card>
              ) : null}

              {revertPreview.data.revertable === 0 ? (
                <Text style={[styles.hintText, { color: theme.textSecondary }]}>
                  Es gibt nichts zurückzunehmen - aus diesem Lauf steht keine Buchung mehr im
                  Bestand.
                </Text>
              ) : null}
            </>
          ) : null}
        </ScrollView>
      </SheetModal>
    </>
  );
}

/** Beschriftung links, Wert rechts - die Kopfangaben eines Importlaufs. */
function SummaryRow({ label, value }: { label: string; value: string }) {
  const theme = useTheme();
  return (
    <View style={styles.summaryRow}>
      <Text style={[styles.summaryLabel, { color: theme.textSecondary }]}>{`${label}:`}</Text>
      <Text style={[styles.summaryValue, { color: theme.textPrimary }]} numberOfLines={1}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    padding: space.lg,
    gap: space.lg,
  },
  hintText: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  actionSection: {
    gap: space.md,
  },
  actionBlock: {
    gap: space.md,
  },
  summaryGrid: {
    gap: space.sm,
  },
  summaryRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: space.md,
  },
  summaryLabel: {
    fontSize: typeScale.body,
  },
  summaryValue: {
    flexShrink: 1,
    fontSize: typeScale.body,
    fontWeight: "700",
  },
  metricsContainer: {
    flexDirection: "row",
    gap: space.sm,
  },
  metricNumber: {
    fontSize: typeScale.large,
    fontWeight: "800",
  },
  processingRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  successText: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  historyRun: {
    gap: space.sm,
  },
  noAmount: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  sheetFooter: {
    gap: space.sm,
  },
  sheetContent: {
    padding: space.lg,
    gap: space.md,
  },
});
