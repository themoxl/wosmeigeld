import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  applyCostImport,
  createCostImportProfile,
  deleteCostImportProfile,
  listCostImportProfiles,
  previewCostImport,
} from "../lib/api";
import type {
  CostImportApplyResult,
  CostImportMappingProfile,
  CostImportMappingProfileInput,
  CostImportPreviewResponse,
  CostImportRowResolution,
} from "../lib/types";
import { costItemsQueryKey, costSectionsQueryKey } from "./useProjects";

/**
 * Datenzugriff fuer den Excel-/CSV-Kostenplanimport (Plan 10, P4): gespeicherte
 * Mappingprofile sowie der Preview-/Apply-Fluss. Preview ist bewusst eine
 * Mutation statt einer Query - sie haengt vom hochgeladenen File-Objekt ab,
 * das sich nicht als stabiler Query-Key eignet, und darf nie im Hintergrund
 * automatisch neu laufen.
 */

interface CostImportContext {
  token: string;
  householdId: string;
  enabled: boolean;
}

function useCostImportContext(): CostImportContext {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  return {
    token: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
  };
}

const costImportProfilesQueryKey = (householdId: string) =>
  ["cost-import-profiles", householdId] as const;

export function useCostImportProfiles() {
  const { token, householdId, enabled } = useCostImportContext();
  return useQuery<CostImportMappingProfile[]>({
    queryKey: costImportProfilesQueryKey(householdId),
    queryFn: () => listCostImportProfiles(token, householdId),
    enabled,
  });
}

export function useCreateCostImportProfile() {
  const { token, householdId } = useCostImportContext();
  const queryClient = useQueryClient();
  return useMutation<CostImportMappingProfile, Error, CostImportMappingProfileInput>({
    mutationFn: (input) => createCostImportProfile(token, householdId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: costImportProfilesQueryKey(householdId) });
    },
  });
}

export function useDeleteCostImportProfile() {
  const { token, householdId } = useCostImportContext();
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (profileId) => deleteCostImportProfile(token, householdId, profileId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: costImportProfilesQueryKey(householdId) });
    },
  });
}

export interface CostImportPreviewVars {
  projectId: string;
  scenarioId: string;
  file: File;
  mapping: Record<string, string>;
  sheetName?: string | null;
  headerRow?: number;
}

export function usePreviewCostImport() {
  const { token, householdId } = useCostImportContext();
  return useMutation<CostImportPreviewResponse, Error, CostImportPreviewVars>({
    mutationFn: ({ projectId, scenarioId, ...params }) =>
      previewCostImport(token, householdId, projectId, scenarioId, params),
  });
}

export interface CostImportApplyVars extends CostImportPreviewVars {
  resolutions: CostImportRowResolution[];
  requestId: string;
  previewFingerprint: string;
}

/**
 * Idempotenzschluessel fuer den Apply-Aufruf (Plan 10.2). `crypto.randomUUID`
 * fehlt in aelteren Testumgebungen, deshalb mit Rueckfallebene (analog zu
 * hooks/useContracts.ts).
 */
export function newRequestId(): string {
  const webCrypto = globalThis.crypto;
  if (webCrypto && typeof webCrypto.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function useApplyCostImport() {
  const { token, householdId } = useCostImportContext();
  const queryClient = useQueryClient();
  return useMutation<CostImportApplyResult, Error, CostImportApplyVars>({
    mutationFn: ({ projectId, scenarioId, ...params }) =>
      applyCostImport(token, householdId, projectId, scenarioId, params),
    onSuccess: (_, { scenarioId }) => {
      void queryClient.invalidateQueries({
        queryKey: costItemsQueryKey(householdId, scenarioId),
      });
      void queryClient.invalidateQueries({
        queryKey: costSectionsQueryKey(householdId, scenarioId),
      });
    },
  });
}
