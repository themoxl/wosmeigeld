/**
 * Die Herleitung einer Frist, offen ausgelegt (Plan 4.7a, P2).
 *
 * Ein Werkzeug, das nur "30.11.2026" anzeigt, verlangt Glauben. Diese Ansicht
 * zeigt stattdessen die ganze Kette - Hauptfaelligkeit, Kuendigungsfrist,
 * rechtliches Fristende, letzter sicherer Zustelltag, empfohlen absenden - und
 * an jedem Glied, woher es kommt: bestaetigt, angenommen oder nicht
 * berechenbar. Wer ein Datum nachrechnen will, kann das hier tun; wer es
 * bestaetigen soll, sieht, was ihn daran hindert.
 *
 * **Warum drei Daten und nicht eines.** Eine Kuendigung ist eine
 * empfangsbeduerftige Willenserklaerung (Paragraph 130 Abs. 1 BGB) - sie
 * wirkt erst mit Zugang. Das rechtliche Fristende ist deshalb nicht der Tag,
 * bis zu dem man handeln kann, und der letzte sichere Zustelltag ist nicht das
 * Fristende. Wer beides zusammenzieht, erzeugt einen der beiden schaedlichen
 * Fehler: eine Frist, die am Samstag als "verpasst" gilt, obwohl elektronisch
 * noch zugegangen werden kann - oder ein Einwurf am Sonntag, der zu spaet
 * ankommt.
 *
 * **Warum Paragraph 193 BGB hier eine Weiche ist und keine Regel.** Er schiebt
 * ein Fristende, das auf Samstag, Sonntag oder Feiertag faellt, auf den
 * naechsten Werktag - auf Kuendigungsfristen aber weder unmittelbar noch
 * entsprechend (BGH, Urteil vom 17.02.2005 - III ZR 172/04). Der Satz steht
 * deshalb je Fristart verschieden da und wird nicht pauschal formuliert.
 */

import { useFormatDate } from "../../hooks/useProfile";
import {
  germanStateLabel,
  isoDaysBetween,
  ANCHOR_BASIS_KIND_LABEL,
  ANCHOR_BASIS_PHRASE,
  CERTAINTY_LABEL,
  DATE_SOURCE_LABEL,
  DEADLINE_KIND_LABEL,
  DEADLINE_KIND_PLURAL,
  NOTICE_ANCHOR_LABEL,
  NOTICE_TERMS_SOURCE_LABEL,
  SERVICE_CHANNEL_LABEL,
  type Certainty,
  type DeadlineKind,
  type InsurancePolicy,
} from "../../lib/insuranceApi";

/**
 * Warum der letzte sichere Tag vor dem Fristende liegt - je Fristart.
 *
 * Kuendigung, Widerruf und Anfechtung sind empfangsbeduerftige
 * Willenserklaerungen; fuer sie gilt Paragraph 130 Abs. 1 BGB. Eine Klage geht
 * ans Gericht und nicht an den Versicherer, und eine Zahlung ist ueberhaupt
 * keine Willenserklaerung - unter einer Zahlungsfrist waere der Satz gleich
 * dreifach falsch. Deshalb eine Zuordnung und kein gemeinsamer Text.
 */
const SERVICE_NOTE: Record<DeadlineKind, string> = {
  kuendigungsfrist:
    "Eine Kündigung wirkt erst mit Zugang beim Versicherer (§ 130 Abs. 1 BGB). An Samstagen, Sonntagen und Feiertagen wird nicht zugestellt – der letzte sichere Tag liegt deshalb vor dem Fristende, und er verlängert es nicht.",
  widerrufsfrist:
    "Ein Widerruf wirkt erst mit Zugang beim Versicherer (§ 130 Abs. 1 BGB). An Samstagen, Sonntagen und Feiertagen wird nicht zugestellt – der letzte sichere Tag liegt deshalb vor dem Fristende, und er verlängert es nicht.",
  anfechtungsfrist:
    "Eine Anfechtung wirkt erst mit Zugang beim Versicherer (§ 130 Abs. 1 BGB). An Samstagen, Sonntagen und Feiertagen wird nicht zugestellt – der letzte sichere Tag liegt deshalb vor dem Fristende, und er verlängert es nicht.",
  klagefrist:
    "Maßgeblich ist der Eingang bei Gericht, nicht beim Versicherer. An Samstagen, Sonntagen und Feiertagen nimmt die Geschäftsstelle nichts entgegen – der letzte sichere Tag liegt deshalb vor dem Fristende.",
  zahlungsfrist:
    "Maßgeblich ist, wann der Betrag beim Versicherer eingeht; eine Zahlung ist keine Willenserklärung, § 130 BGB regelt sie nicht. An Wochenenden und Feiertagen wird nicht gebucht – der letzte sichere Tag liegt deshalb vor dem Fristende.",
};

/** Was ein verpasster Empfehlungstag noch offen laesst - je Fristart.
 *
 *  Der Kuendigungsbutton (Paragraph 312k BGB) steht NUR unter der Kuendigung:
 *  er ist ein Weg, Vertraege zu kuendigen, und hat mit einer Klage- oder
 *  Zahlungsfrist nichts zu tun. */
const RESCUE_NOTE: Record<DeadlineKind, string> = {
  kuendigungsfrist:
    "Wird der empfohlene Tag verpasst, ist noch nichts verloren: bis zum rechtlichen Fristende kann ein Zugang auf einem schnelleren Weg – E-Mail, Kündigungsbutton (§ 312k BGB) oder Fax mit Sendebericht – die Frist noch wahren.",
  widerrufsfrist:
    "Wird der empfohlene Tag verpasst, ist noch nichts verloren: bis zum rechtlichen Fristende kann ein Zugang auf einem schnelleren Weg – E-Mail oder Fax mit Sendebericht – die Frist noch wahren.",
  anfechtungsfrist:
    "Wird der empfohlene Tag verpasst, ist noch nichts verloren: bis zum rechtlichen Fristende kann ein Zugang auf einem schnelleren Weg – E-Mail oder Fax mit Sendebericht – die Frist noch wahren.",
  klagefrist:
    "Wird der empfohlene Tag verpasst, zählt allein der Eingang bei Gericht bis zum rechtlichen Fristende. Ein schnellerer Weg zum Versicherer hilft hier nicht.",
  zahlungsfrist:
    "Wird der empfohlene Tag verpasst, zählt allein der Zahlungseingang beim Versicherer bis zum rechtlichen Fristende. Ein schnellerer Erklärungsweg hilft hier nicht, nur eine schnellere Zahlung.",
};

interface ChainStepProps {
  step: number;
  label: string;
  value: string;
  /** Herkunft dieses Glieds - sie steht an jedem, nicht nur am Ergebnis. */
  origin: string;
  tone: Certainty;
  note?: string | null;
}

function ChainStep({ step, label, value, origin, tone, note }: ChainStepProps) {
  return (
    <li className={`insurance-chain-step insurance-chain-step--${tone}`}>
      <span className="insurance-chain-marker" aria-hidden="true">
        {step}
      </span>
      <div className="insurance-chain-body">
        <span className="insurance-chain-label">{label}</span>
        <span className="insurance-chain-value">{value}</span>
        <span className="insurance-chain-origin">{origin}</span>
        {note && <p className="insurance-chain-note">{note}</p>}
      </div>
    </li>
  );
}

export interface DeadlineDetailProps {
  policy: InsurancePolicy;
  /** Weg in die Police - dort wird bestaetigt, nicht hier. */
  onOpenPolicy?: (policyId: string) => void;
  onClose?: () => void;
}

export function DeadlineDetail({ policy, onOpenPolicy, onClose }: DeadlineDetailProps) {
  const { formatDate } = useFormatDate();
  const deadline = policy.deadline;

  const date = (value: string | null, missing: string) => (value ? formatDate(value) : missing);

  // Die Sicherheit der drei gerechneten Daten ist die des Ergebnisses; die der
  // beiden Eingangsangaben ist ihre eigene. Genau diese Trennung macht
  // sichtbar, welches Glied die ganze Kette unsicher macht.
  const mainDueTone: Certainty =
    policy.main_due_source === "user_confirmed"
      ? "confirmed"
      : policy.main_due_source === "catalog_suggested"
        ? "assumed"
        : "not_computable";
  const noticeTone: Certainty =
    policy.notice_period_months === null
      ? "not_computable"
      : policy.notice_terms_source === "user_confirmed"
        ? "confirmed"
        : "assumed";
  const resultTone = deadline.certainty;

  const noticeValue =
    policy.notice_period_months === null
      ? "nicht hinterlegt"
      : `${policy.notice_period_months} ${policy.notice_period_months === 1 ? "Monat" : "Monate"}`;

  // Die Herkunft haengt am VORHANDENSEIN des Werts, nicht an der Spalte.
  // `notice_terms_source` steht auch dann auf 'catalog_suggested', wenn der
  // Katalog gar nichts vorgeschlagen hat - "nicht hinterlegt, angenommen
  // (Katalog)" behauptete eine Quelle fuer eine Zahl, die es nicht gibt. Das
  // ist Leitsatz 2 in klein: keine erfundene Herkunft, so wenig wie ein
  // erfundenes Datum.
  const noticeOrigin =
    policy.notice_period_months === null
      ? CERTAINTY_LABEL.not_computable
      : NOTICE_TERMS_SOURCE_LABEL[policy.notice_terms_source];

  // Der Versandpuffer wird ABGELESEN, nicht nachgerechnet: welcher Zustellweg
  // wie viele Werktage braucht, weiss der Rechenkern. Stuende die Zuordnung
  // hier noch einmal, gaebe es zwei Meinungen ueber denselben Tag - und die
  // Oberflaeche waere die, die niemand testet.
  const bufferDays = isoDaysBetween(deadline.recommended_action_on, deadline.safe_service_on);
  const channelName = SERVICE_CHANNEL_LABEL[policy.service_channel];
  const channelNote =
    bufferDays === null
      ? `Zustellweg ${channelName}.`
      : bufferDays === 0
        ? `Zustellweg ${channelName}: der Zugang fällt auf den Absendetag, ein Versandpuffer entfällt.`
        : `Zustellweg ${channelName}: ${bufferDays} Tage vor dem sicheren Zustelltag – so lange braucht die Zustellung.`;

  const shiftsForward = deadline.deadline_kind !== "kuendigungsfrist";
  const state = germanStateLabel(policy.recipient_state);

  return (
    <section className="card insurance-derivation" aria-labelledby="insurance-derivation-title">
      <header className="insurance-derivation-head">
        <div>
          <h3 id="insurance-derivation-title">Wie diese Frist zustande kommt</h3>
          <p className="insurance-derivation-subject">
            {policy.label ?? policy.line_label} · {policy.insurer_name} ·{" "}
            {DEADLINE_KIND_LABEL[deadline.deadline_kind]}
          </p>
        </div>
        <div className="insurance-derivation-actions">
          {onOpenPolicy && (
            <button type="button" className="btn-secondary" onClick={() => onOpenPolicy(policy.id)}>
              Police öffnen
            </button>
          )}
          {onClose && (
            <button type="button" className="btn-secondary" onClick={onClose}>
              Schließen
            </button>
          )}
        </div>
      </header>

      <ol className="insurance-chain">
        <ChainStep
          step={1}
          label="Hauptfälligkeit"
          value={date(policy.main_due_on, "nicht erfasst")}
          origin={DATE_SOURCE_LABEL[policy.main_due_source]}
          tone={mainDueTone}
          note="Der Beginn des Versicherungsjahres, nicht sein Ende – die laufende Periode endet am Tag davor."
        />
        <ChainStep
          step={2}
          label="Kündigungsfrist"
          value={noticeValue}
          origin={noticeOrigin}
          tone={noticeTone}
          note={`Gerechnet wird ${NOTICE_ANCHOR_LABEL[policy.notice_anchor]}, ${
            ANCHOR_BASIS_PHRASE[policy.anchor_basis_kind]
          }.`}
        />
        <ChainStep
          step={3}
          label="Rechtliches Fristende"
          value={date(deadline.deadline_legal_on, "nicht berechenbar – Angabe fehlt")}
          origin={CERTAINTY_LABEL[resultTone]}
          tone={resultTone}
          note={
            shiftsForward
              ? `Für ${DEADLINE_KIND_PLURAL[deadline.deadline_kind]} gilt § 193 BGB: fällt das Fristende auf einen Samstag, Sonntag oder Feiertag, tritt der nächste Werktag an seine Stelle.`
              : "§ 193 BGB ist auf Kündigungsfristen weder unmittelbar noch entsprechend anwendbar (BGH, Urteil vom 17.02.2005 – III ZR 172/04). Das Fristende wandert deshalb nicht auf den nächsten Werktag; eine Kündigung danach wäre verspätet."
          }
        />
        <ChainStep
          step={4}
          label="Letzter sicherer Zustelltag"
          value={date(deadline.safe_service_on, "nicht berechenbar")}
          origin={CERTAINTY_LABEL[resultTone]}
          tone={resultTone}
          note={`${SERVICE_NOTE[deadline.deadline_kind]}${
            deadline.safe_service_holiday
              ? ` Hier verschiebt ihn ${deadline.safe_service_holiday}.`
              : ""
          }`}
        />
        <ChainStep
          step={5}
          label="Empfohlen absenden bis"
          value={date(deadline.recommended_action_on, "nicht berechenbar")}
          origin={CERTAINTY_LABEL[resultTone]}
          tone={resultTone}
          note={channelNote}
        />
      </ol>

      <div className="insurance-derivation-outcome">
        {/* "Wirksam zum" beschreibt eine Kuendigung. Bei den uebrigen Fristarten
            endet an diesem Tag nur die laufende Periode - die Police laeuft
            weiter, und ein Wirksamkeitsversprechen waere schlicht falsch. */}
        <span className="insurance-chain-label">
          {deadline.deadline_kind === "kuendigungsfrist"
            ? "Wirksam zum"
            : "Ende der laufenden Periode"}
        </span>{" "}
        <strong>{date(deadline.term_ends_on, "nicht berechenbar")}</strong>
        <p className="insurance-chain-note">
          Bis dahin läuft die Versicherung weiter. {RESCUE_NOTE[deadline.deadline_kind]}
        </p>
      </div>

      <dl className="insurance-derivation-basis">
        <div className="insurance-kv-row">
          <dt>Grundlage</dt>
          <dd>
            {ANCHOR_BASIS_KIND_LABEL[policy.anchor_basis_kind]}
            {deadline.basis && <span className="insurance-kv-origin">{deadline.basis}</span>}
          </dd>
        </div>
        <div className="insurance-kv-row">
          <dt>Feiertagskalender</dt>
          <dd>
            {state ?? "kein Bundesland erfasst"}
            <span className="insurance-kv-origin">
              {state
                ? "Sitz des Empfängers – § 193 BGB stellt auf den Feiertag am Erklärungsort ab."
                : "Ohne Bundesland des Empfängers zählen nur die bundesweiten Feiertage. Die Angabe gehört an die Police, nicht in die Einstellungen."}
            </span>
          </dd>
        </div>
      </dl>

      {deadline.basis_note && <p className="insurance-note">{deadline.basis_note}</p>}
      {deadline.reason && <p className="insurance-note">{deadline.reason}</p>}
      {deadline.action_hint && <p className="insurance-note">{deadline.action_hint}</p>}

      {resultTone !== "confirmed" && (
        <p className="insurance-derivation-warning">
          {resultTone === "not_computable"
            ? "Solange eine Angabe dieser Kette fehlt, gibt es hier kein Datum – geraten wird nicht."
            : "Solange ein Glied dieser Kette angenommen ist, bleibt das Ergebnis eine Annahme: kein Countdown, keine Ampel, keine Erinnerung."}
        </p>
      )}
    </section>
  );
}
