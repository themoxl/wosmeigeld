/**
 * Gesellschaftswechsel mit Historienmitnahme
 * (docs/VERSICHERUNGEN-PLAN.md, 4.15 Punkt 1; P3).
 *
 * **Warum dieser Dialog mit einer Gegenueberstellung beginnt und nicht mit
 * einem Formular.** Ein Wechsel sieht aus wie "alte Police loeschen, neue
 * anlegen", und genau das waere der Datenverlust: die Beitragszeitreihe risse
 * ab, die Vorversicherungszeit ginge verloren, und die Wartezeit begaenne von
 * vorn. Wer den Wechsel richtig erfasst, behaelt beides - aber er muss vorher
 * sehen, was mitkommt und was neu beginnt. Deshalb steht die Bilanz oben und
 * die Eingabe darunter.
 *
 * Der Server macht daraus eine Transaktion: die bisherige Police geht auf
 * `ended` mit `cancellation_reason = 'switch'`, `predecessor_policy_id`
 * verknuepft beide, und `GET /premium-history` fuehrt die Zeitreihe ueber
 * beide Policen zusammen - mit sichtbarem Wechselpunkt statt eines Abbruchs.
 */

import { useState, type FormEvent } from "react";
import { Modal } from "../Modal";
import { useFormatDate } from "../../hooks/useProfile";
import type { InsurancePolicy, ReplaceInsurerInput } from "../../lib/insuranceApi";

export interface ReplaceInsurerModalProps {
  policy: InsurancePolicy;
  /** Bereits erfasste Policen, aus denen eine Nachfolgerin gewaehlt werden kann. */
  candidates: InsurancePolicy[];
  busy: boolean;
  error: string | null;
  onSubmit: (input: ReplaceInsurerInput) => void;
  onClose: () => void;
}

export function ReplaceInsurerModal({
  policy,
  candidates,
  busy,
  error,
  onSubmit,
  onClose,
}: ReplaceInsurerModalProps) {
  const { formatDate } = useFormatDate();
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [existingId, setExistingId] = useState("");
  const [insurerName, setInsurerName] = useState("");
  const [policyNumber, setPolicyNumber] = useState("");
  const [tariffName, setTariffName] = useState("");
  const [startsOn, setStartsOn] = useState("");
  // Der letzte Tag der bisherigen Police. Vorbelegt mit dem Vertragsende,
  // sofern eines berechenbar ist - das ist der Regelfall des geordneten
  // Wechsels zum Ablauf. Ein angenommenes Datum wird NICHT vorbelegt: es
  // stuende hier als Tatsache, obwohl es geraten ist.
  const [endsOn, setEndsOn] = useState(
    () =>
      policy.ends_on ??
      (policy.deadline.certainty === "confirmed" ? (policy.deadline.term_ends_on ?? "") : ""),
  );
  const [priorCoverSince, setPriorCoverSince] = useState(() => policy.starts_on ?? "");
  const [problems, setProblems] = useState<string[]>([]);

  // Gleiche Sparte zuerst: eine Nachfolgerin ist fast immer dieselbe Sparte,
  // und eine Liste, die mit fremden Sparten beginnt, laedt zum Fehlgriff ein.
  const selectable = candidates
    .filter((entry) => entry.id !== policy.id)
    .slice()
    .sort((a, b) => {
      const aSame = a.line_key === policy.line_key ? 0 : 1;
      const bSame = b.line_key === policy.line_key ? 0 : 1;
      return aSame - bSame || a.insurer_name.localeCompare(b.insurer_name, "de-DE");
    });

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    if (!endsOn) {
      found.push("Ohne den letzten Tag der bisherigen Police bleibt offen, wann der Schutz endet.");
    }
    if (mode === "existing" && !existingId) {
      found.push("Wählen Sie die Nachfolgepolice aus – oder legen Sie sie hier an.");
    }
    if (mode === "new" && insurerName.trim().length === 0) {
      found.push("Die neue Gesellschaft braucht einen Namen.");
    }
    if (mode === "new" && startsOn && endsOn && startsOn < endsOn) {
      // Kein Fehler, nur eine Auffälligkeit: eine Überlappung ist beim Wechsel
      // erlaubt und wird ausdrücklich nicht als Doppelversicherung gemeldet.
      // Sie soll aber genannt werden, damit sie beabsichtigt ist.
      found.push(
        `Die neue Police beginnt vor dem Ende der alten. Das ist erlaubt – während der Überlappung entsteht kein Doppelversicherungsbefund –, prüfen Sie aber, ob beide Daten stimmen.`,
      );
    }
    setProblems(found);
    // Die Überlappung ist der einzige Punkt, der nur warnt. Steht sonst nichts
    // an, wird trotzdem gespeichert - sonst käme man an einem beabsichtigten
    // Übergang nie vorbei.
    const blocking = found.filter((entry) => !entry.startsWith("Die neue Police beginnt vor"));
    if (blocking.length > 0) return;

    onSubmit({
      ends_on: endsOn,
      prior_cover_since: priorCoverSince === "" ? null : priorCoverSince,
      ...(mode === "existing"
        ? { new_policy_id: existingId }
        : {
            new_policy: {
              // Die Sparte wandert mit: eine Nachfolgerin in einer anderen
              // Sparte waere kein Wechsel, sondern eine zweite Absicherung.
              line_key: policy.line_key,
              insurer_name: insurerName.trim(),
              policy_number: policyNumber.trim() === "" ? null : policyNumber.trim(),
              tariff_name: tariffName.trim() === "" ? null : tariffName.trim(),
              starts_on: startsOn === "" ? null : startsOn,
            },
          }),
    });
  };

  return (
    <Modal
      title={`Versicherer wechseln · ${policy.line_label}`}
      size="wide"
      onClose={onClose}
      className="insurance-detail-modal"
    >
      <form className="insurance-notice-form" onSubmit={handleSubmit}>
        <p className="insurance-note">
          Die bisherige Police bei <strong>{policy.insurer_name}</strong> wird nicht gelöscht. Sie
          geht auf „beendet“ mit dem Grund „Wechsel“ und bleibt mit der neuen verknüpft.
        </p>

        <div className="insurance-carry-split">
          <div className="insurance-carry insurance-carry--keep">
            <h3>Wird mitgenommen</h3>
            <ul>
              <li>
                <strong>Die Beitragshistorie.</strong> Der Treppenverlauf läuft über beide Policen
                weiter, mit sichtbarem Wechselpunkt statt eines Abbruchs.
              </li>
              <li>
                <strong>Die Vorversicherungszeit.</strong> Sie verkürzt oder erledigt die Wartezeit
                der neuen Police – bei Zahnzusatz und Rechtsschutz ist das der eigentliche Wert des
                Wechsels.
              </li>
              <li>
                <strong>Die Schadenfreiheitsklasse</strong> und die übrigen Spartenangaben. Der
                Wechsel setzt sie nicht zurück.
              </li>
              <li>
                <strong>Die Zählung als eine Absicherung.</strong> Während der Überlappung entsteht
                kein Doppelversicherungsbefund, und der Bedarfsabgleich zählt das Paar einmal.
              </li>
            </ul>
          </div>
          <div className="insurance-carry insurance-carry--new">
            <h3>Beginnt neu</h3>
            <ul>
              <li>
                <strong>Versicherungsschein- und Kundennummer, Tarif.</strong> Sie gehören der neuen
                Gesellschaft.
              </li>
              <li>
                <strong>Hauptfälligkeit und Kündigungsfrist.</strong> Beide gelten zunächst als
                angenommen – bis sie an der neuen Police bestätigt sind, gibt es dort keinen
                Countdown und keine Erinnerung.
              </li>
              <li>
                <strong>Die Geldquelle.</strong> Der neue Beitrag hängt an einem neuen Vertrag; die
                Verknüpfung wird an der neuen Police gesetzt.
              </li>
              <li>
                <strong>Ein laufendes Sonderkündigungsfenster</strong> der alten Police. Es endet
                mit ihr – ein Recht gegen eine beendete Police läuft ins Leere.
              </li>
            </ul>
          </div>
        </div>

        <div className="insurance-form-grid">
          <div className="insurance-form-field">
            <label className="insurance-form-label" htmlFor="replace-ends-on">
              Bisherige Police endet am
            </label>
            <input
              id="replace-ends-on"
              type="date"
              className="form-input"
              required
              value={endsOn}
              onChange={(event) => setEndsOn(event.target.value)}
            />
            <p className="insurance-form-help">
              {policy.deadline.term_ends_on
                ? `Berechnetes Vertragsende: ${formatDate(policy.deadline.term_ends_on)} (${
                    policy.deadline.certainty === "confirmed" ? "bestätigt" : "angenommen"
                  }).`
                : "Für diese Police ist kein Vertragsende berechenbar – tragen Sie den Tag aus der Kündigungsbestätigung ein."}
            </p>
          </div>
          <div className="insurance-form-field">
            <label className="insurance-form-label" htmlFor="replace-prior-cover">
              Vorversichert seit
            </label>
            <input
              id="replace-prior-cover"
              type="date"
              className="form-input"
              value={priorCoverSince}
              onChange={(event) => setPriorCoverSince(event.target.value)}
            />
            <p className="insurance-form-help">
              Vorbelegt mit dem Beginn der bisherigen Police. Reicht Ihr Schutz weiter zurück – über
              eine noch frühere Gesellschaft –, tragen Sie das frühere Datum ein: die Wartezeit
              rechnet ab hier.
            </p>
          </div>
        </div>

        <fieldset className="insurance-mode-choice">
          <legend className="insurance-form-label">Nachfolgepolice</legend>
          <label className="insurance-switch">
            <input
              type="radio"
              name="replace-mode"
              checked={mode === "new"}
              onChange={() => setMode("new")}
            />
            Jetzt anlegen
          </label>
          <label className="insurance-switch">
            <input
              type="radio"
              name="replace-mode"
              checked={mode === "existing"}
              onChange={() => setMode("existing")}
              disabled={selectable.length === 0}
            />
            {selectable.length === 0
              ? "Ist bereits erfasst (keine vorhanden)"
              : "Ist bereits erfasst"}
          </label>
        </fieldset>

        {mode === "existing" ? (
          <div className="insurance-form-field">
            <label className="insurance-form-label" htmlFor="replace-existing">
              Bereits erfasste Police
            </label>
            <select
              id="replace-existing"
              className="form-input"
              value={existingId}
              onChange={(event) => setExistingId(event.target.value)}
            >
              <option value="">Bitte wählen</option>
              {selectable.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.insurer_name} · {entry.line_label}
                  {entry.policy_number ? ` · ${entry.policy_number}` : ""}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div className="insurance-form-grid">
            <div className="insurance-form-field">
              <label className="insurance-form-label" htmlFor="replace-insurer">
                Neue Gesellschaft
              </label>
              <input
                id="replace-insurer"
                type="text"
                className="form-input"
                required
                value={insurerName}
                onChange={(event) => setInsurerName(event.target.value)}
              />
            </div>
            <div className="insurance-form-field">
              <label className="insurance-form-label" htmlFor="replace-number">
                Versicherungsscheinnummer
              </label>
              <input
                id="replace-number"
                type="text"
                className="form-input"
                value={policyNumber}
                onChange={(event) => setPolicyNumber(event.target.value)}
              />
            </div>
            <div className="insurance-form-field">
              <label className="insurance-form-label" htmlFor="replace-tariff">
                Tarif
              </label>
              <input
                id="replace-tariff"
                type="text"
                className="form-input"
                value={tariffName}
                onChange={(event) => setTariffName(event.target.value)}
              />
            </div>
            <div className="insurance-form-field">
              <label className="insurance-form-label" htmlFor="replace-starts">
                Neue Police beginnt am
              </label>
              <input
                id="replace-starts"
                type="date"
                className="form-input"
                value={startsOn}
                onChange={(event) => setStartsOn(event.target.value)}
              />
            </div>
          </div>
        )}

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
            {busy ? "Wird gewechselt …" : "Wechsel eintragen"}
          </button>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
            Abbrechen
          </button>
        </div>
      </form>
    </Modal>
  );
}
