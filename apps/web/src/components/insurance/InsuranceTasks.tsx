import { useFormatDate } from "../../hooks/useProfile";
import type { InsuranceTask, InsuranceTaskKind } from "../../lib/insuranceApi";
import { countdownLabel } from "./countdown";

/** Dringlichkeit je Aufgabenart. Sie faerbt nur den Streifen links - eine
 *  Ampel am Datum bekaeme sonst auch eine angenommene Frist. */
const KIND_TONE: Record<InsuranceTaskKind, "urgent" | "open" | "info"> = {
  deadline_approaching: "urgent",
  missing_main_due: "open",
  unconfirmed_main_due: "open",
  unknown_line: "open",
  missing_premium_source: "info",
};

/** Beschriftung der Hauptschaltflaeche je Aufgabenart. Sie nennt die Handlung,
 *  nicht den Zustand. */
const KIND_ACTION: Record<InsuranceTaskKind, string> = {
  deadline_approaching: "Frist ansehen",
  missing_main_due: "Hauptfälligkeit erfassen",
  unconfirmed_main_due: "Police öffnen",
  unknown_line: "Sparte zuordnen",
  missing_premium_source: "Beitrag klären",
};

export interface InsuranceTasksProps {
  tasks: InsuranceTask[];
  canEdit: boolean;
  busy: boolean;
  onOpenPolicy: (policyId: string) => void;
  /** Ein-Klick-Fall aus Plan 5.2: bestaetigt das vorgeschlagene Katalogdatum
   *  unveraendert. */
  onConfirmSuggestedMainDue: (policyId: string) => void;
}

export function InsuranceTasks({
  tasks,
  canEdit,
  busy,
  onOpenPolicy,
  onConfirmSuggestedMainDue,
}: InsuranceTasksProps) {
  const { formatDate } = useFormatDate();

  // Leer heisst: der Block verschwindet ganz. Ein "alles in Ordnung"-Kasten
  // wuerde eine Prueftiefe behaupten, die es nicht gibt (Plan 2.2).
  if (tasks.length === 0) {
    return null;
  }

  return (
    <section className="insurance-block" aria-labelledby="insurance-tasks-title">
      <div className="insurance-block-head">
        <h2 className="insurance-block-title" id="insurance-tasks-title">
          Jetzt wichtig
        </h2>
        <span className="insurance-block-hint">
          {tasks.length} {tasks.length === 1 ? "Sache geht" : "Sachen gehen"} ohne dich nicht weiter
        </span>
      </div>

      <ol className="card insurance-task-list">
        {tasks.map((task) => (
          <li
            key={`${task.kind}-${task.policy_id}`}
            className={`insurance-task insurance-task--${KIND_TONE[task.kind]}`}
          >
            <span className="insurance-task-stripe" aria-hidden="true" />
            <div className="insurance-task-main">
              <div className="insurance-task-line">
                <strong className="insurance-task-what">{task.what}</strong>
                <span className="insurance-task-policy">{task.policy_label}</span>
              </div>
              <p className="insurance-task-why">{task.why}</p>
            </div>
            <div className="insurance-task-side">
              {task.due_on && (
                <div className="insurance-task-when">
                  <span className="insurance-task-date">{formatDate(task.due_on)}</span>
                  {/* Drei Faelle, nicht zwei. Ein Countdown steht nur an einer
                      bestaetigten Frist; "angenommen" nur dort, wo die
                      Annahme belegt ist. Bei jeder anderen Aufgabenart ist
                      ueber die Sicherheit des Datums nichts bekannt - dann
                      steht dort nichts, statt eine Sicherheit zu behaupten
                      oder abzusprechen. */}
                  {task.kind === "deadline_approaching" && task.days_left !== null && (
                    <span className="insurance-task-days">{countdownLabel(task.days_left)}</span>
                  )}
                  {task.kind === "unconfirmed_main_due" && (
                    <span className="insurance-task-days insurance-task-days--assumed">
                      angenommen
                    </span>
                  )}
                </div>
              )}
              <div className="insurance-task-actions">
                {/* Nur die Bestaetigung haengt an der Rolle. Der Weg zur Police
                    ist rein lesend - haengt er mit dran, sieht ein Viewer die
                    Aufgabe, kommt ihr aber nicht nach. */}
                {canEdit && task.kind === "unconfirmed_main_due" && (
                  <button
                    type="button"
                    className="btn-primary insurance-inline-button"
                    disabled={busy}
                    onClick={() => onConfirmSuggestedMainDue(task.policy_id)}
                  >
                    Datum stimmt
                  </button>
                )}
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => onOpenPolicy(task.policy_id)}
                >
                  {canEdit ? KIND_ACTION[task.kind] : "Police öffnen"}
                </button>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
