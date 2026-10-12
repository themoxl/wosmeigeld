import { useMemo, useState } from "react";
import { gridColumnFromField } from "../../lib/explorerGrid";
import { GridPopover } from "../datagrid/GridPopover";
import { ExplorerColumnMenu } from "./ExplorerColumnMenu";
import type { AnalyticsCondition, AnalyticsQuery, FieldMeta } from "../../lib/types";

// "Filter hinzufuegen" ueber den vollstaendigen Feldkatalog.
//
// Ohne diesen Weg waeren nur die Felder filterbar, die gerade als Spalte
// sichtbar sind - und in der Gruppenansicht gar keines, weil dort keine
// Zeilenspalten stehen. Der Katalog ist derselbe, aus dem der Server seine
// Bedingungen zulaesst; was hier auftaucht, funktioniert also auch.

interface AddFilterControlProps {
  fields: FieldMeta[];
  query: AnalyticsQuery;
  onConditionChange: (condition: AnalyticsCondition | null) => void;
}

export function AddFilterControl({ fields, query, onConditionChange }: AddFilterControlProps) {
  const [open, setOpen] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);

  const filterable = useMemo(() => fields.filter((field) => field.filterable !== false), [fields]);

  const visible = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return filterable;
    return filterable.filter((field) => field.label.toLocaleLowerCase().includes(needle));
  }, [filterable, search]);

  const selected = selectedKey ? filterable.find((field) => field.key === selectedKey) : undefined;

  function close() {
    setOpen(false);
    setSelectedKey(null);
    setSearch("");
  }

  return (
    <>
      <button
        ref={setTrigger}
        type="button"
        className="btn-secondary"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => (open ? close() : setOpen(true))}
      >
        + Filter
      </button>

      {open && (
        <GridPopover anchor={trigger} onClose={close} label="Filter hinzufügen">
          {selected ? (
            <div className="grid-menu">
              <div className="grid-menu-breadcrumb">
                <button
                  type="button"
                  className="grid-menu-link"
                  onClick={() => setSelectedKey(null)}
                >
                  ‹ Feld wechseln
                </button>
                <strong>{selected.label}</strong>
              </div>
              <ExplorerColumnMenu
                column={{ ...gridColumnFromField(selected), sortable: false }}
                query={query}
                onConditionChange={(condition) => onConditionChange(condition)}
                onClose={close}
              />
            </div>
          ) : (
            <div className="grid-menu">
              <section className="grid-menu-section">
                <h3 className="grid-menu-title">Feld wählen</h3>
                <input
                  className="form-input"
                  autoFocus
                  placeholder="Feld suchen…"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  aria-label="Feld suchen"
                />
                <ul className="grid-menu-list">
                  {visible.map((field) => (
                    <li key={field.key}>
                      <button
                        type="button"
                        className="grid-menu-action"
                        onClick={() => setSelectedKey(field.key)}
                      >
                        {field.label}
                      </button>
                    </li>
                  ))}
                  {visible.length === 0 && <li className="grid-menu-hint">Kein passendes Feld.</li>}
                </ul>
              </section>
            </div>
          )}
        </GridPopover>
      )}
    </>
  );
}
