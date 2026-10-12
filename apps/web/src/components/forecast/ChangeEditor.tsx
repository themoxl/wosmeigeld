import { useState } from "react";
import type { Contract, ForecastChangeType, ForecastContribution } from "../../lib/types";
import { MonthSelect } from "./MonthSelect";
import {
  AMOUNT_MODES,
  CHANGE_TEMPLATES,
  CHANGE_TYPES,
  CONFIDENCES,
  FREQUENCIES,
  draftIssue,
  emptyDraft,
  firstOfMonth,
  lastOfMonth,
  monthValueOf,
  nextDraftId,
  normalizeForType,
  syncAmountFromText,
  typeSpec,
  validateDraft,
  type ChangeDraft,
} from "./changeDrafts";

interface ChangeEditorProps {
  drafts: ChangeDraft[];
  /**
   * `sofort` unterscheidet eine fertige Entscheidung (Auswahl, Knopf) von
   * einem Zwischenstand beim Tippen: nur das eine rechnet ohne Verzoegerung.
   */
  onChange: (next: ChangeDraft[], options?: { sofort?: boolean }) => void;
  contracts: Contract[];
  /** Beitrag je aktivierter Zeile, vom Server aus eigenen Rechenläufen. */
  contributions: ForecastContribution[];
  /** Gehoeren die Beitraege noch zum aktuellen Stand der Zeilen? */
  contributionsFresh: boolean;
  startMonth: string;
  formatMoney: (minor: number) => string;
}

export function ChangeEditor({
  drafts,
  onChange,
  contracts,
  contributions,
  contributionsFresh,
  startMonth,
  formatMoney,
}: ChangeEditorProps) {
  const [templatesOpen, setTemplatesOpen] = useState(false);

  // Derselbe Filter, den auch die Seite fuer die Nutzlast benutzt - sonst
  // klebt die Wirkungsmeldung an der falschen Zeile, sobald eine aktivierte
  // Zeile noch unvollstaendig ist.
  const activeIndexOf = (id: string) =>
    drafts.filter((d) => d.enabled && validateDraft(d) === null).findIndex((d) => d.id === id);

  const patch = (
    id: string,
    updater: (d: ChangeDraft) => ChangeDraft,
    options?: { sofort?: boolean },
  ) => {
    onChange(
      drafts.map((d) => (d.id === id ? updater(d) : d)),
      options ?? { sofort: true },
    );
  };

  const applyTemplate = (key: string) => {
    const template = CHANGE_TEMPLATES.find((t) => t.key === key);
    if (!template) return;
    onChange([...drafts, ...template.build(startMonth)], { sofort: true });
    setTemplatesOpen(false);
  };

  return (
    <div>
      <div className="forecast-editor-toolbar">
        <button
          type="button"
          className="btn-secondary"
          onClick={() => onChange([...drafts, emptyDraft(startMonth)], { sofort: true })}
        >
          Änderung hinzufügen
        </button>
        <div className="forecast-templates">
          <button
            type="button"
            className="btn-secondary"
            aria-expanded={templatesOpen}
            onClick={() => setTemplatesOpen((open) => !open)}
          >
            Vorlage wählen
          </button>
          {templatesOpen ? (
            <ul className="forecast-template-menu" aria-label="Vorlagen">
              {CHANGE_TEMPLATES.map((t) => (
                <li key={t.key}>
                  <button type="button" onClick={() => applyTemplate(t.key)}>
                    <span className="forecast-template-name">{t.label}</span>
                    <span className="forecast-template-hint">{t.hint}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        {drafts.length > 0 ? (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => onChange([], { sofort: true })}
          >
            Alles zurücksetzen
          </button>
        ) : null}
      </div>

      {drafts.length === 0 ? (
        <p className="placeholder-note">
          Noch keine Änderung. Eine Vorlage ist der schnellste Weg.
        </p>
      ) : null}

      {drafts.map((d) => {
        const spec = typeSpec(d.change.change_type);
        const issue = draftIssue(d);
        const contribution = d.enabled
          ? contributions.find((c) => c.change_index === activeIndexOf(d.id))
          : undefined;
        const targets = contracts.filter((c) =>
          spec.targetDirection ? c.direction === spec.targetDirection : true,
        );

        return (
          <div key={d.id} className={`forecast-row ${d.enabled ? "" : "forecast-row--off"}`}>
            <div className="forecast-row-head">
              <input
                type="checkbox"
                checked={d.enabled}
                aria-label={`${d.change.label} berücksichtigen`}
                onChange={(event) => patch(d.id, (x) => ({ ...x, enabled: event.target.checked }))}
              />
              <input
                type="text"
                className="form-input forecast-row-label"
                value={d.change.label}
                aria-label="Bezeichnung"
                onChange={(event) =>
                  patch(
                    d.id,
                    (x) => ({ ...x, change: { ...x.change, label: event.target.value } }),
                    { sofort: false },
                  )
                }
              />
              <div className="forecast-row-tools">
                <button
                  type="button"
                  className="btn-secondary"
                  aria-label={`${d.change.label} kopieren`}
                  onClick={() =>
                    onChange(
                      [
                        ...drafts,
                        {
                          ...d,
                          id: nextDraftId(),
                          change: { ...d.change, label: `${d.change.label} (Kopie)` },
                        },
                      ],
                      { sofort: true },
                    )
                  }
                >
                  Kopieren
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  aria-label={`${d.change.label} entfernen`}
                  onClick={() =>
                    onChange(
                      drafts.filter((x) => x.id !== d.id),
                      { sofort: true },
                    )
                  }
                >
                  Entfernen
                </button>
              </div>
            </div>

            <div className="forecast-row-fields">
              <label className="forecast-field">
                <span className="forecast-field-label">Art</span>
                <select
                  className="form-input"
                  value={d.change.change_type}
                  onChange={(event) =>
                    patch(d.id, (x) => ({
                      ...x,
                      change: syncAmountFromText(
                        normalizeForType(x.change, event.target.value as ForecastChangeType),
                        x.amountText,
                      ),
                    }))
                  }
                >
                  {CHANGE_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>

              {spec.needsTarget ? (
                <label className="forecast-field">
                  <span className="forecast-field-label">Ziel</span>
                  <select
                    className="form-input"
                    value={d.change.target_contract_id ?? ""}
                    onChange={(event) =>
                      patch(d.id, (x) => ({
                        ...x,
                        change: { ...x.change, target_contract_id: event.target.value || null },
                      }))
                    }
                  >
                    <option value="">— wählen —</option>
                    {targets.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              {spec.needsAmount ? (
                <>
                  <label className="forecast-field">
                    <span className="forecast-field-label">Wie</span>
                    <select
                      className="form-input"
                      value={d.change.amount_mode ?? "absolute"}
                      onChange={(event) =>
                        patch(d.id, (x) => ({
                          ...x,
                          change: syncAmountFromText(
                            {
                              ...x.change,
                              amount_mode: event.target.value as "absolute" | "delta" | "percent",
                            },
                            x.amountText,
                          ),
                        }))
                      }
                    >
                      {AMOUNT_MODES.filter((m) => spec.needsTarget || m.value === "absolute").map(
                        (m) => (
                          <option key={m.value} value={m.value}>
                            {m.label}
                          </option>
                        ),
                      )}
                    </select>
                  </label>

                  <label className="forecast-field">
                    <span className="forecast-field-label">
                      {d.change.amount_mode === "percent" ? "Prozent" : "Betrag"}
                    </span>
                    <span className="forecast-field-unit">
                      <input
                        type="text"
                        className="form-input form-input--mono"
                        value={d.amountText}
                        inputMode="decimal"
                        aria-label={`${d.change.amount_mode === "percent" ? "Prozentsatz" : "Betrag"} ${d.change.label}`}
                        onChange={(event) => {
                          const raw = event.target.value;
                          patch(
                            d.id,
                            (x) => ({
                              ...x,
                              amountText: raw,
                              change: syncAmountFromText(x.change, raw),
                            }),
                            { sofort: false },
                          );
                        }}
                      />
                      <span aria-hidden="true">
                        {d.change.amount_mode === "percent" ? "%" : "€"}
                      </span>
                    </span>
                  </label>
                </>
              ) : null}

              {d.change.change_type !== "one_off" ? (
                <label className="forecast-field">
                  <span className="forecast-field-label">Rhythmus</span>
                  <select
                    className="form-input"
                    value={d.change.frequency}
                    onChange={(event) =>
                      patch(d.id, (x) => ({
                        ...x,
                        change: {
                          ...x.change,
                          frequency: event.target.value as ChangeDraft["change"]["frequency"],
                        },
                      }))
                    }
                  >
                    {FREQUENCIES.filter((f) => f.value !== "once").map((f) => (
                      <option key={f.value} value={f.value}>
                        {f.label}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              {!spec.needsTarget && d.change.change_type !== "one_off" ? (
                <label className="forecast-field">
                  <span className="forecast-field-label">Fällig am</span>
                  <select
                    className="form-input"
                    value={String(d.change.due_day ?? 1)}
                    aria-label={`Fälligkeitstag ${d.change.label}`}
                    onChange={(event) =>
                      patch(d.id, (x) => ({
                        ...x,
                        change: { ...x.change, due_day: Number(event.target.value) },
                      }))
                    }
                  >
                    {Array.from({ length: 31 }, (_, index) => (
                      <option key={index + 1} value={String(index + 1)}>
                        {index + 1}.
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              {/* Kein <label>: die Gruppe enthaelt mehrere Bedienelemente, ein
                  Klick auf die Beschriftung wuerde sonst irgendwo landen. */}
              <div className="forecast-field forecast-field--month">
                <span className="forecast-field-label">Ab</span>
                <MonthSelect
                  value={monthValueOf(d.change.starts_on)}
                  ariaBase={`Beginn ${d.change.label}`}
                  anchorMonth={startMonth}
                  onChange={(month) =>
                    patch(d.id, (x) => ({
                      ...x,
                      change: { ...x.change, starts_on: firstOfMonth(month ?? startMonth) },
                    }))
                  }
                />
              </div>

              {d.change.change_type !== "one_off" ? (
                <div className="forecast-field forecast-field--month">
                  <span className="forecast-field-label">Bis (optional)</span>
                  <MonthSelect
                    value={d.change.ends_on ? monthValueOf(d.change.ends_on) : null}
                    ariaBase={`Ende ${d.change.label}`}
                    anchorMonth={startMonth}
                    min={monthValueOf(d.change.starts_on)}
                    allowEmpty
                    onChange={(month) =>
                      patch(d.id, (x) => ({
                        ...x,
                        change: {
                          ...x.change,
                          // Letzter Tag des Monats: der Rechenkern hoert bei
                          // ends_on auf, ein Monatserster verschluckt sonst
                          // die Zahlung des gewaehlten Monats.
                          ends_on: month ? lastOfMonth(month) : null,
                        },
                      }))
                    }
                  />
                </div>
              ) : null}

              <label className="forecast-field">
                <span className="forecast-field-label">Sicherheit</span>
                <select
                  className="form-input"
                  value={d.change.confidence}
                  onChange={(event) =>
                    patch(d.id, (x) => ({
                      ...x,
                      change: {
                        ...x.change,
                        confidence: event.target.value as ChangeDraft["change"]["confidence"],
                      },
                    }))
                  }
                >
                  {CONFIDENCES.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {issue?.kind === "widerspruch" ? (
              <p className="forecast-row-error" role="alert">
                {issue.message}
              </p>
            ) : issue ? (
              // Noch nicht fertig ist kein Fehler: ruhig anzeigen, nicht
              // vorlesen lassen, nichts rot faerben. Dass die Zeile nicht
              // mitgerechnet wird, sagt die Statusspalte.
              <p className="forecast-row-hint">{issue.message}</p>
            ) : contribution && contributionsFresh ? (
              <div
                className={`forecast-change-effect ${
                  contribution.monthly_minor < 0
                    ? "forecast-change-effect--cost"
                    : contribution.resolves_shortfall
                      ? "forecast-change-effect--solves"
                      : "forecast-change-effect--partial"
                }`}
              >
                {contribution.monthly_minor < 0
                  ? `Kostet ${formatMoney(-contribution.monthly_minor)} im Monat.`
                  : contribution.resolves_shortfall
                    ? `Beseitigt den Engpass allein — ${formatMoney(contribution.monthly_minor)} im Monat.`
                    : contribution.shift_days && contribution.shift_days > 0
                      ? `Verschiebt den Engpass um ${contribution.shift_days} Tage.`
                      : "Verhindert den Engpass nicht."}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
