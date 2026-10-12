import { useState } from "react";
import type { ExtractedStatementItem } from "../../lib/reimbursementsApi";
import { parseAmountToMinorUnits } from "../../lib/money";

const MONEY_FIELDS = [
  ["billed_minor", "Rechnungsbetrag"],
  ["approved_minor", "Bewilligt"],
  ["deductible_minor", "Selbstbehalt"],
  ["noncovered_minor", "Nicht gedeckt"],
] as const;

export function StatementItemsEditor({
  items,
  onChange,
  onValidity,
  onLocate,
  linkedIndices = [],
}: {
  items: ExtractedStatementItem[];
  onChange: (items: ExtractedStatementItem[]) => void;
  onValidity: (valid: boolean) => void;
  onLocate: (field: string) => void;
  linkedIndices?: number[];
}) {
  const [drafts, setDrafts] = useState(() =>
    items.map((item) =>
      Object.fromEntries(
        MONEY_FIELDS.map(([key]) => [
          key,
          item[key] == null ? "" : (item[key] / 100).toFixed(2).replace(".", ","),
        ]),
      ),
    ),
  );
  const changeMoney = (index: number, key: (typeof MONEY_FIELDS)[number][0], value: string) => {
    const next = drafts.map((draft, i) => (i === index ? { ...draft, [key]: value } : draft));
    setDrafts(next);
    onValidity(
      next.every((draft) =>
        MONEY_FIELDS.every(
          ([field]) => !draft[field]?.trim() || (parseAmountToMinorUnits(draft[field]) ?? -1) >= 0,
        ),
      ),
    );
    onChange(
      items.map((item, i) =>
        i === index
          ? { ...item, [key]: value.trim() ? parseAmountToMinorUnits(value) : null }
          : item,
      ),
    );
  };
  const changeText = (index: number, key: keyof ExtractedStatementItem, value: string) =>
    onChange(
      items.map((item, i) => (i === index ? { ...item, [key]: value.trim() || null } : item)),
    );
  return (
    <fieldset className="reimbursement-statement-items">
      <legend>Einzelne Rechnungen im Bescheid</legend>
      <p>
        Je Rechnung eine Position. Bewilligte Beträge werden erst beim Bankabgleich zu
        Zahlungseingängen.
      </p>
      {items.map((item, index) => (
        <fieldset key={index} className="reimbursement-statement-item">
          <legend>Rechnung {index + 1}</legend>
          {(
            [
              ["person_name", "Person"],
              ["provider_name", "Praxis / Anbieter"],
              ["invoice_number", "Rechnungsnummer"],
              ["invoice_date", "Rechnungsdatum"],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              <span>{label}</span>
              <input
                className="form-input"
                type={key === "invoice_date" ? "date" : "text"}
                value={item[key] ?? ""}
                onChange={(event) => changeText(index, key, event.target.value)}
              />
            </label>
          ))}
          {MONEY_FIELDS.map(([key, label]) => (
            <label key={key}>
              <span>{label}</span>
              <input
                className="form-input"
                inputMode="decimal"
                value={drafts[index]?.[key] ?? ""}
                onChange={(event) => changeMoney(index, key, event.target.value)}
              />
              <button
                type="button"
                className="reimbursement-location-link"
                disabled={item.source_index === null}
                onClick={() => onLocate(`items.${item.source_index ?? index}.${key}`)}
              >
                Fundstelle anzeigen
              </button>
            </label>
          ))}
          <button
            type="button"
            className="btn-secondary"
            disabled={linkedIndices.includes(item.source_index ?? index)}
            onClick={() => {
              const remaining = drafts.filter((_, i) => i !== index);
              setDrafts(remaining);
              onChange(items.filter((_, i) => i !== index));
              onValidity(
                remaining.every((draft) =>
                  MONEY_FIELDS.every(
                    ([key]) =>
                      !draft[key]?.trim() || (parseAmountToMinorUnits(draft[key]) ?? -1) >= 0,
                  ),
                ),
              );
            }}
          >
            Position entfernen
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        className="btn-secondary"
        disabled={items.length >= 200}
        onClick={() => {
          setDrafts([...drafts, Object.fromEntries(MONEY_FIELDS.map(([key]) => [key, ""]))]);
          onChange([
            ...items,
            {
              person_name: null,
              provider_name: null,
              invoice_number: null,
              invoice_date: null,
              billed_minor: null,
              approved_minor: null,
              deductible_minor: null,
              noncovered_minor: null,
              page_number: null,
              source_index: null,
            },
          ]);
        }}
      >
        Weitere Rechnung erfassen
      </button>
    </fieldset>
  );
}
