/**
 * Bedarfsprofil des Haushalts (docs/VERSICHERUNGEN-PLAN.md, 4.9 und 10.1; P6).
 *
 * **Dieses Formular hat genau eine heikle Eigenschaft, und sie ist der Grund
 * fuer fast jede Entscheidung darin: eine unbeantwortete Frage ist keine
 * Verneinung.** Wer nicht sagt, dass er keinen Oeltank hat, hat damit nicht
 * gesagt, dass er keinen hat. Ein Kaestchen mit Haken kann das nicht
 * unterscheiden - deshalb ist jede Ja/Nein-Frage hier eine Auswahl aus DREI
 * Werten, und der dritte ("keine Angabe") ist sichtbar ausgezeichnet, nicht
 * nur der Vorbelegungszustand eines leeren Feldes.
 *
 * Die Folge steht im Formular selbst und nicht im Kleingedruckten: eine offene
 * Frage erzeugt keinen Befund. Das ist die sichere Richtung - ein Befund ueber
 * einen ungepflegten Bestand ist schaedlicher als gar keiner (Plan 10.2, P6).
 *
 * **Das Personenprofil ist der sensibelste Datensatz des Moduls.** Erwerbsstatus,
 * Versichertenstatus und Nettoeinkommen fallen unter denselben Schutz wie die
 * Gesundheitspolicen. Die Oberflaeche baut dafuer KEIN zweites Rechtesystem:
 * sie zeigt genau die Personen, die der Server liefert, und laesst genau die
 * schreiben, die er als schreibbar meldet (`health_can_access_person` bzw.
 * `health_can_manage_person`, Plan 4.9).
 */

import { useState, type ReactNode } from "react";
import { useFormatDate } from "../../hooks/useProfile";
import { parseAmountToMinorUnits } from "../../lib/money";
import {
  ELEMENTAL_ZONE_LABEL,
  EMPLOYMENT_STATUS_LABEL,
  HEALTH_COVERAGE_LABEL,
  HOUSING_FORM_LABEL,
  needsFactLabel,
  PET_KIND_LABEL,
  TRAVEL_FREQUENCY_LABEL,
  type ElementalZone,
  type EmploymentStatus,
  type HealthCoverage,
  type HousingForm,
  type NeedsCompleteness,
  type NeedsPersonProfile,
  type NeedsPersonProfileInput,
  type NeedsProfile,
  type NeedsProfileInput,
  type PetKind,
  type TravelFrequency,
} from "../../lib/insuranceApi";

/** Die vier Tierarten in der Reihenfolge des Regelkatalogs: erst die beiden,
 *  die R14 ausloesen, dann die beiden, die es ausdruecklich nicht tun. */
const PET_KINDS: PetKind[] = ["dog", "horse", "cat", "small_animal"];

const HOUSING_FORMS: HousingForm[] = ["rent", "owner_occupied", "owner_rented_out", "none"];

const ELEMENTAL_ZONES: ElementalZone[] = ["zurs1", "zurs2", "zurs3", "zurs4", "unknown"];

const TRAVEL_FREQUENCIES: TravelFrequency[] = ["never", "occasional", "frequent"];

const EMPLOYMENT_STATUSES: EmploymentStatus[] = [
  "employed",
  "civil_servant",
  "self_employed",
  "apprentice",
  "student",
  "retired",
  "not_employed",
  "child",
];

const HEALTH_COVERAGES: HealthCoverage[] = ["gkv", "pkv", "pkv_beihilfe", "other"];

/** Beihilfebemessungssatz in Basispunkten - die drei Werte, die das Schema
 *  zulaesst (Plan 4.9). */
const BEIHILFE_RATES: number[] = [5000, 7000, 8000];

const UNSET = "";

const UNSET_NOTE = "ohne Angabe – dazu entsteht kein Befund";

/** Ein Ja/Nein-Feld hat drei Zustaende. Der leere String steht fuer "keine
 *  Angabe" und wird beim Speichern zu `null`, nicht zu `false`. */
function booleanToSelect(value: boolean | null): string {
  if (value === null) return UNSET;
  return value ? "yes" : "no";
}

function selectToBoolean(value: string): boolean | null {
  if (value === "yes") return true;
  if (value === "no") return false;
  return null;
}

function numberToInput(value: number | null): string {
  return value === null ? "" : String(value);
}

function minorToInput(minor: number | null): string {
  if (minor === null) return "";
  return (minor / 100).toLocaleString("de-DE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/**
 * Ganze Zahl aus einer Eingabe.
 *
 * Gibt `undefined` zurueck, wenn die Eingabe keine Zahl ist - das ist ein
 * Fehler und nicht dasselbe wie eine leere Eingabe (`null`, "keine Angabe").
 */
function parseInteger(raw: string): number | null | undefined {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  if (!/^\d+$/.test(trimmed)) return undefined;
  return Number(trimmed);
}

function parseMoney(raw: string): number | null | undefined {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const minor = parseAmountToMinorUnits(trimmed);
  if (minor === null || minor < 0) return undefined;
  return minor;
}

/** Kennzeichnung des Serverstands: aendert sie sich, baut React das Formular
 *  neu auf, statt Eingaben in einem Effekt nachzuziehen. */
function fingerprint(profile: NeedsProfile): string {
  return [
    profile.housing_form,
    profile.living_area_sqm,
    profile.elemental_zone,
    profile.has_oil_tank,
    profile.liquid_reserve_minor,
    profile.pets === null ? "null" : profile.pets.join("+"),
    profile.travel_frequency,
    profile.vehicle_count,
    profile.side_business,
    profile.volunteering,
    profile.version ?? "",
  ].join("|");
}

function personFingerprint(person: NeedsPersonProfile): string {
  return [
    person.insured_person_id,
    person.employment_status,
    person.net_income_minor,
    person.health_coverage,
    person.beihilfe_rate_bp,
    person.has_dependents,
    person.education_until,
    person.moved_out_on,
    person.version ?? "",
  ].join("|");
}

/** Die Regeln, die ohne diese Angabe nicht ausgewertet werden koennen. Sie
 *  kommen vom Server (`GET /needs/completeness`), nicht aus dem JSX - der
 *  Browser weiss nicht, welche Regel an welchem Feld haengt. */
function blockedRules(
  completeness: NeedsCompleteness | null,
  field: string,
  personId: string | null,
): string[] {
  if (!completeness) return [];
  return completeness.missing
    .filter((item) => item.field === field && (item.person_id ?? null) === personId)
    .flatMap((item) => item.blocks_rule_keys);
}

interface QuestionProps {
  id: string;
  label: string;
  answered: boolean;
  help?: string;
  blocks: string[];
  children: ReactNode;
}

/**
 * Der Rahmen jeder Frage.
 *
 * Er traegt den einzigen Zustand, den dieses Formular sichtbar machen muss:
 * beantwortet oder nicht. Die unbeantwortete Frage ist nicht ausgegraut und
 * nicht in Warnfarbe - sie ist ein zulaessiger Zustand, kein Versaeumnis.
 */
function Question({ id, label, answered, help, blocks, children }: QuestionProps) {
  return (
    <div
      className={`insurance-needs-question${answered ? "" : " insurance-needs-question--unset"}`}
    >
      <label className="insurance-field" htmlFor={id}>
        <span>{label}</span>
        {children}
      </label>
      {!answered && <span className="insurance-needs-unset-mark">{UNSET_NOTE}</span>}
      {help && <p className="insurance-form-help">{help}</p>}
      {!answered && blocks.length > 0 && (
        <p className="insurance-form-help">Ungeprüft bleiben dadurch: {blocks.join(", ")}.</p>
      )}
    </div>
  );
}

/** Ja/Nein/keine Angabe - die Bauform, die den ganzen Bildschirm traegt. */
function TriStateSelect({
  id,
  value,
  disabled,
  onChange,
}: {
  id: string;
  value: boolean | null;
  disabled: boolean;
  onChange: (next: boolean | null) => void;
}) {
  return (
    <select
      id={id}
      className="form-input"
      disabled={disabled}
      value={booleanToSelect(value)}
      onChange={(event) => onChange(selectToBoolean(event.target.value))}
    >
      <option value={UNSET}>keine Angabe</option>
      <option value="yes">ja</option>
      <option value="no">nein</option>
    </select>
  );
}

interface HouseholdDraft {
  housingForm: string;
  livingArea: string;
  elementalZone: string;
  hasOilTank: boolean | null;
  liquidReserve: string;
  /** Dreiwertig wie jede andere Frage: "keine Angabe", "keine Tiere", "ja". */
  hasPets: boolean | null;
  pets: PetKind[];
  travelFrequency: string;
  vehicleCount: string;
  sideBusiness: boolean | null;
  volunteering: boolean | null;
}

function draftFromProfile(profile: NeedsProfile): HouseholdDraft {
  return {
    housingForm: profile.housing_form ?? UNSET,
    livingArea: numberToInput(profile.living_area_sqm),
    elementalZone: profile.elemental_zone ?? UNSET,
    hasOilTank: profile.has_oil_tank,
    liquidReserve: minorToInput(profile.liquid_reserve_minor),
    hasPets: profile.pets === null ? null : profile.pets.length > 0,
    pets: profile.pets ?? [],
    travelFrequency: profile.travel_frequency ?? UNSET,
    vehicleCount: numberToInput(profile.vehicle_count),
    sideBusiness: profile.side_business,
    volunteering: profile.volunteering,
  };
}

function HouseholdFields({
  profile,
  completeness,
  canEdit,
  saving,
  saveError,
  onSave,
}: {
  profile: NeedsProfile;
  completeness: NeedsCompleteness | null;
  canEdit: boolean;
  saving: boolean;
  saveError: string | null;
  onSave: (input: NeedsProfileInput) => void;
}) {
  const [draft, setDraft] = useState<HouseholdDraft>(() => draftFromProfile(profile));
  const [problems, setProblems] = useState<string[]>([]);

  const update = (patch: Partial<HouseholdDraft>) =>
    setDraft((current) => ({ ...current, ...patch }));

  const togglePet = (kind: PetKind, checked: boolean) =>
    setDraft((current) => ({
      ...current,
      pets: checked
        ? [...current.pets.filter((pet) => pet !== kind), kind]
        : current.pets.filter((pet) => pet !== kind),
    }));

  const submit = () => {
    const found: string[] = [];

    const livingArea = parseInteger(draft.livingArea);
    if (livingArea === undefined)
      found.push("Die Wohnfläche ist eine ganze Zahl in Quadratmetern.");
    const vehicleCount = parseInteger(draft.vehicleCount);
    if (vehicleCount === undefined) found.push("Die Zahl der Fahrzeuge ist eine ganze Zahl.");
    const liquidReserve = parseMoney(draft.liquidReserve);
    if (liquidReserve === undefined) found.push("Die Rücklage ist kein gültiger Betrag.");
    // "Ja, Tiere" ohne Art ist keine Antwort. Ungeprueft ginge eine leere Liste
    // raus, und die heisst serverseitig "gefragt, keine Tiere" - aus dem Ja
    // wuerde ein Nein. Genau diese Umdeutung soll das Formular verhindern.
    if (draft.hasPets === true && draft.pets.length === 0)
      found.push("Bitte wählen Sie mindestens eine Tierart aus – oder antworten Sie mit „nein“.");

    setProblems(found);
    if (found.length > 0) return;

    onSave({
      housing_form: draft.housingForm === UNSET ? null : (draft.housingForm as HousingForm),
      living_area_sqm: livingArea ?? null,
      elemental_zone: draft.elementalZone === UNSET ? null : (draft.elementalZone as ElementalZone),
      has_oil_tank: draft.hasOilTank,
      liquid_reserve_minor: liquidReserve ?? null,
      // Drei Faelle, nicht zwei: nicht beantwortet bleibt `null`, "keine Tiere"
      // wird zur leeren Liste. Nur die leere Liste ist eine Aussage.
      pets: draft.hasPets === null ? null : draft.hasPets ? draft.pets : [],
      travel_frequency:
        draft.travelFrequency === UNSET ? null : (draft.travelFrequency as TravelFrequency),
      vehicle_count: vehicleCount ?? null,
      side_business: draft.sideBusiness,
      volunteering: draft.volunteering,
    });
  };

  return (
    <div className="insurance-needs-form">
      <div className="insurance-needs-grid">
        <Question
          id="needs-housing-form"
          label="Wie wohnen Sie?"
          answered={draft.housingForm !== UNSET}
          blocks={blockedRules(completeness, "housing_form", null)}
        >
          <select
            id="needs-housing-form"
            className="form-input"
            disabled={!canEdit}
            value={draft.housingForm}
            onChange={(event) => update({ housingForm: event.target.value })}
          >
            <option value={UNSET}>keine Angabe</option>
            {HOUSING_FORMS.map((form) => (
              <option key={form} value={form}>
                {HOUSING_FORM_LABEL[form]}
              </option>
            ))}
          </select>
        </Question>

        <Question
          id="needs-living-area"
          label="Wohnfläche in m²"
          answered={draft.livingArea.trim() !== ""}
          blocks={blockedRules(completeness, "living_area_sqm", null)}
        >
          <input
            id="needs-living-area"
            className="form-input"
            inputMode="numeric"
            disabled={!canEdit}
            value={draft.livingArea}
            onChange={(event) => update({ livingArea: event.target.value })}
          />
        </Question>

        <Question
          id="needs-elemental-zone"
          label="Hochwasserzone (ZÜRS)"
          answered={draft.elementalZone !== UNSET}
          help="„Nachgesehen, aber unbekannt“ ist eine Antwort, „keine Angabe“ ist keine."
          blocks={blockedRules(completeness, "elemental_zone", null)}
        >
          <select
            id="needs-elemental-zone"
            className="form-input"
            disabled={!canEdit}
            value={draft.elementalZone}
            onChange={(event) => update({ elementalZone: event.target.value })}
          >
            <option value={UNSET}>keine Angabe</option>
            {ELEMENTAL_ZONES.map((zone) => (
              <option key={zone} value={zone}>
                {ELEMENTAL_ZONE_LABEL[zone]}
              </option>
            ))}
          </select>
        </Question>

        <Question
          id="needs-oil-tank"
          label="Öltank auf dem Grundstück?"
          answered={draft.hasOilTank !== null}
          blocks={blockedRules(completeness, "has_oil_tank", null)}
        >
          <TriStateSelect
            id="needs-oil-tank"
            value={draft.hasOilTank}
            disabled={!canEdit}
            onChange={(next) => update({ hasOilTank: next })}
          />
        </Question>

        <Question
          id="needs-liquid-reserve"
          label="Verfügbare Rücklage"
          answered={draft.liquidReserve.trim() !== ""}
          help="Der Betrag, den Sie im Schadenfall ohne Kredit selbst tragen könnten."
          blocks={blockedRules(completeness, "liquid_reserve_minor", null)}
        >
          <input
            id="needs-liquid-reserve"
            className="form-input"
            inputMode="decimal"
            disabled={!canEdit}
            value={draft.liquidReserve}
            onChange={(event) => update({ liquidReserve: event.target.value })}
          />
        </Question>

        <Question
          id="needs-vehicle-count"
          label="Fahrzeuge im Haushalt"
          answered={draft.vehicleCount.trim() !== ""}
          help="0 ist eine Antwort und heißt „kein Fahrzeug“."
          blocks={blockedRules(completeness, "vehicle_count", null)}
        >
          <input
            id="needs-vehicle-count"
            className="form-input"
            inputMode="numeric"
            disabled={!canEdit}
            value={draft.vehicleCount}
            onChange={(event) => update({ vehicleCount: event.target.value })}
          />
        </Question>

        <Question
          id="needs-travel"
          label="Reisen ins Ausland"
          answered={draft.travelFrequency !== UNSET}
          blocks={blockedRules(completeness, "travel_frequency", null)}
        >
          <select
            id="needs-travel"
            className="form-input"
            disabled={!canEdit}
            value={draft.travelFrequency}
            onChange={(event) => update({ travelFrequency: event.target.value })}
          >
            <option value={UNSET}>keine Angabe</option>
            {TRAVEL_FREQUENCIES.map((frequency) => (
              <option key={frequency} value={frequency}>
                {TRAVEL_FREQUENCY_LABEL[frequency]}
              </option>
            ))}
          </select>
        </Question>

        <Question
          id="needs-side-business"
          label="Nebenerwerb neben dem Hauptberuf?"
          answered={draft.sideBusiness !== null}
          blocks={blockedRules(completeness, "side_business", null)}
        >
          <TriStateSelect
            id="needs-side-business"
            value={draft.sideBusiness}
            disabled={!canEdit}
            onChange={(next) => update({ sideBusiness: next })}
          />
        </Question>

        <Question
          id="needs-volunteering"
          label="Ehrenamt?"
          answered={draft.volunteering !== null}
          blocks={blockedRules(completeness, "volunteering", null)}
        >
          <TriStateSelect
            id="needs-volunteering"
            value={draft.volunteering}
            disabled={!canEdit}
            onChange={(next) => update({ volunteering: next })}
          />
        </Question>

        <Question
          id="needs-has-pets"
          label="Tiere im Haushalt?"
          answered={draft.hasPets !== null}
          help="Hund und Pferd werden anders geprüft als Katze und Kleintier."
          blocks={blockedRules(completeness, "pets", null)}
        >
          <TriStateSelect
            id="needs-has-pets"
            value={draft.hasPets}
            disabled={!canEdit}
            onChange={(next) => update({ hasPets: next })}
          />
        </Question>
      </div>

      {draft.hasPets === true && (
        <fieldset className="insurance-needs-checks">
          <legend>Welche Tiere?</legend>
          {PET_KINDS.map((kind) => (
            <label key={kind} className="insurance-switch">
              <input
                type="checkbox"
                disabled={!canEdit}
                checked={draft.pets.includes(kind)}
                onChange={(event) => togglePet(kind, event.target.checked)}
              />
              <span>{PET_KIND_LABEL[kind]}</span>
            </label>
          ))}
        </fieldset>
      )}

      {problems.length > 0 && (
        <ul className="insurance-error" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {saveError && (
        <p className="insurance-error" role="alert">
          {saveError}
        </p>
      )}

      {canEdit && (
        <div className="insurance-action-row">
          <button type="button" className="btn-primary" disabled={saving} onClick={submit}>
            {saving ? "Wird gespeichert …" : "Angaben speichern"}
          </button>
        </div>
      )}
    </div>
  );
}

interface PersonDraft {
  employmentStatus: string;
  netIncome: string;
  healthCoverage: string;
  beihilfeRate: string;
  hasDependents: boolean | null;
  educationUntil: string;
  movedOutOn: string;
}

function PersonFields({
  person,
  completeness,
  canEdit,
  saving,
  saveError,
  onSave,
}: {
  person: NeedsPersonProfile;
  completeness: NeedsCompleteness | null;
  canEdit: boolean;
  saving: boolean;
  saveError: string | null;
  onSave: (personId: string, input: NeedsPersonProfileInput) => void;
}) {
  const [draft, setDraft] = useState<PersonDraft>(() => ({
    employmentStatus: person.employment_status ?? UNSET,
    netIncome: minorToInput(person.net_income_minor),
    healthCoverage: person.health_coverage ?? UNSET,
    beihilfeRate: person.beihilfe_rate_bp === null ? UNSET : String(person.beihilfe_rate_bp),
    hasDependents: person.has_dependents,
    educationUntil: person.education_until ?? "",
    movedOutOn: person.moved_out_on ?? "",
  }));
  const [problems, setProblems] = useState<string[]>([]);

  // Der Server entscheidet ueber die Gesundheitsfreigabe, nicht die
  // Oberflaeche. Fehlt die Angabe, wird der Datensatz lesend behandelt - die
  // vorsichtige Richtung.
  const editable = canEdit && person.can_manage !== false;
  const personId = person.insured_person_id;
  const prefix = `needs-person-${personId}`;

  const update = (patch: Partial<PersonDraft>) => setDraft((current) => ({ ...current, ...patch }));

  const submit = () => {
    const found: string[] = [];
    const netIncome = parseMoney(draft.netIncome);
    if (netIncome === undefined) found.push("Das Nettoeinkommen ist kein gültiger Betrag.");

    setProblems(found);
    if (found.length > 0) return;

    const coverage =
      draft.healthCoverage === UNSET ? null : (draft.healthCoverage as HealthCoverage);
    onSave(personId, {
      employment_status:
        draft.employmentStatus === UNSET ? null : (draft.employmentStatus as EmploymentStatus),
      net_income_minor: netIncome ?? null,
      health_coverage: coverage,
      // Der Bemessungssatz gehoert zur Beihilfe. Steht die Antwort auf etwas
      // anderes, waere ein stehen gebliebener Satz eine Angabe, die niemand
      // mehr gemacht hat.
      beihilfe_rate_bp:
        coverage === "pkv_beihilfe" && draft.beihilfeRate !== UNSET
          ? Number(draft.beihilfeRate)
          : null,
      has_dependents: draft.hasDependents,
      education_until: draft.educationUntil ? draft.educationUntil : null,
      moved_out_on: draft.movedOutOn ? draft.movedOutOn : null,
    });
  };

  return (
    <div className="insurance-needs-person">
      <h3 className="insurance-panel-subtitle">{person.display_name}</h3>

      <div className="insurance-needs-grid">
        <Question
          id={`${prefix}-employment`}
          label="Erwerbsstatus"
          answered={draft.employmentStatus !== UNSET}
          blocks={blockedRules(completeness, "employment_status", personId)}
        >
          <select
            id={`${prefix}-employment`}
            className="form-input"
            disabled={!editable}
            value={draft.employmentStatus}
            onChange={(event) => update({ employmentStatus: event.target.value })}
          >
            <option value={UNSET}>keine Angabe</option>
            {EMPLOYMENT_STATUSES.map((status) => (
              <option key={status} value={status}>
                {EMPLOYMENT_STATUS_LABEL[status]}
              </option>
            ))}
          </select>
        </Question>

        <Question
          id={`${prefix}-income`}
          label="Nettoeinkommen im Monat"
          answered={draft.netIncome.trim() !== ""}
          help="Wird nur für die Höhe einer Berufsunfähigkeitsrente gebraucht (R4a)."
          blocks={blockedRules(completeness, "net_income_minor", personId)}
        >
          <input
            id={`${prefix}-income`}
            className="form-input"
            inputMode="decimal"
            disabled={!editable}
            value={draft.netIncome}
            onChange={(event) => update({ netIncome: event.target.value })}
          />
        </Question>

        <Question
          id={`${prefix}-health`}
          label="Krankenversicherung"
          answered={draft.healthCoverage !== UNSET}
          blocks={blockedRules(completeness, "health_coverage", personId)}
        >
          <select
            id={`${prefix}-health`}
            className="form-input"
            disabled={!editable}
            value={draft.healthCoverage}
            onChange={(event) => update({ healthCoverage: event.target.value })}
          >
            <option value={UNSET}>keine Angabe</option>
            {HEALTH_COVERAGES.map((coverage) => (
              <option key={coverage} value={coverage}>
                {HEALTH_COVERAGE_LABEL[coverage]}
              </option>
            ))}
          </select>
        </Question>

        {draft.healthCoverage === "pkv_beihilfe" && (
          <Question
            id={`${prefix}-beihilfe`}
            label="Beihilfebemessungssatz"
            answered={draft.beihilfeRate !== UNSET}
            blocks={blockedRules(completeness, "beihilfe_rate_bp", personId)}
          >
            <select
              id={`${prefix}-beihilfe`}
              className="form-input"
              disabled={!editable}
              value={draft.beihilfeRate}
              onChange={(event) => update({ beihilfeRate: event.target.value })}
            >
              <option value={UNSET}>keine Angabe</option>
              {BEIHILFE_RATES.map((rate) => (
                <option key={rate} value={String(rate)}>
                  {rate / 100} %
                </option>
              ))}
            </select>
          </Question>
        )}

        <Question
          id={`${prefix}-dependents`}
          label="Unterhaltsberechtigte Angehörige?"
          answered={draft.hasDependents !== null}
          blocks={blockedRules(completeness, "has_dependents", personId)}
        >
          <TriStateSelect
            id={`${prefix}-dependents`}
            value={draft.hasDependents}
            disabled={!editable}
            onChange={(next) => update({ hasDependents: next })}
          />
        </Question>

        <Question
          id={`${prefix}-education`}
          label="Erstausbildung endet am"
          answered={draft.educationUntil !== ""}
          help="Mit dem Ende der Erstausbildung endet die Mitversicherung in Privathaftpflicht und Hausrat."
          blocks={blockedRules(completeness, "education_until", personId)}
        >
          <input
            id={`${prefix}-education`}
            type="date"
            className="form-input"
            disabled={!editable}
            value={draft.educationUntil}
            onChange={(event) => update({ educationUntil: event.target.value })}
          />
        </Question>

        <Question
          id={`${prefix}-moved-out`}
          label="Eigener Hausstand seit"
          answered={draft.movedOutOn !== ""}
          blocks={blockedRules(completeness, "moved_out_on", personId)}
        >
          <input
            id={`${prefix}-moved-out`}
            type="date"
            className="form-input"
            disabled={!editable}
            value={draft.movedOutOn}
            onChange={(event) => update({ movedOutOn: event.target.value })}
          />
        </Question>
      </div>

      {problems.length > 0 && (
        <ul className="insurance-error" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {saveError && (
        <p className="insurance-error" role="alert">
          {saveError}
        </p>
      )}

      {editable ? (
        <div className="insurance-action-row">
          <button type="button" className="btn-secondary" disabled={saving} onClick={submit}>
            {saving ? "Wird gespeichert …" : `Angaben zu ${person.display_name} speichern`}
          </button>
        </div>
      ) : (
        <p className="insurance-form-help">Diese Angaben dürfen Sie lesen, aber nicht ändern.</p>
      )}
    </div>
  );
}

export interface NeedsProfileFormProps {
  profile: NeedsProfile | null;
  completeness: NeedsCompleteness | null;
  loading: boolean;
  error: string | null;
  /** Der Endpunkt fehlt (404) - kein Fehler, sondern ein Ausbaustand. */
  unavailable?: boolean;
  /** Nur die Haushaltsangaben - Personen haben ihren eigenen Kanal. */
  saving: boolean;
  saveError: string | null;
  /** Person, deren Angaben gerade geschrieben werden, sonst `null`. */
  savingPersonId?: string | null;
  /** Fehler beim Speichern EINER Person, samt der Person, zu der er gehoert. */
  personSaveError?: { personId: string | null; message: string } | null;
  canEdit: boolean;
  onSave: (input: NeedsProfileInput) => void;
  onSavePerson: (personId: string, input: NeedsPersonProfileInput) => void;
}

export function NeedsProfileForm({
  profile,
  completeness,
  loading,
  error,
  unavailable = false,
  saving,
  saveError,
  savingPersonId = null,
  personSaveError = null,
  canEdit,
  onSave,
  onSavePerson,
}: NeedsProfileFormProps) {
  const { formatDate } = useFormatDate();
  const persons = profile?.persons ?? [];

  return (
    <section className="insurance-block" aria-labelledby="insurance-needs-profile-title">
      <div className="insurance-block-head">
        <h2 className="insurance-block-title" id="insurance-needs-profile-title">
          Angaben zu Ihrem Haushalt
        </h2>
        <span className="insurance-block-hint">
          Jede Frage darf offen bleiben – offen ist nicht „nein“
        </span>
      </div>

      <div className="card insurance-needs-card">
        <p className="insurance-scope-note" role="note">
          Aus diesen Angaben und Ihrem Policenbestand entstehen die Befunde weiter unten – nach
          festen Regeln, ohne Bewertung und ohne Empfehlung.{" "}
          <strong>Eine Frage ohne Antwort ist keine Verneinung:</strong> wo nichts steht, prüft der
          Abgleich nichts und meldet dazu auch keine Lücke. Das ist Absicht. Eine behauptete Lücke,
          die nur aus einer ausgebliebenen Antwort entsteht, wäre schlechter als gar kein Befund.
        </p>

        {unavailable && (
          <p className="insurance-empty-note">
            Der Bedarfsabgleich ist auf diesem Server noch nicht eingerichtet. Es fehlen keine
            Angaben – es fehlt der Endpunkt.
          </p>
        )}
        {loading && !profile && !unavailable && (
          <p className="card-body">Die Angaben werden geladen …</p>
        )}
        {error && !profile && !unavailable && (
          <p className="insurance-error" role="alert">
            {error}
          </p>
        )}

        {profile && (
          <>
            <HouseholdFields
              // Der Schluessel bindet das Formular an den geladenen Stand.
              key={fingerprint(profile)}
              profile={profile}
              completeness={completeness}
              canEdit={canEdit}
              saving={saving}
              saveError={saveError}
              onSave={onSave}
            />

            {profile.last_checked_on && (
              <p className="insurance-form-help">
                Zuletzt geprüft am {formatDate(profile.last_checked_on)}.
              </p>
            )}

            <div className="insurance-needs-persons">
              <h3 className="insurance-panel-subtitle">Angaben zu einzelnen Personen</h3>
              <p className="insurance-scope-note" role="note">
                Erwerbsstatus, Versichertenstatus und Einkommen stehen unter demselben Schutz wie
                die Gesundheitspolicen: Sie sehen hier nur die Personen, für die Sie freigegeben
                sind. Wer keine Freigabe hat, liest an dieser Stelle gar nichts – nicht etwa „keine
                Personen erfasst“.
              </p>
              {persons.length === 0 ? (
                <p className="insurance-empty-note">
                  Hier steht keine Person. Das kann zweierlei heißen: es ist keine versicherte
                  Person erfasst, oder Sie sind für keine freigegeben. Personen werden an der Police
                  im Register „Beteiligte“ erfasst.
                </p>
              ) : (
                persons.map((person) => (
                  <PersonFields
                    key={personFingerprint(person)}
                    person={person}
                    completeness={completeness}
                    canEdit={canEdit}
                    saving={savingPersonId === person.insured_person_id}
                    saveError={
                      personSaveError && personSaveError.personId === person.insured_person_id
                        ? personSaveError.message
                        : null
                    }
                    onSave={onSavePerson}
                  />
                ))
              )}
            </div>

            {completeness && completeness.missing.length > 0 && (
              <details className="insurance-needs-missing">
                <summary>Welche Regeln zurzeit nicht geprüft werden können</summary>
                <ul className="insurance-needs-missing-list">
                  {completeness.missing.map((item) => (
                    <li key={`${item.field}-${item.person_id ?? "household"}`}>
                      <strong>{item.label ?? needsFactLabel(item.field)}</strong>
                      {item.person_name ? ` (${item.person_name})` : ""} – ohne diese Angabe bleiben{" "}
                      {item.blocks_rule_keys.join(", ")} ungeprüft.
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </div>
    </section>
  );
}
