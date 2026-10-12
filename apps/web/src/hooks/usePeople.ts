import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  createPerson,
  deactivatePerson,
  getContractIncomeSummary,
  listPeople,
  updatePerson,
} from "../lib/api";
import type {
  ContractIncomeSummary,
  Person,
  PersonCreateInput,
  PersonUpdateInput,
} from "../lib/types";

/**
 * Personen des Haushalts (Plan Stufe 6).
 *
 * Bewusst nicht an `household_members` gekoppelt: Partner und Kinder haben in
 * aller Regel keinen eigenen Zugang, sollen aber als Einnahmequelle zaehlen.
 */
export function usePeople(includeInactive = false) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<Person[]>({
    queryKey: ["people", selectedHouseholdId, includeInactive],
    queryFn: () =>
      listPeople(accessToken as string, selectedHouseholdId as string, includeInactive),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });
}

function usePeopleInvalidation() {
  const queryClient = useQueryClient();
  const { selectedHouseholdId } = useHousehold();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["people", selectedHouseholdId] });
    // Vertraege tragen den Personenbezug, die Auswertung gruppiert danach.
    void queryClient.invalidateQueries({ queryKey: ["contracts", selectedHouseholdId] });
    void queryClient.invalidateQueries({ queryKey: ["income-summary", selectedHouseholdId] });
  };
}

export function useCreatePerson() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const invalidate = usePeopleInvalidation();

  return useMutation<Person, Error, PersonCreateInput>({
    mutationFn: (input) =>
      createPerson(accessToken as string, selectedHouseholdId as string, input),
    onSuccess: invalidate,
  });
}

export function useUpdatePerson() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const invalidate = usePeopleInvalidation();

  return useMutation<Person, Error, { personId: string; input: PersonUpdateInput }>({
    mutationFn: ({ personId, input }) =>
      updatePerson(accessToken as string, selectedHouseholdId as string, personId, input),
    onSuccess: invalidate,
  });
}

export function useDeactivatePerson() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const invalidate = usePeopleInvalidation();

  return useMutation<void, Error, string>({
    mutationFn: (personId) =>
      deactivatePerson(accessToken as string, selectedHouseholdId as string, personId),
    onSuccess: invalidate,
  });
}

export function useIncomeSummary(months = 24) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<ContractIncomeSummary>({
    queryKey: ["income-summary", selectedHouseholdId, months],
    queryFn: () =>
      getContractIncomeSummary(accessToken as string, selectedHouseholdId as string, months),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });
}
