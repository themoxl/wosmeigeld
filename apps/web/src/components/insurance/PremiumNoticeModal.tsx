/**
 * Anpassungsschreiben erfassen (docs/VERSICHERUNGEN-PLAN.md, 4.7; P3).
 *
 * **Die ganze Sorgfalt dieses Dialogs liegt auf zwei Datumsfeldern.** Sie
 * sehen gleich aus, stehen im Brief oft nebeneinander und bedeuten
 * Verschiedenes:
 *
 * - *Schreiben erhalten am* ist der **Fristanker**. Paragraph 40 Abs. 1 S. 1
 *   VVG laesst die Frist ab ZUGANG laufen. Wer hier das Datum des Briefkopfs
 *   eintraegt, bekommt eine Frist, die zu frueh endet - und im schlimmsten Fall
 *   ein Recht, das der Nutzer fuer verfallen haelt, obwohl es noch laeuft.
 * - *Neuer Beitrag gilt ab* ist das **Wirksamwerden**. Es verlaengert die Frist
 *   nicht. Es begrenzt die Wirkung: die Kuendigung wirkt "fruehestens jedoch
 *   zum Zeitpunkt des Wirksamwerdens der Erhoehung".
 *
 * Deshalb tragen beide Felder eine Marke ("Fristanker" / "kein Fristanker"),
 * einen Hilfetext mit dem Grund und eine Beschriftung, die den Gegenstand
 * nennt statt das Datum. "Zugangsdatum" und "Wirksamkeitsdatum" waeren
 * fachlich richtig und in der Sache nutzlos - sie klingen gleich.
 *
 * **Was dieser Dialog NICHT tut:** er rechnet das Fenster nicht aus. Laenge
 * (ein Monat, PKV zwei), sicherer Zustelltag und Handlungsdatum kommen vom
 * Server; ein zweiter Feiertagskalender im Browser ergaebe frueher oder
 * spaeter einen zweiten Termin fuer dieselbe Frist.
 */

import { useState, type FormEvent } from "react";
import { Modal } from "../Modal";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import {
  formatBasisPointsAsPercent,
  FREQUENCY_LABEL,
  PREMIUM_NOTICE_REASON_LABEL,
  type InsurancePolicy,
  type PremiumNoticeCreateInput,
  type PremiumNoticeFrequency,
  type PremiumNoticeReason,
} from "../../lib/insuranceApi";

/** Die vier zulaessigen Zahlweisen eines Anpassungsschreibens, in der
 *  Reihenfolge, in der sie auf Beitragsrechnungen vorkommen. */
const NOTICE_FREQUENCIES: PremiumNoticeFrequency[] = [
  "monthly",
  "quarterly",
  "semiannual",
  "yearly",
];

const NOTICE_REASONS: PremiumNoticeReason[] = [
  "claims_cost",
  "risk_change",
  "demographics",
  "index",
  "tariff_class",
  "regional_class",
  "sf_class",
  "age_bracket",
  "other",
];

/** Sparten, auf denen der Vergleichsbeitrag der Regelfall ist und nicht die
 *  Ausnahme - dort steht das Feld offen statt hinter einer Klappe. */
function isMotorLine(lineKey: string): boolean {
  return lineKey.startsWith("motor");
}

/**
 * Vorbelegung des bisherigen Beitrags.
 *
 * Nur bei vollem Anteil: traegt die Police nur einen Teil des Vertrags
 * (`premium_share_bp < 10000`), ist der Vertragsbetrag NICHT der Beitrag
 * dieser Police. Eine Vorbelegung waere dann eine Zahl, die der Nutzer
 * ungeprueft bestaetigt - und die anschliessend als Preisstufe im Vertrag
 * landet.
 */
function suggestedOldAmount(policy: InsurancePolicy): string {
  const premium = policy.premium;
  if (premium.premium_share_bp !== 10000) return "";
  const minor =
    premium.kind === "contract"
      ? premium.contract_amount_minor
      : premium.kind === "external"
        ? premium.external_premium_minor
        : null;
  if (minor === null) return "";
  return (minor / 100).toLocaleString("de-DE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function suggestedFrequency(policy: InsurancePolicy): PremiumNoticeFrequency | "" {
  const raw =
    policy.premium.kind === "contract"
      ? policy.premium.contract_frequency
      : policy.premium.external_frequency;
  return NOTICE_FREQUENCIES.includes(raw as PremiumNoticeFrequency)
    ? (raw as PremiumNoticeFrequency)
    : "";
}

export interface PremiumNoticeModalProps {
  policy: InsurancePolicy;
  busy: boolean;
  error: string | null;
  onSubmit: (input: PremiumNoticeCreateInput) => void;
  onClose: () => void;
}

export function PremiumNoticeModal({
  policy,
  busy,
  error,
  onSubmit,
  onClose,
}: PremiumNoticeModalProps) {
  const [receivedOn, setReceivedOn] = useState("");
  const [effectiveOn, setEffectiveOn] = useState("");
  const [oldAmount, setOldAmount] = useState(() => suggestedOldAmount(policy));
  const [newAmount, setNewAmount] = useState("");
  const [comparisonAmount, setComparisonAmount] = useState("");
  const [frequency, setFrequency] = useState<PremiumNoticeFrequency | "">(() =>
    suggestedFrequency(policy),
  );
  const [withBenefitIncrease, setWithBenefitIncrease] = useState(false);
  const [reason, setReason] = useState<PremiumNoticeReason | "">("");
  const [comparisonOpen, setComparisonOpen] = useState(() => isMotorLine(policy.line_key));
  const [problems, setProblems] = useState<string[]>([]);

  /**
   * Zuklappen heisst "kein Vergleichsbeitrag ausgewiesen" - und leert das Feld.
   *
   * Ohne das Leeren entschiede ein unsichtbarer Wert ueber das
   * Sonderkuendigungsrecht: der Server nimmt `comparison_amount_minor` als
   * Vergleichsmassstab, und ein zugeklappt stehengebliebener Betrag ueber dem
   * neuen Beitrag loescht das Fenster nach Paragraph 40 VVG lautlos. Die
   * haertere Fassung desselben Fehlers war eine Sackgasse: ein unlesbarer Wert
   * blockierte das Absenden mit einer Meldung zu einem Feld, das nicht mehr auf
   * dem Bildschirm stand.
   */
  const toggleComparison = () => {
    const nextOpen = !comparisonOpen;
    setComparisonOpen(nextOpen);
    if (!nextOpen) setComparisonAmount("");
  };

  const oldMinor = parseAmountToMinorUnits(oldAmount);
  const newMinor = parseAmountToMinorUnits(newAmount);
  const comparisonMinor = comparisonAmount.trim()
    ? parseAmountToMinorUnits(comparisonAmount)
    : null;

  // Die Quote ist frei von der Zahlweise: beide Betraege stehen in derselben,
  // also braucht die Vorschau keine Normalisierung auf ein Jahr.
  const deltaBp =
    oldMinor !== null && newMinor !== null && oldMinor > 0
      ? Math.round(((newMinor - oldMinor) / oldMinor) * 10000)
      : null;

  /**
   * Erzeugt dieses Schreiben nach heutigem Stand ein Fenster?
   *
   * Bewusst nur eine Vorschau der drei Bedingungen, die der Nutzer selbst
   * eingibt - nicht die Frist. Ob ein Recht besteht, entscheidet der Server;
   * die Vorschau sagt nur, welche Angabe es gerade ausschliesst.
   */
  const blocksWindow = (): string | null => {
    if (policy.dynamic_agreed) {
      return "Diese Police hat eine vereinbarte Dynamik. Eine planmäßige Dynamikstufe ist keine Erhöhung im Sinne des § 40 VVG und begründet kein Sonderkündigungsrecht – erfassen können Sie das Schreiben trotzdem.";
    }
    if (withBenefitIncrease) {
      return "Mit entsprechender Leistungserweiterung entsteht kein Sonderkündigungsrecht: § 40 VVG greift nur bei einer Erhöhung ohne Mehrleistung.";
    }
    if (comparisonMinor !== null && newMinor !== null && newMinor <= comparisonMinor) {
      return "Der neue Beitrag liegt nicht über dem Vergleichsbeitrag. Dann bleibt es bei der Hochstufung – ein Sonderkündigungsrecht entsteht daraus nicht.";
    }
    if (
      comparisonMinor === null &&
      oldMinor !== null &&
      newMinor !== null &&
      newMinor <= oldMinor
    ) {
      return "Der Beitrag steigt nicht. Eine Senkung oder ein gleichbleibender Beitrag löst kein Sonderkündigungsrecht aus.";
    }
    return null;
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    if (!receivedOn) {
      found.push(
        "Ohne den Tag des Zugangs entsteht keine Frist, sondern eine Rückfrage. Das Feld ist Pflicht.",
      );
    }
    if (!effectiveOn) {
      found.push("Ohne das Wirksamwerden fehlt das früheste Beendigungsdatum.");
    }
    if (receivedOn && effectiveOn && effectiveOn < receivedOn) {
      found.push(
        "Der neue Beitrag kann nicht vor dem Schreiben gelten. Prüfen Sie, ob die beiden Daten vertauscht sind.",
      );
    }
    if (oldMinor === null || oldMinor <= 0) {
      found.push("Der bisherige Beitrag fehlt oder ist nicht lesbar.");
    }
    if (newMinor === null || newMinor <= 0) {
      found.push("Der neue Beitrag fehlt oder ist nicht lesbar.");
    }
    if (comparisonAmount.trim() && (comparisonMinor === null || comparisonMinor <= 0)) {
      found.push("Der Vergleichsbeitrag ist nicht lesbar. Lassen Sie ihn leer, wenn er fehlt.");
    }
    if (!frequency) {
      found.push("Die Zahlweise fehlt – ohne sie ist der Betrag nicht einzuordnen.");
    }
    setProblems(found);
    if (found.length > 0) return;

    onSubmit({
      received_on: receivedOn,
      effective_on: effectiveOn,
      old_amount_minor: oldMinor as number,
      new_amount_minor: newMinor as number,
      comparison_amount_minor: comparisonMinor,
      frequency: frequency as PremiumNoticeFrequency,
      with_benefit_increase: withBenefitIncrease,
      reason: reason === "" ? null : reason,
    });
  };

  const blocked = blocksWindow();

  return (
    <Modal
      title={`Beitragsanpassung erfassen · ${policy.insurer_name}`}
      size="wide"
      onClose={onClose}
      className="insurance-detail-modal"
    >
      <form className="insurance-notice-form" onSubmit={handleSubmit}>
        <p className="insurance-note">
          Aus dem Schreiben entsteht kein zweiter Beitrag an der Police. Es hält fest, was der
          Versicherer angekündigt hat – und woraus sich ein Sonderkündigungsrecht ergibt. Der neue
          Betrag wandert erst mit „Betrag übernehmen“ in den Vertrag.
        </p>

        {/* Die beiden Daten stehen nebeneinander, damit der Unterschied
            sichtbar ist statt erklärt. Untereinander läse man den zweiten
            Hilfetext nicht mehr. */}
        <div className="insurance-date-pair">
          <div className="insurance-form-field insurance-form-field--anchor">
            <label className="insurance-form-label" htmlFor="premium-notice-received">
              Schreiben erhalten am
              <span className="insurance-anchor-mark">Fristanker</span>
            </label>
            {/* `aria-required` statt `required`: die native Pflichtpruefung
                verhindert das Submit-Ereignis, und damit liefe `handleSubmit`
                nie - der Nutzer bekaeme die Blase des Browsers statt der
                Begruendung, warum gerade DIESES Datum die Frist ausloest. Die
                Ansage an Vorlesewerkzeuge bleibt, die Sperre faellt weg. */}
            <input
              id="premium-notice-received"
              type="date"
              className="form-input"
              aria-required="true"
              value={receivedOn}
              onChange={(event) => setReceivedOn(event.target.value)}
            />
            <p className="insurance-form-help">
              Der Tag, an dem der Brief bei Ihnen ankam – <strong>nicht</strong> das Datum auf dem
              Briefkopf. § 40 Abs. 1 S. 1 VVG lässt die Frist ab Zugang laufen: ein Monat, in der
              privaten Kranken- und Pflegeversicherung zwei. Ist der Tag unsicher, nehmen Sie den
              spätesten, den Sie belegen können.
            </p>
          </div>

          <div className="insurance-form-field">
            <label className="insurance-form-label" htmlFor="premium-notice-effective">
              Neuer Beitrag gilt ab
              <span className="insurance-anchor-mark insurance-anchor-mark--off">
                kein Fristanker
              </span>
            </label>
            <input
              id="premium-notice-effective"
              type="date"
              className="form-input"
              aria-required="true"
              value={effectiveOn}
              onChange={(event) => setEffectiveOn(event.target.value)}
            />
            <p className="insurance-form-help">
              Der Tag, ab dem der Versicherer den neuen Beitrag verlangt. Er verlängert die Frist
              nicht. Er ist aber das früheste Datum, zu dem eine Kündigung wirkt – „mit sofortiger
              Wirkung, frühestens jedoch zum Zeitpunkt des Wirksamwerdens der Erhöhung“.
            </p>
          </div>
        </div>

        <div className="insurance-form-grid">
          <div className="insurance-form-field">
            <label className="insurance-form-label" htmlFor="premium-notice-old">
              Bisheriger Beitrag
            </label>
            <input
              id="premium-notice-old"
              type="text"
              inputMode="decimal"
              className="form-input"
              placeholder="14,90"
              value={oldAmount}
              onChange={(event) => setOldAmount(event.target.value)}
            />
          </div>
          <div className="insurance-form-field">
            <label className="insurance-form-label" htmlFor="premium-notice-new">
              Neuer Beitrag
            </label>
            <input
              id="premium-notice-new"
              type="text"
              inputMode="decimal"
              className="form-input"
              placeholder="16,70"
              value={newAmount}
              onChange={(event) => setNewAmount(event.target.value)}
            />
          </div>
          <div className="insurance-form-field">
            <label className="insurance-form-label" htmlFor="premium-notice-frequency">
              Zahlweise beider Beträge
            </label>
            <select
              id="premium-notice-frequency"
              className="form-input"
              value={frequency}
              onChange={(event) => setFrequency(event.target.value as PremiumNoticeFrequency | "")}
            >
              <option value="">Bitte wählen</option>
              {NOTICE_FREQUENCIES.map((value) => (
                <option key={value} value={value}>
                  {FREQUENCY_LABEL[value]}
                </option>
              ))}
            </select>
            <p className="insurance-form-help">
              Einmalbeiträge stehen hier nicht zur Wahl: ein Einmalbeitrag wird nicht angepasst.
            </p>
          </div>
          <div className="insurance-form-field">
            <label className="insurance-form-label" htmlFor="premium-notice-reason">
              Anlass laut Schreiben
            </label>
            <select
              id="premium-notice-reason"
              className="form-input"
              value={reason}
              onChange={(event) => setReason(event.target.value as PremiumNoticeReason | "")}
            >
              <option value="">nicht angegeben</option>
              {NOTICE_REASONS.map((value) => (
                <option key={value} value={value}>
                  {PREMIUM_NOTICE_REASON_LABEL[value]}
                </option>
              ))}
            </select>
          </div>
        </div>

        {deltaBp !== null && (
          <p className="insurance-delta-preview">
            Änderung: <strong>{formatBasisPointsAsPercent(deltaBp)}</strong>
            {oldMinor !== null && newMinor !== null && (
              <>
                {" "}
                <span className="insurance-kv-origin">
                  {formatMinorUnitsAsEuro(oldMinor)} → {formatMinorUnitsAsEuro(newMinor)}
                  {frequency ? ` · ${FREQUENCY_LABEL[frequency]}` : ""}
                </span>
              </>
            )}
          </p>
        )}

        {/* Vergleichsbeitrag: auf Kfz-Policen offen, sonst hinter einer Klappe.
            Er ist kein Randfall, sondern der einzige Weg, eine Erhöhung zu
            erkennen, die sich hinter der jährlichen SF-Hochstufung versteckt -
            aber auf einer Hausratpolice steht er nie auf der Rechnung. */}
        <div className="insurance-disclosure">
          <button
            type="button"
            className="insurance-inline-button"
            aria-expanded={comparisonOpen}
            onClick={toggleComparison}
          >
            {comparisonOpen
              ? "Vergleichsbeitrag ausblenden"
              : "Auf der Rechnung steht ein Vergleichsbeitrag"}
          </button>
          {comparisonOpen && (
            <div className="insurance-form-field">
              <label className="insurance-form-label" htmlFor="premium-notice-comparison">
                Vergleichsbeitrag
              </label>
              <input
                id="premium-notice-comparison"
                type="text"
                inputMode="decimal"
                className="form-input"
                placeholder="leer lassen, wenn keiner ausgewiesen ist"
                value={comparisonAmount}
                onChange={(event) => setComparisonAmount(event.target.value)}
              />
              <p className="insurance-form-help">
                Der Beitrag mit neuer Schadenfreiheitsklasse, aber <strong>ohne</strong> Typ-,
                Regional- und Tarifanpassung. Liegt der neue Beitrag darüber, besteht das
                Sonderkündigungsrecht – auch dann, wenn die Rechnung gegenüber dem Vorjahr insgesamt
                sinkt.
              </p>
            </div>
          )}
        </div>

        <label className="insurance-switch">
          <input
            type="checkbox"
            checked={withBenefitIncrease}
            onChange={(event) => setWithBenefitIncrease(event.target.checked)}
          />
          Mit entsprechender Leistungserweiterung
        </label>
        <p className="insurance-form-help">
          Nur ankreuzen, wenn das Schreiben eine Mehrleistung nennt, die der Erhöhung entspricht –
          eine höhere Versicherungssumme, ein neuer Baustein. Dann entsteht kein Fenster.
        </p>

        {blocked && <p className="insurance-derivation-warning">{blocked}</p>}

        {problems.length > 0 && (
          <ul className="insurance-error" role="alert">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        )}
        {error && (
          <p className="insurance-error" role="alert">
            {error}
          </p>
        )}

        <div className="insurance-action-row">
          <button type="submit" className="btn-primary" disabled={busy}>
            {busy ? "Wird gespeichert …" : "Schreiben erfassen"}
          </button>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
            Abbrechen
          </button>
        </div>
        <p className="insurance-settings-note">
          Fristende, letzter sicherer Zustelltag und empfohlenes Handlungsdatum rechnet der Server –
          samt Feiertagen des Bundeslands, in das die Kündigung geht. Sie erscheinen nach dem
          Speichern im Register „Beiträge“.
        </p>
      </form>
    </Modal>
  );
}
