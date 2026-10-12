import { useQuery } from "@tanstack/react-query";
import { listPeople } from "../api/client";
import type { Person } from "../api/types";
import { useContractContext } from "./useContracts";

/**
 * Personen des Haushalts (Liquiditaetsplan Stufe 6) - ausschliesslich lesend.
 *
 * Die App bekommt bewusst keine Personenverwaltung: Anlegen, Umbenennen und
 * Loeschen gehoeren ins Web (Plan Abschnitt 9, "Mobile ist kein verkleinertes
 * Web"). Hier werden die Namen nur gebraucht, um die Einnahmen nach Person zu
 * gliedern.
 *
 * Kontext und Rechte kommen aus `useContractContext`: Personen sind in dieser
 * App kein eigenstaendiger Bereich, sondern eine Achse der Vertragsansicht -
 * ein zweiter Kontext-Hook mit denselben drei Werten waere Verdopplung.
 */
export function usePeople() {
  const { accessToken, householdId, enabled } = useContractContext();
  return useQuery<Person[]>({
    queryKey: ["people", householdId],
    queryFn: ({ signal }) => listPeople(accessToken, householdId, signal),
    enabled,
  });
}
