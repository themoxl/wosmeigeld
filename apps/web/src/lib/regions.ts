/** Bundesländer nach ISO 3166-2:DE.
 *
 * Der Code landet in `major_projects.region_code` und wird beim Fördercheck gegen
 * `funding_programs.region_codes` geprüft (`_region_matches` im Matching-Dienst).
 * Ohne gesetzten Code trifft kein regionales Programm — deshalb wird das Feld im
 * Anlage-Wizard erfragt und nicht nur in den Stammdaten nachgereicht.
 *
 * Die Werte erfüllen den CHECK `^[A-Z]{2}-[A-Z0-9]{1,3}$` aus dem Schema.
 */
export const GERMAN_REGION_OPTIONS: { value: string; label: string }[] = [
  { value: "DE-BW", label: "Baden-Württemberg" },
  { value: "DE-BY", label: "Bayern" },
  { value: "DE-BE", label: "Berlin" },
  { value: "DE-BB", label: "Brandenburg" },
  { value: "DE-HB", label: "Bremen" },
  { value: "DE-HH", label: "Hamburg" },
  { value: "DE-HE", label: "Hessen" },
  { value: "DE-MV", label: "Mecklenburg-Vorpommern" },
  { value: "DE-NI", label: "Niedersachsen" },
  { value: "DE-NW", label: "Nordrhein-Westfalen" },
  { value: "DE-RP", label: "Rheinland-Pfalz" },
  { value: "DE-SL", label: "Saarland" },
  { value: "DE-SN", label: "Sachsen" },
  { value: "DE-ST", label: "Sachsen-Anhalt" },
  { value: "DE-SH", label: "Schleswig-Holstein" },
  { value: "DE-TH", label: "Thüringen" },
];

export function regionLabel(regionCode: string | null | undefined): string {
  if (!regionCode) return "—";
  return GERMAN_REGION_OPTIONS.find((entry) => entry.value === regionCode)?.label ?? regionCode;
}
