import AsyncStorage from "@react-native-async-storage/async-storage";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";
import { getMe } from "../api/client";
import type { MeHousehold, MeResponse } from "../api/types";
import { useAuth } from "../auth/AuthProvider";

// Eigener AsyncStorage-Key fuer die zuletzt gewaehlte Haushalts-ID (siehe
// Auftrag Punkt 4). Bewusst nicht user-spezifisch benannt: die Auswahl wird
// bei jedem GET /v1/me gegen die tatsaechlich verfuegbaren Haushalte des
// gerade angemeldeten Nutzers validiert (siehe Effekt unten) und bei
// Ungueltigkeit automatisch auf den ersten verfuegbaren Haushalt korrigiert -
// z. B. wenn sich auf demselben Geraet ein anderer Nutzer anmeldet.
const HOUSEHOLD_STORAGE_KEY = "finanzplaner:mobile:household-id";

export const ME_QUERY_KEY = "me" as const;

// "loading": Auth noch nicht bereit, /v1/me laedt noch, oder die gespeicherte
// Auswahl wurde noch nicht aus AsyncStorage gelesen ("hydriert").
// "error": /v1/me ist fehlgeschlagen (z. B. API nicht erreichbar).
// "no-household": Nutzer angemeldet, aber Mitglied in keinem Haushalt.
// "ready": ein Haushalt ist ausgewaehlt und einsatzbereit.
export type HouseholdState = "loading" | "error" | "no-household" | "ready";

type HouseholdContextValue = {
  state: HouseholdState;
  me: MeResponse | null;
  households: MeHousehold[];
  selectedHouseholdId: string | null;
  selectedHousehold: MeHousehold | null;
  error: Error | null;
  selectHousehold: (householdId: string) => Promise<void>;
  refresh: () => Promise<void>;
};

const HouseholdContext = createContext<HouseholdContextValue | undefined>(undefined);

export function HouseholdProvider({ children }: PropsWithChildren) {
  const { accessToken, status: authStatus } = useAuth();
  const queryClient = useQueryClient();
  // "storedHouseholdId" ist die zuletzt aus AsyncStorage gelesene bzw. per
  // selectHousehold() gesetzte Praeferenz. Sie kann veraltet sein (z. B.
  // Haushalt verlassen, anderer Nutzer auf demselben Geraet) - die
  // tatsaechlich gueltige Auswahl wird unten als "effectiveHouseholdId" rein
  // ueber useMemo aus storedHouseholdId + households abgeleitet, bewusst
  // OHNE setState in einem Effect (vermeidet Kaskaden-Renders).
  const [storedHouseholdId, setStoredHouseholdId] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);

  // Gespeicherte Auswahl einmalig aus AsyncStorage laden.
  useEffect(() => {
    let active = true;
    AsyncStorage.getItem(HOUSEHOLD_STORAGE_KEY)
      .then((value) => {
        if (active) {
          setStoredHouseholdId(value);
        }
      })
      .finally(() => {
        if (active) setHydrated(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const meQuery = useQuery({
    queryKey: [ME_QUERY_KEY, accessToken],
    queryFn: ({ signal }) => getMe(accessToken as string, signal),
    enabled: authStatus === "signedIn" && !!accessToken,
  });

  const households = useMemo(() => meQuery.data?.households ?? [], [meQuery.data]);

  // Die tatsaechlich gueltige Auswahl: die gespeicherte ID, sofern sie noch
  // zu einem Haushalt des Nutzers gehoert, sonst automatisch der erste
  // verfuegbare Haushalt (oder null, wenn keiner vorhanden ist).
  const effectiveHouseholdId = useMemo(() => {
    if (households.length === 0) return null;
    const stillValid = households.some((h) => h.household_id === storedHouseholdId);
    if (stillValid) return storedHouseholdId;
    return households[0]?.household_id ?? null;
  }, [households, storedHouseholdId]);

  // Eine automatische Korrektur (effectiveHouseholdId weicht von
  // storedHouseholdId ab) fuer den naechsten App-Start in AsyncStorage
  // nachziehen. Reiner Storage-Seiteneffekt, kein setState.
  useEffect(() => {
    if (!hydrated || !meQuery.data) return;
    if (effectiveHouseholdId === storedHouseholdId) return;
    if (effectiveHouseholdId) {
      void AsyncStorage.setItem(HOUSEHOLD_STORAGE_KEY, effectiveHouseholdId);
    } else {
      void AsyncStorage.removeItem(HOUSEHOLD_STORAGE_KEY);
    }
  }, [hydrated, meQuery.data, effectiveHouseholdId, storedHouseholdId]);

  async function selectHousehold(householdId: string) {
    setStoredHouseholdId(householdId);
    await AsyncStorage.setItem(HOUSEHOLD_STORAGE_KEY, householdId);
  }

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: [ME_QUERY_KEY, accessToken] });
    await meQuery.refetch();
  }

  const state: HouseholdState = useMemo(() => {
    if (authStatus !== "signedIn") return "loading";
    if (!hydrated || meQuery.isLoading) return "loading";
    if (meQuery.isError) return "error";
    if (!meQuery.data) return "loading";
    if (households.length === 0) return "no-household";
    if (!effectiveHouseholdId) return "loading";
    return "ready";
  }, [
    authStatus,
    hydrated,
    meQuery.isLoading,
    meQuery.isError,
    meQuery.data,
    households,
    effectiveHouseholdId,
  ]);

  const selectedHousehold = households.find((h) => h.household_id === effectiveHouseholdId) ?? null;

  const value: HouseholdContextValue = {
    state,
    me: meQuery.data ?? null,
    households,
    selectedHouseholdId: state === "ready" ? effectiveHouseholdId : null,
    selectedHousehold: state === "ready" ? selectedHousehold : null,
    error: meQuery.error instanceof Error ? meQuery.error : null,
    selectHousehold,
    refresh,
  };

  return <HouseholdContext.Provider value={value}>{children}</HouseholdContext.Provider>;
}

/** Haushaltskontext: Liste der Haushalte des Nutzers (GET /v1/me) und die aktuell ausgewaehlte Haushalts-ID. */
export function useHousehold(): HouseholdContextValue {
  const ctx = useContext(HouseholdContext);
  if (!ctx) {
    throw new Error("useHousehold() muss innerhalb von <HouseholdProvider> verwendet werden.");
  }
  return ctx;
}
