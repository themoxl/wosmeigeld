import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  acceptDuplicatesBulk,
  acceptReviewItem,
  correctReviewItem,
  createRule,
  getReviewInbox,
  listRules,
} from "../api/client";
import type { ClassificationRuleCreate, ReviewAcceptInput, ReviewCorrectInput } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/** Review Inbox des ausgewaehlten Haushalts (GET /v1/review). */
export function useReviewInbox() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["review", selectedHouseholdId],
    queryFn: ({ signal }) =>
      getReviewInbox(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

/** Akzeptieren eines Review-Eintrags (POST /v1/review/accept). */
export function useAcceptReview() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: ReviewAcceptInput) =>
      acceptReviewItem(accessToken as string, selectedHouseholdId as string, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["review", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["transactions", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["rules", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["budget", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["overviewReport", selectedHouseholdId] });
    },
  });
}

/** Korrigieren eines Review-Eintrags (POST /v1/review/correct). */
/**
 * Sammelbestaetigung mehrerer Dubletten.
 *
 * Eigener Hook statt einer Schleife ueber useAcceptReview: der Server
 * entscheidet in EINEM Vorgang ueber alles, und die Liste wird danach genau
 * einmal neu geladen statt fuenfzigmal.
 */
export function useAcceptDuplicatesBulk() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (importRowIds: string[]) =>
      acceptDuplicatesBulk(accessToken as string, selectedHouseholdId as string, importRowIds),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["review", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["transactions", selectedHouseholdId] });
    },
  });
}

export function useCorrectReview() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: ReviewCorrectInput) =>
      correctReviewItem(accessToken as string, selectedHouseholdId as string, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["review", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["transactions", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["rules", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["budget", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["overviewReport", selectedHouseholdId] });
    },
  });
}

/** Klassifikationsregeln des Haushalts (GET /v1/rules). */
export function useRules() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["rules", selectedHouseholdId],
    queryFn: ({ signal }) =>
      listRules(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

/** Erstellen einer neuen Regel (POST /v1/rules). */
export function useCreateRule() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: ClassificationRuleCreate) =>
      createRule(accessToken as string, selectedHouseholdId as string, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["rules", selectedHouseholdId] });
    },
  });
}
