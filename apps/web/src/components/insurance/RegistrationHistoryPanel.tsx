/**
 * Register "Kennzeichen" - Halter- und Kennzeichenhistorie
 * (docs/VERSICHERUNGEN-PLAN.md, 4.5; P4).
 *
 * **Ein Kennzeichenwechsel ist eine neue Zeile, kein Ueberschreiben.** Stuenden
 * Kennzeichen und Halter als Merkmal am Objekt, wuerde der Umzug von M nach N
 * die Zuordnung aller alten Buchungen und Schaeden mitnehmen - und niemand
 * koennte spaeter sagen, unter welchem Kennzeichen ein Schaden passiert ist.
 * Deshalb schliesst der Server beim Speichern die laufende Zeile und oeffnet
 * eine neue.
 *
 * **Und genau das steht im Formular, bevor gespeichert wird.** Eine Aktion,
 * die einen bestehenden Eintrag beendet, darf nicht wie ein blosses Hinzufuegen
 * aussehen. Der Vorschaublock nennt die laufende Zeile beim Namen und sagt,
 * dass sie geschlossen wird.
 *
 * **Das `valid_to` der alten Zeile rechnet diese Oberflaeche nicht aus.** Sie
 * sagt, DASS geschlossen wird, und zeigt den letzten Tag erst, wenn er vom
 * Server zurueckkommt. Zwei Rechnungen ergaeben sonst zwei verschiedene letzte
 * Tage derselben Zulassung - dieselbe Regel, nach der dieses Modul auch keine
 * Fristen im Browser nachrechnet.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { useFormatDate } from "../../hooks/useProfile";
import { toIsoDateString } from "../../lib/dates";
import {
  createObjectRegistration,
  hasRegistrationHistory,
  insuranceObjectRegistrationsQueryKey,
  insuranceObjectsQueryKey,
  insurancePolicyObjectsQueryKey,
  invalidateInsuranceQueries,
  listObjectRegistrations,
  listInsuredObjects,
  listPolicyObjects,
  INSURED_OBJECT_KIND_LABEL,
  type InsurancePolicy,
  type InsuredObject,
  type ObjectRegistration,
  type ObjectRegistrationInput,
} from "../../lib/insuranceApi";
import { listReimbursementPeople } from "../../lib/reimbursementsApi";

const PEOPLE_QUERY_KEY = (householdId: string) => ["reimbursement-people", householdId] as const;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unbekannter Fehler";
}

/** Neueste Zulassung zuerst - die laufende Zeile steht oben, weil sie die
 *  Frage beantwortet, mit der man dieses Register oeffnet. */
function newestFirst(entries: ObjectRegistration[]): ObjectRegistration[] {
  return entries.slice().sort((a, b) => b.valid_from.localeCompare(a.valid_from));
}

export interface RegistrationHistoryPanelProps {
  policy: InsurancePolicy;
  canEdit: boolean;
}

/** Was dieses Register von einem Fahrzeug braucht: Kennung, Art, Bezeichnung. */
interface VehicleChoice {
  id: string;
  kind: InsuredObject["kind"];
  label: string;
}

export function RegistrationHistoryPanel({ policy, canEdit }: RegistrationHistoryPanelProps) {
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
  const peopleQuery = useQuery({
    queryKey: PEOPLE_QUERY_KEY(householdId),
    queryFn: () => listReimbursementPeople(token, householdId),
    enabled,
  });

  const [chosenObjectId, setChosenObjectId] = useState<string | null>(null);

  // Die Fahrzeuge dieser Police stehen VOLLSTAENDIG in der Verknuepfungsliste -
  // sie nennt Kennung, Art und Bezeichnung selbst. Die Haushaltsliste liefert
  // nur den Namen nach, unter dem das Objekt gepflegt wird. Andersherum -
  // Fahrzeuge erst aus der Haushaltsliste aufloesen - haenge die Aussage
  // "an dieser Police haengt kein Fahrzeug" an einer zweiten Abfrage, und ein
  // Fehler dort widerspraeche stumm dem Register "Objekte", das dasselbe
  // Fahrzeug daneben anzeigt.
  const objectById = new Map((objectsQuery.data ?? []).map((object) => [object.id, object]));
  const vehicles: VehicleChoice[] = (linksQuery.data ?? [])
    .filter((entry) => hasRegistrationHistory(entry.kind))
    .map((entry) => {
      const object = objectById.get(entry.insured_object_id);
      return {
        id: entry.insured_object_id,
        kind: object?.kind ?? entry.kind,
        label: object?.label ?? entry.label,
      };
    });

  // Ohne Effekt: die Auswahl faellt auf das erste Fahrzeug zurueck, sobald die
  // gemerkte Kennung nicht mehr zur Liste passt. Ein Effekt, der `setState`
  // ruft, waere hier ein zweiter Rendergang fuer dieselbe Aussage.
  const activeObject =
    vehicles.find((object) => object.id === chosenObjectId) ?? vehicles[0] ?? null;

  const registrationsQuery = useQuery({
    queryKey: insuranceObjectRegistrationsQueryKey(householdId, activeObject?.id ?? "none"),
    queryFn: () => listObjectRegistrations(token, householdId, activeObject?.id as string),
    enabled: enabled && Boolean(activeObject),
  });

  const create = useMutation({
    mutationFn: (vars: { objectId: string; input: ObjectRegistrationInput }) =>
      createObjectRegistration(token, householdId, vars.objectId, vars.input),
    onSuccess: async () => {
      await invalidateInsuranceQueries(queryClient, householdId);
    },
  });

  const entries = newestFirst(registrationsQuery.data ?? []);
  const running = entries.find((entry) => entry.valid_to === null) ?? null;
  const people = peopleQuery.data ?? [];
  const nameById = new Map(people.map((person) => [person.id, person.display_name]));

  const keeperName = (entry: ObjectRegistration): string | null =>
    entry.keeper_name ??
    (entry.keeper_person_id ? (nameById.get(entry.keeper_person_id) ?? null) : null);

  return (
    <section className="insurance-panel" aria-labelledby="insurance-registrations-title">
      <h3 id="insurance-registrations-title">Halter und Kennzeichen</h3>
      <p className="card-body">
        Jeder Wechsel ist eine eigene Zeile mit eigenem Zeitraum. Alte Zeilen bleiben stehen – an
        ihnen hängt, unter welchem Kennzeichen ein Vorgang passiert ist.
      </p>

      {(linksQuery.error || objectsQuery.error) && (
        <p className="insurance-error" role="alert">
          {errorMessage(linksQuery.error ?? objectsQuery.error)}
        </p>
      )}

      {/* Nur wenn die Verknuepfungsliste wirklich da ist: eine gescheiterte
          Abfrage weiss nicht, dass kein Fahrzeug daran haengt. */}
      {!linksQuery.isLoading && !linksQuery.error && vehicles.length === 0 && (
        <p className="insurance-empty-note">
          An dieser Police hängt kein Fahrzeug. Kennzeichen und Halter werden am Objekt geführt –
          legen Sie das Fahrzeug zuerst im Register „Objekte“ an.
        </p>
      )}

      {vehicles.length > 1 && (
        <label className="insurance-field">
          <span>Fahrzeug</span>
          <select
            className="form-input"
            value={activeObject?.id ?? ""}
            onChange={(event) => setChosenObjectId(event.target.value)}
          >
            {vehicles.map((object) => (
              <option key={object.id} value={object.id}>
                {INSURED_OBJECT_KIND_LABEL[object.kind]}: {object.label}
              </option>
            ))}
          </select>
        </label>
      )}

      {activeObject && (
        <>
          {registrationsQuery.isLoading && <p className="card-body">Die Historie wird geladen …</p>}
          {registrationsQuery.error && (
            <p className="insurance-error" role="alert">
              {errorMessage(registrationsQuery.error)}
            </p>
          )}

          {!registrationsQuery.isLoading && entries.length === 0 && (
            <p className="insurance-empty-note">
              Für „{activeObject.label}“ ist noch keine Zulassung erfasst.
            </p>
          )}

          {entries.length > 0 && (
            <ol className="insurance-reg-timeline">
              {entries.map((entry) => {
                const current = entry.valid_to === null;
                const keeper = keeperName(entry);
                return (
                  <li
                    key={entry.id}
                    className={`insurance-reg-entry ${current ? "insurance-reg-entry--current" : ""}`}
                  >
                    <span className="insurance-reg-dot" aria-hidden="true" />
                    <div className="insurance-reg-body">
                      <p className="insurance-reg-plate">
                        {entry.plate ?? <em>kein Kennzeichen erfasst</em>}
                        {current && <span className="insurance-reg-chip">laufend</span>}
                      </p>
                      <p className="insurance-entity-meta">
                        <span>
                          {current
                            ? `seit ${formatDate(entry.valid_from)}`
                            : `${formatDate(entry.valid_from)} – ${formatDate(entry.valid_to as string)}`}
                        </span>
                        {entry.keeper_person_id && (
                          <span>Halter: {keeper ?? "nicht mehr im Haushalt"}</span>
                        )}
                      </p>
                      {entry.note && <p className="insurance-entity-note">{entry.note}</p>}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}

          {canEdit && (
            <RegistrationForm
              key={`${activeObject.id}:${running?.id ?? "none"}`}
              objectLabel={activeObject.label}
              running={running}
              people={people}
              busy={create.isPending}
              error={create.error ? errorMessage(create.error) : null}
              onSubmit={(input) => create.mutate({ objectId: activeObject.id, input })}
            />
          )}
        </>
      )}
    </section>
  );
}

/**
 * Das Formular fuer den Wechsel.
 *
 * Eigene Komponente mit einem Schluessel aus Objekt und laufender Zeile: nach
 * dem Speichern ist die laufende Zeile eine andere, und das Formular startet
 * dadurch leer, ohne dass ein Effekt Felder zuruecksetzt.
 */
function RegistrationForm({
  objectLabel,
  running,
  people,
  busy,
  error,
  onSubmit,
}: {
  objectLabel: string;
  running: ObjectRegistration | null;
  people: { id: string; display_name: string }[];
  busy: boolean;
  error: string | null;
  onSubmit: (input: ObjectRegistrationInput) => void;
}) {
  const { formatDate } = useFormatDate();
  const [plate, setPlate] = useState("");
  const [keeperId, setKeeperId] = useState("");
  const [validFrom, setValidFrom] = useState(() => toIsoDateString(new Date()));
  const [note, setNote] = useState("");
  const [problems, setProblems] = useState<string[]>([]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    if (!validFrom) {
      found.push("Ohne Beginn lässt sich der Eintrag nicht einordnen.");
    }
    // Der Server schliesst die laufende Zeile zum Tag vor diesem Beginn. Liegt
    // der Beginn nicht spaeter, entstuende eine Zeile, deren Ende vor ihrem
    // Anfang laege - das weist die Datenbank ab. Besser hier mit einem Satz,
    // der den Grund nennt, als dort mit einem 23514.
    if (running && validFrom && validFrom <= running.valid_from) {
      found.push(
        `Der neue Eintrag muss nach dem Beginn des laufenden Eintrags liegen (${formatDate(
          running.valid_from,
        )}). Für eine Korrektur des laufenden Eintrags ist dies der falsche Weg – dann wäre der Wechsel nie passiert.`,
      );
    }
    if (!plate.trim() && !keeperId) {
      found.push("Erfassen Sie mindestens ein Kennzeichen oder einen Halter.");
    }
    if (plate.trim() && plate.trim().length < 2) {
      found.push("Ein Kennzeichen hat mindestens zwei Zeichen.");
    }
    setProblems(found);
    if (found.length > 0) return;

    onSubmit({
      plate: plate.trim() ? plate.trim().toUpperCase() : null,
      keeper_person_id: keeperId || null,
      valid_from: validFrom,
      note: note.trim() ? note.trim() : null,
    });
  };

  return (
    <form className="insurance-action-card" onSubmit={handleSubmit}>
      <h4 className="insurance-panel-subtitle">Wechsel erfassen</h4>

      <div className="insurance-action-row">
        <label className="insurance-field">
          <span>Kennzeichen</span>
          <input
            type="text"
            className="form-input"
            placeholder="M-AB 1234"
            value={plate}
            onChange={(event) => setPlate(event.target.value)}
          />
        </label>
        <label className="insurance-field">
          <span>Halter</span>
          <select
            className="form-input"
            value={keeperId}
            onChange={(event) => setKeeperId(event.target.value)}
          >
            <option value="">nicht erfasst</option>
            {people.map((person) => (
              <option key={person.id} value={person.id}>
                {person.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="insurance-field insurance-field--anchor">
          <span>Gilt ab</span>
          <input
            type="date"
            className="form-input"
            value={validFrom}
            onChange={(event) => setValidFrom(event.target.value)}
          />
        </label>
        <label className="insurance-field">
          <span>Notiz (optional)</span>
          <input
            type="text"
            className="form-input"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
      </div>

      {/* Die Folge des Speicherns steht VOR dem Knopf, nicht als Meldung
          danach: der neue Eintrag beendet einen bestehenden. */}
      <div className="insurance-preview" role="note">
        <p className="insurance-preview-title">Was beim Speichern passiert</p>
        {running ? (
          <ul>
            <li>
              Der laufende Eintrag <strong>{running.plate ?? "ohne Kennzeichen"}</strong> (seit{" "}
              {formatDate(running.valid_from)}) wird geschlossen. Er bleibt in der Historie stehen.
            </li>
            <li>
              Ab {validFrom ? formatDate(validFrom) : "dem gewählten Tag"} gilt für „{objectLabel}“
              der neue Eintrag.
            </li>
          </ul>
        ) : (
          <ul>
            <li>Dies wird der erste Eintrag für „{objectLabel}“. Es wird nichts geschlossen.</li>
          </ul>
        )}
      </div>

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

      <div className="insurance-action-row">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Wird gespeichert …" : running ? "Wechsel speichern" : "Zulassung erfassen"}
        </button>
      </div>
    </form>
  );
}
