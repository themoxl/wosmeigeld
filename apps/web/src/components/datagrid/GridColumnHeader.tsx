import { useId, useState, type ReactNode } from "react";
import { GridPopover } from "./GridPopover";
import type { GridColumn, SortDirection } from "./types";

// Ein interaktiver Spaltenkopf: Beschriftung (Klick sortiert), Sortier- und
// Filteranzeige, Menueschalter.
//
// Die zugaengliche Beschriftung der Kopfzelle wird ausdruecklich per
// `aria-label` auf das reine Spaltenlabel festgenagelt. Ohne das waere der
// Name der Kopfzelle die Summe ihres Textinhalts, also "Datum Sortieren
// Menue" - und jede Abfrage nach der Spalte "Datum" (Screenreader wie Test)
// ginge ins Leere.

interface GridColumnHeaderProps {
  column: GridColumn;
  className?: string;
  sortDirection: SortDirection | null;
  /** Klick auf die Beschriftung; ohne Angabe ist die Spalte nicht sortierbar. */
  onToggleSort?: () => void;
  /** true, wenn auf dieser Spalte ein Filter liegt. */
  filtered?: boolean;
  grouped?: boolean;
  /** Menueinhalt; ohne Angabe erscheint kein Menueschalter. */
  menu?: (close: () => void) => ReactNode;
}

export function GridColumnHeader({
  column,
  className,
  sortDirection,
  onToggleSort,
  filtered,
  grouped,
  menu,
}: GridColumnHeaderProps) {
  const [open, setOpen] = useState(false);
  // Der Anker liegt bewusst im State und nicht in einem Ref: das Menue muss
  // sich neu positionieren, sobald das Element steht - ein Ref-Wert loest
  // kein Rendern aus, und die Lintregeln verbieten seine Auswertung waehrend
  // des Renderns ohnehin.
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);
  const menuId = useId();

  const sortable = column.sortable !== false && Boolean(onToggleSort);

  return (
    <th
      className={`txn-th grid-th ${className ?? ""}`}
      aria-label={column.label}
      aria-sort={
        sortDirection === "asc" ? "ascending" : sortDirection === "desc" ? "descending" : "none"
      }
      scope="col"
    >
      <span className="grid-th-inner">
        {sortable ? (
          <button
            type="button"
            className="grid-th-label"
            onClick={onToggleSort}
            // Die Beschriftung bleibt fest, obwohl der Pfeil im Inhalt
            // wechselt: der Name der Schaltflaeche soll die AKTION benennen.
            // Die aktuelle Richtung steht als aria-sort an der Kopfzelle.
            aria-label={`Nach ${column.label} sortieren`}
            title={`Nach ${column.label} sortieren`}
          >
            <span className="grid-th-text">{column.label}</span>
            <span className="grid-th-sort" aria-hidden="true">
              {sortDirection === "asc" ? "↑" : sortDirection === "desc" ? "↓" : ""}
            </span>
          </button>
        ) : (
          <span className="grid-th-label grid-th-label--static">
            <span className="grid-th-text">{column.label}</span>
          </span>
        )}

        {grouped && (
          <span
            className="grid-th-flag"
            title="Nach dieser Spalte wird gruppiert"
            aria-hidden="true"
          >
            ⌸
          </span>
        )}

        {menu && (
          <button
            ref={setTrigger}
            type="button"
            id={menuId}
            className={`grid-th-menu ${filtered ? "grid-th-menu--active" : ""}`}
            aria-label={`${column.label}: filtern, sortieren, gruppieren`}
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            <span aria-hidden="true">{filtered ? "⏷●" : "⏷"}</span>
          </button>
        )}
      </span>

      {open && menu && (
        <GridPopover
          anchor={trigger}
          onClose={() => setOpen(false)}
          label={`Spaltenmenü ${column.label}`}
          align="end"
        >
          {menu(() => setOpen(false))}
        </GridPopover>
      )}
    </th>
  );
}
