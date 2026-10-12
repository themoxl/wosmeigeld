import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { getMe } from "../lib/api";
import type { DateFormatPreference } from "../lib/dates";
import type { Membership } from "../lib/types";

// Nach dem Login wird GET /v1/me abgerufen. Kein Haushalt vorhanden ->
// Onboarding (siehe RequireHousehold.tsx). Ein oder mehrere Haushalte
// vorhanden -> automatisch der erste, bei mehreren per Picker in der
// Sidebar umschaltbar. Die Auswahl wird in localStorage gemerkt (analog zum
// Theme-Muster in App.tsx).
//
// Die "gueltige/erste Haushalt"-Ableitung passiert bewusst per useMemo
// waehrend des Renderns statt per useEffect + setState: households aendert
// sich, wenn sich der Access Token aendert (Login/Logout) oder die
// /v1/me-Query neu aufgeloest wird - der abgeleitete Wert faellt dann
// automatisch auf den ersten Haushalt bzw. auf null zurueck, ohne eine
// zusaetzliche Renderkaskade ueber einen Effekt zu benoetigen.

const HOUSEHOLD_STORAGE_KEY = "finanzplaner:web:household-id";

interface HouseholdContextValue {
  households: Membership[];
  selectedHouseholdId: string | null;
  selectedHousehold: Membership | null;
  selectHousehold: (id: string) => void;
  dateFormat: DateFormatPreference;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;
}

const HouseholdContext = createContext<HouseholdContextValue | undefined>(undefined);

function readStoredHouseholdId(): string | null {
  try {
    return window.localStorage.getItem(HOUSEHOLD_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function HouseholdProvider({ children }: { children: ReactNode }) {
  const { accessToken } = useAuth();
  const [storedHouseholdId, setStoredHouseholdId] = useState<string | null>(readStoredHouseholdId);

  const {
    data,
    isLoading: isMeLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["me", accessToken],
    queryFn: () => getMe(accessToken as string),
    enabled: Boolean(accessToken),
  });

  const households = useMemo(() => data?.households ?? [], [data]);
  const dateFormat: DateFormatPreference = data?.date_format ?? "de";

  // Die gespeicherte Auswahl uebernehmen, sofern sie zu einem der geladenen
  // Haushalte passt - sonst automatisch den ersten waehlen (oder null, wenn
  // (noch) keine Haushalte vorhanden sind, z. B. direkt nach dem Logout).
  const selectedHouseholdId = useMemo(() => {
    if (storedHouseholdId && households.some((h) => h.household_id === storedHouseholdId)) {
      return storedHouseholdId;
    }
    return households[0]?.household_id ?? null;
  }, [households, storedHouseholdId]);

  const selectHousehold = useCallback((id: string) => {
    setStoredHouseholdId(id);
    try {
      window.localStorage.setItem(HOUSEHOLD_STORAGE_KEY, id);
    } catch {
      // localStorage evtl. nicht verfuegbar - Auswahl bleibt fuer die
      // Sitzung erhalten.
    }
  }, []);

  const selectedHousehold = useMemo(
    () => households.find((h) => h.household_id === selectedHouseholdId) ?? null,
    [households, selectedHouseholdId],
  );

  const value = useMemo<HouseholdContextValue>(
    () => ({
      households,
      selectedHouseholdId,
      selectedHousehold,
      selectHousehold,
      dateFormat,
      isLoading: Boolean(accessToken) && isMeLoading,
      isError,
      refetch: () => {
        void refetch();
      },
    }),
    [
      households,
      selectedHouseholdId,
      selectedHousehold,
      selectHousehold,
      dateFormat,
      accessToken,
      isMeLoading,
      isError,
      refetch,
    ],
  );

  return <HouseholdContext.Provider value={value}>{children}</HouseholdContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useHousehold(): HouseholdContextValue {
  const ctx = useContext(HouseholdContext);
  if (!ctx) {
    throw new Error("useHousehold() muss innerhalb von <HouseholdProvider> verwendet werden.");
  }
  return ctx;
}
