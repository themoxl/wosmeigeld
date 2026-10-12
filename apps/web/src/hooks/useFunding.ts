import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  adoptFundingMatch,
  deactivateFundingProgram,
  evaluateFundingCombinations,
  exportFundingPrograms,
  getEligibilityProfile,
  getFundingCatalogStatus,
  getFundingProgram,
  getFundingMatches,
  getFundingPackage,
  listFundingPackages,
  listFundingPrograms,
  previewFundingImport,
  runFundingCheck,
  runFundingImport,
  saveEligibilityProfile,
  updateFundingProgramTerms,
} from "../lib/api";
import type {
  AdoptMatchRequest,
  CombinationEvaluateRequest,
  EligibilityProfileInput,
  FundingCheckRunResponse,
  FundingExportRequest,
  FundingImportRequest,
  FundingProgramTermsUpdate,
} from "../lib/types";

/**
 * Datenzugriff fuer den dynamischen Foerderkatalog (Plan 6/7.5, P3) inklusive
 * Foerderregister und Paket-/Dateiimport (docs/FOERDERKATALOG-IMPORT-PLAN.md).
 *
 * Der Katalog ist haushaltsbezogen: kuratierte Programme sind fuer jeden
 * sichtbar, importierte gehoeren dem jeweiligen Haushalt. Deshalb traegt jeder
 * Abfrageschluessel die householdId.
 */

interface FundingContext {
  token: string;
  householdId: string;
  enabled: boolean;
}

function useFundingContext(): FundingContext {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  return {
    token: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
  };
}

const eligibilityProfileQueryKey = (householdId: string, projectId: string) =>
  ["eligibility-profile", householdId, projectId] as const;
const fundingMatchesQueryKey = (householdId: string, scenarioId: string) =>
  ["funding-matches", householdId, scenarioId] as const;

export function useEligibilityProfile(projectId: string | null) {
  const { token, householdId, enabled } = useFundingContext();
  return useQuery({
    queryKey: eligibilityProfileQueryKey(householdId, projectId ?? ""),
    queryFn: () => getEligibilityProfile(token, householdId, projectId as string),
    enabled: enabled && Boolean(projectId),
  });
}

export function useSaveEligibilityProfile() {
  const { token, householdId } = useFundingContext();
  const queryClient = useQueryClient();
  return useMutation<
    Awaited<ReturnType<typeof saveEligibilityProfile>>,
    Error,
    { projectId: string; input: EligibilityProfileInput }
  >({
    mutationFn: ({ projectId, input }) =>
      saveEligibilityProfile(token, householdId, projectId, input),
    onSuccess: (_, { projectId }) => {
      void queryClient.invalidateQueries({
        queryKey: eligibilityProfileQueryKey(householdId, projectId),
      });
    },
  });
}

export function useFundingMatches(projectId: string | null, scenarioId: string | null) {
  const { token, householdId, enabled } = useFundingContext();
  return useQuery<FundingCheckRunResponse | null>({
    queryKey: fundingMatchesQueryKey(householdId, scenarioId ?? ""),
    queryFn: () => getFundingMatches(token, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

export function useRunFundingCheck() {
  const { token, householdId } = useFundingContext();
  const queryClient = useQueryClient();
  return useMutation<FundingCheckRunResponse, Error, { projectId: string; scenarioId: string }>({
    mutationFn: ({ projectId, scenarioId }) =>
      runFundingCheck(token, householdId, projectId, scenarioId),
    onSuccess: (_, { scenarioId }) => {
      void queryClient.invalidateQueries({
        queryKey: fundingMatchesQueryKey(householdId, scenarioId),
      });
    },
  });
}

export function useEvaluateFundingCombinations() {
  const { token, householdId } = useFundingContext();
  return useMutation<
    Awaited<ReturnType<typeof evaluateFundingCombinations>>,
    Error,
    { projectId: string; scenarioId: string; input: CombinationEvaluateRequest }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      evaluateFundingCombinations(token, householdId, projectId, scenarioId, input),
  });
}

// ---------------------------------------------------------------------------
// Foerderregister und Import/Export
// ---------------------------------------------------------------------------

// Alle Filter gehoeren in den Schluessel, seit die Liste an zwei Stellen mit
// unterschiedlichem Zuschnitt geholt wird: die Katalogseite fragt bundesweit,
// der Foerder-Tab eines Vorhabens zusaetzlich nach Region und Vorhabenart.
// Faellt ein Filter aus dem Schluessel, bekommt die eine Seite die
// gecachte Liste der anderen - und es sieht aus, als seien Programme
// verschwunden.
const fundingProgramsQueryKey = (
  householdId: string,
  filters: {
    countryCode?: string;
    regionCode?: string;
    projectType?: string;
    includeInactive: boolean;
  },
) =>
  [
    "funding-programs",
    householdId,
    filters.countryCode ?? "DE",
    filters.regionCode ?? null,
    filters.projectType ?? null,
    filters.includeInactive,
  ] as const;
const fundingPackagesQueryKey = (householdId: string) => ["funding-packages", householdId] as const;
const fundingCatalogStatusQueryKey = (householdId: string) =>
  ["funding-catalog-status", householdId] as const;

export function useFundingPrograms(
  options: {
    countryCode?: string;
    regionCode?: string;
    projectType?: string;
    includeInactive?: boolean;
  } = {},
) {
  const { token, householdId, enabled } = useFundingContext();
  const filters = {
    countryCode: options.countryCode,
    regionCode: options.regionCode,
    projectType: options.projectType,
    includeInactive: options.includeInactive ?? false,
  };
  return useQuery({
    queryKey: fundingProgramsQueryKey(householdId, filters),
    queryFn: () => listFundingPrograms(token, householdId, filters),
    enabled,
  });
}

export function useFundingProgram(programId: string | null) {
  const { token, householdId, enabled } = useFundingContext();
  return useQuery({
    queryKey: ["funding-program", householdId, programId ?? ""],
    queryFn: () => getFundingProgram(token, householdId, programId as string),
    enabled: enabled && Boolean(programId),
  });
}

export function useFundingPackages() {
  const { token, householdId, enabled } = useFundingContext();
  return useQuery({
    queryKey: fundingPackagesQueryKey(householdId),
    queryFn: () => listFundingPackages(token, householdId),
    enabled,
  });
}

export function useFundingCatalogStatus() {
  const { token, householdId, enabled } = useFundingContext();
  return useQuery({
    queryKey: fundingCatalogStatusQueryKey(householdId),
    queryFn: () => getFundingCatalogStatus(token, householdId),
    enabled,
  });
}

export function useLoadFundingPackage() {
  const { token, householdId } = useFundingContext();
  return useMutation<Awaited<ReturnType<typeof getFundingPackage>>, Error, string>({
    mutationFn: (packageId) => getFundingPackage(token, householdId, packageId),
  });
}

export function usePreviewFundingImport() {
  const { token, householdId } = useFundingContext();
  return useMutation<Awaited<ReturnType<typeof previewFundingImport>>, Error, FundingImportRequest>(
    {
      mutationFn: (input) => previewFundingImport(token, householdId, input),
    },
  );
}

/** Nach einem Import ist alles drei betroffen: Registerliste, Paketkacheln
 * (Installationsstand) und der Katalogstatus-Streifen. */
function useInvalidateFundingCatalog() {
  const { householdId } = useFundingContext();
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["funding-programs", householdId] });
    void queryClient.invalidateQueries({ queryKey: fundingPackagesQueryKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: fundingCatalogStatusQueryKey(householdId) });
  };
}

export function useRunFundingImport() {
  const { token, householdId } = useFundingContext();
  const invalidate = useInvalidateFundingCatalog();
  return useMutation<Awaited<ReturnType<typeof runFundingImport>>, Error, FundingImportRequest>({
    mutationFn: (input) => runFundingImport(token, householdId, input),
    onSuccess: invalidate,
  });
}

export function useExportFundingPrograms() {
  const { token, householdId } = useFundingContext();
  return useMutation<
    Awaited<ReturnType<typeof exportFundingPrograms>>,
    Error,
    FundingExportRequest
  >({
    mutationFn: (input) => exportFundingPrograms(token, householdId, input),
  });
}

export function useDeactivateFundingProgram() {
  const { token, householdId } = useFundingContext();
  const invalidate = useInvalidateFundingCatalog();
  return useMutation<Awaited<ReturnType<typeof deactivateFundingProgram>>, Error, string>({
    mutationFn: (programId) => deactivateFundingProgram(token, householdId, programId),
    onSuccess: invalidate,
  });
}

export function useUpdateFundingProgramTerms() {
  const { token, householdId } = useFundingContext();
  const invalidate = useInvalidateFundingCatalog();
  const queryClient = useQueryClient();
  return useMutation<
    Awaited<ReturnType<typeof updateFundingProgramTerms>>,
    Error,
    { programId: string; input: FundingProgramTermsUpdate }
  >({
    mutationFn: ({ programId, input }) =>
      updateFundingProgramTerms(token, householdId, programId, input),
    onSuccess: (program) => {
      queryClient.setQueryData(["funding-program", householdId, program.id], program);
      invalidate();
    },
  });
}

export function useAdoptFundingMatch() {
  const { token, householdId } = useFundingContext();
  const queryClient = useQueryClient();
  return useMutation<
    Awaited<ReturnType<typeof adoptFundingMatch>>,
    Error,
    { projectId: string; matchId: string; input: AdoptMatchRequest }
  >({
    mutationFn: ({ projectId, matchId, input }) =>
      adoptFundingMatch(token, householdId, projectId, matchId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["financing-sources", householdId] });
    },
  });
}
