/**
 * Register "Personen" (docs/VERSICHERUNGEN-PLAN.md, 2.3 Punkt 5 und 4.4; P4).
 *
 * **Es gibt keine zweite Personenliste.** Versicherte Personen sind dieselben
 * Menschen, die das Erstattungsmodul fuehrt (`health_insured_persons`) - eine
 * eigene Tabelle waeren zwei Wahrheiten ueber dieselbe Familie. Die Auswahl
 * kommt deshalb aus `/v1/reimbursements/people`, und wer dort fehlt, wird in
 * derselben Anfrage angelegt, mit der er an die Police kommt.
 *
 * **Die Sichtbarkeit haengt an diesen Zeilen, und das steht hier auch so.**
 * Bei einer gesundheitsnahen Police (`is_health_scoped`) darf sie nur lesen,
 * wer Zugriff auf mindestens eine der genannten Personen hat; ohne jede Person
 * bleibt sie fuer alle Haushaltsmitglieder sichtbar (Plan 4.3, P4-Nachtrag).
 * Beide Uebergaenge - die erste Person hinein, die letzte Person heraus -
 * aendern also, wer diese Police morgen noch findet. Das vor dem Speichern zu
 * verschweigen waere die bequemere Oberflaeche und der schlechtere Umgang mit
 * einem Datenschutzmerkmal.
 *
 * **Die Namen kommen vom Server, nicht aus der Auswahlliste.**
 * `insured_person_label()` gibt den Anzeigenamen zurueck, solange die Person
 * zum Haushalt gehoert - exponiert wird ein Name, nicht die Zeile. Ein `null`
 * heisst deshalb "gehoert nicht mehr zum Haushalt" und nicht "keiner
 * hinterlegt". Die Oberflaeche schreibt genau das hin, statt eine Kennung oder
 * einen Gedankenstrich zu zeigen: die Zuordnung bleibt bestehen, und dass sie
 * es tut, ist eine Auskunft.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { useFormatDate } from "../../hooks/useProfile";
import {
  addPolicyPerson,
  formatShareAsPercent,
  insurancePolicyPersonsQueryKey,
  invalidateInsuranceQueries,
  isHealthScoped,
  listPolicyPersons,
  parsePercentToBasisPoints,
  removePolicyPerson,
  shareLabelForRole,
  POLICY_PERSON_ROLE_HINT,
  POLICY_PERSON_ROLE_LABEL,
  type InsurancePolicy,
  type PolicyPerson,
  type PolicyPersonInput,
  type PolicyPersonRole,
} from "../../lib/insuranceApi";
import { listReimbursementPeople } from "../../lib/reimbursementsApi";

/** Reihenfolge der Auswahl: was am haeufigsten gebraucht wird, steht oben. */
const ROLES: PolicyPersonRole[] = [
  "insured",
  "policyholder",
  "premium_payer",
  "beneficiary",
  "driver",
  "vehicle_keeper",
];

/** Die Personenliste des Erstattungsmoduls - derselbe Schluessel wie dort,
 *  damit beide Seiten denselben Stand sehen und nicht zwei Kopien halten. */
const PEOPLE_QUERY_KEY = (householdId: string) => ["reimbursement-people", householdId] as const;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unbekannter Fehler";
}

/**
 * Der Name einer verknuepften Person.
 *
 * Zuerst der Server (`insured_person_label`), dann die Auswahlliste. Faellt
 * beides aus, ist der Name nicht sichtbar - und genau das steht dann da.
 */
function personName(entry: PolicyPerson, fallback: Map<string, string>): string | null {
  return entry.display_name ?? fallback.get(entry.insured_person_id) ?? null;
}

export interface PersonsEditorProps {
  policy: InsurancePolicy;
  canEdit: boolean;
}

export function PersonsEditor({ policy, canEdit }: PersonsEditorProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();

  const token = accessToken as string;
  const householdId = selectedHouseholdId as string;
  const enabled = Boolean(accessToken && selectedHouseholdId);

  const personsQuery = useQuery({
    queryKey: insurancePolicyPersonsQueryKey(householdId, policy.id),
    queryFn: () => listPolicyPersons(token, householdId, policy.id),
    enabled,
  });
  const peopleQuery = useQuery({
    queryKey: PEOPLE_QUERY_KEY(householdId),
    queryFn: () => listReimbursementPeople(token, householdId),
    enabled,
  });

  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [personId, setPersonId] = useState("");
  const [newName, setNewName] = useState("");
  const [newBirthDate, setNewBirthDate] = useState("");
  const [role, setRole] = useState<PolicyPersonRole>("insured");
  const [share, setShare] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const [validTo, setValidTo] = useState("");
  const [note, setNote] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  /** Welche Verknuepfung gerade zum Entfernen bestaetigt werden soll. Ein
   *  zweiter Klick statt eines Dialogs: die Folge steht daneben im Klartext. */
  const [pendingRemoval, setPendingRemoval] = useState<string | null>(null);

  const resetForm = () => {
    setPersonId("");
    setNewName("");
    setNewBirthDate("");
    setShare("");
    setValidFrom("");
    setValidTo("");
    setNote("");
    setProblems([]);
  };

  const invalidate = () => invalidateInsuranceQueries(queryClient, householdId);

  const add = useMutation({
    mutationFn: (input: PolicyPersonInput) => addPolicyPerson(token, householdId, policy.id, input),
    onSuccess: async () => {
      resetForm();
      setMode("existing");
      // Eine neu angelegte Person gehoert ab sofort auch in die Auswahlliste
      // des Erstattungsmoduls - beide lesen denselben Schluessel.
      await queryClient.invalidateQueries({ queryKey: PEOPLE_QUERY_KEY(householdId) });
      await invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: (entryId: string) => removePolicyPerson(token, householdId, policy.id, entryId),
    onSuccess: async () => {
      setPendingRemoval(null);
      await invalidate();
    },
  });

  const entries = personsQuery.data ?? [];
  const people = peopleQuery.data ?? [];
  const nameById = new Map(people.map((person) => [person.id, person.display_name]));
  const healthScoped = isHealthScoped(policy);
  const busy = add.isPending || remove.isPending;

  // Je Police genau ein Versicherungsnehmer - das ist ein Unique-Index am
  // Schema. Die Auswahl bietet die Rolle deshalb gar nicht erst an, statt den
  // Nutzer in einen 409 laufen zu lassen.
  const holderTaken = entries.some((entry) => entry.role === "policyholder");
  const takenPairs = new Set(entries.map((entry) => `${entry.insured_person_id}:${entry.role}`));
  const shareLabel = shareLabelForRole(role);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    if (mode === "existing" && !personId) {
      found.push("Wählen Sie eine Person aus – oder legen Sie eine neue an.");
    }
    if (mode === "new" && newName.trim().length === 0) {
      found.push("Eine neue Person braucht einen Namen.");
    }
    if (mode === "existing" && personId && takenPairs.has(`${personId}:${role}`)) {
      found.push(
        `Diese Person ist an dieser Police bereits als „${POLICY_PERSON_ROLE_LABEL[role]}“ erfasst.`,
      );
    }
    let shareBp: number | null = null;
    if (share.trim() !== "") {
      shareBp = parsePercentToBasisPoints(share);
      if (shareBp === null) {
        found.push("Der Anteil ist ein Prozentwert zwischen 0 und 100, zum Beispiel „70“.");
      }
    }
    if (validFrom && validTo && validTo < validFrom) {
      found.push("Das Ende darf nicht vor dem Beginn liegen.");
    }
    setProblems(found);
    if (found.length > 0) return;

    add.mutate({
      ...(mode === "existing"
        ? { insured_person_id: personId }
        : {
            new_person: {
              display_name: newName.trim(),
              birth_date: newBirthDate ? newBirthDate : null,
            },
          }),
      role,
      // Nur bei den beiden Rollen, an denen die Zahl eine Bedeutung hat -
      // sonst stuende ein Bezugsanteil an einem Fahrer.
      share_bp: shareLabel ? shareBp : null,
      valid_from: validFrom || null,
      valid_to: validTo || null,
      note: note.trim() ? note.trim() : null,
    });
  };

  const loadError = personsQuery.error ?? peopleQuery.error;
  const actionError = add.error ?? remove.error;

  return (
    <section className="insurance-panel" aria-labelledby="insurance-persons-title">
      <h3 id="insurance-persons-title">Wer ist hier versichert?</h3>
      <p className="card-body">
        Personen kommen aus derselben Liste wie im Erstattungsmodul. Eine Police kann mehrere
        Beteiligte tragen – versichert, Versicherungsnehmer, Beitragszahler, bezugsberechtigt.
      </p>

      {healthScoped && (
        <p className="insurance-scope-note" role="note">
          <strong>Diese Police ist gesundheitsnah.</strong> Ab der ersten hier genannten Person
          entscheidet die Gesundheitsfreigabe für diese Person darüber, wer die Police überhaupt
          noch sieht – Eigentümer sehen sie immer. Solange keine Person genannt ist, ist sie für
          alle Mitglieder des Haushalts sichtbar. Die Freigaben selbst werden im Erstattungsmodul
          verwaltet; dieses Register vergibt keine.
        </p>
      )}

      {loadError && (
        <p className="insurance-error" role="alert">
          {errorMessage(loadError)}
        </p>
      )}
      {personsQuery.isLoading && <p className="card-body">Die Beteiligten werden geladen …</p>}

      {!personsQuery.isLoading && entries.length === 0 && (
        <p className="insurance-empty-note">
          Noch keine Person erfasst.
          {healthScoped
            ? " Die Police ist damit für alle Mitglieder des Haushalts sichtbar."
            : " Ohne Personenbezug lässt sich die Frage „welche Policen betreffen dieses Kind?“ nicht beantworten."}
        </p>
      )}

      {entries.length > 0 && (
        <ul className="insurance-entity-list">
          {entries.map((entry) => {
            const name = personName(entry, nameById);
            const entryShareLabel = shareLabelForRole(entry.role);
            const isLast = entries.length === 1;
            return (
              <li className="insurance-entity" key={entry.id}>
                <div className="insurance-entity-head">
                  <span className="insurance-entity-title">
                    {name ?? <em>Name nicht auflösbar</em>}
                  </span>
                  <span className="insurance-role-chip">
                    {POLICY_PERSON_ROLE_LABEL[entry.role]}
                  </span>
                </div>
                <p className="insurance-entity-meta">
                  {entryShareLabel && entry.share_bp !== null && (
                    <span>
                      {entryShareLabel}: {formatShareAsPercent(entry.share_bp)}
                    </span>
                  )}
                  {entry.valid_from && <span>ab {formatDate(entry.valid_from)}</span>}
                  {entry.valid_to && <span>bis {formatDate(entry.valid_to)}</span>}
                  {name === null && (
                    <span>
                      Diese Person gehört nicht mehr zum Haushalt. Die Zuordnung bleibt bestehen –
                      sie stillschweigend zu entfernen hieße, der Police ihren Bezug zu nehmen.
                    </span>
                  )}
                </p>
                {entry.note && <p className="insurance-entity-note">{entry.note}</p>}
                {canEdit && (
                  <div className="insurance-entity-actions">
                    {pendingRemoval === entry.id ? (
                      <div className="insurance-confirm">
                        <p>
                          Verknüpfung lösen? Die Person selbst bleibt erhalten.
                          {healthScoped && isLast
                            ? " Danach hat diese Police keinen Personenbezug mehr und ist wieder für alle Mitglieder des Haushalts sichtbar."
                            : ""}
                        </p>
                        <div className="insurance-action-row">
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={busy}
                            onClick={() => remove.mutate(entry.id)}
                          >
                            Ja, entfernen
                          </button>
                          <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => setPendingRemoval(null)}
                          >
                            Abbrechen
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="insurance-inline-button"
                        onClick={() => setPendingRemoval(entry.id)}
                      >
                        Entfernen
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
          <h4 className="insurance-panel-subtitle">Person hinzufügen</h4>

          <fieldset className="insurance-mode-choice">
            <legend className="insurance-visually-hidden">Woher kommt die Person?</legend>
            <label>
              <input
                type="radio"
                name="insurance-person-mode"
                checked={mode === "existing"}
                onChange={() => setMode("existing")}
              />{" "}
              aus der Liste
            </label>
            <label>
              <input
                type="radio"
                name="insurance-person-mode"
                checked={mode === "new"}
                onChange={() => setMode("new")}
              />{" "}
              neue Person anlegen
            </label>
          </fieldset>

          {mode === "existing" ? (
            <div className="insurance-action-row">
              <label className="insurance-field">
                <span>Person</span>
                <select
                  className="form-input"
                  value={personId}
                  onChange={(event) => setPersonId(event.target.value)}
                >
                  <option value="">Bitte wählen</option>
                  {people.map((person) => (
                    <option key={person.id} value={person.id}>
                      {person.display_name}
                    </option>
                  ))}
                </select>
              </label>
              {people.length === 0 && !peopleQuery.isLoading && (
                <p className="insurance-form-help">
                  Es steht keine Person zur Auswahl. Entweder ist noch keine erfasst – oder Sie
                  haben für die vorhandenen keine Gesundheitsfreigabe. Legen Sie in diesem Fall
                  keine zweite Zeile für denselben Menschen an, sondern lassen Sie sich freigeben.
                </p>
              )}
            </div>
          ) : (
            <div className="insurance-action-row">
              <label className="insurance-field">
                <span>Name</span>
                <input
                  type="text"
                  className="form-input"
                  value={newName}
                  onChange={(event) => setNewName(event.target.value)}
                />
              </label>
              <label className="insurance-field">
                <span>Geburtsdatum (optional)</span>
                <input
                  type="date"
                  className="form-input"
                  value={newBirthDate}
                  onChange={(event) => setNewBirthDate(event.target.value)}
                />
              </label>
            </div>
          )}

          <div className="insurance-action-row">
            <label className="insurance-field">
              <span>Rolle</span>
              <select
                className="form-input"
                value={role}
                onChange={(event) => setRole(event.target.value as PolicyPersonRole)}
              >
                {ROLES.map((value) => (
                  <option
                    key={value}
                    value={value}
                    disabled={value === "policyholder" && holderTaken}
                  >
                    {POLICY_PERSON_ROLE_LABEL[value]}
                    {value === "policyholder" && holderTaken ? " (bereits vergeben)" : ""}
                  </option>
                ))}
              </select>
            </label>
            {shareLabel && (
              <label className="insurance-field">
                <span>{shareLabel} in Prozent</span>
                <input
                  type="text"
                  inputMode="decimal"
                  className="form-input"
                  placeholder="70"
                  value={share}
                  onChange={(event) => setShare(event.target.value)}
                />
              </label>
            )}
            <label className="insurance-field">
              <span>gilt ab (optional)</span>
              <input
                type="date"
                className="form-input"
                value={validFrom}
                onChange={(event) => setValidFrom(event.target.value)}
              />
            </label>
            <label className="insurance-field">
              <span>gilt bis (optional)</span>
              <input
                type="date"
                className="form-input"
                value={validTo}
                onChange={(event) => setValidTo(event.target.value)}
              />
            </label>
          </div>

          <p className="insurance-form-help">{POLICY_PERSON_ROLE_HINT[role]}</p>
          {shareLabel === "Beihilfebemessungssatz" && (
            <p className="insurance-form-help">
              Der Bemessungssatz sagt, welchen Anteil die Beihilfe trägt – 70 heißt 70 %. Er ist
              kein Geldbetrag und verändert keinen Beitrag.
            </p>
          )}

          <label className="insurance-field">
            <span>Notiz (optional)</span>
            <input
              type="text"
              className="form-input"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>

          {healthScoped && entries.length === 0 && (
            <p className="insurance-form-help">
              <strong>Was sich damit ändert:</strong> Mit der ersten Person verengt sich die
              Sichtbarkeit dieser Police auf Eigentümer und auf alle mit Freigabe für diese Person.
              Mitglieder ohne Freigabe finden sie danach nicht mehr in ihrer Liste.
            </p>
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
              {add.isPending ? "Wird gespeichert …" : "Person hinzufügen"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
