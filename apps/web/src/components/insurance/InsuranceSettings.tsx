/**
 * Erinnerungen zu Fristen (Plan 4.10 und 8.3, P2).
 *
 * Hier stehen zwei Vorlaufstaffeln und drei Schalter - mehr nicht. Das ist
 * eine bewusste Enge:
 *
 * * **Das Bundesland gehoert nicht hierher.** Es entscheidet ueber die
 *   Feiertage, die den sicheren Zustelltag verschieben, und Paragraph 193 BGB
 *   stellt auf den Feiertag am Erklaerungsort ab - also am Sitz des
 *   EMPFAENGERS. Ein Haushaltswert waere fuer jede Police ausser einer falsch;
 *   die Angabe steht deshalb an der Police.
 * * **Zwei Staffeln, nicht eine.** Eine ordentliche Kuendigungsfrist braucht
 *   Vorlauf fuer Entschluss, Angebot und Postlaufzeit; ein
 *   Sonderkuendigungsfenster ist meist nur einen Monat lang, ein Vorlauf von
 *   60 Tagen waere dort nie erreichbar.
 * * **Kein Schalter fuer "Erinnerung an angenommene Termine".** Ein
 *   angenommenes Datum erzeugt in diesem Modul nie eine Erinnerung, sondern
 *   eine Rueckfrage - das ist keine Einstellung, sondern eine Zusage.
 */

import { useState } from "react";
import type { InsuranceSettings as InsuranceSettingsValue } from "../../lib/insuranceApi";

/** Obergrenze eines Vorlaufs. Ein Jahr Vorlauf auf eine Jahresfrist waere
 *  eine Dauererinnerung, kein Hinweis. */
const MAX_LEAD_DAYS = 365;
const MAX_LEAD_STEPS = 5;

interface ParsedLeadDays {
  days: number[] | null;
  problem: string | null;
}

/**
 * "60, 14, 3" als Staffel.
 *
 * Absteigend sortiert, weil die Stufen eine Eskalation sind und in dieser
 * Reihenfolge gelesen werden. Doppelte Werte werden abgelehnt statt still
 * entfernt: sie sind fast immer ein Tippfehler, und eine stille Korrektur
 * liesse den Nutzer glauben, seine Eingabe sei angekommen.
 */
function parseLeadDays(raw: string): ParsedLeadDays {
  const parts = raw
    .split(/[,;/\s]+/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
  if (parts.length === 0) {
    return { days: null, problem: "Mindestens eine Stufe angeben, zum Beispiel 60, 14, 3." };
  }
  if (parts.length > MAX_LEAD_STEPS) {
    return { days: null, problem: `Höchstens ${MAX_LEAD_STEPS} Stufen.` };
  }
  const days: number[] = [];
  for (const part of parts) {
    if (!/^\d+$/.test(part)) {
      return { days: null, problem: `„${part}“ ist keine Tageszahl.` };
    }
    const value = Number(part);
    if (value > MAX_LEAD_DAYS) {
      return { days: null, problem: `Höchstens ${MAX_LEAD_DAYS} Tage Vorlauf.` };
    }
    if (days.includes(value)) {
      return { days: null, problem: `${value} steht doppelt.` };
    }
    days.push(value);
  }
  return { days: days.sort((a, b) => b - a), problem: null };
}

function formatLeadDays(days: number[]): string {
  return days.join(", ");
}

/** Die Staffel als Satz, damit "0" nicht als Zahl dasteht, sondern als
 *  "sofort" - genau so ist sie im Plan gemeint. */
function leadDaysSentence(days: number[]): string {
  if (days.length === 0) return "keine Erinnerung";
  return days.map((day) => (day === 0 ? "sofort" : `${day} Tage vorher`)).join(", dann ");
}

/** Lesende Fassung fuer Viewer und Child. Sie bekommen kein Formular, das sie
 *  nicht abschicken duerfen - und tragen deshalb auch keinen Formularzustand. */
function SettingsSummary({ settings }: { settings: InsuranceSettingsValue }) {
  return (
    <dl className="insurance-kv">
      <div className="insurance-kv-row">
        <dt>Ordentliche Kündigungsfristen</dt>
        <dd>
          {settings.notify_deadline ? leadDaysSentence(settings.lead_days) : "keine Erinnerung"}
        </dd>
      </div>
      <div className="insurance-kv-row">
        <dt>Sonderkündigungsfenster</dt>
        <dd>
          {settings.notify_premium_notice
            ? leadDaysSentence(settings.lead_days_special)
            : "keine Erinnerung"}
        </dd>
      </div>
      <div className="insurance-kv-row">
        <dt>Rückmeldung zu Vorgängen</dt>
        <dd>{settings.notify_claim_followup ? "erinnern" : "keine Erinnerung"}</dd>
      </div>
    </dl>
  );
}

interface SettingsFormProps {
  settings: InsuranceSettingsValue;
  saving: boolean;
  saveError: string | null;
  onSave: (input: InsuranceSettingsValue) => void;
}

function SettingsForm({ settings, saving, saveError, onSave }: SettingsFormProps) {
  // Der Anfangszustand kommt aus den geladenen Einstellungen. Die Komponente
  // wird ueber `key` neu gemountet, sobald der Server andere Werte liefert -
  // deshalb braucht es keinen Abgleich in einem Effekt, und der Fall "Server
  // hat gespeichert, Formular zeigt noch den alten Stand" kann nicht
  // entstehen.
  const [notifyDeadline, setNotifyDeadline] = useState(settings.notify_deadline);
  const [notifyPremium, setNotifyPremium] = useState(settings.notify_premium_notice);
  const [notifyClaim, setNotifyClaim] = useState(settings.notify_claim_followup);
  const [leadRaw, setLeadRaw] = useState(formatLeadDays(settings.lead_days));
  const [leadSpecialRaw, setLeadSpecialRaw] = useState(formatLeadDays(settings.lead_days_special));

  const lead = parseLeadDays(leadRaw);
  const leadSpecial = parseLeadDays(leadSpecialRaw);

  const save = () => {
    if (!lead.days || !leadSpecial.days) return;
    // Der geladene Stand wird mitgeschickt, nicht nur das Geaenderte: so
    // bleibt ein Feld erhalten, das diese Oberflaeche gar nicht anbietet.
    onSave({
      ...settings,
      notify_deadline: notifyDeadline,
      notify_premium_notice: notifyPremium,
      notify_claim_followup: notifyClaim,
      lead_days: lead.days,
      lead_days_special: leadSpecial.days,
    });
  };

  return (
    <div className="insurance-settings-form">
      <div className="insurance-settings-group">
        <label className="insurance-switch">
          <input
            type="checkbox"
            checked={notifyDeadline}
            onChange={(event) => setNotifyDeadline(event.target.checked)}
          />
          <span>An ordentliche Kündigungsfristen erinnern</span>
        </label>
        <label className="insurance-field">
          <span>Vorlauf in Tagen</span>
          <input
            className="form-input"
            inputMode="numeric"
            value={leadRaw}
            disabled={!notifyDeadline}
            onChange={(event) => setLeadRaw(event.target.value)}
            aria-describedby="insurance-lead-help"
          />
        </label>
        <p className="insurance-settings-help" id="insurance-lead-help">
          {lead.problem ? (
            <span className="insurance-settings-problem">{lead.problem}</span>
          ) : (
            <>
              Erinnerung {leadDaysSentence(lead.days ?? [])} – gemessen am empfohlenen Absendetag.
            </>
          )}{" "}
          60 Tage sind die Voreinstellung, weil zwischen Entschluss, Angebot und sicherem Zugang bei
          Briefpost mehrere Werktage liegen.
        </p>
      </div>

      <div className="insurance-settings-group">
        <label className="insurance-switch">
          <input
            type="checkbox"
            checked={notifyPremium}
            onChange={(event) => setNotifyPremium(event.target.checked)}
          />
          <span>An Sonderkündigungsfenster erinnern</span>
        </label>
        <label className="insurance-field">
          <span>Vorlauf in Tagen</span>
          <input
            className="form-input"
            inputMode="numeric"
            value={leadSpecialRaw}
            disabled={!notifyPremium}
            onChange={(event) => setLeadSpecialRaw(event.target.value)}
            aria-describedby="insurance-lead-special-help"
          />
        </label>
        <p className="insurance-settings-help" id="insurance-lead-special-help">
          {leadSpecial.problem ? (
            <span className="insurance-settings-problem">{leadSpecial.problem}</span>
          ) : (
            <>Erinnerung {leadDaysSentence(leadSpecial.days ?? [])}.</>
          )}{" "}
          Ein solches Fenster ist meist nur einen Monat lang; deshalb beginnt die Staffel bei
          „sofort“ und nicht bei 60 Tagen. Sie greift, sobald eine Beitragsanpassung erfasst ist.
        </p>
      </div>

      <div className="insurance-settings-group">
        <label className="insurance-switch">
          <input
            type="checkbox"
            checked={notifyClaim}
            onChange={(event) => setNotifyClaim(event.target.checked)}
          />
          <span>An offene Rückmeldungen zu Vorgängen erinnern</span>
        </label>
        <p className="insurance-settings-help">
          Gilt für gemeldete Vorgänge, die länger ohne Antwort bleiben.
        </p>
      </div>

      {saveError && (
        <p className="insurance-error" role="alert">
          {saveError}
        </p>
      )}

      <div className="insurance-action-row">
        <button
          type="button"
          className="btn-primary"
          disabled={saving || !lead.days || !leadSpecial.days}
          onClick={save}
        >
          {saving ? "Wird gespeichert …" : "Erinnerungen speichern"}
        </button>
      </div>
    </div>
  );
}

export interface InsuranceSettingsProps {
  settings: InsuranceSettingsValue | null;
  loading: boolean;
  error: string | null;
  saving: boolean;
  saveError: string | null;
  canEdit: boolean;
  onSave: (input: InsuranceSettingsValue) => void;
}

export function InsuranceSettings({
  settings,
  loading,
  error,
  saving,
  saveError,
  canEdit,
  onSave,
}: InsuranceSettingsProps) {
  return (
    <section className="insurance-block" aria-labelledby="insurance-settings-title">
      <div className="insurance-block-head">
        <h2 className="insurance-block-title" id="insurance-settings-title">
          Erinnerungen
        </h2>
        <span className="insurance-block-hint">
          Erinnert wird nur an bestätigte Termine – nie an angenommene
        </span>
      </div>

      <div className="card insurance-settings">
        {loading && !settings && <p className="card-body">Einstellungen werden geladen …</p>}
        {error && !settings && (
          <p className="insurance-error" role="alert">
            {error}
          </p>
        )}
        {settings &&
          (canEdit ? (
            <SettingsForm
              // Der Schluessel bindet das Formular an den geladenen Stand:
              // kommt vom Server ein anderer, entsteht ein neues Formular
              // statt eines Abgleichs im Effekt.
              key={[
                settings.notify_deadline,
                settings.notify_premium_notice,
                settings.notify_claim_followup,
                settings.lead_days.join("-"),
                settings.lead_days_special.join("-"),
              ].join("|")}
              settings={settings}
              saving={saving}
              saveError={saveError}
              onSave={onSave}
            />
          ) : (
            <SettingsSummary settings={settings} />
          ))}

        <p className="insurance-settings-note">
          Eine Erinnerung setzt einen bestätigten Termin voraus. Ein angenommenes Datum erzeugt eine
          Rückfrage, keine Erinnerung – und eine Bestätigung beendet die weiteren Stufen: die
          Staffel ist eine Eskalation für den Fall des Nichthandelns, keine Terminserie.
        </p>
        <p className="insurance-settings-note">
          Auf dem gesperrten Gerät steht in der Nachricht weder ein Betrag noch eine Person noch die
          Versicherungsart.
        </p>
        <p className="insurance-settings-note">
          Das <strong>Bundesland des Empfängers</strong> steht bewusst nicht hier, sondern an jeder
          Police: § 193 BGB stellt auf den Feiertag am Erklärungsort ab, und der ist der Sitz des
          Versicherers. Ein Wert für den ganzen Haushalt wäre für jede Police außer einer falsch.
        </p>
      </div>
    </section>
  );
}
