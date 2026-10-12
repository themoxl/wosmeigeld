import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { useInvalidateContracts } from "../hooks/useContracts";
import {
  ackPremiumNotice,
  clearRuleDecision,
  confirmLine,
  confirmMainDue,
  confirmNoticeTerms,
  confirmPremiumNotice,
  createPremiumNotice,
  fetchBackfillPreview,
  getInsuranceSettings,
  getInsuranceSummary,
  getNeedsCheck,
  getNeedsCompleteness,
  getNeedsProfile,
  getPolicyPurpose,
  getPremiumHistory,
  insuranceBackfillPreviewQueryKey,
  insuranceDuplicatesQueryKey,
  insuranceLinesQueryKey,
  insuranceNeedsCheckQueryKey,
  insuranceNeedsCompletenessQueryKey,
  insuranceNeedsProfileQueryKey,
  insurancePoliciesQueryKey,
  insurancePremiumHistoryQueryKey,
  insurancePremiumNoticesQueryKey,
  insurancePurposeQueryKey,
  insuranceSettingsQueryKey,
  insuranceSummaryQueryKey,
  insuranceTasksQueryKey,
  invalidateInsuranceQueries,
  invalidateNeedsQueries,
  isNeedsEndpointMissing,
  listDuplicateFindings,
  listInsuranceLines,
  listInsurancePolicies,
  listInsuranceTasks,
  listPremiumNotices,
  replaceInsurer,
  runBackfill,
  saveRuleDecision,
  updateInsuranceSettings,
  updateNeedsPersonProfile,
  updateNeedsProfile,
  type ConfirmLineInput,
  type InsuranceSettings,
  type NeedsPersonProfileInput,
  type NeedsProfileInput,
  type PremiumNoticeCreateInput,
  type ReplaceInsurerInput,
  type RuleDecisionInput,
} from "../lib/insuranceApi";
import { InsurancePage } from "./InsurancePage";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unbekannter Fehler";
}

/**
 * Datenanbindung der Versicherungsseite.
 *
 * Eine Bestaetigung aendert immer mehr als die Police, auf die geklickt wurde:
 * die Aufgabenliste verliert eine Zeile, die Zusammenfassung eine Zahl, die
 * Uebernahme-Vorschau einen Kandidaten. Deshalb haengt hinter jeder Mutation
 * dieselbe vollstaendige Invalidierung - eine vergessene liesse eine erledigte
 * Aufgabe stehen. Verknuepfte Vertraege werden mitinvalidiert, weil die
 * bestaetigte Frist den Kuendigungstermin des Vertrags aendert (Plan 5.6).
 */
export function InsuranceRoutePage() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const queryClient = useQueryClient();
  const invalidateContracts = useInvalidateContracts();
  const [selectedPolicyId, setSelectedPolicyId] = useState<string | null>(null);

  const token = accessToken as string;
  const householdId = selectedHouseholdId as string;
  const enabled = Boolean(accessToken && selectedHouseholdId);
  // Viewer und Child lesen nur; sie bekommen gar keine Aktionsschaltflaechen
  // statt funktionsloser deaktivierter.
  const role = selectedHousehold?.role ?? null;
  const canEdit = role === "owner" || role === "editor";

  const policiesQuery = useQuery({
    queryKey: insurancePoliciesQueryKey(householdId),
    queryFn: () => listInsurancePolicies(token, householdId),
    enabled,
  });
  const tasksQuery = useQuery({
    queryKey: insuranceTasksQueryKey(householdId),
    queryFn: () => listInsuranceTasks(token, householdId),
    enabled,
  });
  const summaryQuery = useQuery({
    queryKey: insuranceSummaryQueryKey(householdId),
    queryFn: () => getInsuranceSummary(token, householdId),
    enabled,
  });
  // Der Spartenkatalog ist global und aendert sich nicht je Haushalt; er wird
  // nur gebraucht, wenn eine Police eine Sparte sucht.
  const linesQuery = useQuery({
    queryKey: insuranceLinesQueryKey(),
    queryFn: () => listInsuranceLines(token, householdId),
    enabled,
    staleTime: 30 * 60 * 1000,
  });
  // Vorschau-Semantik: nichts zwischenspeichern, nicht wiederholen. Eine alte
  // Vorschau wuerde Vertraege anbieten, die es laengst als Police gibt.
  const backfillPreviewQuery = useQuery({
    queryKey: insuranceBackfillPreviewQueryKey(householdId),
    queryFn: () => fetchBackfillPreview(token, householdId),
    enabled,
    gcTime: 0,
    retry: false,
  });
  const purposeQuery = useQuery({
    queryKey: insurancePurposeQueryKey(householdId, selectedPolicyId),
    queryFn: () => getPolicyPurpose(token, householdId, selectedPolicyId as string),
    enabled: enabled && Boolean(selectedPolicyId),
  });
  // Die Erinnerungen aendern sich fast nie und haengen an keiner Police -
  // deshalb ein eigener Abruf mit langer Haltbarkeit und ohne Wiederholung.
  // Faellt er aus, bleibt der Rest der Seite vollstaendig bedienbar; der
  // Einstellungsblock sagt dann selbst, dass er nichts laden konnte.
  const settingsQuery = useQuery({
    queryKey: insuranceSettingsQueryKey(householdId),
    queryFn: () => getInsuranceSettings(token, householdId),
    enabled,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  /* ---- Beitraege (P3) -----------------------------------------------------
   *
   * Beide Abfragen haengen an der geoeffneten Police und laufen deshalb erst,
   * wenn eine offen ist. Die Anpassungsschreiben werden auch gebraucht, wenn
   * das Register selbst geschlossen ist: aus ihnen entsteht das
   * Sonderkuendigungsband oben im Detail, und ein Band, das erst nach einem
   * Registerwechsel erscheint, erreicht niemanden.
   */
  const premiumNoticesQuery = useQuery({
    queryKey: insurancePremiumNoticesQueryKey(householdId, selectedPolicyId),
    queryFn: () => listPremiumNotices(token, householdId, selectedPolicyId as string),
    enabled: enabled && Boolean(selectedPolicyId),
    retry: false,
  });
  const premiumHistoryQuery = useQuery({
    queryKey: insurancePremiumHistoryQueryKey(householdId, selectedPolicyId),
    queryFn: () => getPremiumHistory(token, householdId, selectedPolicyId as string),
    enabled: enabled && Boolean(selectedPolicyId),
    retry: false,
  });

  /* ---- Bedarfsabgleich (P6) ----------------------------------------------
   *
   * Vier Abfragen, alle mit `retry: false`. Der Grund ist derselbe wie bei den
   * Einstellungen und wiegt hier schwerer: solange die P6-Endpunkte auf einem
   * Server fehlen, antworten sie mit 404, und drei Wiederholungen je Abfrage
   * verzoegern nur die ehrliche Aussage "hier ist noch nichts eingerichtet".
   * Die Befunde selbst werden bei jedem Aufruf frisch gerechnet (Plan 4.9),
   * deshalb keine lange Haltbarkeit - gespeichert ist nur die Entscheidung.
   */
  const needsProfileQuery = useQuery({
    queryKey: insuranceNeedsProfileQueryKey(householdId),
    queryFn: () => getNeedsProfile(token, householdId),
    enabled,
    retry: false,
  });
  const needsCompletenessQuery = useQuery({
    queryKey: insuranceNeedsCompletenessQueryKey(householdId),
    queryFn: () => getNeedsCompleteness(token, householdId),
    enabled,
    retry: false,
  });
  const needsCheckQuery = useQuery({
    queryKey: insuranceNeedsCheckQueryKey(householdId),
    queryFn: () => getNeedsCheck(token, householdId),
    enabled,
    retry: false,
  });
  const duplicatesQuery = useQuery({
    queryKey: insuranceDuplicatesQueryKey(householdId),
    queryFn: () => listDuplicateFindings(token, householdId),
    enabled,
    retry: false,
  });

  /** Alle Abfragen dieses Moduls. Die Liste steht in `insuranceApi`, weil
   *  inzwischen zwei Stellen schreiben - Seite und Beitragsregister - und zwei
   *  Listen genau so lange gleich laufen, bis eine Phase einen Schluessel
   *  dazulegt. Die Vertraege haengen an ihrem eigenen Invalidierer. */
  const invalidateInsurance = () =>
    invalidateInsuranceQueries(queryClient, householdId).then(() => {
      invalidateContracts();
    });

  const backfill = useMutation({
    mutationFn: () => runBackfill(token, householdId),
    onSuccess: () => invalidateInsurance(),
  });

  const mainDue = useMutation({
    mutationFn: (vars: { policyId: string; mainDueOn?: string }) =>
      confirmMainDue(
        token,
        householdId,
        vars.policyId,
        vars.mainDueOn ? { main_due_on: vars.mainDueOn } : undefined,
      ),
    onSuccess: () => invalidateInsurance(),
  });

  const noticeTerms = useMutation({
    mutationFn: (vars: { policyId: string; months: number }) =>
      confirmNoticeTerms(token, householdId, vars.policyId, {
        notice_period_months: vars.months,
      }),
    onSuccess: () => invalidateInsurance(),
  });

  const line = useMutation({
    mutationFn: (vars: { policyId: string; input: ConfirmLineInput }) =>
      confirmLine(token, householdId, vars.policyId, vars.input),
    // Der Zwecktext haengt an der Sparte und ist nach der Zuordnung ein
    // anderer. Er wird seit P4 zentral mitinvalidiert (`insuranceApi`), weil
    // ihn nicht mehr nur die Sparte aendert: erfasste Deckungsbausteine
    // kippen `coverage_source` auf `policy` und damit die Aussage der Karte.
    // Zwei Stellen mit derselben Aufgabe waeren genau die, von der eine
    // irgendwann vergessen wird.
    onSuccess: () => invalidateInsurance(),
  });

  // Ein erfasstes Schreiben aendert die Aufgabenliste (neue Zeile
  // "Sonderkuendigungsrecht laeuft ab"), die Uebernahme aendert zusaetzlich
  // den Vertragsbetrag und damit die Fixkosten - beide laufen deshalb durch
  // dieselbe vollstaendige Invalidierung wie die uebrigen Aktionen.
  const premiumNotice = useMutation({
    mutationFn: (vars: { policyId: string; input: PremiumNoticeCreateInput }) =>
      createPremiumNotice(token, householdId, vars.policyId, vars.input),
    onSuccess: () => invalidateInsurance(),
  });

  const premiumConfirm = useMutation({
    mutationFn: (noticeId: string) => confirmPremiumNotice(token, householdId, noticeId),
    onSuccess: () => invalidateInsurance(),
  });

  const premiumAck = useMutation({
    mutationFn: (noticeId: string) => ackPremiumNotice(token, householdId, noticeId),
    onSuccess: () => invalidateInsurance(),
  });

  // Der Wechsel beruehrt ZWEI Policen, und die Nachfolgerin ist nicht die
  // geoeffnete. Deshalb wird danach der ganze Bestand neu geladen und die
  // Auswahl auf die neue Police umgehaengt - sonst staende der Dialog auf
  // einer soeben beendeten Police.
  const insurerSwitch = useMutation({
    mutationFn: (vars: { policyId: string; input: ReplaceInsurerInput }) =>
      replaceInsurer(token, householdId, vars.policyId, vars.input),
    onSuccess: async (result) => {
      await invalidateInsurance();
      setSelectedPolicyId(result.successor.id);
    },
  });

  // Die Einstellungen haengen an keiner Police: sie brauchen weder die
  // Aufgabenliste noch die Vertraege, nur sich selbst.
  const settings = useMutation({
    mutationFn: (input: InsuranceSettings) => updateInsuranceSettings(token, householdId, input),
    onSuccess: (saved) => {
      queryClient.setQueryData(insuranceSettingsQueryKey(householdId), saved);
    },
  });

  /* ---- Schreibende Wege des Bedarfsabgleichs ------------------------------
   *
   * Sie invalidieren NUR die Bedarfsabfragen und nicht den ganzen Bestand: eine
   * geaenderte Profilangabe und eine weggelegte Regel aendern die Befunde, aber
   * weder eine Police noch eine Frist noch einen Vertrag. Umgekehrt gilt das
   * nicht - `invalidateInsuranceQueries` zieht die Befunde mit, weil eine neue
   * Police sie tatsaechlich kippt (Plan, `insuranceApi`).
   */
  const invalidateNeeds = () => invalidateNeedsQueries(queryClient, householdId);

  const needsProfile = useMutation({
    mutationFn: (input: NeedsProfileInput) => updateNeedsProfile(token, householdId, input),
    onSuccess: () => invalidateNeeds(),
  });

  const needsPerson = useMutation({
    mutationFn: (vars: { personId: string; input: NeedsPersonProfileInput }) =>
      updateNeedsPersonProfile(token, householdId, vars.personId, vars.input),
    onSuccess: () => invalidateNeeds(),
  });

  const ruleDecision = useMutation({
    mutationFn: (vars: { ruleKey: string; input: RuleDecisionInput }) =>
      saveRuleDecision(token, householdId, vars.ruleKey, vars.input),
    onSuccess: () => invalidateNeeds(),
  });

  const clearDecision = useMutation({
    mutationFn: (vars: { ruleKey: string; policyId: string | null }) =>
      clearRuleDecision(token, householdId, vars.ruleKey, vars.policyId),
    onSuccess: () => invalidateNeeds(),
  });

  // WELCHE Regel gerade schreibt, steht in den Aufrufparametern der laufenden
  // Mutation - kein zweiter Zustand daneben, der mit ihr aus dem Tritt geraten
  // koennte. Ein globales "busy" waere ohnehin falsch: es sperrte alle Befunde
  // zugleich, obwohl nur einer betroffen ist.
  const decisionBusyRuleKey = ruleDecision.isPending
    ? (ruleDecision.variables?.ruleKey ?? null)
    : clearDecision.isPending
      ? (clearDecision.variables?.ruleKey ?? null)
      : null;

  const reload = () => {
    void policiesQuery.refetch();
    void tasksQuery.refetch();
    void summaryQuery.refetch();
    void backfillPreviewQuery.refetch();
  };

  const loading = policiesQuery.isLoading || tasksQuery.isLoading || summaryQuery.isLoading;
  const firstError = policiesQuery.error ?? tasksQuery.error ?? summaryQuery.error;
  const actionError = mainDue.error ?? noticeTerms.error ?? line.error;

  const policies = policiesQuery.data ?? [];
  const premiumError = premiumNoticesQuery.error ?? premiumHistoryQuery.error;
  const premiumActionError =
    premiumNotice.error ?? premiumConfirm.error ?? premiumAck.error ?? insurerSwitch.error;
  // Die Datenanbindung des Beitragsregisters, als ein Buendel. Es entsteht nur
  // mit einer geoeffneten Police: ohne sie gibt es weder eine Zeitreihe noch
  // ein Fenster, und ein leeres Register waere eine Aussage, die niemand
  // geprueft hat.
  const premiumRegister = selectedPolicyId
    ? {
        notices: premiumNoticesQuery.data ?? [],
        steps: premiumHistoryQuery.data?.steps ?? [],
        totalIncreaseBp: premiumHistoryQuery.data?.total_increase_bp ?? null,
        loading: premiumNoticesQuery.isLoading || premiumHistoryQuery.isLoading,
        error: premiumError ? errorMessage(premiumError) : null,
        busy:
          premiumNotice.isPending ||
          premiumConfirm.isPending ||
          premiumAck.isPending ||
          insurerSwitch.isPending,
        actionError: premiumActionError ? errorMessage(premiumActionError) : null,
        canEdit,
        successorCandidates: policies,
        onCreateNotice: (input: PremiumNoticeCreateInput) =>
          premiumNotice.mutate({ policyId: selectedPolicyId, input }),
        onConfirmNotice: (noticeId: string) => premiumConfirm.mutate(noticeId),
        onAckNotice: (noticeId: string) => premiumAck.mutate(noticeId),
        onReplaceInsurer: (input: ReplaceInsurerInput) =>
          insurerSwitch.mutate({ policyId: selectedPolicyId, input }),
      }
    : undefined;

  /* ---- Buendel des Bedarfsabgleichs ---------------------------------------
   *
   * Der 404-Fall wird ausdruecklich vom Fehlerfall getrennt. Fehlt der
   * Endpunkt, ist auf diesem Server P6 nicht eingerichtet - das ist ein
   * Ausbaustand und bekommt einen Satz, keine Fehlerfarbe. Alles andere ist ein
   * echter Fehler und wird als solcher gezeigt (`isNeedsEndpointMissing`).
   */
  const needsError =
    needsProfileQuery.error ?? needsCompletenessQuery.error ?? needsCheckQuery.error;
  const needsUnavailable = Boolean(needsError && isNeedsEndpointMissing(needsError));
  const duplicatesError = duplicatesQuery.error;
  const duplicatesUnavailable = Boolean(duplicatesError && isNeedsEndpointMissing(duplicatesError));
  const decisionError = ruleDecision.error ?? clearDecision.error;

  const needs = {
    profile: needsProfileQuery.data ?? null,
    completeness: needsCompletenessQuery.data ?? null,
    check: needsCheckQuery.data ?? null,
    duplicates: duplicatesQuery.data ?? [],
    loading: needsProfileQuery.isLoading || needsCheckQuery.isLoading,
    error: needsError && !needsUnavailable ? errorMessage(needsError) : null,
    unavailable: needsUnavailable,
    // Getrennte Kanäle: ein gemeinsames `saving` liess einen einzigen
    // Speichervorgang an den Haushaltsangaben UND an jeder Person melden, und
    // der Fehler von Person B stand auch unter Person A.
    saving: needsProfile.isPending,
    saveError: needsProfile.error ? errorMessage(needsProfile.error) : null,
    savingPersonId: needsPerson.isPending ? (needsPerson.variables?.personId ?? null) : null,
    personSaveError: needsPerson.error
      ? {
          personId: needsPerson.variables?.personId ?? null,
          message: errorMessage(needsPerson.error),
        }
      : null,
    decisionBusyRuleKey,
    decisionError: decisionError ? errorMessage(decisionError) : null,
    duplicatesLoading: duplicatesQuery.isLoading,
    duplicatesError:
      duplicatesError && !duplicatesUnavailable ? errorMessage(duplicatesError) : null,
    duplicatesUnavailable,
    onSaveProfile: (input: NeedsProfileInput) => needsProfile.mutate(input),
    onSavePerson: (personId: string, input: NeedsPersonProfileInput) =>
      needsPerson.mutate({ personId, input }),
    onDecide: (ruleKey: string, input: RuleDecisionInput) =>
      ruleDecision.mutate({ ruleKey, input }),
    onClearDecision: (ruleKey: string, policyId: string | null) =>
      clearDecision.mutate({ ruleKey, policyId }),
  };

  return (
    <InsurancePage
      policies={policies}
      tasks={tasksQuery.data ?? []}
      summary={summaryQuery.data ?? null}
      backfillPreview={backfillPreviewQuery.data ?? null}
      lines={linesQuery.data ?? []}
      loading={loading}
      error={firstError ? errorMessage(firstError) : null}
      onReload={reload}
      canEdit={canEdit}
      selectedPolicyId={selectedPolicyId}
      onSelectPolicy={setSelectedPolicyId}
      purpose={purposeQuery.data ?? null}
      purposeLoading={Boolean(selectedPolicyId) && purposeQuery.isLoading}
      purposeError={purposeQuery.error ? errorMessage(purposeQuery.error) : null}
      actionBusy={mainDue.isPending || noticeTerms.isPending || line.isPending}
      actionError={actionError ? errorMessage(actionError) : null}
      backfillBusy={backfill.isPending}
      backfillError={backfill.error ? errorMessage(backfill.error) : null}
      onRunBackfill={() => backfill.mutate()}
      onConfirmMainDue={(policyId, mainDueOn) => mainDue.mutate({ policyId, mainDueOn })}
      onConfirmNoticeTerms={(policyId, months) => noticeTerms.mutate({ policyId, months })}
      onConfirmLine={(policyId, input) => line.mutate({ policyId, input })}
      settings={settingsQuery.data ?? null}
      settingsLoading={settingsQuery.isLoading}
      settingsError={settingsQuery.error ? errorMessage(settingsQuery.error) : null}
      settingsSaving={settings.isPending}
      settingsSaveError={settings.error ? errorMessage(settings.error) : null}
      onSaveSettings={(input) => settings.mutate(input)}
      premiumRegister={premiumRegister}
      needs={needs}
    />
  );
}
