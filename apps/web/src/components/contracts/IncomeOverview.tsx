import { useState } from "react";
import { Chart } from "../charts/Chart";
import { cashflowOption } from "../charts/buildOptions";
import {
  useCreatePerson,
  useDeactivatePerson,
  useIncomeSummary,
  usePeople,
  useUpdatePerson,
} from "../../hooks/usePeople";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { ContractIncomeKind } from "../../lib/types";
import "./IncomeOverview.css";

// Einnahmen nach Person und Art (Plan Stufe 6).
//
// Sitzt in der Einnahmenansicht der Vertragsseite und nicht auf einer eigenen
// Seite: eine Einnahmequelle IST ein Vertrag (Abschnitt 3), eine zweite
// Verwaltung daneben waere Doppelarbeit. Was hier dazukommt, ist die
// Auswertung - nicht das Pflegen.

const KIND_LABELS: Record<ContractIncomeKind, string> = {
  salary: "Gehalt",
  self_employment: "Selbständigkeit",
  pension: "Rente",
  benefit: "Staatliche Leistung",
  rental: "Vermietung",
  capital: "Kapitalertrag",
  support: "Unterhalt",
  other: "Sonstiges",
};

const eur = formatMinorUnitsAsEuro;

export function IncomeOverview({ canEdit }: { canEdit: boolean }) {
  const summaryQuery = useIncomeSummary(24);
  const peopleQuery = usePeople();
  const createPerson = useCreatePerson();
  const updatePerson = useUpdatePerson();
  const deactivate = useDeactivatePerson();

  const [newName, setNewName] = useState("");
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Eine unerwartete Antwort darf die Vertragsseite nicht mitreissen: die
  // Auswertung ist eine Zugabe, nicht die Hauptsache dieser Seite.
  const raw = summaryQuery.data;
  const summary =
    raw && Array.isArray(raw.trend) && Array.isArray(raw.by_person) ? raw : null;
  const people = Array.isArray(peopleQuery.data) ? peopleQuery.data : [];

  const submitNew = () => {
    const name = newName.trim();
    if (!name) return;
    setError(null);
    createPerson.mutate(
      { name },
      {
        onSuccess: () => setNewName(""),
        onError: (err) => setError(err.message),
      },
    );
  };

  const submitRename = () => {
    if (!editing) return;
    const name = editing.name.trim();
    if (!name) return;
    setError(null);
    updatePerson.mutate(
      { personId: editing.id, input: { name } },
      {
        onSuccess: () => setEditing(null),
        onError: (err) => setError(err.message),
      },
    );
  };

  return (
    <div className="income-overview" data-testid="income-overview">
      {summaryQuery.isLoading ? (
        <p className="placeholder-note" role="status">
          Einnahmen werden ausgewertet…
        </p>
      ) : null}

      {summary ? (
        <>
          <div className="metric-grid">
            <div className="card metric-card">
              <span className="metric-label">Einnahmen ⌀ Monat</span>
              <span className="metric-value transaction-amount--positive">
                {eur(summary.monthly_income_minor)}
              </span>
              <span className="metric-hint">{people.length} Personen gepflegt</span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Fixkosten ⌀ Monat</span>
              <span className="metric-value">{eur(summary.monthly_fixed_minor)}</span>
              <span className="metric-hint">
                {summary.monthly_income_minor > 0
                  ? `${Math.round((summary.monthly_fixed_minor / summary.monthly_income_minor) * 100)} % der Einnahmen`
                  : "keine Einnahmen"}
              </span>
            </div>
            <div className="card metric-card">
              <span className="metric-label">Bleibt ⌀ Monat</span>
              <span
                className={`metric-value ${summary.monthly_income_minor - summary.monthly_fixed_minor < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}`}
              >
                {eur(summary.monthly_income_minor - summary.monthly_fixed_minor)}
              </span>
              <span className="metric-hint">vor variablen Ausgaben</span>
            </div>
          </div>

          <div className="card">
            <h3 className="card-heading">Einnahmen gegen Fixkosten</h3>
            <p className="card-note">
              Die Frage hinter dieser Kurve: ist mein Netto stärker gestiegen als meine Fixkosten?
            </p>
            <Chart
              option={cashflowOption(
                summary.trend.map((point) => ({
                  label: point.label,
                  income: point.income_minor,
                  expense: point.fixed_minor,
                  net: point.net_minor,
                })),
              )}
              height={240}
              description="Einnahmen und Fixkosten je Monat über 24 Monate"
              tableFallback={
                <div className="txn-table-wrapper">
                  <table className="txn-table" data-testid="income-trend">
                    <thead>
                      <tr>
                        <th className="txn-th">Monat</th>
                        <th className="txn-th txn-th--amount">Einnahmen</th>
                        <th className="txn-th txn-th--amount">Fixkosten</th>
                        <th className="txn-th txn-th--amount">Bleibt</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.trend.map((point) => (
                        <tr key={point.period_start}>
                          <td className="txn-td">{point.label}</td>
                          <td className="txn-td txn-td--amount transaction-amount--positive">
                            {eur(point.income_minor)}
                          </td>
                          <td className="txn-td txn-td--amount">{eur(point.fixed_minor)}</td>
                          <td
                            className={`txn-td txn-td--amount ${point.net_minor < 0 ? "transaction-amount--negative" : ""}`}
                          >
                            {eur(point.net_minor)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              }
            />
          </div>

          <div className="section-grid">
            <div className="card">
              <h3 className="card-heading">Nach Person</h3>
              {summary.by_person.length === 0 ? (
                <p className="placeholder-note">Keine Einnahme einer Person zugeordnet.</p>
              ) : (
                <div className="txn-table-wrapper">
                  <table className="txn-table" data-testid="income-by-person">
                    <thead>
                      <tr>
                        <th className="txn-th">Person</th>
                        <th className="txn-th txn-th--amount">Quellen</th>
                        <th className="txn-th txn-th--amount">⌀ Monat</th>
                        <th className="txn-th txn-th--amount">Anteil</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.by_person.map((bucket) => (
                        <tr key={bucket.person_id ?? "none"}>
                          <td className="txn-td">
                            {bucket.person_name ?? "Ohne Person"}
                            {bucket.person_is_active ? "" : " (entfernt)"}
                            <span className="income-kinds">
                              {bucket.by_kind
                                .map((k) =>
                                  k.income_kind ? KIND_LABELS[k.income_kind] : "Ohne Art",
                                )
                                .join(", ")}
                            </span>
                          </td>
                          <td className="txn-td txn-td--amount">{bucket.contract_count}</td>
                          <td className="txn-td txn-td--amount transaction-amount--positive">
                            {eur(bucket.monthly_minor)}
                          </td>
                          <td className="txn-td txn-td--amount">
                            {summary.monthly_income_minor > 0
                              ? `${Math.round((bucket.monthly_minor / summary.monthly_income_minor) * 100)} %`
                              : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="card">
              <h3 className="card-heading">Personen</h3>
              <p className="card-note">
                Namen sind je Haushalt eindeutig — auch über Groß- und Kleinschreibung hinweg.
              </p>
              <ul className="income-people">
                {people.map((person) => (
                  <li key={person.id}>
                    {editing?.id === person.id ? (
                      <>
                        <input
                          className="form-input"
                          value={editing.name}
                          aria-label={`Name von ${person.name}`}
                          onChange={(event) =>
                            setEditing({ id: person.id, name: event.target.value })
                          }
                        />
                        <button type="button" className="btn-primary" onClick={submitRename}>
                          Speichern
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => setEditing(null)}
                        >
                          Abbrechen
                        </button>
                      </>
                    ) : (
                      <>
                        <span className="income-person-name">{person.name}</span>
                        {canEdit ? (
                          <>
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={() => setEditing({ id: person.id, name: person.name })}
                            >
                              Umbenennen
                            </button>
                            <button
                              type="button"
                              className="btn-secondary"
                              aria-label={`${person.name} entfernen`}
                              onClick={() => deactivate.mutate(person.id)}
                            >
                              Entfernen
                            </button>
                          </>
                        ) : null}
                      </>
                    )}
                  </li>
                ))}
              </ul>
              {canEdit ? (
                <div className="income-people-add">
                  <input
                    className="form-input"
                    value={newName}
                    placeholder="Neue Person"
                    aria-label="Neue Person"
                    onChange={(event) => setNewName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") submitNew();
                    }}
                  />
                  <button type="button" className="btn-primary" onClick={submitNew}>
                    Hinzufügen
                  </button>
                </div>
              ) : null}
              {error ? (
                <p className="forecast-row-error" role="alert">
                  {error}
                </p>
              ) : null}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
