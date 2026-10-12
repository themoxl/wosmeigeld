import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { getInsights } from "../lib/api";
import type { InsightReport } from "../lib/types";

/**
 * Finanz-Empfehlungen.
 *
 * Zwei getrennte Abrufe unter unterschiedlichen Schluesseln, bewusst nicht
 * einer mit Parameter: Der Bericht ohne KI ist eine reine Datenbankabfrage
 * und darf beim Oeffnen der Seite sofort laufen. Der Bericht mit
 * Formulierung belegt ein lokales Modell fuer mehrere Sekunden und startet
 * erst, wenn der Nutzer ihn anfordert (`withAi = true`). Wuerden sich beide
 * einen Cache-Schluessel teilen, wuerde jeder Refetch der Seite den teuren
 * Lauf erneut ausloesen.
 */
export function useInsights(
  months: number,
  withAi: boolean,
  anchor: { year: number; month: number },
) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<InsightReport>({
    queryKey: ["insights", selectedHouseholdId, months, withAi, anchor.year, anchor.month],
    queryFn: () =>
      getInsights(accessToken as string, selectedHouseholdId as string, {
        months,
        withAi,
        anchorYear: anchor.year,
        anchorMonth: anchor.month,
      }),
    enabled: Boolean(accessToken && selectedHouseholdId),
    // Der Bericht rechnet ueber Monate hinweg; ein Nachladen bei jedem
    // Fensterwechsel waere sinnlos teuer.
    staleTime: 5 * 60 * 1000,
  });
}
