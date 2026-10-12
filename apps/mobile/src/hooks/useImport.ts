import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  commitImportRun,
  getImportRows,
  getImportRun,
  listImportRuns,
  previewImportRevert,
  revertImportRun,
  uploadImport,
} from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

export function useImportRun(runId: string | null) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["importRun", selectedHouseholdId, runId],
    queryFn: ({ signal }) =>
      getImportRun(accessToken as string, selectedHouseholdId as string, runId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId && !!runId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "pending" || status === "analysing" ? 2000 : false;
    },
  });
}

/** Historie der Importlaeufe (GET /v1/imports). */
export function useImportRuns() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["importRuns", selectedHouseholdId],
    queryFn: ({ signal }) =>
      listImportRuns(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

/**
 * Vorschau auf das Zuruecknehmen eines Laufs.
 *
 * Ohne Zwischenspeicher (staleTime/gcTime 0): zwischen zwei Aufrufen kann sich
 * der Bestand geaendert haben - eine Buchung bearbeitet, einem Vertrag
 * zugeordnet - und der Dialog zeigte sonst eine Zahl, die nicht mehr gilt.
 */
export function useImportRevertPreview(runId: string | null) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["importRevertPreview", selectedHouseholdId, runId],
    queryFn: ({ signal }) =>
      previewImportRevert(
        accessToken as string,
        selectedHouseholdId as string,
        runId as string,
        signal,
      ),
    enabled: !!accessToken && !!selectedHouseholdId && !!runId,
    staleTime: 0,
    gcTime: 0,
  });
}

/** Nimmt die Buchungen eines uebernommenen Laufs aus dem Bestand. */
export function useRevertImportRun() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (runId: string) =>
      revertImportRun(accessToken as string, selectedHouseholdId as string, runId),
    onSuccess: () => {
      // Der Buchungsbestand aendert sich - alles, was darauf aufbaut, neu laden.
      for (const key of [
        "importRuns",
        "transactions",
        "accounts",
        "budget",
        "review",
        "overviewReport",
      ]) {
        void queryClient.invalidateQueries({ queryKey: [key, selectedHouseholdId] });
      }
    },
  });
}

export function useImportRows(runId: string | null, enabled = true) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["importRows", selectedHouseholdId, runId],
    queryFn: ({ signal }) =>
      getImportRows(accessToken as string, selectedHouseholdId as string, runId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId && !!runId && enabled,
  });
}

export function useUploadImport() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useMutation({
    mutationFn: (data: { file: { uri: string; name: string; type: string }; accountId?: string }) =>
      uploadImport(accessToken as string, selectedHouseholdId as string, data.file, data.accountId),
  });
}

export function useCommitImportRun() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: { runId: string; rowIds?: string[] }) =>
      commitImportRun(
        accessToken as string,
        selectedHouseholdId as string,
        data.runId,
        data.rowIds,
      ),
    onSuccess: (_, variables) => {
      void queryClient.invalidateQueries({
        queryKey: ["importRun", selectedHouseholdId, variables.runId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["importRows", selectedHouseholdId, variables.runId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["transactions", selectedHouseholdId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["accounts", selectedHouseholdId],
      });
    },
  });
}
