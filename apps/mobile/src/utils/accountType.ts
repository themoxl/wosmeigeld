import type { AccountType } from "../api/types";

// Deutsche Anzeige-Labels fuer die 8 erlaubten Kontotypen (siehe
// FINANZ_APP_ARCHITEKTURPLAN.md Abschnitt 6.2 und Auftrag).
export const ACCOUNT_TYPE_LABELS: Record<AccountType, string> = {
  checking: "Girokonto",
  credit_card: "Kreditkarte",
  savings: "Tagesgeld/Sparen",
  cash: "Bargeld",
  brokerage: "Depot",
  loan: "Darlehen",
  real_estate: "Immobilie",
  other: "Sonstiges",
};

export const ACCOUNT_TYPE_OPTIONS: { value: AccountType; label: string }[] = (
  Object.keys(ACCOUNT_TYPE_LABELS) as AccountType[]
).map((value) => ({ value, label: ACCOUNT_TYPE_LABELS[value] }));

// Account.account_type ist backendseitig plain string (kein Literal-Union -
// die DB kennt dafuer aber ein Postgres-Enum, siehe
// supabase/migrations/0001_schema.sql), deshalb hier mit Fallback statt
// direkter Indizierung.
export function getAccountTypeLabel(accountType: string): string {
  return ACCOUNT_TYPE_LABELS[accountType as AccountType] ?? accountType;
}
