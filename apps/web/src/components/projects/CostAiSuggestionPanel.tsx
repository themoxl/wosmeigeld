import { useState } from "react";
import {
  isAiTaskWorking,
  useAiTask,
  useCompleteAiTask,
  useEnqueueAiTask,
} from "../../hooks/useAiTaskQueue";
import { useApplyCostAiSuggestions } from "../../hooks/useProjects";
import { newRequestId } from "../../hooks/useProjectCostImport";
import { apiErrorDetail } from "../../lib/api";
import { aiReasonCodeIn } from "../../lib/aiReasons";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import type { CostAiSuggestionPreview, CostAiSuggestionResult } from "../../lib/types";
import { AiReasonNotice } from "../AiReasonNotice";
import { Modal } from "../Modal";

// Vorschlaege ab dieser Konfidenz sind vorausgewaehlt - derselbe Schwellenwert
// und dieselbe Begruendung wie bei den Kategorienvorschlaegen (CategoriesPage):
// der haeufige Fall ist "passt", der seltene braucht einen Blick.
const SAFE_SUGGESTION_CONFIDENCE = 0.75;

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

interface CostAiSuggestionPanelProps {
  projectId: string;
  scenarioId: string;
  initialTaskId?: string | null;
  onClose: () => void;
}

/**
 * KI-Kostenschaetzung: aus einer Freitextbeschreibung des Vorhabens schlaegt
 * das lokale Modell Schaetzwerte fuer die bestehenden Kostenpositionen des
 * Szenarios vor (Nachtrag zum Plan nach P0-P5, siehe
 * VORHABEN-GROSSINVESTITIONEN-PLAN.md).
 *
 * Dieselbe zweistufige Vorschau/Uebernehmen-Mechanik wie die
 * Kategorienvorschlaege: der Lauf startet erst auf Knopfdruck, und
 * uebernommen wird ausschliesslich, was hier je Position bestaetigt ist -
 * "KI schlaegt vor, Mensch entscheidet" gilt hier genauso.
 */
export function CostAiSuggestionPanel({
  projectId,
  scenarioId,
  initialTaskId = null,
  onClose,
}: CostAiSuggestionPanelProps) {
  const enqueueTask = useEnqueueAiTask();
  const applyMutation = useApplyCostAiSuggestions();
  const completeTask = useCompleteAiTask();

  const [description, setDescription] = useState("");
  const [useWebSearch, setUseWebSearch] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(initialTaskId);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [result, setResult] = useState<CostAiSuggestionResult | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const taskQuery = useAiTask(taskId);
  const task = taskQuery.data;
  const working = enqueueTask.isPending || taskQuery.isLoading || isAiTaskWorking(task);
  const taskPreview =
    task?.task_type === "project_cost_estimate" &&
    (task.status === "awaiting_confirmation" || task.status === "completed") &&
    task.result?.operation_id
      ? (task.result as unknown as CostAiSuggestionPreview)
      : null;
  const preview = result ? null : taskPreview;

  const taskError =
    task?.task_type !== undefined && task.task_type !== "project_cost_estimate"
      ? "Diese KI-Aufgabe gehört nicht zu einer Kostenschätzung."
      : task?.status === "failed"
        ? (task.error_message ?? "Die KI-Kostenschätzung ist technisch fehlgeschlagen.")
        : task?.status === "cancelled"
          ? "Die KI-Kostenschätzung wurde abgebrochen."
          : taskQuery.isError
            ? "Der gespeicherte KI-Status konnte nicht geladen werden."
            : null;
  // Grund des KI-Kerns zum Fehlschlag: die gescheiterte Vorschau steht samt
  // `error_code` (model_timeout, model_budget_exhausted, …) im Ergebnis der
  // Aufgabe; sonst steht er hoechstens als Kennung im Text.
  const failedCode =
    task?.status === "failed"
      ? ((typeof task.result?.error_code === "string" ? task.result.error_code : null) ??
        aiReasonCodeIn(task.error_message))
      : null;

  const startRun = () => {
    if (!description.trim()) {
      setFormError("Bitte kurz beschreiben, worum es bei dem Vorhaben geht.");
      return;
    }
    setFormError(null);
    setResult(null);
    setSelected({});
    setAmounts({});
    setTaskId(null);
    enqueueTask.mutate(
      {
        task_type: "project_cost_estimate",
        input: {
          project_id: projectId,
          scenario_id: scenarioId,
          description: description.trim(),
          use_web_search: useWebSearch,
        },
      },
      {
        onSuccess: (created) => setTaskId(created.id),
        onError: (error) => {
          setFormError(errorText(error, "Die Kostenschätzung konnte nicht gestartet werden."));
        },
      },
    );
  };

  const proposedItems = preview
    ? preview.suggestions.filter((item) => item.proposed_estimate_minor !== null)
    : [];
  const untouchedItems = preview
    ? preview.suggestions.filter((item) => item.proposed_estimate_minor === null)
    : [];
  const isSelected = (costItemId: string, confidence: number) =>
    selected[costItemId] ?? confidence >= SAFE_SUGGESTION_CONFIDENCE;
  const amountFor = (costItemId: string, proposedMinor: number | null) =>
    amounts[costItemId] ??
    (proposedMinor === null ? "" : String(proposedMinor / 100).replace(".", ","));
  const acceptedItems = proposedItems.filter((item) =>
    isSelected(item.cost_item_id, item.confidence),
  );

  const handleApply = () => {
    if (!preview) return;
    setFormError(null);
    applyMutation.mutate(
      {
        projectId,
        scenarioId,
        input: {
          operation_id: preview.operation_id,
          request_id: newRequestId(),
          accepted: acceptedItems.map((item) => ({
            cost_item_id: item.cost_item_id,
            estimate_minor:
              parseAmountToMinorUnits(amountFor(item.cost_item_id, item.proposed_estimate_minor)) ??
              0,
          })),
        },
      },
      {
        onSuccess: (data) => {
          setResult(data);
          if (taskId) completeTask.mutate(taskId);
        },
        onError: (error) => {
          setFormError(errorText(error, "Die Vorschläge konnten nicht übernommen werden."));
          if (apiErrorDetail(error) === "cost_ai_preview_stale") setTaskId(null);
        },
      },
    );
  };

  return (
    <Modal title="KI-Kostenvorschlag" size="wide" onClose={onClose}>
      <div className="edit-transaction-form">
        {!preview && !result && !working && (
          <>
            <p className="card-body">
              Beschreibe das Vorhaben in eigenen Worten – zum Beispiel ob ein Grundstück bereits
              vorhanden oder geerbt ist, ob es ein Neubau oder ein Hauskauf ist, und wo. Das lokale
              Modell schlägt daraus Schätzwerte für die bestehenden Kostenpositionen dieses
              Szenarios vor – in einem Aufruf mit Reasoning und Antwortbudget aus Einstellungen ›
              KI. Übernommen wird nur, was du unten bestätigst.
            </p>
            <div className="form-field">
              <label className="form-label" htmlFor="cost-ai-description">
                Beschreibung
              </label>
              <textarea
                id="cost-ai-description"
                className="form-input"
                rows={5}
                maxLength={4000}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Z. B.: Grundstück von den Eltern geerbt, kein Kaufpreis. Sonst normaler Hausbau in Regensburg, Einfamilienhaus, Keller, ca. 150 m²."
              />
            </div>
            <label className="suggestion-checkbox">
              <input
                type="checkbox"
                checked={useWebSearch}
                onChange={(event) => setUseWebSearch(event.target.checked)}
              />
              Websuche zur Einordnung nutzen (SearXNG, falls in den Einstellungen hinterlegt)
            </label>
            {(formError || taskError) && (
              <AiReasonNotice
                role="alert"
                className="status-error"
                code={formError ? null : failedCode}
                message={formError ?? taskError}
                suffix="Details und alle Modellschritte findest du im KI-Status."
              />
            )}
            <div className="modal-actions">
              <button type="button" className="btn-secondary" onClick={onClose}>
                Abbrechen
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={enqueueTask.isPending}
                onClick={startRun}
              >
                {enqueueTask.isPending ? "Wird eingereiht…" : "Vorschlag erstellen"}
              </button>
            </div>
          </>
        )}

        {working && !preview && (
          <>
            <p role="status">
              {task?.status_message ?? "Die Kostenschätzung wird in die KI-Warteschlange gelegt…"}
            </p>
            <p className="placeholder-note">
              Du kannst dieses Fenster schließen. Der Lauf arbeitet im Hintergrund weiter und bleibt
              oben im KI-Status sichtbar.
            </p>
            <div className="modal-actions">
              <button type="button" className="btn-secondary" onClick={onClose}>
                Im Hintergrund weiterlaufen lassen
              </button>
            </div>
          </>
        )}

        {preview && (
          <>
            <p className="card-body" role="status">
              {proposedItems.length} von {preview.suggestions.length} Positionen geschätzt
              {preview.model_name ? ` · Modell: ${preview.model_name}` : ""}
            </p>
            {preview.search_requested && (
              <p className="placeholder-note">
                {preview.search_performed
                  ? `Websuche genutzt${
                      (preview.search_snippets ?? []).length > 0
                        ? ` (${(preview.search_snippets ?? []).length} Treffer)`
                        : " (keine Treffer)"
                    }.`
                  : (preview.search_unavailable_reason ?? "Websuche nicht durchgeführt.")}
              </p>
            )}

            {proposedItems.length === 0 ? (
              <p className="placeholder-note">
                Das Modell konnte für keine Position eine Schätzung begründen. Formuliere die
                Beschreibung genauer oder trage die Werte manuell ein.
              </p>
            ) : (
              <ul className="suggestion-list">
                {proposedItems.map((item) => {
                  const checked = isSelected(item.cost_item_id, item.confidence);
                  return (
                    <li key={item.cost_item_id} className="suggestion-item">
                      <label className="suggestion-head">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(event) =>
                            setSelected((current) => ({
                              ...current,
                              [item.cost_item_id]: event.target.checked,
                            }))
                          }
                        />
                        <span className="suggestion-title">
                          {item.section_name} · {item.item_name}
                          <span className="suggestion-meta">
                            bisher {formatMinorUnitsAsEuro(item.current_estimate_minor)}
                          </span>
                        </span>
                        <span
                          className={
                            item.confidence >= SAFE_SUGGESTION_CONFIDENCE
                              ? "status-badge status-badge--neutral"
                              : "status-badge status-badge--warning"
                          }
                        >
                          {Math.round(item.confidence * 100)} %
                        </span>
                      </label>
                      <div className="form-field">
                        <label
                          className="form-label"
                          htmlFor={`cost-ai-amount-${item.cost_item_id}`}
                        >
                          Vorgeschlagener Wert (€)
                        </label>
                        <input
                          id={`cost-ai-amount-${item.cost_item_id}`}
                          className="form-input form-input--mono"
                          value={amountFor(item.cost_item_id, item.proposed_estimate_minor)}
                          onChange={(event) =>
                            setAmounts((current) => ({
                              ...current,
                              [item.cost_item_id]: event.target.value,
                            }))
                          }
                        />
                      </div>
                      {item.rationale && <p className="suggestion-reason">{item.rationale}</p>}
                    </li>
                  );
                })}
              </ul>
            )}

            {untouchedItems.length > 0 && (
              <p className="placeholder-note">
                Ohne Vorschlag geblieben: {untouchedItems.map((item) => item.item_name).join(", ")}.
              </p>
            )}

            {formError && (
              <p role="alert" className="status-error">
                {formError}
              </p>
            )}

            <div className="modal-actions">
              <button type="button" className="btn-secondary" onClick={onClose}>
                Abbrechen
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={acceptedItems.length === 0 || applyMutation.isPending}
                onClick={handleApply}
              >
                {applyMutation.isPending
                  ? "Wird übernommen…"
                  : `${acceptedItems.length} Position${acceptedItems.length === 1 ? "" : "en"} übernehmen`}
              </button>
            </div>
          </>
        )}

        {result && (
          <>
            <p className="card-body" role="status">
              {result.updated_count} Position{result.updated_count === 1 ? "" : "en"} übernommen.
            </p>
            <div className="modal-actions">
              <button type="button" className="btn-primary" onClick={onClose}>
                Schließen
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
