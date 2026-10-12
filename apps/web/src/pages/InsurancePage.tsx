import { useState } from "react";
import { BackfillPanel } from "../components/insurance/BackfillPanel";
import { DeadlineDetail } from "../components/insurance/DeadlineDetail";
import { DeadlineTimeline } from "../components/insurance/DeadlineTimeline";
import { DoubleInsurance } from "../components/insurance/DoubleInsurance";
import { InsuranceSettings } from "../components/insurance/InsuranceSettings";
import { InsuranceTasks } from "../components/insurance/InsuranceTasks";
import { NeedsProfileForm } from "../components/insurance/NeedsProfileForm";
import { NeedsResults } from "../components/insurance/NeedsResults";
import { PolicyDetail } from "../components/insurance/PolicyDetail";
import { PolicyList } from "../components/insurance/PolicyList";
import type { PremiumRegisterData } from "../components/insurance/PremiumHistory";
import { formatMinorUnitsAsEuro } from "../lib/money";
import type {
  BackfillPreview,
  ConfirmLineInput,
  DuplicateFinding,
  InsuranceLine,
  InsurancePolicy,
  InsuranceSettings as InsuranceSettingsValue,
  InsuranceSummary,
  InsuranceTask,
  NeedsCheckResult,
  NeedsCompleteness,
  NeedsPersonProfileInput,
  NeedsProfile,
  NeedsProfileInput,
  PolicyPurpose,
  RuleDecisionInput,
} from "../lib/insuranceApi";
import "./insurance.css";

/**
 * Datenanbindung des Bedarfsabgleichs (P6), als ein Buendel.
 *
 * Ein Buendel und nicht fuenfzehn Einzelfelder, aus demselben Grund wie beim
 * Beitragsregister: die drei Bloecke haengen an vier Abfragen, die entweder
 * gemeinsam da sind oder gemeinsam fehlen. Fehlt das Buendel, erscheint der
 * ganze Bereich nicht - eine Bedarfsansicht ohne Anbindung waere ein Formular
 * ohne Ziel und eine Befundliste ohne Regeln.
 */
export interface NeedsRegisterData {
  profile: NeedsProfile | null;
  completeness: NeedsCompleteness | null;
  check: NeedsCheckResult | null;
  duplicates: DuplicateFinding[];
  loading: boolean;
  error: string | null;
  /** Kein Fehler, sondern ein Ausbaustand: die Endpunkte antworten mit 404. */
  unavailable: boolean;
  /** Nur die Haushaltsangaben. Personen haben ihren eigenen Kanal - ein
   *  gemeinsames Flag meldete einen Speichervorgang an drei Stellen zugleich. */
  saving: boolean;
  saveError: string | null;
  /** Person, deren Angaben gerade geschrieben werden, sonst `null`. */
  savingPersonId: string | null;
  /** Fehler beim Speichern EINER Person - samt der Person, zu der er gehoert.
   *  Bei Angaben, deren Kern die Trennung nach Personenfreigabe ist, darf ein
   *  Fehler nicht unter einer fremden Person stehen. */
  personSaveError: { personId: string | null; message: string } | null;
  /** Regelschluessel der gerade schreibenden Entscheidung, sonst `null`. */
  decisionBusyRuleKey: string | null;
  decisionError: string | null;
  duplicatesLoading: boolean;
  duplicatesError: string | null;
  duplicatesUnavailable: boolean;
  onSaveProfile: (input: NeedsProfileInput) => void;
  onSavePerson: (personId: string, input: NeedsPersonProfileInput) => void;
  onDecide: (ruleKey: string, input: RuleDecisionInput) => void;
  onClearDecision: (ruleKey: string, policyId: string | null) => void;
}

export interface InsurancePageProps {
  policies: InsurancePolicy[];
  tasks: InsuranceTask[];
  summary: InsuranceSummary | null;
  backfillPreview: BackfillPreview | null;
  lines: InsuranceLine[];
  loading: boolean;
  error: string | null;
  onReload: () => void;
  canEdit: boolean;
  /** Ausgewaehlte Police. Die Route haelt die Auswahl, weil an ihr ein
   *  eigener Abruf haengt (Zwecktext). */
  selectedPolicyId: string | null;
  onSelectPolicy: (policyId: string | null) => void;
  purpose: PolicyPurpose | null;
  purposeLoading: boolean;
  purposeError: string | null;
  actionBusy: boolean;
  actionError: string | null;
  backfillBusy: boolean;
  backfillError: string | null;
  onRunBackfill: () => void;
  onConfirmMainDue: (policyId: string, mainDueOn?: string) => void;
  onConfirmNoticeTerms: (policyId: string, months: number) => void;
  onConfirmLine: (policyId: string, input: ConfirmLineInput) => void;

  /* ---- Erinnerungen (P2) -------------------------------------------------
   *
   * Bewusst optional: der Einstellungsblock haengt an einem eigenen Abruf, und
   * ohne `onSaveSettings` erscheint er gar nicht. So bleibt die Seite ohne
   * diesen Abruf vollstaendig bedienbar - und ein Formular, das nirgendwo
   * hinspeichert, kann nicht entstehen.
   */
  settings?: InsuranceSettingsValue | null;
  settingsLoading?: boolean;
  settingsError?: string | null;
  settingsSaving?: boolean;
  settingsSaveError?: string | null;
  onSaveSettings?: (input: InsuranceSettingsValue) => void;

  /** Datenanbindung des Beitragsregisters (P3). Nur durchgereicht - die Seite
   *  selbst zeigt keine Beitragshistorie, sie gehoert ins Policendetail.
   *  Fehlt sie, gibt es dort weder das Register noch das
   *  Sonderkuendigungsband. */
  premiumRegister?: PremiumRegisterData;

  /** Datenanbindung des Bedarfsabgleichs (P6). Fehlt sie, gibt es weder das
   *  Bedarfsprofil noch Befunde noch Doppelungen - und keinen halben
   *  Bildschirm, der ohne Server nichts beantworten kann. */
  needs?: NeedsRegisterData;
}

/**
 * Versicherungsuebersicht, rein darstellend.
 *
 * Die Reihenfolge ist die des Plans und keine Geschmacksfrage: erst was zu tun
 * ist, dann wie der Bestand hereinkommt, dann der Bestand selbst. Vier
 * Kennzahlen oben beantworten keine Frage, solange die Erfassung nicht
 * gepflegt ist - sie stehen deshalb als eine Zeile unter der Liste und tragen
 * dort ihre Unschaerfe mit.
 */
export function InsurancePage({
  policies,
  tasks,
  summary,
  backfillPreview,
  lines,
  loading,
  error,
  onReload,
  canEdit,
  selectedPolicyId,
  onSelectPolicy,
  purpose,
  purposeLoading,
  purposeError,
  actionBusy,
  actionError,
  backfillBusy,
  backfillError,
  onRunBackfill,
  onConfirmMainDue,
  onConfirmNoticeTerms,
  onConfirmLine,
  settings = null,
  settingsLoading = false,
  settingsError = null,
  settingsSaving = false,
  settingsSaveError = null,
  onSaveSettings,
  premiumRegister,
  needs,
}: InsurancePageProps) {
  // Welche Frist gerade aufgeklappt ist. Eine eigene Auswahl neben der Police:
  // die Herleitung beantwortet eine andere Frage als das Policendetail
  // ("stimmt dieses Datum?" statt "was steht in dieser Police?"), und sie
  // gehoert neben die Achse, auf der sie angeklickt wurde.
  const [deadlinePolicyId, setDeadlinePolicyId] = useState<string | null>(null);

  const selectedPolicy = policies.find((policy) => policy.id === selectedPolicyId) ?? null;
  // Aus der Liste gelesen statt gespeichert: verschwindet die Police, ist auch
  // ihre Herleitung weg - ohne einen Effekt, der aufraeumt.
  const deadlinePolicy = policies.find((policy) => policy.id === deadlinePolicyId) ?? null;

  return (
    <section className="page insurance-page">
      <header className="page-header">
        <div>
          <h1 className="page-heading">Versicherungen</h1>
          <p className="card-body">
            Was versichert ist, wofür es da ist und bis wann gekündigt werden kann – jedes Datum mit
            seiner Herkunft.
          </p>
        </div>
        <button type="button" className="btn-secondary" onClick={onReload} disabled={loading}>
          {loading ? "Wird geladen …" : "Neu laden"}
        </button>
      </header>

      {error && (
        <p className="insurance-error" role="alert">
          {error}
        </p>
      )}

      {loading && policies.length === 0 && !error && (
        <div className="card">
          <p className="card-body">Bestand wird geladen …</p>
        </div>
      )}

      <InsuranceTasks
        tasks={tasks}
        canEdit={canEdit}
        busy={actionBusy}
        onOpenPolicy={onSelectPolicy}
        onConfirmSuggestedMainDue={(policyId) => onConfirmMainDue(policyId)}
      />

      {/* Nur solange kein Detail offen ist: dort steht derselbe Fehler an der
          Aktion, zu der er gehoert. Zweimal gerendert liest ein Screenreader
          ihn zweimal vor. */}
      {actionError && !selectedPolicy && (
        <p className="insurance-error" role="alert">
          {actionError}
        </p>
      )}

      <BackfillPanel
        preview={backfillPreview}
        policies={policies}
        summary={summary}
        canEdit={canEdit}
        busy={backfillBusy}
        error={backfillError}
        onRunBackfill={onRunBackfill}
      />

      <PolicyList
        policies={policies}
        selectedPolicyId={selectedPolicyId}
        onSelectPolicy={onSelectPolicy}
      />

      {summary && policies.length > 0 && (
        <p className="insurance-metrics">
          {summary.policy_count} Policen ·{" "}
          {formatMinorUnitsAsEuro(summary.yearly_in_fixed_costs_minor)}
          /Jahr aus Verträgen
          {summary.yearly_paid_by_others_minor > 0 &&
            ` · ${formatMinorUnitsAsEuro(summary.yearly_paid_by_others_minor)}/Jahr zahlen Dritte (nicht in den Fixkosten)`}
          {summary.unknown_line_count > 0 && ` · ${summary.unknown_line_count} ohne Sparte`}
          {summary.missing_main_due_count > 0 &&
            ` · ${summary.missing_main_due_count} ohne Hauptfälligkeit`}
          {summary.unconfirmed_main_due_count > 0 &&
            ` · ${summary.unconfirmed_main_due_count} Hauptfälligkeit nur angenommen`}
        </p>
      )}

      <DeadlineTimeline
        policies={policies}
        selectedPolicyId={deadlinePolicyId}
        onSelectPolicy={(policyId) =>
          setDeadlinePolicyId((current) => (current === policyId ? null : policyId))
        }
      />

      {deadlinePolicy && (
        <DeadlineDetail
          key={deadlinePolicy.id}
          policy={deadlinePolicy}
          onOpenPolicy={onSelectPolicy}
          onClose={() => setDeadlinePolicyId(null)}
        />
      )}

      {/* Der Bedarfsabgleich steht NACH dem Bestand und nicht davor. Das ist
          die Reihenfolge des Plans und zugleich seine Begruendung: ein Befund
          ueber einen ungepflegten Bestand ist schaedlicher als keiner, also
          sieht man erst, was erfasst ist, und dann, was die Regeln dazu
          sagen. */}
      {needs && (
        <>
          <NeedsProfileForm
            profile={needs.profile}
            completeness={needs.completeness}
            loading={needs.loading}
            error={needs.error}
            unavailable={needs.unavailable}
            saving={needs.saving}
            saveError={needs.saveError}
            savingPersonId={needs.savingPersonId}
            personSaveError={needs.personSaveError}
            canEdit={canEdit}
            onSave={needs.onSaveProfile}
            onSavePerson={needs.onSavePerson}
          />

          <NeedsResults
            result={needs.check}
            policies={policies}
            loading={needs.loading}
            error={needs.error}
            unavailable={needs.unavailable}
            canEdit={canEdit}
            busyRuleKey={needs.decisionBusyRuleKey}
            actionError={needs.decisionError}
            onDecide={needs.onDecide}
            onClearDecision={needs.onClearDecision}
            onOpenPolicy={onSelectPolicy}
          />

          <DoubleInsurance
            findings={needs.duplicates}
            policies={policies}
            loading={needs.duplicatesLoading}
            error={needs.duplicatesError}
            unavailable={needs.duplicatesUnavailable}
            onOpenPolicy={onSelectPolicy}
          />
        </>
      )}

      {onSaveSettings && (
        <InsuranceSettings
          settings={settings}
          loading={settingsLoading}
          error={settingsError}
          saving={settingsSaving}
          saveError={settingsSaveError}
          canEdit={canEdit}
          onSave={onSaveSettings}
        />
      )}

      {selectedPolicy && (
        <PolicyDetail
          key={selectedPolicy.id}
          policy={selectedPolicy}
          purpose={purpose}
          purposeLoading={purposeLoading}
          purposeError={purposeError}
          lines={lines}
          canEdit={canEdit}
          busy={actionBusy}
          error={actionError}
          onClose={() => onSelectPolicy(null)}
          onConfirmMainDue={(policyId, mainDueOn) => onConfirmMainDue(policyId, mainDueOn)}
          onConfirmNoticeTerms={onConfirmNoticeTerms}
          onConfirmLine={onConfirmLine}
          premiumRegister={premiumRegister}
        />
      )}
    </section>
  );
}
