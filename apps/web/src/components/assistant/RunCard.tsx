import { useState } from "react";
import { useAssistantRunLog } from "../../hooks/useAssistant";
import type {
  MessageStatus,
  RunCall,
  RunEvent,
  RunLog,
  RunSummary,
  RunToolResult,
} from "../../lib/assistantTypes";
import { type LiveRunState, type LiveTool, liveActivity } from "../../lib/assistantLive";
import {
  describeBudget,
  describeBudgetParts,
  formatCount,
  formatDateTimeShort,
  formatSeconds,
  plural,
} from "../../lib/assistantFormat";

// Laufkarte einer Antwort (Vorbild: sia_public ki_protokoll_karte.js).
//
//   Kopf         Art, antwortendes Modell, Dauer, Status
//   Messwerte    Aufrufe, Werkzeuge, Token, Fenster, Denken
//   Warnungen    ganz oben - was frueher still auswich, steht hier
//   Ablauf       je Modellaufruf eine Zeile mit Budgetzeile (Fenster, Eingabe,
//                Abstand, Reserve, Antwortbudget), davor seine Ereignisse,
//                danach seine Werkzeugergebnisse GENAU so, wie das Modell sie
//                bekam
//
// Zugeklappt kostet die Karte nichts: das Protokoll (GET /runs/{id}) wird
// erst beim Aufklappen geladen. Solange der Lauf laeuft, zeigt sie den
// Live-Zustand aus dem Strom - nur dann gibt es den Denktext.

const STATUS_LABEL: Record<MessageStatus, string> = {
  queued: "wartet",
  running: "läuft",
  done: "fertig",
  failed: "fehlgeschlagen",
  cancelled: "abgebrochen",
};

const END_REASON_LABEL: Record<NonNullable<RunLog["end_reason"]>, string> = {
  answer: "Antwort gegeben",
  stall: "Stillstand – zweimal nichts Neues",
  runtime: "Laufzeitgrenze erreicht",
  cancelled: "abgebrochen",
  failed: "Fehler",
};

const TOOL_STATUS_LABEL: Record<LiveTool["status"], string> = {
  forming: "Argumente entstehen …",
  running: "läuft …",
  ok: "ok",
  rejected: "abgelehnt",
  error: "Fehler",
  cancelled: "abgebrochen",
};

/** Werkzeugergebnisse koennen gross sein; gezeigt wird der Anfang. */
const RESULT_PREVIEW_CHARS = 6000;
const THINK_PREVIEW_CHARS = 4000;

const isLoud = (event: RunEvent) => event.level === "warnung" || event.level === "fehler";

function summaryLine(label: string, summary: RunSummary | null): string {
  if (!summary) return label;
  return [
    label,
    plural(summary.queries, "Abfrage", "Abfragen"),
    plural(summary.warnings, "Warnung", "Warnungen"),
    `${plural(summary.unverified_numbers, "Zahl", "Zahlen")} ohne Beleg`,
  ].join(" · ");
}

function liveSummaryLine(label: string, state: LiveRunState): string {
  const parts = [`${label} ${state.status === "queued" ? "wartet" : "läuft"}`];
  if (state.calls.length) parts.push(`Aufruf ${state.calls[state.calls.length - 1]!.nr}`);
  const done = state.tools.filter((tool) => tool.status === "ok").length;
  if (state.tools.length) parts.push(plural(done, "Abfrage", "Abfragen"));
  const warnings = state.events.filter(isLoud).length;
  if (warnings) parts.push(plural(warnings, "Warnung", "Warnungen"));
  return parts.join(" · ");
}

function argsPreview(args: Record<string, unknown> | null): string {
  if (!args) return "";
  const text = JSON.stringify(args);
  return text.length > 90 ? `${text.slice(0, 90)} …` : text;
}

function clip(text: string, limit: number): string {
  return text.length > limit
    ? `${text.slice(0, limit)}\n… (${formatCount(text.length)} Zeichen, gekürzt angezeigt)`
    : text;
}

function Warnings({ events, error }: { events: RunEvent[]; error?: string | null }) {
  const loud = events.filter(isLoud);
  if (!loud.length && !error) return null;
  return (
    <ul className="assistant-run__warnings" data-testid="assistant-run-warnings">
      {error && (
        <li className="assistant-run__warning assistant-run__warning--fehler">✕ {error}</li>
      )}
      {loud.map((event) => (
        <li
          key={event.seq}
          className={`assistant-run__warning assistant-run__warning--${event.level}`}
        >
          {event.level === "fehler" ? "✕" : "⚠"} {event.message}
        </li>
      ))}
    </ul>
  );
}

function EventRow({ event }: { event: RunEvent }) {
  return (
    <li
      className={`assistant-run__row assistant-run__row--event assistant-run__row--${event.level}`}
    >
      <span className="assistant-run__pill">{event.kind}</span>
      <span>{event.message}</span>
    </li>
  );
}

function BudgetLine({ call }: { call: Pick<RunCall, "budget" | "usage"> }) {
  if (!call.budget) return null;
  const measured = call.usage.prompt;
  const overshoot = measured !== null && measured > call.budget.E + call.budget.A;
  return (
    <p className="assistant-run__budget" title={describeBudgetParts(call.budget)}>
      {describeBudget(call.budget)}
      {measured !== null && <> · gemessen {formatCount(measured)}</>}
      {overshoot && <span className="assistant-run__overshoot"> · über Schätzung + Abstand</span>}
    </p>
  );
}

function callHeadline(call: RunCall): string {
  if (call.error) return `Fehler: ${call.error.slice(0, 140)}`;
  if (call.tool_call_names.length) return `ruft ${call.tool_call_names.join(", ")}`;
  if (call.finish_reason === "length") return "abgeschnitten – Antwortbudget erreicht";
  return "antwortet";
}

function CallRow({ call }: { call: RunCall }) {
  const meta = [
    call.duration_ms !== null ? formatSeconds(call.duration_ms) : null,
    call.tokens_per_s !== null
      ? `${call.tokens_per_s.toLocaleString("de-DE", { maximumFractionDigits: 1 })} Token/s`
      : null,
    call.usage.reasoning ? `${formatCount(call.usage.reasoning)} Denk-Token` : null,
  ].filter(Boolean);
  const details = [
    call.model,
    call.ttft_ms !== null ? `erstes Token nach ${formatSeconds(call.ttft_ms)}` : null,
    call.usage.prompt !== null || call.usage.completion !== null
      ? `${formatCount(call.usage.prompt)} Token gelesen · ${formatCount(call.usage.completion)} erzeugt`
      : null,
    call.finish_reason ? `Ende: ${call.finish_reason}` : null,
  ].filter(Boolean);
  return (
    <li className={`assistant-run__row${call.error ? " assistant-run__row--fehler" : ""}`}>
      <details>
        <summary>
          <span className="assistant-run__kind">Modell</span>
          <span className="assistant-run__what">
            {call.step ? `${call.step} · ` : ""}
            {callHeadline(call)}
          </span>
          <span className="assistant-run__meta">{meta.join(" · ")}</span>
        </summary>
        <div className="assistant-run__detail">
          {call.retry_reason && (
            <p className="assistant-run__note">
              Wiederholung{call.retry_of !== null ? ` von Aufruf ${call.retry_of}` : ""}:{" "}
              {call.retry_reason}
            </p>
          )}
          <BudgetLine call={call} />
          {details.length > 0 && <p className="assistant-run__note">{details.join(" · ")}</p>}
          {call.tools_offered.length > 0 && (
            <p className="assistant-run__note">Angeboten: {call.tools_offered.join(", ")}</p>
          )}
          {call.usage.reasoning ? (
            <p className="assistant-run__note">
              Denktext wurde nicht gespeichert ({formatCount(call.usage.reasoning)} Token).
            </p>
          ) : null}
          {call.answer_text && (
            <details className="assistant-run__more">
              <summary>
                Antwort dieses Aufrufs · {formatCount(call.answer_text.length)} Zeichen
              </summary>
              <pre className="assistant-run__pre">
                {clip(call.answer_text, RESULT_PREVIEW_CHARS)}
              </pre>
            </details>
          )}
        </div>
      </details>
    </li>
  );
}

function ToolRow({ tool }: { tool: RunToolResult }) {
  const meta = [
    tool.status === "ok"
      ? tool.row_count !== null
        ? plural(tool.row_count, "Zeile", "Zeilen")
        : "ok"
      : TOOL_STATUS_LABEL[tool.status],
    tool.duration_ms !== null ? formatSeconds(tool.duration_ms) : null,
  ].filter(Boolean);
  return (
    <li
      className={`assistant-run__row${tool.status === "ok" ? "" : " assistant-run__row--warnung"}`}
    >
      <details>
        <summary>
          <span className="assistant-run__kind assistant-run__kind--tool">{tool.key}</span>
          <span className="assistant-run__what">
            {tool.tool_name} {argsPreview(tool.args)}
          </span>
          <span className="assistant-run__meta">{meta.join(" · ")}</span>
        </summary>
        <div className="assistant-run__detail">
          <pre className="assistant-run__pre">{JSON.stringify(tool.args, null, 1)}</pre>
          <p className="assistant-run__note">
            <strong>Das hat das Modell zurückbekommen</strong> ·{" "}
            {formatCount(tool.result_text.length)} Zeichen
          </p>
          <pre className="assistant-run__pre">{clip(tool.result_text, RESULT_PREVIEW_CHARS)}</pre>
        </div>
      </details>
    </li>
  );
}

function RunLogView({ log }: { log: RunLog }) {
  const started = log.started_at ? new Date(log.started_at).getTime() : null;
  const finished = log.finished_at ? new Date(log.finished_at).getTime() : null;
  const duration =
    started !== null && finished !== null ? finished - started : log.totals.duration_ms;
  const quiet = log.events.filter((event) => !isLoud(event) && event.call_nr === null);
  const knownCalls = new Set(log.calls.map((call) => call.nr));
  const lastCall = log.calls[log.calls.length - 1];
  const contextWindow = log.window;
  const metrics = [
    plural(log.totals.calls, "Modellaufruf", "Modellaufrufe"),
    plural(log.totals.tool_calls, "Werkzeugaufruf", "Werkzeugaufrufe"),
    `${formatCount(log.totals.prompt_tokens)} Token gelesen`,
    `${formatCount(log.totals.completion_tokens)} erzeugt`,
    log.totals.reasoning_tokens ? `${formatCount(log.totals.reasoning_tokens)} Denk-Token` : null,
    lastCall?.tokens_per_s
      ? `${lastCall.tokens_per_s.toLocaleString("de-DE", { maximumFractionDigits: 1 })} Token/s`
      : null,
    log.reasoning_sent ? `Denken: ${log.reasoning_sent}` : null,
  ].filter(Boolean);

  return (
    <>
      <div className="assistant-run__head">
        <span
          className={`assistant-run__dot assistant-run__dot--${log.status}`}
          aria-hidden="true"
        />
        <div className="assistant-run__title">
          <strong>{log.mode === "compact" ? "Verdichtung" : "Antwort"}</strong>
          <span>
            {[log.model_answering ?? log.model_configured, STATUS_LABEL[log.status]]
              .filter(Boolean)
              .join(" · ")}
            {log.end_reason ? ` · ${END_REASON_LABEL[log.end_reason]}` : ""}
          </span>
        </div>
        <span className="assistant-run__clock" title="Dauer des Laufs">
          {formatSeconds(duration)}
        </span>
      </div>
      <p className="assistant-run__metrics">
        {metrics.map((metric) => (
          <span key={metric}>{metric}</span>
        ))}
        {contextWindow.effective !== null && (
          <span
            title={`geladen ${formatCount(contextWindow.loaded)} · Deckel ${formatCount(contextWindow.cap)} · Quelle ${contextWindow.source ?? "–"}`}
          >
            Fenster {formatCount(contextWindow.effective)} Token
          </span>
        )}
      </p>
      <Warnings
        events={log.events}
        error={
          log.error_message
            ? `${log.error_message}${log.error_code ? ` (Kennung: ${log.error_code})` : ""}`
            : null
        }
      />
      {quiet.length > 0 && (
        <details className="assistant-run__more">
          <summary>{plural(quiet.length, "Hinweis", "Hinweise")} zum Ablauf</summary>
          <ul className="assistant-run__rows">
            {quiet.map((event) => (
              <EventRow key={event.seq} event={event} />
            ))}
          </ul>
        </details>
      )}
      <ol className="assistant-run__rows" data-testid="assistant-run-timeline">
        {log.calls.map((call) => (
          <CallGroup key={call.nr} call={call} log={log} />
        ))}
        {log.tools
          .filter((tool) => !knownCalls.has(tool.call_nr))
          .map((tool) => (
            <ToolRow key={tool.key} tool={tool} />
          ))}
        {log.events
          .filter((event) => event.call_nr !== null && !knownCalls.has(event.call_nr))
          .map((event) => (
            <EventRow key={event.seq} event={event} />
          ))}
      </ol>
      <p className="assistant-run__foot">
        Lauf {log.id} · gestartet {formatDateTimeShort(log.started_at)} · Denktext wird nicht
        gespeichert.
      </p>
    </>
  );
}

function CallGroup({ call, log }: { call: RunCall; log: RunLog }) {
  return (
    <>
      {log.events
        .filter((event) => event.call_nr === call.nr)
        .map((event) => (
          <EventRow key={event.seq} event={event} />
        ))}
      <CallRow call={call} />
      {log.tools
        .filter((tool) => tool.call_nr === call.nr)
        .map((tool) => (
          <ToolRow key={tool.key} tool={tool} />
        ))}
    </>
  );
}

function LiveToolRow({ tool }: { tool: LiveTool }) {
  const meta =
    tool.status === "ok" && tool.rowCount !== null
      ? plural(tool.rowCount, "Zeile", "Zeilen")
      : TOOL_STATUS_LABEL[tool.status];
  return (
    <li className="assistant-run__row">
      <details>
        <summary>
          <span className="assistant-run__kind assistant-run__kind--tool">{tool.key}</span>
          <span className="assistant-run__what">
            {tool.name || "Werkzeug"} {argsPreview(tool.args)}
          </span>
          <span className="assistant-run__meta">{meta}</span>
        </summary>
        <div className="assistant-run__detail">
          <pre className="assistant-run__pre">
            {tool.args ? JSON.stringify(tool.args, null, 1) : tool.argsText}
          </pre>
          {tool.preview && (
            <>
              <p className="assistant-run__note">Anfang des Ergebnisses</p>
              <pre className="assistant-run__pre">{tool.preview}</pre>
            </>
          )}
        </div>
      </details>
    </li>
  );
}

function LiveRunView({ state }: { state: LiveRunState }) {
  const current = state.calls[state.calls.length - 1];
  return (
    <>
      <p className="assistant-run__activity" aria-live="polite">
        ● {liveActivity(state)}
      </p>
      {state.connectionNote && <p className="assistant-run__note">{state.connectionNote}</p>}
      <Warnings events={state.events} />
      <ol className="assistant-run__rows" data-testid="assistant-run-timeline">
        {state.calls.map((call) => (
          <LiveCallGroup key={call.nr} state={state} nr={call.nr} />
        ))}
        {state.tools
          .filter((tool) => tool.callNr === null)
          .map((tool) => (
            <LiveToolRow key={tool.key} tool={tool} />
          ))}
      </ol>
      {current && current.thinkText && (
        <div className="assistant-run__think" data-testid="assistant-run-think">
          <p className="assistant-run__think-text">
            {current.thinkText.length > THINK_PREVIEW_CHARS
              ? `…${current.thinkText.slice(-THINK_PREVIEW_CHARS)}`
              : current.thinkText}
          </p>
          <small>Denktext: nur live sichtbar, wird nicht gespeichert</small>
        </div>
      )}
      {state.invalidLines > 0 && (
        <p className="assistant-run__note assistant-run__overshoot">
          {plural(state.invalidLines, "Zeile", "Zeilen")} im Strom waren kein gültiges Ereignis.
        </p>
      )}
    </>
  );
}

function LiveCallGroup({ state, nr }: { state: LiveRunState; nr: number }) {
  const call = state.calls.find((item) => item.nr === nr)!;
  return (
    <>
      {state.events
        .filter((event) => event.call_nr === nr && !isLoud(event))
        .map((event) => (
          <EventRow key={event.seq} event={event} />
        ))}
      <li className="assistant-run__row">
        <div className="assistant-run__summary">
          <span className="assistant-run__kind">Modell</span>
          <span className="assistant-run__what">
            {call.step ? `${call.step} · ` : ""}Aufruf {call.nr}
          </span>
          <span className="assistant-run__meta">
            {[
              call.thinkChars ? `${formatCount(call.thinkChars)} Denk-Zeichen` : null,
              call.answerText ? `${formatCount(call.answerText.length)} Zeichen Antwort` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </div>
        {call.budget && (
          <BudgetLine
            call={{
              budget: call.budget,
              usage: { prompt: null, completion: null, reasoning: null },
            }}
          />
        )}
      </li>
      {state.tools
        .filter((tool) => tool.callNr === nr)
        .map((tool) => (
          <LiveToolRow key={tool.key} tool={tool} />
        ))}
    </>
  );
}

interface RunCardProps {
  runId: string;
  summary: RunSummary | null;
  /** Live-Zustand, solange der Lauf laeuft */
  live?: LiveRunState | null;
  label?: string;
}

export function RunCard({ runId, summary, live, label = "KI-Lauf" }: RunCardProps) {
  const [open, setOpen] = useState(false);
  const liveState = live && !live.final ? live : null;
  const log = useAssistantRunLog(runId, open && !liveState);
  const headline = liveState ? liveSummaryLine(label, liveState) : summaryLine(label, summary);

  return (
    <section
      className={`assistant-run${liveState ? " assistant-run--live" : ""}`}
      aria-label={`${label} ${runId}`}
      data-testid="assistant-run-card"
    >
      <button
        type="button"
        className="assistant-run__toggle"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="assistant-run__chevron" aria-hidden="true">
          ▸
        </span>
        <span>{headline}</span>
      </button>
      {open && (
        <div className="assistant-run__body">
          {liveState ? (
            <LiveRunView state={liveState} />
          ) : log.isPending ? (
            <p className="assistant-run__note">Protokoll wird gelesen …</p>
          ) : log.isError ? (
            <p className="assistant-run__note assistant-run__overshoot" role="alert">
              Protokoll nicht lesbar: {log.error.message}
            </p>
          ) : (
            <RunLogView log={log.data} />
          )}
        </div>
      )}
    </section>
  );
}
