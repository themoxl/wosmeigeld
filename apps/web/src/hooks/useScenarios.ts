import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  compareScenarios,
  createScenario,
  deleteScenario,
  duplicateScenario,
  getScenario,
  listScenarios,
  putScenarioChanges,
  updateScenario,
} from "../lib/api";
import type {
  HouseholdScenario,
  HouseholdScenarioChangeInput,
  HouseholdScenarioCreateInput,
  HouseholdScenarioDetail,
  HouseholdScenarioUpdateInput,
  ScenarioComparisonResponse,
} from "../lib/types";

/**
 * Gespeicherte Haushaltsszenarien (Plan Stufe 7).
 *
 * `household_scenarios` ist ab jetzt das kanonische Szenariosystem; die
 * Fixkostenszenarien sind darin aufgegangen. Ein gespeichertes Szenario ist
 * nichts als eine gespeicherte Aenderungsliste - deshalb liefert
 * `GET /{id}/forecast` dieselbe Form wie `POST /forecast/simulate`.
 */
export const SCENARIO_QUERY_KEY = "scenarios";

function useInvalidate() {
  const queryClient = useQueryClient();
  const { selectedHouseholdId } = useHousehold();
  return () =>
    void queryClient.invalidateQueries({ queryKey: [SCENARIO_QUERY_KEY, selectedHouseholdId] });
}

export function useScenarios() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<HouseholdScenario[]>({
    queryKey: [SCENARIO_QUERY_KEY, selectedHouseholdId],
    queryFn: () => listScenarios(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });
}

export function useScenario(scenarioId: string | null) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<HouseholdScenarioDetail>({
    queryKey: [SCENARIO_QUERY_KEY, selectedHouseholdId, scenarioId],
    queryFn: () =>
      getScenario(accessToken as string, selectedHouseholdId as string, scenarioId as string),
    enabled: Boolean(accessToken && selectedHouseholdId && scenarioId),
  });
}

export function useCreateScenario() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const invalidate = useInvalidate();

  return useMutation<HouseholdScenarioDetail, Error, HouseholdScenarioCreateInput>({
    mutationFn: (input) =>
      createScenario(accessToken as string, selectedHouseholdId as string, input),
    onSuccess: invalidate,
  });
}

export function useUpdateScenario() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const invalidate = useInvalidate();

  return useMutation<
    HouseholdScenarioDetail,
    Error,
    { scenarioId: string; version: number; input: HouseholdScenarioUpdateInput }
  >({
    mutationFn: ({ scenarioId, version, input }) =>
      updateScenario(
        accessToken as string,
        selectedHouseholdId as string,
        scenarioId,
        version,
        input,
      ),
    onSuccess: invalidate,
  });
}

export function useDeleteScenario() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const invalidate = useInvalidate();

  return useMutation<void, Error, string>({
    mutationFn: (scenarioId) =>
      deleteScenario(accessToken as string, selectedHouseholdId as string, scenarioId),
    onSuccess: invalidate,
  });
}

export function useDuplicateScenario() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const invalidate = useInvalidate();

  return useMutation<HouseholdScenarioDetail, Error, string>({
    mutationFn: (scenarioId) =>
      duplicateScenario(accessToken as string, selectedHouseholdId as string, scenarioId),
    onSuccess: invalidate,
  });
}

export function useSaveScenarioChanges() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const invalidate = useInvalidate();

  return useMutation<
    HouseholdScenarioDetail,
    Error,
    { scenarioId: string; changes: HouseholdScenarioChangeInput[] }
  >({
    mutationFn: ({ scenarioId, changes }) =>
      putScenarioChanges(accessToken as string, selectedHouseholdId as string, scenarioId, changes),
    onSuccess: invalidate,
  });
}

/**
 * Vergleich mehrerer Szenarien. Hoechstens vier - mehr Linien sind in einem
 * Diagramm nicht mehr lesbar, und der Server lehnt es ohnehin ab.
 */
export const MAX_COMPARED_SCENARIOS = 4;

export function useScenarioComparison(scenarioIds: string[], includeSavings: boolean) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const ids = scenarioIds.slice(0, MAX_COMPARED_SCENARIOS);

  return useQuery<ScenarioComparisonResponse>({
    queryKey: [SCENARIO_QUERY_KEY, selectedHouseholdId, "compare", ids.join(","), includeSavings],
    queryFn: () =>
      compareScenarios(
        accessToken as string,
        selectedHouseholdId as string,
        ids,
        includeSavings,
      ),
    enabled: Boolean(accessToken && selectedHouseholdId && ids.length > 0),
  });
}
