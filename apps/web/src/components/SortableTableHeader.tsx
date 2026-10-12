/**
 * Klickbarer Spaltenkopf im Stil von GroupedResultTable (Explorer): dieselben
 * Klassen (`grid-th-inner`/`grid-th-label`/`grid-th-text`/`grid-th-sort`),
 * damit sortierbare Tabellen in der App gleich aussehen und sich gleich
 * bedienen - unabhaengig davon, welche Sortierschluessel eine Tabelle kennt
 * (deshalb generisch statt an einen festen Schluesseltyp gebunden).
 */
export function SortableTableHeader<K extends string>({
  label,
  sortKey,
  active,
  direction,
  onSort,
  align,
}: {
  label: string;
  sortKey: K;
  active: boolean;
  direction: "asc" | "desc";
  onSort: (key: K) => void;
  align?: "amount";
}) {
  return (
    <th className={`txn-th grid-th ${align === "amount" ? "txn-th--amount" : ""}`} scope="col">
      <span className={`grid-th-inner ${align === "amount" ? "grid-th-inner--end" : ""}`}>
        <button
          type="button"
          className="grid-th-label"
          aria-label={`Nach ${label} sortieren`}
          onClick={() => onSort(sortKey)}
        >
          <span className="grid-th-text">{label}</span>
          <span className="grid-th-sort" aria-hidden="true">
            {active ? (direction === "asc" ? "↑" : "↓") : ""}
          </span>
        </button>
      </span>
    </th>
  );
}
