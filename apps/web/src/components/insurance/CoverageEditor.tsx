/**
 * Register "Deckung" (docs/VERSICHERUNGEN-PLAN.md, 2.3 Punkt 2 und 4.6; P4).
 *
 * **Dieses Register ist der Grund, warum die Katalogspalten `typically_covers`
 * heissen.** Solange hier nichts erfasst ist, sagt das Register "Zweck", was in
 * dieser SPARTE ueblicherweise enthalten ist - eine Aussage ueber den Markt,
 * mit ausdruecklichem Vorbehalt. Sobald ein einziger Baustein erfasst ist,
 * antwortet der Dienst mit `coverage_source = 'policy'`, die Ueberschriften
 * dort wechseln auf "In dieser Police enthalten", und der Vorbehalt gilt fuer
 * diese beiden Spalten nicht mehr: dann ist es eine Aussage ueber genau diese
 * Police. Deshalb steht der Hinweis auf diese Folge hier oben und nicht im
 * Kleingedruckten.
 *
 * **Drei Zustaende je Baustein, nicht zwei.** "nicht erfasst" und "nicht
 * enthalten" sind verschiedene Aussagen: das eine heisst "darueber wurde nichts
 * festgestellt", das andere "der Baustein ist ausdruecklich nicht
 * mitversichert". Ein Kaestchen mit Haken kann das nicht unterscheiden - und
 * genau diese Unterscheidung braucht der spaetere Doppelungsbefund
 * (`contained_in`), der erst ausgespielt wird, wenn die Bausteine BEIDER
 * Policen erfasst sind.
 *
 * **Gespeichert wird immer die ganze Menge.** Das PUT ersetzt, was an der
 * Police steht; ein Baustein, der im Formular auf "nicht erfasst" steht, ist
 * danach geloescht. Deshalb wandern auch Bausteine mit, die nicht (mehr) im
 * Katalog dieser Sparte stehen - sonst loeschte ein Speichern nach einem
 * Spartenwechsel stillschweigend fremde Zeilen.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { parseAmountToMinorUnits } from "../../lib/money";
import {
  insuranceCoverageKeysQueryKey,
  insurancePolicyCoveragesQueryKey,
  invalidateInsuranceQueries,
  listCoverageKeys,
  listPolicyCoverages,
  parsePercentToBasisPoints,
  replacePolicyCoverages,
  type InsuranceCoverageKey,
  type InsurancePolicy,
  type PolicyCoverage,
  type PolicyCoverageInput,
} from "../../lib/insuranceApi";

type CoverageState = "unset" | "included" | "excluded";

interface CoverageDraft {
  state: CoverageState;
  sumInsured: string;
  deductible: string;
  benefitRate: string;
  waitingUntil: string;
  note: string;
}

/** Eine Zeile der Liste: Katalogangabe und - falls vorhanden - das, was an
 *  dieser Police dazu erfasst ist. */
interface CoverageRow {
  coverageKey: string;
  label: string;
  purposeShort: string | null;
  /** Baustein, den der Katalog dieser Sparte nicht (mehr) kennt. Er steht
   *  unten und wird beim Speichern mitgenommen, nicht verschluckt. */
  offCatalog: boolean;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unbekannter Fehler";
}

/** Minor Units als Eingabetext - ohne Waehrungszeichen, weil daraus wieder
 *  eine Eingabe wird. Der Parser liest genau diese Form zurueck. */
function minorToInput(minor: number | null): string {
  if (minor === null) return "";
  return (minor / 100).toLocaleString("de-DE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function basisPointsToInput(bp: number | null): string {
  if (bp === null) return "";
  return (bp / 100).toLocaleString("de-DE", { maximumFractionDigits: 2 });
}

function emptyDraft(): CoverageDraft {
  return {
    state: "unset",
    sumInsured: "",
    deductible: "",
    benefitRate: "",
    waitingUntil: "",
    note: "",
  };
}

function draftFromCoverage(coverage: PolicyCoverage): CoverageDraft {
  return {
    state: coverage.is_included ? "included" : "excluded",
    sumInsured: minorToInput(coverage.sum_insured_minor),
    deductible: minorToInput(coverage.deductible_minor),
    benefitRate: basisPointsToInput(coverage.benefit_rate_bp),
    waitingUntil: coverage.waiting_until ?? "",
    note: coverage.note ?? "",
  };
}

/**
 * Kennzeichnung des Serverstands.
 *
 * Sie dient als Schluessel des Formulars: kommt vom Server ein anderer Stand -
 * nach dem Speichern oder weil jemand anderes geschrieben hat -, baut React
 * das Formular neu auf und die Felder stehen wieder auf der Wahrheit. Ein
 * identischer Nachlauf derselben Abfrage aendert den Schluessel dagegen nicht
 * und laesst begonnene Eingaben stehen.
 */
function fingerprint(coverages: PolicyCoverage[]): string {
  return coverages
    .map((coverage) =>
      [
        coverage.coverage_key,
        coverage.is_included ? "1" : "0",
        coverage.sum_insured_minor ?? "",
        coverage.deductible_minor ?? "",
        coverage.benefit_rate_bp ?? "",
        coverage.waiting_until ?? "",
        coverage.note ?? "",
      ].join("~"),
    )
    .sort()
    .join("|");
}

export interface CoverageEditorProps {
  policy: InsurancePolicy;
  canEdit: boolean;
}

export function CoverageEditor({ policy, canEdit }: CoverageEditorProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const token = accessToken as string;
  const householdId = selectedHouseholdId as string;
  const enabled = Boolean(accessToken && selectedHouseholdId);

  const keysQuery = useQuery({
    queryKey: insuranceCoverageKeysQueryKey(policy.line_key),
    queryFn: () => listCoverageKeys(token, householdId, policy.line_key),
    enabled,
    // Global wie der Spartenkatalog - er aendert sich nicht je Haushalt und
    // nicht zwischen zwei Registerwechseln.
    staleTime: 30 * 60 * 1000,
  });
  const coveragesQuery = useQuery({
    queryKey: insurancePolicyCoveragesQueryKey(householdId, policy.id),
    queryFn: () => listPolicyCoverages(token, householdId, policy.id),
    enabled,
  });

  const save = useMutation({
    mutationFn: (coverages: PolicyCoverageInput[]) =>
      replacePolicyCoverages(token, householdId, policy.id, coverages),
    onSuccess: async () => {
      // Der Zwecktext haengt mit dran: mit der ersten erfassten Zeile kippt
      // `coverage_source` von `catalog` auf `policy`.
      await invalidateInsuranceQueries(queryClient, householdId);
    },
  });

  const catalog = keysQuery.data ?? [];
  const coverages = coveragesQuery.data ?? [];
  const loadError = keysQuery.error ?? coveragesQuery.error;
  const loading = keysQuery.isLoading || coveragesQuery.isLoading;

  return (
    <section className="insurance-panel" aria-labelledby="insurance-coverage-title">
      <h3 id="insurance-coverage-title">Was ist an dieser Police vereinbart?</h3>

      <p className="insurance-scope-note" role="note">
        Solange hier nichts erfasst ist, zeigt das Register „Zweck“ die Aussage des Katalogs: was in
        dieser Sparte <em>typischerweise</em> enthalten ist. Sobald mindestens ein Baustein erfasst
        ist, steht dort stattdessen, was an <em>Ihrer</em> Police vereinbart ist – und der Vorbehalt
        „Ihre konkrete Police kann abweichen“ gilt für diese beiden Spalten nicht mehr.
      </p>
      <p className="insurance-form-help">
        Zurzeit:{" "}
        {coverages.length === 0
          ? "Katalogaussage der Sparte – kein Baustein erfasst."
          : `${coverages.length} ${coverages.length === 1 ? "Baustein" : "Bausteine"} erfasst; das Register „Zweck“ spricht über diese Police.`}
      </p>

      {loadError && (
        <p className="insurance-error" role="alert">
          {errorMessage(loadError)}
        </p>
      )}
      {loading && <p className="card-body">Die Bausteine werden geladen …</p>}

      {!loading && catalog.length === 0 && coverages.length === 0 && (
        <p className="insurance-empty-note">
          Für die Sparte „{policy.line_label}“ führt der Katalog keine Bausteine. Ist die Sparte
          noch offen, klären Sie sie zuerst im Register „Übersicht“ – die Bausteinliste hängt an
          ihr.
        </p>
      )}

      {!loading && (catalog.length > 0 || coverages.length > 0) && (
        <CoverageForm
          key={fingerprint(coverages)}
          catalog={catalog}
          coverages={coverages}
          canEdit={canEdit}
          busy={save.isPending}
          error={save.error ? errorMessage(save.error) : null}
          onSave={(input) => save.mutate(input)}
        />
      )}
    </section>
  );
}

/**
 * Das Formular selbst.
 *
 * Es haelt den Entwurf und wird ueber seinen Schluessel neu aufgebaut, wenn
 * sich der Serverstand aendert - deshalb kommt es ohne einen Effekt aus, der
 * Eingaben nachtraeglich ueberschreibt.
 */
function CoverageForm({
  catalog,
  coverages,
  canEdit,
  busy,
  error,
  onSave,
}: {
  catalog: InsuranceCoverageKey[];
  coverages: PolicyCoverage[];
  canEdit: boolean;
  busy: boolean;
  error: string | null;
  onSave: (input: PolicyCoverageInput[]) => void;
}) {
  const recorded = new Map(coverages.map((coverage) => [coverage.coverage_key, coverage]));

  const rows: CoverageRow[] = [
    ...catalog
      .slice()
      .sort((a, b) => a.sort_order - b.sort_order || a.label.localeCompare(b.label, "de-DE"))
      .map((entry) => ({
        coverageKey: entry.coverage_key,
        label: entry.label,
        purposeShort: entry.purpose_short,
        offCatalog: false,
      })),
    ...coverages
      .filter((coverage) => !catalog.some((entry) => entry.coverage_key === coverage.coverage_key))
      .map((coverage) => ({
        coverageKey: coverage.coverage_key,
        // Beschriftung und Kurztext kommen auch hier aus dem Katalog - der
        // Dienst loest sie an der Zeile auf. Sonst stuende an einem Baustein,
        // der zur Sparte nicht mehr passt, eine Kennung statt eines Namens.
        label: coverage.label,
        purposeShort: coverage.purpose_short,
        offCatalog: true,
      })),
  ];

  const [drafts, setDrafts] = useState<Record<string, CoverageDraft>>(() =>
    Object.fromEntries(
      rows.map((row) => {
        const coverage = recorded.get(row.coverageKey);
        return [row.coverageKey, coverage ? draftFromCoverage(coverage) : emptyDraft()];
      }),
    ),
  );
  const [problems, setProblems] = useState<string[]>([]);

  const update = (coverageKey: string, patch: Partial<CoverageDraft>) => {
    setDrafts((current) => ({
      ...current,
      [coverageKey]: { ...(current[coverageKey] ?? emptyDraft()), ...patch },
    }));
  };

  const chosen = rows.filter((row) => (drafts[row.coverageKey]?.state ?? "unset") !== "unset");

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    const payload: PolicyCoverageInput[] = [];

    for (const row of chosen) {
      const draft = drafts[row.coverageKey] as CoverageDraft;
      const included = draft.state === "included";
      let sum: number | null = null;
      let deductible: number | null = null;
      let rate: number | null = null;

      if (included && draft.sumInsured.trim()) {
        sum = parseAmountToMinorUnits(draft.sumInsured);
        if (sum === null || sum < 0) {
          found.push(`${row.label}: Die Versicherungssumme ist kein gültiger Betrag.`);
        }
      }
      if (included && draft.deductible.trim()) {
        deductible = parseAmountToMinorUnits(draft.deductible);
        if (deductible === null || deductible < 0) {
          found.push(`${row.label}: Die Selbstbeteiligung ist kein gültiger Betrag.`);
        }
      }
      if (included && draft.benefitRate.trim()) {
        rate = parsePercentToBasisPoints(draft.benefitRate);
        if (rate === null) {
          found.push(`${row.label}: Der Leistungssatz ist ein Prozentwert zwischen 0 und 100.`);
        }
      }

      payload.push({
        coverage_key: row.coverageKey,
        is_included: included,
        sum_insured_minor: included ? sum : null,
        deductible_minor: included ? deductible : null,
        benefit_rate_bp: included ? rate : null,
        waiting_until: included && draft.waitingUntil ? draft.waitingUntil : null,
        note: draft.note.trim() ? draft.note.trim() : null,
      });
    }

    setProblems(found);
    if (found.length > 0) return;
    onSave(payload);
  };

  const clearsEverything = coverages.length > 0 && chosen.length === 0;

  return (
    <form onSubmit={handleSubmit}>
      <ul className="insurance-coverage-list">
        {rows.map((row) => {
          const draft = drafts[row.coverageKey] ?? emptyDraft();
          const fieldId = `coverage-${row.coverageKey}`;
          return (
            <li
              key={row.coverageKey}
              className={`insurance-coverage-row ${
                draft.state === "included" ? "insurance-coverage-row--included" : ""
              } ${draft.state === "excluded" ? "insurance-coverage-row--excluded" : ""}`}
            >
              <div className="insurance-coverage-head">
                <label className="insurance-field" htmlFor={fieldId}>
                  <span className="insurance-visually-hidden">Zustand von {row.label}</span>
                  <select
                    id={fieldId}
                    className="form-input"
                    disabled={!canEdit}
                    value={draft.state}
                    onChange={(event) =>
                      update(row.coverageKey, { state: event.target.value as CoverageState })
                    }
                  >
                    <option value="unset">nicht erfasst</option>
                    <option value="included">enthalten</option>
                    <option value="excluded">nicht enthalten</option>
                  </select>
                </label>
                <div className="insurance-coverage-text">
                  <span className="insurance-entity-title">{row.label}</span>
                  {row.purposeShort && (
                    <span className="insurance-coverage-purpose">{row.purposeShort}</span>
                  )}
                  {row.offCatalog && (
                    <span className="insurance-coverage-purpose">
                      Dieser Baustein steht nicht im Katalog dieser Sparte – er ist an der Police
                      erfasst und bleibt beim Speichern erhalten.
                    </span>
                  )}
                </div>
              </div>

              {draft.state === "included" && (
                <div className="insurance-action-row insurance-coverage-detail">
                  <label className="insurance-field">
                    <span>Versicherungssumme</span>
                    <input
                      type="text"
                      inputMode="decimal"
                      className="form-input"
                      disabled={!canEdit}
                      value={draft.sumInsured}
                      onChange={(event) =>
                        update(row.coverageKey, { sumInsured: event.target.value })
                      }
                    />
                  </label>
                  <label className="insurance-field">
                    <span>Selbstbeteiligung</span>
                    <input
                      type="text"
                      inputMode="decimal"
                      className="form-input"
                      disabled={!canEdit}
                      value={draft.deductible}
                      onChange={(event) =>
                        update(row.coverageKey, { deductible: event.target.value })
                      }
                    />
                  </label>
                  <label className="insurance-field">
                    <span>Leistungssatz in %</span>
                    <input
                      type="text"
                      inputMode="decimal"
                      className="form-input"
                      disabled={!canEdit}
                      value={draft.benefitRate}
                      onChange={(event) =>
                        update(row.coverageKey, { benefitRate: event.target.value })
                      }
                    />
                  </label>
                  <label className="insurance-field">
                    <span>Wartezeit bis</span>
                    <input
                      type="date"
                      className="form-input"
                      disabled={!canEdit}
                      value={draft.waitingUntil}
                      onChange={(event) =>
                        update(row.coverageKey, { waitingUntil: event.target.value })
                      }
                    />
                  </label>
                </div>
              )}

              {draft.state !== "unset" && (
                <label className="insurance-field insurance-coverage-detail">
                  <span>Notiz (optional)</span>
                  <input
                    type="text"
                    className="form-input"
                    disabled={!canEdit}
                    value={draft.note}
                    onChange={(event) => update(row.coverageKey, { note: event.target.value })}
                  />
                </label>
              )}
            </li>
          );
        })}
      </ul>

      {problems.length > 0 && (
        <ul className="insurance-error" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {error && (
        <p className="insurance-error" role="alert">
          {error}
        </p>
      )}

      {canEdit && (
        <div className="insurance-action-card">
          <p className="insurance-form-help">
            Gespeichert wird die vollständige Liste: was auf „nicht erfasst“ steht, ist danach an
            dieser Police nicht mehr hinterlegt.
            {clearsEverything
              ? " Damit wäre kein Baustein mehr erfasst – das Register „Zweck“ zeigt danach wieder die Aussage des Katalogs."
              : ""}
          </p>
          <div className="insurance-action-row">
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? "Wird gespeichert …" : "Bausteine speichern"}
            </button>
            <span className="insurance-form-help">
              {chosen.length === 0
                ? "kein Baustein erfasst"
                : `${chosen.length} von ${rows.length} erfasst`}
            </span>
          </div>
        </div>
      )}
    </form>
  );
}
