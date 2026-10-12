import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createInsurancePolicy,
  getInsurancePolicy,
  getInsuranceSummary,
  listInsuranceLines,
  listInsurancePolicies,
  RISK_FIELD_LABEL,
  RISK_FIELD_ORDER,
  toPolicyCreateBody,
  type SimplePolicyDraft,
} from "../api/insurance";
import type {
  InsuranceLine,
  InsurancePolicy,
  InsurancePolicyDetail,
  InsuranceSummary,
  RiskField,
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/**
 * Versicherungen auf Mobile (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 7, P2b).
 *
 * Vier Lese-Hooks und ein Schreib-Hook - die Begruendung fuer diesen Zuschnitt
 * steht im Kopf von ../api/insurance.ts. Was hier zusaetzlich lebt, ist die
 * Gliederung der Liste nach Risikofeld: sie ersetzt auf Mobile die Filterleiste
 * des Web und ist deshalb Datenformung, keine Darstellung (gleiche Bauart wie
 * groupContractsByCategory in ./useContracts.ts).
 *
 * Zur Vorratshaltung: keiner dieser Abfrageschluessel steht in
 * PERSISTED_QUERY_KEYS (../api/queryClient.ts). Das ist Absicht und nicht
 * vergessen. Plan 7.1 verbietet, dass eine Versicherungsart auf einem
 * gesperrten Bildschirm erscheint - "Berufsunfaehigkeit" oder "Pflegezusatz"
 * ist eine Gesundheitsangabe. Derselbe Grund spricht gegen eine Kopie des
 * Bestands im Klartext auf der Platte; der persistente Cache ist unverschluesselt
 * (siehe die Begruendung dort). Sichtbare Folge: ohne Netz zeigt die Liste
 * nichts. Der Preis ist bewusst.
 */

interface InsuranceContext {
  accessToken: string;
  householdId: string;
  enabled: boolean;
  canEdit: boolean;
}

export function useInsuranceContext(): InsuranceContext {
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const role = selectedHousehold?.role ?? null;
  return {
    accessToken: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
    canEdit: role === "owner" || role === "editor",
  };
}

const policiesKey = (householdId: string) => ["insurance-policies", householdId] as const;
const policyKey = (householdId: string, policyId: string) =>
  ["insurance-policy", householdId, policyId] as const;
const summaryKey = (householdId: string) => ["insurance-summary", householdId] as const;
// Ohne Haushalt: der Spartenkatalog ist global (Migrationsgut) und aendert sich
// durch nichts, was die App tut. Ein Haushaltswechsel darf ihn deshalb weder
// verwerfen noch neu laden - gleiche Entscheidung wie im Web.
const linesKey = () => ["insurance-lines"] as const;

export function useInvalidateInsurance() {
  const queryClient = useQueryClient();
  const { householdId } = useInsuranceContext();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: policiesKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: summaryKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: ["insurance-policy", householdId] });
    // Vertraege und Fixkosten bleiben absichtlich unberuehrt: eine neue Police
    // legt keinen Vertrag an und aendert keinen Betrag. Sie verknuepft
    // hoechstens einen bestehenden Vertrag - dessen Zahlen bleiben dieselben,
    // nur die Versicherungs-Kennzahlen zaehlen ihn jetzt mit.
  }, [queryClient, householdId]);
}

// -----------------------------------------------------------------------
// Gliederung nach Risikofeld
// -----------------------------------------------------------------------

export interface PolicyRiskSection {
  key: string;
  title: string;
  data: InsurancePolicy[];
}

/**
 * Gliedert den Bestand fuer die SectionList in app/mehr/versicherungen.tsx.
 *
 * Die Reihenfolge der Gruppen kommt aus RISK_FIELD_ORDER, die Reihenfolge
 * INNERHALB einer Gruppe aus der Serverantwort (`sort=line`, also
 * Katalogreihenfolge der Sparten, dann Versicherername). Hier wird bewusst
 * nicht nachsortiert: eine zweite Sortierregel im Client waere die zweite
 * Stelle, an der sich die Reihenfolge derselben Liste entscheidet.
 *
 * Ein Risikofeld, das der Server kennt und diese App noch nicht, faellt nicht
 * heraus - es bekommt seinen Rohschluessel als Ueberschrift und steht am Ende.
 * Eine Police stillschweigend zu verschlucken waere der schlechtere Fehler:
 * genau die eine, die man am Unfallort sucht, koennte fehlen.
 */
export function groupPoliciesByRiskField(policies: InsurancePolicy[]): PolicyRiskSection[] {
  interface Bucket {
    title: string;
    order: number;
    data: InsurancePolicy[];
  }
  const buckets = new Map<string, Bucket>();

  for (const policy of policies) {
    const field = policy.risk_field;
    let bucket = buckets.get(field);
    if (!bucket) {
      const order = RISK_FIELD_ORDER.indexOf(field);
      bucket = {
        title: RISK_FIELD_LABEL[field] ?? field,
        order: order >= 0 ? order : Number.POSITIVE_INFINITY,
        data: [],
      };
      buckets.set(field, bucket);
    }
    bucket.data.push(policy);
  }

  return Array.from(buckets.entries())
    .sort(([keyA, a], [keyB, b]) => a.order - b.order || keyA.localeCompare(keyB, "de"))
    .map(([key, bucket]) => ({ key, title: bucket.title, data: bucket.data }));
}

/** Alle Risikofelder in Anzeigereihenfolge - fuer die Auswahl im Anlegeformular. */
export function groupLinesByRiskField(
  lines: InsuranceLine[],
): { key: RiskField; title: string; data: InsuranceLine[] }[] {
  return RISK_FIELD_ORDER.map((field) => ({
    key: field,
    title: RISK_FIELD_LABEL[field],
    // `sort_order` kommt aus dem Katalog und ist bereits die redaktionell
    // gewollte Reihenfolge; der Endpunkt liefert sie so aus.
    data: lines.filter((line) => line.risk_field === field),
  })).filter((section) => section.data.length > 0);
}

// -----------------------------------------------------------------------
// Abfragen
// -----------------------------------------------------------------------

/** Der laufende Bestand (ohne beendete Policen), gruppierbar nach Risikofeld. */
export function useInsurancePolicies() {
  const { accessToken, householdId, enabled } = useInsuranceContext();
  return useQuery<InsurancePolicy[]>({
    queryKey: policiesKey(householdId),
    queryFn: ({ signal }) => listInsurancePolicies(accessToken, householdId, signal),
    enabled,
  });
}

/** Police, Spartenblock und fehlende Pflichtfelder in einer Antwort. */
export function useInsurancePolicy(policyId: string | null) {
  const { accessToken, householdId, enabled } = useInsuranceContext();
  return useQuery<InsurancePolicyDetail>({
    queryKey: policyKey(householdId, policyId ?? ""),
    queryFn: ({ signal }) =>
      getInsurancePolicy(accessToken, householdId, policyId as string, signal),
    enabled: enabled && Boolean(policyId),
  });
}

/**
 * Spartenkatalog fuer das Anlegeformular.
 *
 * `enabled` ist standardmaessig aus: der Katalog hat rund ein Dutzend Eintraege
 * mit langen Zwecktexten und wird nur vom Formular gebraucht. Die Liste laedt
 * ihn nicht mit.
 */
export function useInsuranceLines(options: { enabled?: boolean } = {}) {
  const { accessToken, householdId, enabled } = useInsuranceContext();
  return useQuery<InsuranceLine[]>({
    queryKey: linesKey(),
    queryFn: ({ signal }) => listInsuranceLines(accessToken, householdId, signal),
    enabled: enabled && (options.enabled ?? false),
    // Ein global gepflegter Katalog aendert sich nicht waehrend einer Sitzung.
    staleTime: 60 * 60 * 1000,
  });
}

/** Kennzahlen der Kopfzeile. */
export function useInsuranceSummary() {
  const { accessToken, householdId, enabled } = useInsuranceContext();
  return useQuery<InsuranceSummary>({
    queryKey: summaryKey(householdId),
    queryFn: ({ signal }) => getInsuranceSummary(accessToken, householdId, signal),
    enabled,
  });
}

// -----------------------------------------------------------------------
// Die eine Schreibaktion
// -----------------------------------------------------------------------

/**
 * Einfache Police mit vier Feldern anlegen.
 *
 * Nimmt die Formularform entgegen (`SimplePolicyDraft`) und nicht den
 * Vertragskoerper: der Geldfall ist dort eine unterscheidbare Union, "Vertrag
 * UND Fremdbetrag zugleich" laesst sich also gar nicht erst absenden.
 *
 * Bewusst ohne optimistisches Einfuegen in die Liste. Die Antwort traegt Frist,
 * Sicherheit und `next_task` - alles serverseitig gerechnet. Ein vorab
 * eingefuegter Platzhalter muesste diese Werte erfinden und zeigte fuer einen
 * Moment eine Police ohne Frist, die es so nie gab.
 */
export function useCreateInsurancePolicy() {
  const { accessToken, householdId } = useInsuranceContext();
  const invalidate = useInvalidateInsurance();
  return useMutation<InsurancePolicy, Error, SimplePolicyDraft>({
    mutationFn: (draft) =>
      createInsurancePolicy(accessToken, householdId, toPolicyCreateBody(draft)),
    onSuccess: invalidate,
  });
}
