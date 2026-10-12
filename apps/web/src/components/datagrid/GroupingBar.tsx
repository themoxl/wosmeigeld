// Die Gruppierungsleiste: welche Dimensionen in welcher Reihenfolge die
// Zeilen schachteln.
//
// Reihenfolge per Schaltflaeche statt per Drag & Drop - dieselbe Wirkung,
// aber ohne Zeigergeraet bedienbar und ohne zusaetzliche Abhaengigkeit. Die
// Reihenfolge ist keine Kosmetik: "Kategorie je Monat" und "Monat je
// Kategorie" beantworten verschiedene Fragen.

interface GroupingBarProps {
  groups: { key: string; label: string }[];
  available: { key: string; label: string }[];
  onAdd: (key: string) => void;
  onRemove: (key: string) => void;
  onMove: (key: string, delta: -1 | 1) => void;
  onClear: () => void;
  /** Erklaerender Zusatz rechts, z. B. der Hinweis auf die Kennzahlen. */
  note?: string;
}

export function GroupingBar({
  groups,
  available,
  onAdd,
  onRemove,
  onMove,
  onClear,
  note,
}: GroupingBarProps) {
  return (
    <div className="grid-grouping" role="group" aria-label="Gruppierung">
      <span className="grid-grouping-label">Gruppierung</span>

      {groups.length === 0 && (
        <span className="grid-grouping-empty">keine – die Tabelle zeigt Einzelbuchungen</span>
      )}

      {groups.map((group, index) => (
        <span key={group.key} className="grid-grouping-chip">
          <span className="grid-grouping-chip-order">{index + 1}</span>
          <span className="grid-grouping-chip-label">{group.label}</span>
          <button
            type="button"
            className="grid-grouping-chip-btn"
            onClick={() => onMove(group.key, -1)}
            disabled={index === 0}
            aria-label={`${group.label} eine Ebene nach oben`}
            title="Eine Ebene nach oben"
          >
            ◂
          </button>
          <button
            type="button"
            className="grid-grouping-chip-btn"
            onClick={() => onMove(group.key, 1)}
            disabled={index === groups.length - 1}
            aria-label={`${group.label} eine Ebene nach unten`}
            title="Eine Ebene nach unten"
          >
            ▸
          </button>
          <button
            type="button"
            className="grid-grouping-chip-btn grid-grouping-chip-btn--remove"
            onClick={() => onRemove(group.key)}
            aria-label={`Gruppierung nach ${group.label} entfernen`}
            title="Ebene entfernen"
          >
            ×
          </button>
        </span>
      ))}

      {available.length > 0 && (
        <select
          className="form-input grid-grouping-add"
          // Beschriftung ausdruecklich am Feld statt an einem umschliessenden
          // <label>: dessen Textinhalt wuerde den gewaehlten Optionstext
          // mitzaehlen, und der Name des Feldes aenderte sich mit jeder
          // Auswahl.
          aria-label="Gruppierungsebene hinzufügen"
          value=""
          onChange={(event) => {
            if (event.target.value) onAdd(event.target.value);
          }}
        >
          <option value="">+ Ebene…</option>
          {available.map((entry) => (
            <option key={entry.key} value={entry.key}>
              {entry.label}
            </option>
          ))}
        </select>
      )}

      {groups.length > 0 && (
        <button type="button" className="grid-menu-link" onClick={onClear}>
          Gruppierung aufheben
        </button>
      )}

      {note && <span className="grid-grouping-note">{note}</span>}
    </div>
  );
}
