import AsyncStorage from "@react-native-async-storage/async-storage";
import { useCallback, useEffect, useState } from "react";

export const RESERVE_PLANNING_PREFERENCE_KEY = "finanzplaner:mobile:reserve-planning-in-month:v1";

export function useReservePlanningPreference() {
  const [includeInPlan, setIncludeInPlanState] = useState(false);
  const [isHydrated, setHydrated] = useState(false);

  useEffect(() => {
    let active = true;
    AsyncStorage.getItem(RESERVE_PLANNING_PREFERENCE_KEY)
      .then((value) => {
        if (active) setIncludeInPlanState(value === "true");
      })
      .finally(() => {
        if (active) setHydrated(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const setIncludeInPlan = useCallback((next: boolean) => {
    setIncludeInPlanState(next);
    void AsyncStorage.setItem(RESERVE_PLANNING_PREFERENCE_KEY, String(next));
  }, []);

  return { includeInPlan, setIncludeInPlan, isHydrated };
}
