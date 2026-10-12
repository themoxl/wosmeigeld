import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  useAiTask,
  useAiTasks,
  useCancelAiTask,
  useCompleteAiTask,
  useDeleteAiTask,
  useForceCancelAiTask,
  useRetryAiTask,
} from "../hooks/useAiTaskQueue";
import type { AITask, AITaskStatus, AITaskType } from "../lib/aiTaskTypes";
import { AiRuntimeStrip } from "./AiRuntimeStrip";

const TYPE_LABEL: Record<AITaskType, string> = {
  category_suggestions: "Kategorien vorschlagen",
  transaction_classification: "Offene Buchungen klassifizieren",
  contract_duplicate_crosscheck: "Verträge gegenprüfen",
  contract_transaction_check: "Freie Vertragsbuchungen prüfen",
  project_cost_estimate: "Vorhabenkosten schätzen",
  health_document_extraction: "Gesundheitsdokumente auswerten",
  assistant_chat: "KI-Frage beantworten",
};

const STATUS_LABEL: Record<AITaskStatus, string> = {
  queued: "Wartet",
  running: "Läuft",
  awaiting_confirmation: "Zur Prüfung",
  completed: "Erledigt",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
};

function progressText(task: AITask): string | null {
  if (task.progress_total == null) return null;
  return `${task.progress_current} von ${task.progress_total}`;
}

/** "vor 12 Sekunden" / "vor 4 Minuten" — null, wenn kein Zeitpunkt vorliegt. */
function agoText(iso: string | null): string | null {
  if (!iso) return null;
  const milliseconds = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(milliseconds) || milliseconds < 0) return null;
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `vor ${seconds} Sekunden`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `vor ${minutes} Minute${minutes === 1 ? "" : "n"}`;
  const hours = Math.round(minutes / 60);
  return `vor ${hours} Stunde${hours === 1 ? "" : "n"}`;
}

/**
 * Der Satz, der während eines Laufs im Fenster steht. Wichtig ist der Fall
 * "Abbruch angefordert": vorher endete die Auskunft dort und ließ offen, wie
 * lange das noch dauert und was der Nutzer tun kann.
 */
function messageText(task: AITask): string {
  if (task.status !== "running") return task.status_message ?? STATUS_LABEL[task.status];
  if (task.is_stale) {
    const last = agoText(task.heartbeat_at ?? task.started_at);
    return (
      `Der Verarbeitungsdienst meldet sich nicht mehr${last ? ` (letztes Lebenszeichen ${last})` : ""}. ` +
      "Beende die Aufgabe, um die Warteschlange wieder freizugeben."
    );
  }
  if (task.cancel_requested) {
    const requested = agoText(task.cancel_requested_at);
    return (
      `Abbruch angefordert${requested ? ` ${requested}` : ""} – der laufende Modellaufruf ` +
      "wird noch beendet, damit kein halbes Ergebnis zurückbleibt. Dauert das zu lange, " +
      "beende die Aufgabe sofort; bereits geprüfte Buchungen bleiben erhalten."
    );
  }
  return task.status_message ?? STATUS_LABEL[task.status];
}

function TaskRow({ task, onNavigate }: { task: AITask; onNavigate: (path: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const detail = useAiTask(expanded ? task.id : null);
  const cancel = useCancelAiTask();
  const forceCancel = useForceCancelAiTask();
  const complete = useCompleteAiTask();
  const retry = useRetryAiTask();
  const deleteTask = useDeleteAiTask();
  const isWorking = task.status === "queued" || task.status === "running";
  const canOpen = task.status === "awaiting_confirmation" || task.status === "completed";
  // Die Notbremse erscheint erst, wenn die schonende Bitte gestellt ist (oder
  // der Dienst schweigt) — genau die Lage, in der "Abbrechen" folgenlos wirkte.
  const canForce = task.status === "running" && (task.cancel_requested || task.is_stale);
  // Der Server lehnt queued/running ohnehin ab (ai_task_not_deletable) - erst
  // abbrechen, dann loeschen, damit nichts aus der Liste verschwindet, waehrend
  // der Worker noch daran schreibt.
  const canDelete =
    task.status === "completed" || task.status === "failed" || task.status === "cancelled";

  return (
    <article className={`ai-task ai-task--${task.status}`}>
      <div className="ai-task__heading">
        <div>
          <h3>{TYPE_LABEL[task.task_type]}</h3>
          <p>
            <span
              className={`status-badge status-badge--${task.status === "failed" ? "warning" : task.status === "running" ? "highlight" : "neutral"}`}
            >
              {STATUS_LABEL[task.status]}
            </span>
            {progressText(task) && <span className="ai-task__progress">{progressText(task)}</span>}
          </p>
        </div>
        <time dateTime={task.created_at}>
          {new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "short" }).format(
            new Date(task.created_at),
          )}
        </time>
      </div>

      <p className="ai-task__message">{messageText(task)}</p>
      {task.error_message && <p className="status-error">{task.error_message}</p>}

      <div className="ai-task__actions">
        {canOpen && (
          <button type="button" className="btn-primary" onClick={() => onNavigate(task.route_path)}>
            Ergebnis öffnen
          </button>
        )}
        {isWorking && !canForce && (
          <button
            type="button"
            className="btn-secondary"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate(task.id)}
          >
            Abbrechen
          </button>
        )}
        {canForce && (
          <button
            type="button"
            className="btn-secondary ai-task__force"
            disabled={forceCancel.isPending}
            onClick={() => forceCancel.mutate(task.id)}
          >
            {forceCancel.isPending ? "Wird beendet …" : "Sofort beenden"}
          </button>
        )}
        {task.status === "awaiting_confirmation" && (
          <button
            type="button"
            className="btn-secondary"
            disabled={complete.isPending}
            onClick={() => complete.mutate(task.id)}
          >
            Als erledigt markieren
          </button>
        )}
        {(task.status === "failed" || task.status === "cancelled") && (
          <button
            type="button"
            className="btn-secondary"
            disabled={retry.isPending}
            onClick={() => retry.mutate(task.id)}
          >
            Erneut einreihen
          </button>
        )}
        <button type="button" className="btn-ghost" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Aktivität ausblenden" : "Aktivität anzeigen"}
        </button>
        {canDelete && (
          <button
            type="button"
            className="btn-ghost ai-task__delete"
            disabled={deleteTask.isPending}
            onClick={() => {
              if (window.confirm("Diesen Verlaufseintrag dauerhaft löschen?")) {
                deleteTask.mutate(task.id);
              }
            }}
          >
            Löschen
          </button>
        )}
      </div>

      {expanded && (
        <ol className="ai-task-events" aria-label="Aktivitätsverlauf">
          {detail.isLoading && <li>Aktivität wird geladen …</li>}
          {detail.data?.events.map((event) => (
            <li key={event.id}>
              <time dateTime={event.created_at}>
                {new Intl.DateTimeFormat("de-DE", { timeStyle: "short" }).format(
                  new Date(event.created_at),
                )}
              </time>
              <span>{event.message}</span>
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}

export function AiTaskQueuePanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const tasksQuery = useAiTasks();
  const navigate = useNavigate();
  const closeButton = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    closeButton.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const tasks = tasksQuery.data ?? [];
  return (
    <div className="ai-queue-overlay" role="presentation" onMouseDown={onClose}>
      <aside
        className="ai-queue-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-queue-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="ai-queue-panel__header">
          <div>
            <p className="eyebrow">Status, Ursache & Verlauf</p>
            <h2 id="ai-queue-title">KI-Status & Verlauf</h2>
          </div>
          <button ref={closeButton} type="button" className="btn-ghost" onClick={onClose}>
            Schließen
          </button>
        </header>
        <p className="ai-queue-panel__intro">
          Hier siehst du jederzeit, woran die KI arbeitet und warum ein Lauf fehlgeschlagen ist.
          Ergebnisse und technische Schritte bleiben gespeichert; fachliche Änderungen bestätigst
          weiterhin du.
        </p>
        <AiRuntimeStrip active={open} />
        <div className="ai-queue-live" aria-live="polite">
          {tasksQuery.isLoading && <p>Warteschlange wird geladen …</p>}
          {tasksQuery.isError && (
            <p className="status-error">Die Warteschlange ist nicht erreichbar.</p>
          )}
          {!tasksQuery.isLoading && !tasksQuery.isError && tasks.length === 0 && (
            <p className="placeholder-note">Noch keine KI-Aufgabe vorhanden.</p>
          )}
          {tasks.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              onNavigate={(path) => {
                onClose();
                void navigate(path);
              }}
            />
          ))}
        </div>
      </aside>
    </div>
  );
}
