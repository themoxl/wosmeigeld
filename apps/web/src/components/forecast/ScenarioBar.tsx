import { useState } from "react";
import {
  useCreateScenario,
  useDeleteScenario,
  useDuplicateScenario,
  useSaveScenarioChanges,
  useScenarios,
} from "../../hooks/useScenarios";
import { draftsToChanges, type ChangeDraft } from "./changeDrafts";

// Speichern, Laden und Verwalten von Szenarien (Plan Stufe 7).
//
// Der Entwurf im Editor bleibt der Normalfall - gespeichert wird nur, was man
// wiedersehen will. Deshalb ist das hier eine Leiste ueber dem Editor und
// keine eigene Seite: ein Szenario ist eine gespeicherte Aenderungsliste,
// nichts weiter.

export interface ScenarioBarProps {
  drafts: ChangeDraft[];
  /** Aktuell geladenes Szenario, null bei einem ungespeicherten Entwurf. */
  activeId: string | null;
  onLoad: (scenarioId: string | null) => void;
  /** Fuer den Vergleich ausgewaehlte Szenarien. */
  compared: string[];
  onCompare: (ids: string[]) => void;
  maxCompared: number;
  canEdit: boolean;
}

export function ScenarioBar({
  drafts,
  activeId,
  onLoad,
  compared,
  onCompare,
  maxCompared,
  canEdit,
}: ScenarioBarProps) {
  const scenariosQuery = useScenarios();
  const create = useCreateScenario();
  const saveChanges = useSaveScenarioChanges();
  const duplicate = useDuplicateScenario();
  const remove = useDeleteScenario();

  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const scenarios = scenariosQuery.data ?? [];
  const active = scenarios.find((s) => s.id === activeId) ?? null;
  const usable = draftsToChanges(drafts);

  const saveAsNew = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setError(null);
    create.mutate(
      { name: trimmed, changes: usable },
      {
        onSuccess: (detail) => {
          setNaming(false);
          setName("");
          onLoad(detail.id);
        },
        onError: (err) => setError(err.message),
      },
    );
  };

  const overwrite = () => {
    if (!active) return;
    setError(null);
    saveChanges.mutate(
      { scenarioId: active.id, changes: usable },
      { onError: (err) => setError(err.message) },
    );
  };

  const toggleCompare = (id: string) => {
    if (compared.includes(id)) {
      onCompare(compared.filter((x) => x !== id));
    } else if (compared.length < maxCompared) {
      onCompare([...compared, id]);
    }
  };

  return (
    <div className="scenario-bar" data-testid="scenario-bar">
      <div className="scenario-bar-head">
        <h2 className="card-heading">Szenarien</h2>
        {canEdit ? (
          <div className="scenario-bar-actions">
            {active ? (
              <button
                type="button"
                className="btn-secondary"
                disabled={saveChanges.isPending || usable.length === 0}
                onClick={overwrite}
              >
                {saveChanges.isPending ? "Wird gespeichert…" : `„${active.name}" überschreiben`}
              </button>
            ) : null}
            <button
              type="button"
              className="btn-primary"
              disabled={usable.length === 0}
              onClick={() => setNaming(true)}
            >
              Als Szenario speichern
            </button>
          </div>
        ) : null}
      </div>

      {naming ? (
        <div className="scenario-name-row">
          <input
            className="form-input"
            value={name}
            placeholder="Name des Szenarios"
            aria-label="Name des Szenarios"
            autoFocus
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") saveAsNew();
              if (event.key === "Escape") setNaming(false);
            }}
          />
          <button type="button" className="btn-primary" disabled={create.isPending} onClick={saveAsNew}>
            Speichern
          </button>
          <button type="button" className="btn-secondary" onClick={() => setNaming(false)}>
            Abbrechen
          </button>
        </div>
      ) : null}

      {scenarios.length === 0 ? (
        <p className="placeholder-note">
          Noch kein Szenario gespeichert. Ein Entwurf reicht für eine einzelne Frage — speichern
          lohnt sich, wenn du ihn wiedersehen willst.
        </p>
      ) : (
        <ul className="scenario-list">
          {scenarios.map((scenario) => (
            <li
              key={scenario.id}
              className={scenario.id === activeId ? "scenario-item scenario-item--active" : "scenario-item"}
            >
              <button
                type="button"
                className="scenario-open"
                onClick={() => onLoad(scenario.id === activeId ? null : scenario.id)}
              >
                <span className="scenario-name">{scenario.name}</span>
                <span className="scenario-meta">
                  {scenario.change_count} {scenario.change_count === 1 ? "Änderung" : "Änderungen"}
                  {scenario.origin === "contract_scenario" ? " · aus dem Fixkostenplan" : ""}
                </span>
              </button>
              <label className="scenario-compare">
                <input
                  type="checkbox"
                  checked={compared.includes(scenario.id)}
                  disabled={!compared.includes(scenario.id) && compared.length >= maxCompared}
                  aria-label={`${scenario.name} vergleichen`}
                  onChange={() => toggleCompare(scenario.id)}
                />
                <span>Vergleichen</span>
              </label>
              {canEdit ? (
                <span className="scenario-tools">
                  <button
                    type="button"
                    className="btn-secondary"
                    aria-label={`${scenario.name} kopieren`}
                    onClick={() => duplicate.mutate(scenario.id)}
                  >
                    Kopieren
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    aria-label={`${scenario.name} löschen`}
                    onClick={() => {
                      remove.mutate(scenario.id);
                      if (scenario.id === activeId) onLoad(null);
                      onCompare(compared.filter((x) => x !== scenario.id));
                    }}
                  >
                    Löschen
                  </button>
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {compared.length >= maxCompared ? (
        <p className="scenario-hint">
          Mehr als {maxCompared} Linien sind in einem Diagramm nicht mehr lesbar.
        </p>
      ) : null}

      {error ? (
        <p className="forecast-row-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

