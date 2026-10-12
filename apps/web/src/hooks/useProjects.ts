import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  applyCostAiSuggestions,
  applyProjectCompletion,
  archiveProject,
  calculateProjectScenario,
  cloneProjectScenario,
  compareProjectScenarios,
  createCostItem,
  createCostSection,
  createFinancingSource,
  createFinancingConfiguration,
  createProject,
  createProjectScenario,
  createRecurringImpact,
  deleteCostItem,
  deleteCostSection,
  deleteFinancingSource,
  deleteGoalAssumption,
  deleteProject,
  deleteProjectScenario,
  deleteRecurringImpact,
  getCashflowAssumptions,
  getEquityAvailability,
  getFinancialSummary,
  getProject,
  getProjectDeleteImpact,
  getProjectTimeline,
  listCostItems,
  listCostSections,
  listFinancingSources,
  listGoalAssumptions,
  listProjectScenarios,
  listProjects,
  listRecurringImpacts,
  previewCostAiSuggestions,
  previewFinancingDraws,
  previewProjectCompletion,
  replaceCostSchedule,
  saveCashflowAssumptions,
  saveGoalAssumption,
  selectProjectScenario,
  suggestCashflowAssumptions,
  transitionProject,
  updateCostItem,
  updateCostSection,
  updateFinancingSource,
  updateFinancingConfiguration,
  updateProject,
  updateProjectScenario,
  updateRecurringImpact,
  type ProjectListParams,
} from "../lib/api";
import type {
  CashflowAssumptions,
  CashflowAssumptionsInput,
  CostAiSuggestionApplyInput,
  CostAiSuggestionPreview,
  CostAiSuggestionRequest,
  CostAiSuggestionResult,
  CostDrawPreview,
  CostDrawPreviewInput,
  CostItem,
  CostItemCreateInput,
  CostItemUpdateInput,
  CostScheduleReplaceInput,
  CostSection,
  CostSectionCreateInput,
  CostSectionUpdateInput,
  EquityAvailability,
  FinancingSource,
  FinancingConfigurationCreateInput,
  FinancingConfigurationResponse,
  FinancingConfigurationUpdateInput,
  FinancingSourceCreateInput,
  FinancingSourceUpdateInput,
  GoalAssumption,
  GoalAssumptionInput,
  MajorProject,
  MajorProjectCreateInput,
  MajorProjectUpdateInput,
  ProjectListItem,
  ProjectScenario,
  ProjectScenarioCreateInput,
  ProjectScenarioUpdateInput,
  ProjectTransitionRequest,
  RecurringImpact,
  RecurringImpactCreateInput,
  RecurringImpactUpdateInput,
  ScenarioCloneRequest,
  ScenarioFinancialSummary,
  ScenarioTimeline,
} from "../lib/types";

/**
 * Datenzugriff fuer Vorhaben & Grossinvestitionen
 * (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md, P1).
 *
 * Caches: `["projects", householdId]` die Liste, je Vorhaben Kopf/Szenarien/
 * Kostenplan getrennt. Jede Mutation invalidiert das betroffene Vorhaben (und
 * bei Statuswechseln/Klon/Auswahl zusaetzlich die Liste, weil sich dort
 * Status bzw. ausgewaehltes Szenario aendern).
 */

export const projectsQueryKey = (householdId: string) => ["projects", householdId] as const;
export const projectQueryKey = (householdId: string, projectId: string) =>
  ["project", householdId, projectId] as const;
export const projectScenariosQueryKey = (householdId: string, projectId: string) =>
  ["project-scenarios", householdId, projectId] as const;
export const costSectionsQueryKey = (householdId: string, scenarioId: string) =>
  ["cost-sections", householdId, scenarioId] as const;
export const costItemsQueryKey = (householdId: string, scenarioId: string) =>
  ["cost-items", householdId, scenarioId] as const;

interface ProjectContext {
  token: string;
  householdId: string;
  enabled: boolean;
  role: string | null;
  canEdit: boolean;
}

export function useProjectContext(): ProjectContext {
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const role = selectedHousehold?.role ?? null;
  return {
    token: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
    role,
    canEdit: role === "owner" || role === "editor",
  };
}

export function useInvalidateProjects() {
  const queryClient = useQueryClient();
  const { householdId } = useProjectContext();
  return useCallback(
    (projectId?: string) => {
      void queryClient.invalidateQueries({ queryKey: projectsQueryKey(householdId) });
      if (projectId) {
        void queryClient.invalidateQueries({ queryKey: projectQueryKey(householdId, projectId) });
        void queryClient.invalidateQueries({
          queryKey: projectScenariosQueryKey(householdId, projectId),
        });
      }
    },
    [queryClient, householdId],
  );
}

export function useProjectList(params: ProjectListParams = {}) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<ProjectListItem[]>({
    queryKey: [...projectsQueryKey(householdId), params],
    queryFn: () => listProjects(token, householdId, params),
    enabled,
  });
}

export function useProject(projectId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<MajorProject>({
    queryKey: projectQueryKey(householdId, projectId ?? ""),
    queryFn: () => getProject(token, householdId, projectId as string),
    enabled: enabled && Boolean(projectId),
  });
}

export function useProjectDeleteImpact(projectId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: ["project-delete-impact", householdId, projectId],
    queryFn: () => getProjectDeleteImpact(token, householdId, projectId as string),
    enabled: enabled && Boolean(projectId),
  });
}

export function useCreateProject() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<MajorProject, Error, MajorProjectCreateInput>({
    mutationFn: (input) => createProject(token, householdId, input),
    onSuccess: () => invalidate(),
  });
}

export function useUpdateProject() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<
    MajorProject,
    Error,
    { projectId: string; version: number; input: MajorProjectUpdateInput }
  >({
    mutationFn: ({ projectId, version, input }) =>
      updateProject(token, householdId, projectId, input, version),
    onSuccess: (_, { projectId }) => invalidate(projectId),
  });
}

export function useTransitionProject() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<
    MajorProject,
    Error,
    { projectId: string; version: number; input: ProjectTransitionRequest }
  >({
    mutationFn: ({ projectId, version, input }) =>
      transitionProject(token, householdId, projectId, input, version),
    onSuccess: (_, { projectId }) => invalidate(projectId),
  });
}

export function useDeleteProject() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<void, Error, string>({
    mutationFn: (projectId) => deleteProject(token, householdId, projectId),
    onSuccess: () => invalidate(),
  });
}

export function useArchiveProject() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<MajorProject, Error, string>({
    mutationFn: (projectId) => archiveProject(token, householdId, projectId),
    onSuccess: (_, projectId) => invalidate(projectId),
  });
}

export function useProjectScenarios(projectId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<ProjectScenario[]>({
    queryKey: projectScenariosQueryKey(householdId, projectId ?? ""),
    queryFn: () => listProjectScenarios(token, householdId, projectId as string),
    enabled: enabled && Boolean(projectId),
  });
}

export function useCreateProjectScenario() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<
    ProjectScenario,
    Error,
    { projectId: string; input: ProjectScenarioCreateInput }
  >({
    mutationFn: ({ projectId, input }) =>
      createProjectScenario(token, householdId, projectId, input),
    onSuccess: (_, { projectId }) => invalidate(projectId),
  });
}

export function useUpdateProjectScenario() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<
    ProjectScenario,
    Error,
    { projectId: string; scenarioId: string; version: number; input: ProjectScenarioUpdateInput }
  >({
    mutationFn: ({ projectId, scenarioId, version, input }) =>
      updateProjectScenario(token, householdId, projectId, scenarioId, input, version),
    onSuccess: (_, { projectId }) => invalidate(projectId),
  });
}

export function useDeleteProjectScenario() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<void, Error, { projectId: string; scenarioId: string }>({
    mutationFn: ({ projectId, scenarioId }) =>
      deleteProjectScenario(token, householdId, projectId, scenarioId),
    onSuccess: (_, { projectId }) => invalidate(projectId),
  });
}

export function useCloneProjectScenario() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<
    ProjectScenario,
    Error,
    { projectId: string; scenarioId: string; input: ScenarioCloneRequest }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      cloneProjectScenario(token, householdId, projectId, scenarioId, input),
    onSuccess: (_, { projectId }) => invalidate(projectId),
  });
}

export function useSelectProjectScenario() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<ProjectScenario, Error, { projectId: string; scenarioId: string }>({
    mutationFn: ({ projectId, scenarioId }) =>
      selectProjectScenario(token, householdId, projectId, scenarioId),
    onSuccess: (_, { projectId }) => invalidate(projectId),
  });
}

export function useScenarioComparison(projectId: string | null, scenarioIds: string[]) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: ["scenario-comparison", householdId, projectId, scenarioIds],
    queryFn: () => compareProjectScenarios(token, householdId, projectId as string, scenarioIds),
    enabled: enabled && Boolean(projectId) && scenarioIds.length >= 2,
  });
}

export function useScenarioCalculation(projectId: string | null, scenarioId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: ["scenario-calculation", householdId, projectId, scenarioId],
    queryFn: () =>
      calculateProjectScenario(token, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

export function useCostSections(projectId: string | null, scenarioId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<CostSection[]>({
    queryKey: costSectionsQueryKey(householdId, scenarioId ?? ""),
    queryFn: () => listCostSections(token, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

export function useCostItems(projectId: string | null, scenarioId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<CostItem[]>({
    queryKey: costItemsQueryKey(householdId, scenarioId ?? ""),
    queryFn: () => listCostItems(token, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

function useInvalidateCostPlan() {
  const queryClient = useQueryClient();
  const { householdId } = useProjectContext();
  return useCallback(
    (scenarioId: string, projectId?: string) => {
      void queryClient.invalidateQueries({
        queryKey: costSectionsQueryKey(householdId, scenarioId),
      });
      void queryClient.invalidateQueries({ queryKey: costItemsQueryKey(householdId, scenarioId) });
      if (projectId) {
        void queryClient.invalidateQueries({
          queryKey: ["scenario-calculation", householdId, projectId, scenarioId],
        });
      }
    },
    [queryClient, householdId],
  );
}

export function useCreateCostSection() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateCostPlan();
  return useMutation<
    CostSection,
    Error,
    { projectId: string; scenarioId: string; input: CostSectionCreateInput }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      createCostSection(token, householdId, projectId, scenarioId, input),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useUpdateCostSection() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateCostPlan();
  return useMutation<
    CostSection,
    Error,
    {
      projectId: string;
      scenarioId: string;
      sectionId: string;
      version: number;
      input: CostSectionUpdateInput;
    }
  >({
    mutationFn: ({ projectId, sectionId, version, input }) =>
      updateCostSection(token, householdId, projectId, sectionId, input, version),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useDeleteCostSection() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateCostPlan();
  return useMutation<void, Error, { projectId: string; scenarioId: string; sectionId: string }>({
    mutationFn: ({ projectId, sectionId }) =>
      deleteCostSection(token, householdId, projectId, sectionId),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useCreateCostItem() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateCostPlan();
  return useMutation<
    CostItem,
    Error,
    { projectId: string; scenarioId: string; input: CostItemCreateInput }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      createCostItem(token, householdId, projectId, scenarioId, input),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useUpdateCostItem() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateCostPlan();
  return useMutation<
    CostItem,
    Error,
    {
      projectId: string;
      scenarioId: string;
      itemId: string;
      version: number;
      input: CostItemUpdateInput;
    }
  >({
    mutationFn: ({ projectId, itemId, version, input }) =>
      updateCostItem(token, householdId, projectId, itemId, input, version),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useDeleteCostItem() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateCostPlan();
  return useMutation<void, Error, { projectId: string; scenarioId: string; itemId: string }>({
    mutationFn: ({ projectId, itemId }) => deleteCostItem(token, householdId, projectId, itemId),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useReplaceCostSchedule() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateCostPlan();
  return useMutation<
    unknown,
    Error,
    { projectId: string; scenarioId: string; itemId: string; input: CostScheduleReplaceInput }
  >({
    mutationFn: ({ projectId, itemId, input }) =>
      replaceCostSchedule(token, householdId, projectId, itemId, input),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

/**
 * KI-Kostenschaetzung: Vorschau ist bewusst eine Mutation (kein `useQuery`) -
 * sie loest einen Modellaufruf aus und legt eine kurzlebige Operation ab,
 * ist also kein zwischenspeicherbarer Lesezugriff.
 */
export function usePreviewCostAiSuggestions() {
  const { token, householdId } = useProjectContext();
  return useMutation<
    CostAiSuggestionPreview,
    Error,
    { projectId: string; scenarioId: string; input: CostAiSuggestionRequest }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      previewCostAiSuggestions(token, householdId, projectId, scenarioId, input),
  });
}

export function useApplyCostAiSuggestions() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateCostPlan();
  return useMutation<
    CostAiSuggestionResult,
    Error,
    { projectId: string; scenarioId: string; input: CostAiSuggestionApplyInput }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      applyCostAiSuggestions(token, householdId, projectId, scenarioId, input),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

// ---------------------------------------------------------------------------
// Finanzierung, Annahmen, Zielwirkung, Abschluss (P2)
// ---------------------------------------------------------------------------

const financingSourcesQueryKey = (householdId: string, scenarioId: string) =>
  ["financing-sources", householdId, scenarioId] as const;
const equityAvailabilityQueryKey = (householdId: string, projectId: string) =>
  ["equity-availability", householdId, projectId] as const;
const recurringImpactsQueryKey = (householdId: string, scenarioId: string) =>
  ["recurring-impacts", householdId, scenarioId] as const;
const cashflowAssumptionsQueryKey = (householdId: string, scenarioId: string) =>
  ["cashflow-assumptions", householdId, scenarioId] as const;
const goalAssumptionsQueryKey = (householdId: string, scenarioId: string) =>
  ["goal-assumptions", householdId, scenarioId] as const;
const financialSummaryQueryKey = (householdId: string, scenarioId: string) =>
  ["financial-summary", householdId, scenarioId] as const;
const timelineQueryKey = (householdId: string, scenarioId: string) =>
  ["project-timeline", householdId, scenarioId] as const;

function useInvalidateFinancials() {
  const queryClient = useQueryClient();
  const { householdId } = useProjectContext();
  return useCallback(
    (scenarioId: string, projectId?: string) => {
      void queryClient.invalidateQueries({
        queryKey: financingSourcesQueryKey(householdId, scenarioId),
      });
      void queryClient.invalidateQueries({
        queryKey: recurringImpactsQueryKey(householdId, scenarioId),
      });
      void queryClient.invalidateQueries({
        queryKey: cashflowAssumptionsQueryKey(householdId, scenarioId),
      });
      void queryClient.invalidateQueries({
        queryKey: goalAssumptionsQueryKey(householdId, scenarioId),
      });
      void queryClient.invalidateQueries({
        queryKey: financialSummaryQueryKey(householdId, scenarioId),
      });
      void queryClient.invalidateQueries({ queryKey: timelineQueryKey(householdId, scenarioId) });
      void queryClient.invalidateQueries({ queryKey: ["debt-plans", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["debt-plan", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["debt-schedule", householdId] });
      if (projectId) {
        void queryClient.invalidateQueries({
          queryKey: equityAvailabilityQueryKey(householdId, projectId),
        });
      }
    },
    [queryClient, householdId],
  );
}

export function useFinancingSources(projectId: string | null, scenarioId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<FinancingSource[]>({
    queryKey: financingSourcesQueryKey(householdId, scenarioId ?? ""),
    queryFn: () =>
      listFinancingSources(token, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

export function useCreateFinancingSource() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<
    FinancingSource,
    Error,
    { projectId: string; scenarioId: string; input: FinancingSourceCreateInput }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      createFinancingSource(token, householdId, projectId, scenarioId, input),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useCreateFinancingConfiguration() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<
    FinancingConfigurationResponse,
    Error,
    { projectId: string; scenarioId: string; input: FinancingConfigurationCreateInput }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      createFinancingConfiguration(token, householdId, projectId, scenarioId, input),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useFinancingDrawPreview(
  projectId: string,
  scenarioId: string,
  input: CostDrawPreviewInput,
  enabled: boolean,
) {
  const { token, householdId, enabled: contextEnabled } = useProjectContext();
  return useQuery<CostDrawPreview>({
    queryKey: [
      "financing-draw-preview",
      householdId,
      scenarioId,
      input.principal_limit_minor,
      input.starts_on,
      input.draw_mode,
      input.draws,
    ],
    queryFn: () => previewFinancingDraws(token, householdId, projectId, scenarioId, input),
    enabled: contextEnabled && enabled,
    staleTime: 30_000,
  });
}

export function useUpdateFinancingConfiguration() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<
    FinancingConfigurationResponse,
    Error,
    {
      projectId: string;
      scenarioId: string;
      sourceId: string;
      version: number;
      input: FinancingConfigurationUpdateInput;
    }
  >({
    mutationFn: ({ projectId, sourceId, version, input }) =>
      updateFinancingConfiguration(token, householdId, projectId, sourceId, input, version),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useUpdateFinancingSource() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<
    FinancingSource,
    Error,
    {
      projectId: string;
      scenarioId: string;
      sourceId: string;
      version: number;
      input: FinancingSourceUpdateInput;
    }
  >({
    mutationFn: ({ projectId, sourceId, version, input }) =>
      updateFinancingSource(token, householdId, projectId, sourceId, input, version),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useDeleteFinancingSource() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<void, Error, { projectId: string; scenarioId: string; sourceId: string }>({
    mutationFn: ({ projectId, sourceId }) =>
      deleteFinancingSource(token, householdId, projectId, sourceId),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useEquityAvailability(projectId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<EquityAvailability>({
    queryKey: equityAvailabilityQueryKey(householdId, projectId ?? ""),
    queryFn: () => getEquityAvailability(token, householdId, projectId as string),
    enabled: enabled && Boolean(projectId),
  });
}

export function useCashflowAssumptions(projectId: string | null, scenarioId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<CashflowAssumptions | null>({
    queryKey: cashflowAssumptionsQueryKey(householdId, scenarioId ?? ""),
    queryFn: () =>
      getCashflowAssumptions(token, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId && scenarioId),
  });
}

export function useSuggestCashflowAssumptions() {
  const { token, householdId } = useProjectContext();
  return useMutation<
    Awaited<ReturnType<typeof suggestCashflowAssumptions>>,
    Error,
    { projectId: string; scenarioId: string }
  >({
    mutationFn: ({ projectId, scenarioId }) =>
      suggestCashflowAssumptions(token, householdId, projectId, scenarioId),
  });
}

export function useSaveCashflowAssumptions() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<
    Awaited<ReturnType<typeof saveCashflowAssumptions>>,
    Error,
    { projectId: string; scenarioId: string; input: CashflowAssumptionsInput }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      saveCashflowAssumptions(token, householdId, projectId, scenarioId, input),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useRecurringImpacts(projectId: string | null, scenarioId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<RecurringImpact[]>({
    queryKey: recurringImpactsQueryKey(householdId, scenarioId ?? ""),
    queryFn: () =>
      listRecurringImpacts(token, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

export function useCreateRecurringImpact() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<
    RecurringImpact,
    Error,
    { projectId: string; scenarioId: string; input: RecurringImpactCreateInput }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      createRecurringImpact(token, householdId, projectId, scenarioId, input),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useUpdateRecurringImpact() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<
    RecurringImpact,
    Error,
    {
      projectId: string;
      scenarioId: string;
      impactId: string;
      version: number;
      input: RecurringImpactUpdateInput;
    }
  >({
    mutationFn: ({ projectId, impactId, version, input }) =>
      updateRecurringImpact(token, householdId, projectId, impactId, input, version),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useDeleteRecurringImpact() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<void, Error, { projectId: string; scenarioId: string; impactId: string }>({
    mutationFn: ({ projectId, impactId }) =>
      deleteRecurringImpact(token, householdId, projectId, impactId),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useGoalAssumptions(projectId: string | null, scenarioId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<GoalAssumption[]>({
    queryKey: goalAssumptionsQueryKey(householdId, scenarioId ?? ""),
    queryFn: () =>
      listGoalAssumptions(token, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

export function useSaveGoalAssumption() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<
    GoalAssumption,
    Error,
    { projectId: string; scenarioId: string; input: GoalAssumptionInput }
  >({
    mutationFn: ({ projectId, scenarioId, input }) =>
      saveGoalAssumption(token, householdId, projectId, scenarioId, input),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useDeleteGoalAssumption() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateFinancials();
  return useMutation<
    void,
    Error,
    { projectId: string; scenarioId: string; goalAssumptionId: string }
  >({
    mutationFn: ({ projectId, goalAssumptionId }) =>
      deleteGoalAssumption(token, householdId, projectId, goalAssumptionId),
    onSuccess: (_, { scenarioId, projectId }) => invalidate(scenarioId, projectId),
  });
}

export function useFinancialSummary(projectId: string | null, scenarioId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<ScenarioFinancialSummary>({
    queryKey: financialSummaryQueryKey(householdId, scenarioId ?? ""),
    queryFn: () =>
      getFinancialSummary(token, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

export function useProjectTimeline(projectId: string | null, scenarioId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery<ScenarioTimeline>({
    queryKey: timelineQueryKey(householdId, scenarioId ?? ""),
    queryFn: () =>
      getProjectTimeline(token, householdId, projectId as string, scenarioId as string),
    enabled: enabled && Boolean(projectId) && Boolean(scenarioId),
  });
}

export function useCompletionPreview(projectId: string | null) {
  const { token, householdId, enabled } = useProjectContext();
  return useQuery({
    queryKey: ["completion-preview", householdId, projectId],
    queryFn: () => previewProjectCompletion(token, householdId, projectId as string),
    enabled: enabled && Boolean(projectId),
  });
}

export function useApplyCompletion() {
  const { token, householdId } = useProjectContext();
  const invalidate = useInvalidateProjects();
  return useMutation<Awaited<ReturnType<typeof applyProjectCompletion>>, Error, string>({
    mutationFn: (projectId) => applyProjectCompletion(token, householdId, projectId),
    onSuccess: (_, projectId) => invalidate(projectId),
  });
}
