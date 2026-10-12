import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  calculateProjectScenario,
  createActual,
  createProject,
  getFundingMatches,
  getFundingProgram,
  getProject,
  listActuals,
  listCostItems,
  listFinancingSources,
  listProjectScenarios,
  listProjects,
  listTransactionCandidates,
  updateCostItem,
  type ProjectListParams,
  type TransactionCandidateParams,
} from "../api/client";
import type {
  CostActualCreateInput,
  CostItemUpdateInput,
  MajorProjectCreateInput,
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/**
 * Vorhaben & Grossinvestitionen auf Mobile
 * (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md, Abschnitt 9, P5).
 *
 * Bewusst nur Lese-Hooks plus die drei erlaubten Schreibaktionen (Vorhaben
 * anlegen, Kostenposition als bezahlt markieren, Banktransaktion zuordnen).
 * Finanzierungsquellen-Bearbeitung, Foerderprofil, Darlehensplaene und
 * Szenario-Klon/-Auswahl bleiben Web-only (dort mehrstufige Formulare mit
 * Kontostand-/Reservierungspruefung, siehe apps/web/src/hooks/useProjects.ts).
 */

interface ProjectContext {
  accessToken: string;
  householdId: string;
  enabled: boolean;
  canEdit: boolean;
}

export function useProjectContext(): ProjectContext {
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const role = selectedHousehold?.role ?? null;
  return {
    accessToken: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
    canEdit: role === "owner" || role === "editor",
  };
}

const projectsKey = (householdId: string) => ["projects", householdId] as const;
const projectKey = (householdId: string, projectId: string) =>
  ["project", householdId, projectId] as const;
const scenariosKey = (householdId: string, projectId: string) =>
  ["project-scenarios", householdId, projectId] as const;
const calculationKey = (householdId: string, scenarioId: string) =>
  ["scenario-calculation", householdId, scenarioId] as const;
const costItemsKey = (householdId: string, scenarioId: string) =>
  ["cost-items", householdId, scenarioId] as const;
const financingSourcesKey = (householdId: string, scenarioId: string) =>
  ["financing-sources", householdId, scenarioId] as const;
const fundingMatchesKey = (householdId: string, scenarioId: string) =>
  ["funding-matches", householdId, scenarioId] as const;
const actualsKey = (householdId: string, itemId: string) =>
  ["cost-actuals", householdId, itemId] as const;

/** Vorhabenliste (GET /v1/projects). */
export function useProjectList(params: ProjectListParams = {}) {
  const { accessToken, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: [...projectsKey(householdId), params],
    queryFn: ({ signal }) => listProjects(accessToken, householdId, params, signal),
    enabled,
  });
}

/** Vorhabenkopf (GET /v1/projects/{id}). */
export function useProject(projectId: string | null) {
  const { accessToken, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: projectKey(householdId, projectId ?? ""),
    queryFn: ({ signal }) => getProject(accessToken, householdId, projectId as string, signal),
    enabled: enabled && Boolean(projectId),
  });
}

/** Neues, einfaches Vorhaben anlegen (POST /v1/projects). */
export function useCreateProject() {
  const { accessToken, householdId } = useProjectContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: MajorProjectCreateInput) => createProject(accessToken, householdId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: projectsKey(householdId) });
    },
  });
}

/** Szenarien mit Kurzkennzahlen (GET .../scenarios) - Grundlage der Szenarioauswahl. */
export function useProjectScenarios(projectId: string | null) {
  const { accessToken, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: scenariosKey(householdId, projectId ?? ""),
    queryFn: ({ signal }) =>
      listProjectScenarios(accessToken, householdId, projectId as string, signal),
    enabled: enabled && Boolean(projectId),
  });
}

/** Liveberechnung des aktiven Szenarios (POST .../calculate, keine DB-Aenderung). */
export function useScenarioCalculation(projectId: string | null, scenarioId: string | null) {
  const { accessToken, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: calculationKey(householdId, scenarioId ?? ""),
    queryFn: () =>
      calculateProjectScenario(accessToken, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

/** Kostenpositionen des aktiven Szenarios (GET .../cost-items). */
export function useCostItems(projectId: string | null, scenarioId: string | null) {
  const { accessToken, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: costItemsKey(householdId, scenarioId ?? ""),
    queryFn: ({ signal }) =>
      listCostItems(accessToken, householdId, projectId as string, scenarioId as string, signal),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

/**
 * "Als bezahlt markieren" (PATCH .../cost-items/{id}, minimaler Status-Patch).
 * Invalidiert zusaetzlich die Liveberechnung, weil sich Ist-/Restbetrag der
 * Szenario-Kennzahlen serverseitig aus dem Kostenstatus ableiten.
 */
export function useUpdateCostItem() {
  const { accessToken, householdId } = useProjectContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      itemId,
      version,
      input,
    }: {
      projectId: string;
      scenarioId: string;
      itemId: string;
      version: number;
      input: CostItemUpdateInput;
    }) => updateCostItem(accessToken, householdId, projectId, itemId, input, version),
    onSuccess: (_, { scenarioId }) => {
      void queryClient.invalidateQueries({ queryKey: costItemsKey(householdId, scenarioId) });
      void queryClient.invalidateQueries({ queryKey: calculationKey(householdId, scenarioId) });
    },
  });
}

/** Finanzierungsquellen des aktiven Szenarios, read-only (GET .../financing-sources). */
export function useFinancingSources(projectId: string | null, scenarioId: string | null) {
  const { accessToken, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: financingSourcesKey(householdId, scenarioId ?? ""),
    queryFn: ({ signal }) =>
      listFinancingSources(
        accessToken,
        householdId,
        projectId as string,
        scenarioId as string,
        signal,
      ),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

/** Letzter Fördercheck-Lauf, read-only (GET .../funding-matches) - "Datenstand sehen". */
export function useFundingMatches(projectId: string | null, scenarioId: string | null) {
  const { accessToken, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: fundingMatchesKey(householdId, scenarioId ?? ""),
    queryFn: ({ signal }) =>
      getFundingMatches(
        accessToken,
        householdId,
        projectId as string,
        scenarioId as string,
        signal,
      ),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

/** Programmdetail mit Frische/Quelle (GET /v1/funding/programs/{id}) - "Datenstand sehen". */
export function useFundingProgram(programId: string | null) {
  const { accessToken, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: ["funding-program", householdId, programId ?? ""] as const,
    queryFn: ({ signal }) =>
      getFundingProgram(accessToken, householdId, programId as string, signal),
    enabled: enabled && Boolean(programId),
  });
}

/** Vorhandene Ist-Zuordnungen einer Kostenposition (GET .../actuals). */
export function useActuals(projectId: string | null, itemId: string | null) {
  const { accessToken, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: actualsKey(householdId, itemId ?? ""),
    queryFn: ({ signal }) =>
      listActuals(accessToken, householdId, projectId as string, itemId as string, signal),
    enabled: enabled && Boolean(projectId) && Boolean(itemId),
  });
}

/** "Banktransaktion zuordnen" (POST .../actuals). Kann mit 422 `actuals_overbooking` fehlschlagen. */
export function useCreateActual() {
  const { accessToken, householdId } = useProjectContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      itemId,
      input,
    }: {
      projectId: string;
      scenarioId: string;
      itemId: string;
      input: CostActualCreateInput;
    }) => createActual(accessToken, householdId, projectId, itemId, input),
    onSuccess: (_, { itemId, scenarioId }) => {
      void queryClient.invalidateQueries({ queryKey: actualsKey(householdId, itemId) });
      void queryClient.invalidateQueries({ queryKey: costItemsKey(householdId, scenarioId) });
      void queryClient.invalidateQueries({ queryKey: calculationKey(householdId, scenarioId) });
    },
  });
}

/** Buchungen mit noch verfuegbarem Zuordnungsbetrag (GET .../transaction-candidates). */
export function useTransactionCandidates(
  projectId: string | null,
  filters: TransactionCandidateParams,
) {
  const { accessToken, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: ["transaction-candidates", householdId, projectId ?? "", filters] as const,
    queryFn: ({ signal }) =>
      listTransactionCandidates(accessToken, householdId, projectId as string, filters, signal),
    enabled: enabled && Boolean(projectId),
  });
}
