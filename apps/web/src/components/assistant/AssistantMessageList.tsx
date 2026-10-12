import { Fragment, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type {
  ChatDetail,
  ChatMessage,
  ChatSummary,
  ReasoningOverride,
} from "../../lib/assistantTypes";
import { type LiveRunState, liveActivity, liveAnswerText } from "../../lib/assistantLive";
import { describeError, formatCount, formatDateTimeShort, plural } from "../../lib/assistantFormat";
import { AI_SETTINGS_PATH, aiSettingsLabel } from "../../lib/aiReasons";
import { NumberMarkedText } from "./NumberMarkedText";
import { RunCard } from "./RunCard";

interface AssistantMessageListProps {
  chat: ChatDetail;
  /** Live-Zustand des gestreamten Laufs (vorderster aktiver Lauf) */
  live: LiveRunState | null;
  liveRunId: string | null;
  onRetry: (runId: string, reasoningOverride?: ReasoningOverride) => void;
  retryPending: boolean;
  retryError: string | null;
  onCancelRun: (runId: string) => void;
  onReconnect: () => void;
  onOpenCompact: () => void;
}

/** Wie viele Fragen stehen bis einschliesslich dieser Nachricht im Verlauf? */
function questionsUpTo(messages: ChatMessage[], messageId: string): number | null {
  const index = messages.findIndex((message) => message.id === messageId);
  if (index < 0) return null;
  return messages.slice(0, index + 1).filter((message) => message.role === "user").length;
}

/**
 * Strich im Verlauf: "Fragen 1–n zusammengefasst · X → Y Token ·
 * Zusammenfassung ansehen". Aufgeklappt der Text und die Karte des Laufs, in
 * dem die Zusammenfassung entstand.
 */
function SummaryLine({ summary, questions }: { summary: ChatSummary; questions: number | null }) {
  const [open, setOpen] = useState(false);
  const parts = [
    questions === null
      ? `Zusammenfassung V${summary.nr} passt nicht mehr zum Verlauf – sie wird beim nächsten Auftrag verworfen`
      : questions <= 1
        ? "Frage 1 zusammengefasst"
        : `Fragen 1–${questions} zusammengefasst`,
    summary.tokens_before !== null && summary.tokens_after !== null
      ? `${formatCount(summary.tokens_before)} → ${formatCount(summary.tokens_after)} Token`
      : null,
    summary.unverified_numbers
      ? `${plural(summary.unverified_numbers, "Zahl", "Zahlen")} ohne Beleg`
      : null,
    "Zusammenfassung ansehen",
  ].filter(Boolean);
  return (
    <li
      className={`assistant-summary${questions === null ? " assistant-summary--stale" : ""}`}
      data-testid="assistant-summary-line"
    >
      <button
        type="button"
        className="assistant-summary__toggle"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        {parts.join(" · ")}
      </button>
      {open && (
        <div className="assistant-summary__body">
          <p className="assistant-summary__meta">
            Verdichtung V{summary.nr} · {formatDateTimeShort(summary.created_at)} · Die Originale
            bleiben im Chat gespeichert und stehen im Laufprotokoll.
          </p>
          <div className="assistant-summary__text">{summary.text}</div>
          <RunCard runId={summary.run_id} summary={null} label="Verdichtungslauf" />
        </div>
      )}
    </li>
  );
}

function ErrorAnswer({
  message,
  onRetry,
  retryPending,
  retryError,
  onOpenCompact,
}: {
  message: ChatMessage;
  onRetry: AssistantMessageListProps["onRetry"];
  retryPending: boolean;
  retryError: string | null;
  onOpenCompact: () => void;
}) {
  const view = describeError(message.status, message.error_code);
  const runId = message.run_id;
  return (
    <div
      className={`assistant-error${message.status === "cancelled" ? " assistant-error--cancelled" : ""}`}
      role={message.status === "cancelled" ? undefined : "alert"}
    >
      <p className="assistant-error__headline">{view.headline}</p>
      {message.error_message && <p className="assistant-error__text">{message.error_message}</p>}
      {view.loadModelHint && <p className="assistant-error__text">{view.loadModelHint}</p>}
      {message.error_code && <p className="assistant-error__code">Kennung: {message.error_code}</p>}
      {message.content && <div className="assistant-error__partial">{message.content}</div>}
      {runId && (
        <div className="assistant-error__actions">
          {view.retryWithoutThinking && (
            <button
              type="button"
              className="btn-primary"
              disabled={retryPending}
              onClick={() => onRetry(runId, "off")}
            >
              Ohne Denken wiederholen
            </button>
          )}
          {view.compact && (
            <button type="button" className="btn-secondary" onClick={onOpenCompact}>
              Verdichten…
            </button>
          )}
          {view.settings && (
            <Link className="btn-secondary assistant-error__link" to={AI_SETTINGS_PATH}>
              {aiSettingsLabel(message.error_code)}
            </Link>
          )}
          <button
            type="button"
            className="btn-secondary"
            disabled={retryPending}
            onClick={() => onRetry(runId)}
          >
            Erneut versuchen
          </button>
        </div>
      )}
      {retryError && (
        <p className="assistant-error__text" role="alert">
          Wiederholen fehlgeschlagen: {retryError}
        </p>
      )}
    </div>
  );
}

function LiveAnswer({ state, onReconnect }: { state: LiveRunState; onReconnect: () => void }) {
  const text = liveAnswerText(state);
  return (
    <div className="assistant-live">
      <p
        className={`assistant-live__status${state.connection === "lost" ? " assistant-live__status--lost" : ""}`}
        aria-live="polite"
        data-testid="assistant-live-status"
      >
        <span className="assistant-live__pulse" aria-hidden="true" />
        {liveActivity(state)}
      </p>
      {state.connectionNote && (
        <p
          className="assistant-live__note"
          role={state.connection === "lost" ? "alert" : undefined}
        >
          {state.connectionNote}
          {state.connection === "lost" && (
            <>
              {" "}
              <button type="button" className="btn-inline" onClick={onReconnect}>
                Erneut verbinden
              </button>
            </>
          )}
        </p>
      )}
      {state.tools.length > 0 && (
        <ul className="assistant-live__tools">
          {state.tools.map((tool) => (
            <li
              key={tool.key}
              className={`assistant-live__tool assistant-live__tool--${tool.status}`}
            >
              <span className="assistant-run__kind assistant-run__kind--tool">{tool.key}</span>
              <span>{tool.name || "Werkzeug"}</span>
              <span className="assistant-live__tool-meta">
                {tool.status === "ok"
                  ? tool.rowCount !== null
                    ? plural(tool.rowCount, "Zeile", "Zeilen")
                    : "ok"
                  : tool.status === "running" || tool.status === "forming"
                    ? "läuft …"
                    : tool.status === "rejected"
                      ? "abgelehnt"
                      : tool.status === "cancelled"
                        ? "abgebrochen"
                        : "Fehler"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {text && (
        <div
          className="assistant-answer__text assistant-live__text"
          data-testid="assistant-live-text"
        >
          {text}
          <span className="assistant-live__cursor" aria-hidden="true" />
        </div>
      )}
    </div>
  );
}

/**
 * Verlauf eines Chats: Fragen, Antworten mit markierten Zahlen und
 * Laufkarte, Fehler mit passender Aktion, der laufende Auftrag live und der
 * Verdichtungsstrich hinter der letzten zusammengefassten Nachricht.
 */
export function AssistantMessageList({
  chat,
  live,
  liveRunId,
  onRetry,
  retryPending,
  retryError,
  onCancelRun,
  onReconnect,
  onOpenCompact,
}: AssistantMessageListProps) {
  const listRef = useRef<HTMLOListElement>(null);
  const stickToBottom = useRef(true);
  const messages = chat.messages;
  const summary = chat.summary;
  const summaryQuestions = summary
    ? questionsUpTo(messages, summary.covers_until_message_id)
    : null;
  const liveText = live ? liveAnswerText(live) : "";

  // Mitlaufen, solange der Nutzer unten liest; wer hochscrollt, wird nicht
  // zurueckgerissen.
  useEffect(() => {
    const list = listRef.current;
    if (list && stickToBottom.current) list.scrollTop = list.scrollHeight;
  }, [messages.length, liveText.length, live?.tools.length, live?.status]);

  let lastFailedRunId: string | null = null;
  for (const message of messages) {
    if (
      message.role === "assistant" &&
      (message.status === "failed" || message.status === "cancelled")
    ) {
      lastFailedRunId = message.run_id;
    }
  }

  return (
    <ol
      className="assistant-messages"
      ref={listRef}
      aria-label="Verlauf"
      onScroll={(event) => {
        const list = event.currentTarget;
        stickToBottom.current = list.scrollHeight - list.scrollTop - list.clientHeight < 48;
      }}
    >
      {messages.length === 0 && (
        <li className="assistant-empty">
          Noch keine Frage in diesem Chat. Die KI liest deine Buchungen, Konten, Budgets und
          Verträge – ändern kann sie nichts.
        </li>
      )}
      {messages.map((message) => {
        const isLive =
          message.role === "assistant" &&
          message.run_id !== null &&
          message.run_id === liveRunId &&
          live !== null &&
          !live.final &&
          (message.status === "queued" || message.status === "running");
        return (
          <Fragment key={message.id}>
            {message.role === "user" ? (
              <li className="assistant-msg assistant-msg--user">
                <div className="assistant-msg__bubble">{message.content}</div>
              </li>
            ) : (
              <li
                className={`assistant-msg assistant-msg--assistant assistant-msg--${message.status}`}
              >
                {isLive && live ? (
                  <LiveAnswer state={live} onReconnect={onReconnect} />
                ) : message.status === "done" ? (
                  <NumberMarkedText content={message.content} numbers={message.numbers} />
                ) : message.status === "failed" || message.status === "cancelled" ? (
                  <ErrorAnswer
                    message={message}
                    onRetry={onRetry}
                    retryPending={retryPending}
                    retryError={message.run_id === lastFailedRunId ? retryError : null}
                    onOpenCompact={onOpenCompact}
                  />
                ) : (
                  <p className="assistant-live__status">
                    <span className="assistant-live__pulse" aria-hidden="true" />
                    {message.status === "queued" ? "wartet in der Warteschlange" : "läuft …"}
                    {message.status === "queued" && message.run_id && (
                      <>
                        {" · "}
                        <button
                          type="button"
                          className="btn-inline"
                          onClick={() => onCancelRun(message.run_id!)}
                        >
                          Aus der Warteschlange nehmen
                        </button>
                      </>
                    )}
                  </p>
                )}
                {message.run_id && (
                  <RunCard
                    runId={message.run_id}
                    summary={message.run_summary}
                    live={isLive ? live : null}
                  />
                )}
              </li>
            )}
            {summary && summary.covers_until_message_id === message.id && (
              <SummaryLine summary={summary} questions={summaryQuestions} />
            )}
          </Fragment>
        );
      })}
      {summary && summaryQuestions === null && <SummaryLine summary={summary} questions={null} />}
    </ol>
  );
}
