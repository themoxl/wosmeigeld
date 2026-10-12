import { useState } from "react";
import type { MealWeek } from "../api/types";

/**
 * Gemeinsame Wochenauswahl fuer Essensplan- und Einkaufsliste-Screen:
 * faellt ohne manuelle Auswahl auf die neueste geplante Woche zurueck
 * (Backend liefert meal-weeks absteigend nach week_start sortiert).
 */
export function useWeekSelection(weeks: MealWeek[] | undefined) {
  const [selectedWeekId, setSelectedWeekId] = useState<string | null>(null);
  const currentWeekId = selectedWeekId ?? weeks?.[0]?.id;

  return { selectedWeekId: currentWeekId ?? null, setSelectedWeekId, currentWeekId };
}
