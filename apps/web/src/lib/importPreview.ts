import type { ImportRow } from "./types";

export type MappingFieldKey = "booked_date" | "amount" | "payee" | "purpose";
export type MappingDraft = Partial<Record<MappingFieldKey, string>>;

export const MAPPING_FIELDS: { key: MappingFieldKey; label: string; required: boolean }[] = [
  { key: "booked_date", label: "Datum", required: true },
  { key: "amount", label: "Betrag", required: true },
  { key: "payee", label: "Empfänger", required: false },
  { key: "purpose", label: "Verwendungszweck", required: false },
];

const ALIASES: Record<MappingFieldKey, string[]> = {
  booked_date: [
    "datum",
    "buchungstag",
    "buchungsdatum",
    "date",
    "booking date",
    "valuta",
    "valutadatum",
    "wertstellung",
    "wertstellungsdatum",
    "umsatzdatum",
    "transaktionsdatum",
    "belegdatum",
  ],
  amount: [
    "betrag",
    "betrag_eur",
    "amount",
    "umsatz",
    "wert",
    "betrag (eur)",
    "betrag eur",
    "betrag in eur",
    "buchungsbetrag",
    "umsatzbetrag",
    "soll/haben",
    "value",
  ],
  payee: [
    "empfaenger",
    "empfänger",
    "zahlungsempfänger",
    "name zahlungsbeteiligter",
    "zahlungspflichtiger",
    "begünstigter/zahlungspflichtiger",
    "empfänger/auftraggeber",
    "empfaenger/auftraggeber",
    "payee",
    "name",
    "empfaenger/zweck",
    "auftraggeber/empfänger",
    "beguenstigter/zahlungspflichtiger",
    "haendler",
    "händler",
    "merchant",
    "beschreibung",
    "description",
  ],
  purpose: [
    "verwendungszweck",
    "zweck",
    "buchungstext",
    "purpose",
    "notiz",
    "details",
    "beschreibung",
  ],
};

export function inferMapping(columns: string[]): MappingDraft {
  const normalized = new Map(
    columns.map((column) => [column.trim().toLocaleLowerCase("de"), column]),
  );
  return Object.fromEntries(
    MAPPING_FIELDS.map(({ key }) => [
      key,
      ALIASES[key].map((alias) => normalized.get(alias)).find(Boolean) ?? "",
    ]),
  );
}

/** Calendar validation prevents JavaScript silently turning 31 February into March. */
export function previewDate(value: unknown): string | null {
  const raw = String(value ?? "").trim();
  const iso = raw.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?$/,
  );
  const local = raw.match(/^(\d{1,2})([./])(\d{1,2})\2(\d{4}|\d{2})$/);
  if (!iso && !local) return null;
  if (local?.[2] === "/" && local[4]?.length !== 4) return null;
  const time = raw.match(/[T ](\d{2}):(\d{2}):(\d{2})/);
  if (time && (Number(time[1]) > 23 || Number(time[2]) > 59 || Number(time[3]) > 59)) return null;
  let year = Number(iso?.[1] ?? local?.[4]);
  if (year < 100) year += year >= 69 ? 1900 : 2000;
  const month = Number(iso?.[2] ?? local?.[3]);
  const day = Number(iso?.[3] ?? local?.[1]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    year < 100 ||
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  )
    return null;
  return `${String(day).padStart(2, "0")}.${String(month).padStart(2, "0")}.${year}`;
}

/** Match bank amount conventions, but flag ambiguous/over-precise values instead of rounding the preview. */
export function previewAmount(value: unknown): number | null {
  let raw = String(value ?? "").trim();
  const negative = raw.startsWith("-") || raw.endsWith("-");
  raw = raw.replace(/^[-+]|-$/g, "").replace(/€| /g, "");
  if (raw.includes(",")) {
    if (!/^(?:\d+|\d{1,3}(?:\.\d{3})+),\d{1,2}$/.test(raw)) return null;
    raw = raw.replace(/\./g, "").replace(",", ".");
  } else if (!/^\d+(?:\.\d{1,2})?$/.test(raw)) return null;
  const [whole, fraction = ""] = raw.split(".");
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  return Number.isSafeInteger(cents) ? cents * (negative ? -1 : 1) : null;
}

export function previewRow(row: Pick<ImportRow, "raw_data">, draft: MappingDraft) {
  const source = (key: MappingFieldKey) =>
    draft[key] ? String(row.raw_data?.[draft[key]!] ?? "").trim() : "";
  return {
    date: previewDate(source("booked_date")),
    amount: previewAmount(source("amount")),
    payee: source("payee"),
    purpose: source("purpose") === source("payee") ? "" : source("purpose"),
  };
}
