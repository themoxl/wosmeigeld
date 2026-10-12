import type { FundingMatchStatus } from "./types";

export const FUNDING_STATUS_LABELS: Record<FundingMatchStatus, string> = {
  eligible: "Förderfähig",
  potentially_eligible: "Möglicherweise förderfähig",
  ineligible: "Nicht förderfähig",
  unknown: "Unklar",
};

export const FUNDING_STATUS_BADGE_CLASS: Record<FundingMatchStatus, string> = {
  eligible: "status-badge--positive",
  potentially_eligible: "status-badge--warning",
  ineligible: "status-badge--error",
  unknown: "status-badge--neutral",
};

/** Antragstellerarten, wie sie in `funding_program_versions.applicant_types` stehen.
 *
 * Die Vorauswahl im Matching filtert nur, wenn beide Seiten gesetzt sind: ein Profil
 * ohne Angabe passt auf jedes Programm. Deshalb ist "Keine Angabe" der Ausgangswert
 * und nicht `private_individual` — eine geratene Antragstellerart würde Programme
 * stillschweigend ausblenden.
 */
export const APPLICANT_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "private_individual", label: "Privatperson" },
  { value: "self_employed", label: "Selbstständig / freiberuflich" },
  { value: "company", label: "Unternehmen" },
  { value: "association", label: "Verein / gemeinnützige Einrichtung" },
  { value: "municipality", label: "Kommune / öffentliche Einrichtung" },
];

export const NORMALIZATION_STATUS_LABELS: Record<string, string> = {
  discovery_only: "Nur Fundstelle",
  rules_partial: "Regeln teilweise recherchiert",
  rules_complete: "Regeln vollständig",
  retired: "Nicht mehr angeboten",
};

export const BENEFIT_TYPE_LABELS: Record<string, string> = {
  grant: "Zuschuss",
  loan: "Darlehen",
  loan_with_grant: "Darlehen mit Zuschuss",
  guarantee: "Bürgschaft",
  tax: "Steuerlicher Vorteil",
  information: "Nur Information",
};

export const FUNDING_SCOPE_LABELS: Record<string, string> = {
  supranational: "EU",
  national: "Bundesweit",
  regional: "Land",
  municipal: "Kommunal",
};
