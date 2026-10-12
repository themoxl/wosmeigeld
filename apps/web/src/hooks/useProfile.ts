import { useCallback, useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { updateMe } from "../lib/api";
import { formatDate, formatDateTime } from "../lib/dates";
import type { ProfileUpdate } from "../lib/types";

/**
 * Speichert Profil-Einstellungen (aktuell nur date_format). Der `["me", ...]`-
 * Query-Key ist derselbe, den HouseholdProvider fuer getMe() verwendet - eine
 * Invalidierung hier laesst den Haushalts-Kontext das neue Datumsformat
 * automatisch mitziehen, ohne einen zweiten Query zu brauchen.
 */
export function useUpdateProfile() {
  const { accessToken } = useAuth();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (patch: ProfileUpdate) => updateMe(accessToken as string, patch),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["me", accessToken] });
    },
  });
}

/** An die aktuelle Datumsformat-Einstellung gebundene Formatierungsfunktionen. */
export function useFormatDate() {
  const { dateFormat } = useHousehold();

  const boundFormatDate = useCallback(
    (value: string | Date) => formatDate(value, dateFormat),
    [dateFormat],
  );
  const boundFormatDateTime = useCallback(
    (value: string | Date) => formatDateTime(value, dateFormat),
    [dateFormat],
  );

  return useMemo(
    () => ({ dateFormat, formatDate: boundFormatDate, formatDateTime: boundFormatDateTime }),
    [dateFormat, boundFormatDate, boundFormatDateTime],
  );
}
