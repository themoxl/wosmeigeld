/**
 * Register "Objekte" (docs/VERSICHERUNGEN-PLAN.md, 4.5; P4).
 *
 * **Das Erwerbsdatum ist der Grund, warum es dieses Register gibt.** An ihm
 * haengt die Erwerberfrist nach § 96 Abs. 2 S. 2 VVG - ein Monat ab Erwerb,
 * Fristbeginn am Folgetag. Ohne das Datum ist die Frist nicht berechenbar, und
 * der Dienst antwortet auf den Versuch mit
 * `insurance_event_missing_acquired_on`. Die Oberflaeche sagt das an der
 * leeren Stelle, statt eine Police mit stillschweigend fehlender Frist
 * dastehen zu lassen.
 *
 * **Die Veraeusserung ist ausdruecklich KEINE Kuendigungsfrist.** Der
 * Veraeusserer hat kein Kuendigungsrecht, er hat eine Anzeigepflicht
 * (§ 97 Abs. 1 VVG). Ein abgegebenes Objekt wird deshalb datiert und nicht
 * geloescht - sonst verlieren alte Buchungen und spaetere Schaeden ihren
 * Bezug.
 *
 * **Kennzeichen und Halter stehen NICHT in diesem Register.** Sie sind eine
 * Historie und kein Merkmal: ein Kennzeichenwechsel ist eine neue Zeile im
 * Register "Kennzeichen", kein Ueberschreiben eines Feldes hier.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { useFormatDate } from "../../hooks/useProfile";
import {
  hasRegistrationHistory,
  insuranceObjectsQueryKey,
  insurancePolicyObjectsQueryKey,
  invalidateInsuranceQueries,
  linkPolicyObject,
  listInsuredObjects,
  listPolicyObjects,
  unlinkPolicyObject,
  updateInsuredObject,
  INSURED_OBJECT_KINDS,
  INSURED_OBJECT_KIND_LABEL,
  type InsuranceLine,
  type InsurancePolicy,
  type InsuredObject,
  type InsuredObjectClearableField,
  type InsuredObjectInput,
  type InsuredObjectKind,
} from "../../lib/insuranceApi";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unbekannter Fehler";
}

/** Nur bei Immobilien haben Anschrift, Wohnflaeche und Baujahr eine
 *  Bedeutung; ein Tier hat kein Baujahr. */
function isProperty(kind: InsuredObjectKind): boolean {
  return kind === "property";
}

function objectAddress(object: InsuredObject): string | null {
  const parts = [object.address_line, [object.postal_code, object.city].filter(Boolean).join(" ")]
    .filter((part) => part && part.trim().length > 0)
    .map((part) => (part as string).trim());
  return parts.length > 0 ? parts.join(", ") : null;
}

/** Ganze Zahl aus einem Eingabefeld, oder `null`. Bewusst streng: eine
 *  unlesbare Wohnflaeche wird nicht als 0 gespeichert. */
function parseWholeNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  return /^\d+$/.test(trimmed) ? Number(trimmed) : null;
}

export interface ObjectsPanelProps {
  policy: InsurancePolicy;
  canEdit: boolean;
  /** Spartenkatalog - nur fuer den Hinweis, welche Objektart die Sparte
   *  erwartet (`requires_object_kind`). Fehlt er, entfaellt der Hinweis. */
  lines?: InsuranceLine[];
}

export function ObjectsPanel({ policy, canEdit, lines = [] }: ObjectsPanelProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();

  const token = accessToken as string;
  const householdId = selectedHouseholdId as string;
  const enabled = Boolean(accessToken && selectedHouseholdId);

  const linksQuery = useQuery({
    queryKey: insurancePolicyObjectsQueryKey(householdId, policy.id),
    queryFn: () => listPolicyObjects(token, householdId, policy.id),
    enabled,
  });
  const objectsQuery = useQuery({
    queryKey: insuranceObjectsQueryKey(householdId),
    queryFn: () => listInsuredObjects(token, householdId),
    enabled,
  });

  const expectedKind = lines.find(
    (line) => line.line_key === policy.line_key,
  )?.requires_object_kind;

  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [existingId, setExistingId] = useState("");
  const [kind, setKind] = useState<InsuredObjectKind>(
    (expectedKind as InsuredObjectKind | undefined) ?? "vehicle",
  );
  const [label, setLabel] = useState("");
  const [acquiredOn, setAcquiredOn] = useState("");
  const [addressLine, setAddressLine] = useState("");
  const [postalCode, setPostalCode] = useState("");
  const [city, setCity] = useState("");
  const [livingArea, setLivingArea] = useState("");
  const [buildYear, setBuildYear] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [pendingUnlink, setPendingUnlink] = useState<string | null>(null);
  /** Welches Objekt gerade seine Daten bearbeitet. Nur eines zur Zeit - zwei
   *  offene Formulare auf derselben Zeile waeren zwei Staende. */
  const [editingId, setEditingId] = useState<string | null>(null);

  const invalidate = () => invalidateInsuranceQueries(queryClient, householdId);

  const resetForm = () => {
    setExistingId("");
    setLabel("");
    setAcquiredOn("");
    setAddressLine("");
    setPostalCode("");
    setCity("");
    setLivingArea("");
    setBuildYear("");
    setProblems([]);
  };

  /**
   * Verknuepfen - mit einem bestehenden Objekt oder mit einem neuen.
   *
   * Ein Aufruf, weil der Dienst beides in einer Anfrage entgegennimmt. Zwei
   * Aufrufe koennten auf halbem Weg abbrechen und ein Objekt hinterlassen, das
   * an keiner Police haengt.
   */
  const link = useMutation({
    mutationFn: (
      input:
        | { insured_object_id: string; note?: string | null }
        | { new_object: InsuredObjectInput; note?: string | null },
    ) => linkPolicyObject(token, householdId, policy.id, input),
    onSuccess: async () => {
      resetForm();
      setMode("existing");
      await invalidate();
    },
  });

  const unlink = useMutation({
    mutationFn: (objectId: string) => unlinkPolicyObject(token, householdId, policy.id, objectId),
    onSuccess: async () => {
      setPendingUnlink(null);
      await invalidate();
    },
  });

  /**
   * Aenderung an Bezeichnung, Erwerb und Veraeusserung.
   *
   * Ein geleertes Datumsfeld wandert nach `clear_fields` und nicht als `null`
   * in den Rumpf: `null` heisst beim Server "nicht mitgeschickt". Ohne diese
   * Unterscheidung liesse sich ein irrtuemlich erfasstes Erwerbsdatum nie
   * wieder entfernen - und an ihm haengt eine Frist.
   */
  const patch = useMutation({
    mutationFn: (vars: {
      object: InsuredObject;
      label: string;
      acquiredOn: string;
      disposedOn: string;
    }) => {
      const clearFields: InsuredObjectClearableField[] = [];
      if (!vars.acquiredOn) clearFields.push("acquired_on");
      if (!vars.disposedOn) clearFields.push("disposed_on");
      return updateInsuredObject(token, householdId, vars.object.id, vars.object.version, {
        label: vars.label,
        ...(vars.acquiredOn ? { acquired_on: vars.acquiredOn } : {}),
        ...(vars.disposedOn ? { disposed_on: vars.disposedOn } : {}),
        ...(clearFields.length > 0 ? { clear_fields: clearFields } : {}),
      });
    },
    onSuccess: async () => {
      setEditingId(null);
      await invalidate();
    },
  });

  const links = linksQuery.data ?? [];
  const objects = objectsQuery.data ?? [];
  const objectById = new Map(objects.map((object) => [object.id, object]));
  const linkedIds = new Set(links.map((entry) => entry.insured_object_id));
  // Abgegebene Objekte stehen nicht zur Auswahl: sie an eine weitere Police zu
  // haengen waere fast immer ein Griff daneben. Erfasst bleiben sie trotzdem.
  const available = objects.filter(
    (object) => !linkedIds.has(object.id) && object.is_active && object.disposed_on === null,
  );
  const busy = link.isPending || unlink.isPending || patch.isPending;

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    if (mode === "existing") {
      if (!existingId) found.push("Wählen Sie ein Objekt aus – oder legen Sie ein neues an.");
      setProblems(found);
      if (found.length > 0) return;
      link.mutate({ insured_object_id: existingId });
      return;
    }

    if (label.trim().length === 0) {
      found.push("Das Objekt braucht eine Bezeichnung, unter der Sie es wiedererkennen.");
    }
    const area = isProperty(kind) ? parseWholeNumber(livingArea) : null;
    const year = isProperty(kind) ? parseWholeNumber(buildYear) : null;
    // Die Grenzen sind die des Schemas (1 bis 2000 m², 1500 bis 2200). Sie
    // hier zu pruefen erspart dem Nutzer ein 422 zu einem Feld, das er gerade
    // ausgefuellt hat.
    if (isProperty(kind) && livingArea.trim() && (area === null || area < 1 || area > 2000)) {
      found.push("Die Wohnfläche wird in ganzen Quadratmetern erfasst, zwischen 1 und 2000.");
    }
    if (isProperty(kind) && buildYear.trim() && (year === null || year < 1500 || year > 2200)) {
      found.push("Das Baujahr wird als Jahreszahl zwischen 1500 und 2200 erfasst.");
    }
    setProblems(found);
    if (found.length > 0) return;

    link.mutate({
      new_object: {
        kind,
        label: label.trim(),
        acquired_on: acquiredOn || null,
        ...(isProperty(kind)
          ? {
              address_line: addressLine.trim() || null,
              postal_code: postalCode.trim() || null,
              city: city.trim() || null,
              living_area_sqm: area,
              build_year: year,
            }
          : {}),
      },
    });
  };

  const loadError = linksQuery.error ?? objectsQuery.error;
  const actionError = link.error ?? unlink.error ?? patch.error;

  return (
    <section className="insurance-panel" aria-labelledby="insurance-objects-title">
      <h3 id="insurance-objects-title">Was ist hier versichert?</h3>
      <p className="card-body">
        Fahrzeug, Immobilie, Tier, Gerät oder Fahrrad. Dasselbe Objekt kann an mehreren Policen
        hängen – ein Auto trägt Haftpflicht und Kasko.
      </p>

      {loadError && (
        <p className="insurance-error" role="alert">
          {errorMessage(loadError)}
        </p>
      )}
      {linksQuery.isLoading && <p className="card-body">Die Objekte werden geladen …</p>}

      {/* Eine gescheiterte Abfrage ist keine leere Liste. Stuenden Fehlermeldung
          und "noch kein Objekt erfasst" untereinander, waere die zweite
          Behauptung falsch - und die naheliegende Handlung daraufhin, das
          bereits erfasste Objekt ein zweites Mal anzulegen. */}
      {!linksQuery.isLoading && !linksQuery.error && links.length === 0 && (
        <p className="insurance-empty-note">
          Noch kein Objekt erfasst.
          {expectedKind
            ? ` Diese Sparte erwartet ein Objekt der Art „${
                INSURED_OBJECT_KIND_LABEL[expectedKind as InsuredObjectKind] ?? expectedKind
              }“.`
            : ""}
        </p>
      )}

      {links.length > 0 && (
        <ul className="insurance-entity-list">
          {links.map((entry) => {
            /* Die Verknuepfungszeile traegt Art, Bezeichnung und die beiden
               Lebenszyklusdaten selbst - sie reicht zum Anzeigen. Der volle
               Objektsatz kommt aus der Haushaltsliste und wird zum Bearbeiten
               gebraucht (Versionszaehler fuer `If-Match`). Fehlt er, entfaellt
               nur die Bearbeitung, nicht die Zeile. */
            const object = objectById.get(entry.insured_object_id) ?? null;
            const address = object ? objectAddress(object) : null;
            const acquiredOn = object?.acquired_on ?? entry.acquired_on;
            const disposedOn = object?.disposed_on ?? entry.disposed_on;
            const plate = object?.current_plate ?? entry.current_plate;
            return (
              <li className="insurance-entity" key={entry.id}>
                <div className="insurance-entity-head">
                  <span className="insurance-entity-title">{object?.label ?? entry.label}</span>
                  <span className="insurance-kind-chip">
                    {INSURED_OBJECT_KIND_LABEL[entry.kind]}
                  </span>
                  {disposedOn && (
                    <span className="insurance-kind-chip insurance-kind-chip--gone">abgegeben</span>
                  )}
                </div>

                <p className="insurance-entity-meta">
                  {acquiredOn ? (
                    <span>erworben am {formatDate(acquiredOn)}</span>
                  ) : (
                    <span className="insurance-missing">Erwerbsdatum fehlt</span>
                  )}
                  {disposedOn && <span>abgegeben am {formatDate(disposedOn)}</span>}
                  {plate && <span>Kennzeichen {plate}</span>}
                  {address && <span>{address}</span>}
                  {object?.living_area_sqm != null && <span>{object.living_area_sqm} m²</span>}
                  {object?.build_year != null && <span>Baujahr {object.build_year}</span>}
                </p>

                {!acquiredOn && (
                  <p className="insurance-form-help">
                    Ohne Erwerbsdatum ist die Erwerberfrist nach § 96 Abs. 2 S. 2 VVG nicht
                    berechenbar – ein Monat ab Erwerb, gerechnet ab dem Folgetag. Sie wird dann
                    nicht geschätzt, sondern gar nicht angezeigt.
                  </p>
                )}
                {disposedOn && (
                  <p className="insurance-form-help">
                    Die Veräußerung begründet kein Kündigungsrecht, sondern eine Anzeigepflicht (§
                    97 Abs. 1 VVG). Das Objekt bleibt deshalb erfasst.
                  </p>
                )}
                {hasRegistrationHistory(entry.kind) && (
                  <p className="insurance-form-help">
                    Kennzeichen und Halter stehen im Register „Kennzeichen“ – dort ist ein Wechsel
                    eine neue Zeile und kein Überschreiben.
                  </p>
                )}

                {canEdit && object && editingId === object.id && (
                  <ObjectDatesForm
                    object={object}
                    busy={patch.isPending}
                    onCancel={() => setEditingId(null)}
                    onSave={(next) => patch.mutate({ object, ...next })}
                  />
                )}

                {canEdit && editingId !== entry.insured_object_id && (
                  <div className="insurance-entity-actions">
                    {object && (
                      <button
                        type="button"
                        className="insurance-inline-button"
                        onClick={() => setEditingId(object.id)}
                      >
                        {acquiredOn ? "Angaben bearbeiten" : "Erwerbsdatum nachtragen"}
                      </button>
                    )}
                    {pendingUnlink === entry.id ? (
                      <div className="insurance-confirm">
                        <p>
                          Verknüpfung zu dieser Police lösen? Das Objekt selbst bleibt erhalten – es
                          hängt an weiteren Policen und an alten Buchungen.
                        </p>
                        <div className="insurance-action-row">
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={busy}
                            onClick={() => unlink.mutate(entry.insured_object_id)}
                          >
                            Ja, lösen
                          </button>
                          <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => setPendingUnlink(null)}
                          >
                            Abbrechen
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="insurance-inline-button"
                        onClick={() => setPendingUnlink(entry.id)}
                      >
                        Verknüpfung lösen
                      </button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {canEdit && (
        <form className="insurance-action-card" onSubmit={handleSubmit}>
          <h4 className="insurance-panel-subtitle">Objekt hinzufügen</h4>

          <fieldset className="insurance-mode-choice">
            <legend className="insurance-visually-hidden">Woher kommt das Objekt?</legend>
            <label>
              <input
                type="radio"
                name="insurance-object-mode"
                checked={mode === "existing"}
                onChange={() => setMode("existing")}
              />{" "}
              schon erfasstes Objekt
            </label>
            <label>
              <input
                type="radio"
                name="insurance-object-mode"
                checked={mode === "new"}
                onChange={() => setMode("new")}
              />{" "}
              neues Objekt anlegen
            </label>
          </fieldset>

          {mode === "existing" ? (
            <div className="insurance-action-row">
              <label className="insurance-field">
                <span>Objekt</span>
                <select
                  className="form-input"
                  value={existingId}
                  onChange={(event) => setExistingId(event.target.value)}
                >
                  <option value="">Bitte wählen</option>
                  {available.map((object) => (
                    <option key={object.id} value={object.id}>
                      {INSURED_OBJECT_KIND_LABEL[object.kind]}: {object.label}
                    </option>
                  ))}
                </select>
              </label>
              {available.length === 0 && !objectsQuery.isLoading && (
                <p className="insurance-form-help">
                  Es gibt kein weiteres Objekt im Haushalt, das noch nicht an dieser Police hängt.
                </p>
              )}
            </div>
          ) : (
            <>
              <div className="insurance-action-row">
                <label className="insurance-field">
                  <span>Art</span>
                  <select
                    className="form-input"
                    value={kind}
                    onChange={(event) => setKind(event.target.value as InsuredObjectKind)}
                  >
                    {INSURED_OBJECT_KINDS.map((value) => (
                      <option key={value} value={value}>
                        {INSURED_OBJECT_KIND_LABEL[value]}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="insurance-field">
                  <span>Bezeichnung</span>
                  <input
                    type="text"
                    className="form-input"
                    placeholder={kind === "vehicle" ? "VW Golf, blau" : "Wohnung Hauptstraße"}
                    value={label}
                    onChange={(event) => setLabel(event.target.value)}
                  />
                </label>
                <label className="insurance-field insurance-field--anchor">
                  <span>Erwerb am</span>
                  <input
                    type="date"
                    className="form-input"
                    value={acquiredOn}
                    onChange={(event) => setAcquiredOn(event.target.value)}
                  />
                </label>
              </div>

              <p className="insurance-form-help">
                Das Erwerbsdatum ist der Anker der Erwerberfrist (§ 96 Abs. 2 S. 2 VVG): ein Monat
                ab Erwerb, Fristbeginn am Folgetag. Es wird nie aus einem anderen Feld abgeleitet –
                was hier nicht steht, gibt es für die Fristrechnung nicht.
              </p>
              {kind === "vehicle" && (
                <p className="insurance-form-help">
                  Kennzeichen und Halter tragen Sie danach im Register „Kennzeichen“ ein. Sie stehen
                  dort als Historie, damit ein späterer Wechsel die alte Zuordnung nicht
                  überschreibt.
                </p>
              )}

              {isProperty(kind) && (
                <div className="insurance-action-row">
                  <label className="insurance-field">
                    <span>Straße und Hausnummer</span>
                    <input
                      type="text"
                      className="form-input"
                      value={addressLine}
                      onChange={(event) => setAddressLine(event.target.value)}
                    />
                  </label>
                  <label className="insurance-field">
                    <span>PLZ</span>
                    <input
                      type="text"
                      className="form-input"
                      value={postalCode}
                      onChange={(event) => setPostalCode(event.target.value)}
                    />
                  </label>
                  <label className="insurance-field">
                    <span>Ort</span>
                    <input
                      type="text"
                      className="form-input"
                      value={city}
                      onChange={(event) => setCity(event.target.value)}
                    />
                  </label>
                  <label className="insurance-field">
                    <span>Wohnfläche in m²</span>
                    <input
                      type="text"
                      inputMode="numeric"
                      className="form-input"
                      value={livingArea}
                      onChange={(event) => setLivingArea(event.target.value)}
                    />
                  </label>
                  <label className="insurance-field">
                    <span>Baujahr</span>
                    <input
                      type="text"
                      inputMode="numeric"
                      className="form-input"
                      value={buildYear}
                      onChange={(event) => setBuildYear(event.target.value)}
                    />
                  </label>
                </div>
              )}
            </>
          )}

          {problems.length > 0 && (
            <ul className="insurance-error" role="alert">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          )}
          {actionError && (
            <p className="insurance-error" role="alert">
              {errorMessage(actionError)}
            </p>
          )}

          <div className="insurance-action-row">
            <button type="submit" className="btn-primary" disabled={busy}>
              {busy ? "Wird gespeichert …" : "Objekt hinzufügen"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

/**
 * Erwerb, Veraeusserung und Bezeichnung eines bestehenden Objekts.
 *
 * Eigene Komponente, damit die Felder mit dem Objekt zusammen entstehen und
 * vergehen: `useState` liest den Anfangswert genau einmal, und die Zeile wird
 * ueber ihren Schluessel neu aufgebaut, wenn sich der Serverstand aendert. So
 * braucht es keinen Effekt, der Eingaben nachtraeglich ueberschreibt.
 */
function ObjectDatesForm({
  object,
  busy,
  onSave,
  onCancel,
}: {
  object: InsuredObject;
  busy: boolean;
  onSave: (next: { label: string; acquiredOn: string; disposedOn: string }) => void;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState(object.label);
  const [acquiredOn, setAcquiredOn] = useState(object.acquired_on ?? "");
  const [disposedOn, setDisposedOn] = useState(object.disposed_on ?? "");
  const [problem, setProblem] = useState<string | null>(null);

  const save = () => {
    if (label.trim().length === 0) {
      setProblem("Die Bezeichnung darf nicht leer sein.");
      return;
    }
    if (acquiredOn && disposedOn && disposedOn < acquiredOn) {
      setProblem("Die Veräußerung kann nicht vor dem Erwerb liegen.");
      return;
    }
    setProblem(null);
    onSave({ label: label.trim(), acquiredOn, disposedOn });
  };

  return (
    <div className="insurance-action-card">
      <div className="insurance-action-row">
        <label className="insurance-field">
          <span>Bezeichnung</span>
          <input
            type="text"
            className="form-input"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
        <label className="insurance-field insurance-field--anchor">
          <span>Erwerb am</span>
          <input
            type="date"
            className="form-input"
            value={acquiredOn}
            onChange={(event) => setAcquiredOn(event.target.value)}
          />
        </label>
        <label className="insurance-field">
          <span>Veräußert am</span>
          <input
            type="date"
            className="form-input"
            value={disposedOn}
            onChange={(event) => setDisposedOn(event.target.value)}
          />
        </label>
      </div>
      <p className="insurance-form-help">
        Der Erwerb trägt die Erwerberfrist (§ 96 Abs. 2 S. 2 VVG). Die Veräußerung trägt keine
        Frist, sondern die Anzeigepflicht nach § 97 Abs. 1 VVG – sie erzeugt eine Aufgabe, keinen
        Countdown.
      </p>
      {problem && (
        <p className="insurance-error" role="alert">
          {problem}
        </p>
      )}
      <div className="insurance-action-row">
        <button type="button" className="btn-primary" disabled={busy} onClick={save}>
          {busy ? "Wird gespeichert …" : "Angaben speichern"}
        </button>
        <button type="button" className="btn-secondary" onClick={onCancel}>
          Abbrechen
        </button>
      </div>
    </div>
  );
}
