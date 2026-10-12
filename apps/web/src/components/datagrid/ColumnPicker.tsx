import { useState } from "react";
import { GridPopover } from "./GridPopover";

// Spaltenwahl und -reihenfolge (docs/ANALYSE-EXPLORER-PLAN.md, C.2).
//
// Die Auswahl liegt bewusst beim Aufrufer: der Explorer legt sie in der URL
// ab (teilbare Ansicht), das Buchungsregister im lokalen Speicher. Diese
// Komponente kennt nur die Liste und meldet Aenderungen.

interface ColumnPickerProps {
  columns: { key: string; label: string; locked?: boolean }[];
  /** Sichtbare Spalten in Anzeigereihenfolge. */
  visible: string[];
  onChange: (keys: string[]) => void;
  onReset?: () => void;
  label?: string;
}

export function ColumnPicker({
  columns,
  visible,
  onChange,
  onReset,
  label = "Spalten",
}: ColumnPickerProps) {
  const [open, setOpen] = useState(false);
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);

  // Sichtbare zuerst (in ihrer Reihenfolge), danach die ausgeblendeten -
  // sonst muesste man die Reihenfolge in einer Liste bearbeiten, deren
  // Positionen nicht der Tabelle entsprechen.
  const ordered = [
    ...visible
      .map((key) => columns.find((column) => column.key === key))
      .filter((column): column is (typeof columns)[number] => Boolean(column)),
    ...columns.filter((column) => !visible.includes(column.key)),
  ];

  function toggle(key: string) {
    onChange(
      visible.includes(key)
        ? visible.filter((entry) => entry !== key)
        : // Beim Einblenden an die Stelle setzen, an der die Spalte im
          // Katalog steht - sonst landet jede neu gezeigte Spalte am Ende.
          columns
            .filter((column) => column.key === key || visible.includes(column.key))
            .map((column) => column.key),
    );
  }

  function move(key: string, delta: -1 | 1) {
    const index = visible.indexOf(key);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= visible.length) return;
    const next = [...visible];
    const [entry] = next.splice(index, 1);
    if (entry) next.splice(target, 0, entry);
    onChange(next);
  }

  return (
    <>
      <button
        ref={setTrigger}
        type="button"
        className="btn-secondary"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        ▤ {label} ({visible.length})
      </button>

      {open && (
        <GridPopover
          anchor={trigger}
          onClose={() => setOpen(false)}
          label="Spalten wählen"
          align="end"
        >
          <div className="grid-menu">
            <section className="grid-menu-section">
              <h3 className="grid-menu-title">Spalten und Reihenfolge</h3>
              <ul className="grid-menu-list grid-menu-list--columns">
                {ordered.map((column) => {
                  const isVisible = visible.includes(column.key);
                  return (
                    <li key={column.key} className="grid-menu-column">
                      <label className="grid-menu-option">
                        <input
                          type="checkbox"
                          checked={isVisible}
                          disabled={column.locked}
                          onChange={() => toggle(column.key)}
                        />
                        <span className="grid-menu-option-label">{column.label}</span>
                      </label>
                      <span className="grid-menu-column-actions">
                        <button
                          type="button"
                          className="grid-grouping-chip-btn"
                          disabled={!isVisible || visible.indexOf(column.key) === 0}
                          onClick={() => move(column.key, -1)}
                          aria-label={`${column.label} nach links`}
                          title="Nach links"
                        >
                          ◂
                        </button>
                        <button
                          type="button"
                          className="grid-grouping-chip-btn"
                          disabled={
                            !isVisible || visible.indexOf(column.key) === visible.length - 1
                          }
                          onClick={() => move(column.key, 1)}
                          aria-label={`${column.label} nach rechts`}
                          title="Nach rechts"
                        >
                          ▸
                        </button>
                      </span>
                    </li>
                  );
                })}
              </ul>
              {onReset && (
                <button type="button" className="grid-menu-link" onClick={onReset}>
                  Auf Standard zurücksetzen
                </button>
              )}
            </section>
          </div>
        </GridPopover>
      )}
    </>
  );
}
