import type { ContextMeter as ContextMeterData } from "../../lib/assistantTypes";
import { describeMeter } from "../../lib/assistantFormat";

/**
 * "Kontext N % · E / W" aus dem letzten Modellaufruf. Klasse `voll`, wenn die
 * Eingabe in die Antwortreserve reichte (E > W − R). Ohne Messung kein Balken
 * - geraten wird nicht.
 */
export function ContextMeter({ meter }: { meter: ContextMeterData | null | undefined }) {
  const view = describeMeter(meter);
  if (!view || !meter) return null;
  return (
    <span
      className={`assistant-meter${view.full ? " voll" : ""}`}
      title={view.tooltip}
      role="meter"
      aria-label="Kontext"
      aria-valuemin={0}
      aria-valuemax={meter.window_tokens}
      aria-valuenow={Math.min(meter.input_tokens, meter.window_tokens)}
      aria-valuetext={`${view.label}${view.full ? " – reicht in die Antwortreserve" : ""}`}
      data-testid="assistant-context-meter"
    >
      <span className="assistant-meter__label">Kontext</span>
      <span className="assistant-meter__bar" aria-hidden="true">
        <span
          className="assistant-meter__fill"
          style={{ width: `${Math.min(view.percent, 100)}%` }}
        />
      </span>
      <span className="assistant-meter__value">{view.label}</span>
      {!meter.exact && <span className="assistant-meter__estimate">geschätzt</span>}
    </span>
  );
}
