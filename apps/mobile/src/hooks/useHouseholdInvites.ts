import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createHouseholdInvite,
  listHouseholdInvites,
  listHouseholdMembers,
  previewHouseholdInvite,
  redeemHouseholdInvite,
  revokeHouseholdInvite,
} from "../api/client";
import type { HouseholdRole } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

export function useHouseholdMembers() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["householdMembers", selectedHouseholdId],
    queryFn: () => listHouseholdMembers(accessToken as string, selectedHouseholdId as string),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

export function useHouseholdInvites(enabled: boolean) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["householdInvites", selectedHouseholdId],
    queryFn: () => listHouseholdInvites(accessToken as string, selectedHouseholdId as string),
    enabled: !!accessToken && !!selectedHouseholdId && enabled,
  });
}

export function useCreateHouseholdInvite() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (role: HouseholdRole) =>
      createHouseholdInvite(accessToken as string, selectedHouseholdId as string, role),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["householdInvites", selectedHouseholdId],
      });
    },
  });
}

export function useRevokeHouseholdInvite() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (inviteId: string) =>
      revokeHouseholdInvite(accessToken as string, selectedHouseholdId as string, inviteId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["householdInvites", selectedHouseholdId],
      });
    },
  });
}

export function usePreviewHouseholdInvite() {
  const { accessToken } = useAuth();

  return useMutation({
    mutationFn: (code: string) => previewHouseholdInvite(accessToken as string, code),
  });
}

export function useRedeemHouseholdInvite() {
  const { accessToken } = useAuth();

  return useMutation({
    mutationFn: (code: string) => redeemHouseholdInvite(accessToken as string, code),
  });
}
