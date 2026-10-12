import { useMemo, useState, type FormEvent } from "react";
import {
  NULL_VALUE_KEY,
  VALUE_LIST_OPERATORS,
  buildCondition,
  conditionInputs,
  conditionValueKeys,
  operatorSpec,
  operatorsFor,
  valueKeysToCondition,
} from "../../lib/gridFilters";
import type { AnalyticsCompareOp, AnalyticsCondition } from "../../lib/types";
import type { GridColumn, GridValueOption, SortDirection } from "./types";

// Der Inhalt eines Spaltenmenues: Sortierung, genau EIN Filter, Gruppierung,
// Spalte ausblenden.
//
// "Genau ein Filter je Spalte" ist keine Vereinfachung, sondern die Zusage des
// Filtermodells (`upsertCondition` ersetzt je Feld). Deshalb steht die
// Werteauswahl nicht neben der Bedingung, sondern IST eine Bedingung -
// erreichbar ueber die Operatoren "ist einer von" / "ist keiner von". Zwei
// nebeneinander bedienbare Filter derselben Spalte waeren nicht darstellbar
// und liessen sich in der Chip-Leiste nicht mehr einzeln zuruecknehmen.

interface ColumnFilterMenuProps {
  column: GridColumn;
  condition: AnalyticsCondition | null;
  onConditionChange: (condition: AnalyticsCondition | null) => void;
  sortDirection: SortDirection | null;
  onSortChange?: (direction: SortDirection | null) => void;
  grouped?: boolean;
  onToggleGroup?: () => void;
  onHide?: () => void;
  /** `undefined` = diese Spalte bietet keine Werteliste an. */
  values?: GridValueOption[];
  valuesLoading?: boolean;
  valuesCapped?: boolean;
  onValueSearchChange?: (search: string) => void;
  onClose: () => void;
}

export function ColumnFilterMenu({
  column,
  condition,
  onConditionChange,
  sortDirection,
  onSortChange,
  grouped,
  onToggleGroup,
  onHide,
  values,
  valuesLoading,
  valuesCapped,
  onValueSearchChange,
  onClose,
}: ColumnFilterMenuProps) {
  const isMoney = column.isMoney ?? false;
  const supportsValues = values !== undefined;

  const [op, setOp] = useState<AnalyticsCompareOp>(() => {
    if (condition) return condition.op;
    return supportsValues ? "in" : defaultOperator(column);
  });
  const [inputs, setInputs] = useState<[string, string]>(() =>
    conditionInputs(condition, column.kind, isMoney),
  );
  const [valueKeys, setValueKeys] = useState<string[]>(() => conditionValueKeys(condition));
  const [valueSearch, setValueSearch] = useState("");

  const operators = useMemo(
    () => [...(supportsValues ? VALUE_LIST_OPERATORS : []), ...operatorsFor(column.kind)],
    [supportsValues, column.kind],
  );

  const isValueMode = op === "in" || op === "not_in";
  const spec = operatorSpec(column.kind, op);

  const visibleValues = useMemo(() => {
    if (!values) return [];
    const needle = valueSearch.trim().toLocaleLowerCase();
    if (!needle) return values;
    return values.filter((entry) => entry.label.toLocaleLowerCase().includes(needle));
  }, [values, valueSearch]);

  function commitValueKeys(next: string[]) {
    setValueKeys(next);
    onConditionChange(valueKeysToCondition(column.key, isValueMode ? op : "in", next));
  }

  function toggleValue(key: string) {
    commitValueKeys(
      valueKeys.includes(key) ? valueKeys.filter((entry) => entry !== key) : [...valueKeys, key],
    );
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const next = buildCondition(column.key, column.kind, isMoney, op, inputs);
    onConditionChange(next);
    if (next) onClose();
  }

  function handleOperatorChange(nextOp: AnalyticsCompareOp) {
    setOp(nextOp);
    // Ein wertloser Operator (leer / gesetzt) greift sofort - auf eine
    // Eingabe zu warten, auf die es nicht ankommt, waere eine Leerstelle.
    if (operatorSpec(column.kind, nextOp).arity === 0) {
      onConditionChange({ field: column.key, op: nextOp, value: null });
      return;
    }
    if (nextOp === "in" || nextOp === "not_in") {
      onConditionChange(valueKeysToCondition(column.key, nextOp, valueKeys));
    }
  }

  return (
    <div className="grid-menu">
      {onSortChange && (
        <section className="grid-menu-section">
          <h3 className="grid-menu-title">Sortierung</h3>
          {column.sortable === false ? (
            <p className="grid-menu-hint">
              {column.sortHint ?? "Diese Spalte lässt sich nicht sortieren."}
            </p>
          ) : (
            <div className="grid-menu-row">
              <button
                type="button"
                className={sortButtonClass(sortDirection === "asc")}
                onClick={() => onSortChange("asc")}
              >
                ↑ Aufsteigend
              </button>
              <button
                type="button"
                className={sortButtonClass(sortDirection === "desc")}
                onClick={() => onSortChange("desc")}
              >
                ↓ Absteigend
              </button>
              <button
                type="button"
                className="grid-menu-link"
                onClick={() => onSortChange(null)}
                disabled={sortDirection === null}
              >
                Aufheben
              </button>
            </div>
          )}
        </section>
      )}

      {column.filterable !== false && (
        <section className="grid-menu-section">
          <h3 className="grid-menu-title">Filter</h3>
          <label className="grid-menu-field">
            <span className="form-label">Bedingung</span>
            <select
              className="form-input"
              value={op}
              onChange={(event) => handleOperatorChange(event.target.value as AnalyticsCompareOp)}
              aria-label={`Filteroperator für ${column.label}`}
            >
              {operators.map((entry) => (
                <option key={entry.op} value={entry.op}>
                  {entry.label}
                </option>
              ))}
            </select>
          </label>

          {isValueMode && supportsValues && (
            <div className="grid-menu-values">
              <input
                className="form-input"
                placeholder="Werte durchsuchen…"
                value={valueSearch}
                onChange={(event) => {
                  setValueSearch(event.target.value);
                  onValueSearchChange?.(event.target.value);
                }}
                aria-label={`Werte von ${column.label} durchsuchen`}
              />
              <div className="grid-menu-row">
                <button
                  type="button"
                  className="grid-menu-link"
                  onClick={() =>
                    commitValueKeys(visibleValues.map((entry) => entry.value ?? NULL_VALUE_KEY))
                  }
                >
                  Alle sichtbaren
                </button>
                <button
                  type="button"
                  className="grid-menu-link"
                  onClick={() => commitValueKeys([])}
                  disabled={valueKeys.length === 0}
                >
                  Keine
                </button>
              </div>

              {valuesLoading && <p className="grid-menu-hint">Lade Werte…</p>}
              {!valuesLoading && visibleValues.length === 0 && (
                <p className="grid-menu-hint">Keine Werte für diese Filterkombination.</p>
              )}

              <ul className="grid-menu-list">
                {visibleValues.map((entry) => {
                  const key = entry.value ?? NULL_VALUE_KEY;
                  return (
                    <li key={key}>
                      <label className="grid-menu-option">
                        <input
                          type="checkbox"
                          checked={valueKeys.includes(key)}
                          onChange={() => toggleValue(key)}
                        />
                        <span className="grid-menu-option-label">{entry.label}</span>
                        {typeof entry.count === "number" && (
                          <span className="grid-menu-option-count">{entry.count}</span>
                        )}
                      </label>
                    </li>
                  );
                })}
              </ul>

              {valuesCapped && (
                <p className="grid-menu-hint">
                  Nur die häufigsten Werte werden gezeigt – bitte suchen, um weitere zu finden.
                </p>
              )}
            </div>
          )}

          {!isValueMode && spec.arity > 0 && (
            <form className="grid-menu-form" onSubmit={handleSubmit}>
              {column.kind === "bool" ? (
                <select
                  className="form-input"
                  value={inputs[0] || "true"}
                  onChange={(event) => setInputs([event.target.value, inputs[1]])}
                  aria-label={`Wert für ${column.label}`}
                >
                  <option value="true">ja</option>
                  <option value="false">nein</option>
                </select>
              ) : (
                <input
                  className="form-input"
                  autoFocus
                  type={column.kind === "date" ? "date" : "text"}
                  inputMode={column.kind === "num" ? "decimal" : undefined}
                  placeholder={placeholderFor(column, isMoney)}
                  value={inputs[0]}
                  onChange={(event) => setInputs([event.target.value, inputs[1]])}
                  aria-label={`Wert für ${column.label}`}
                />
              )}

              {spec.arity === 2 && (
                <input
                  className="form-input"
                  type={column.kind === "date" ? "date" : "text"}
                  inputMode={column.kind === "num" ? "decimal" : undefined}
                  placeholder="bis"
                  value={inputs[1]}
                  onChange={(event) => setInputs([inputs[0], event.target.value])}
                  aria-label={`Obergrenze für ${column.label}`}
                />
              )}

              <button type="submit" className="btn-secondary grid-menu-apply">
                Filter übernehmen
              </button>
            </form>
          )}

          {condition && (
            <button
              type="button"
              className="grid-menu-link grid-menu-link--danger"
              onClick={() => {
                onConditionChange(null);
                setValueKeys([]);
                setInputs(["", ""]);
              }}
            >
              Filter dieser Spalte entfernen
            </button>
          )}
        </section>
      )}

      {(onToggleGroup || onHide) && (
        <section className="grid-menu-section grid-menu-section--actions">
          {onToggleGroup && (
            <button
              type="button"
              className="grid-menu-action"
              onClick={() => {
                onToggleGroup();
                onClose();
              }}
            >
              {grouped ? "Gruppierung dieser Spalte aufheben" : "Nach dieser Spalte gruppieren"}
            </button>
          )}
          {onHide && (
            <button
              type="button"
              className="grid-menu-action"
              onClick={() => {
                onHide();
                onClose();
              }}
            >
              Spalte ausblenden
            </button>
          )}
        </section>
      )}
    </div>
  );
}

function sortButtonClass(active: boolean): string {
  return active ? "grid-menu-toggle grid-menu-toggle--active" : "grid-menu-toggle";
}

function defaultOperator(column: GridColumn): AnalyticsCompareOp {
  return column.kind === "num" || column.kind === "date" || column.kind === "bool"
    ? "eq"
    : "contains";
}

function placeholderFor(column: GridColumn, isMoney: boolean): string {
  if (column.kind === "num") return isMoney ? "z. B. 100,00" : "Zahl";
  if (column.kind === "date") return "JJJJ-MM-TT";
  return "Text";
}
