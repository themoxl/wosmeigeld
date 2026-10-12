import { useId } from "react";
import type { ReasoningCapability, ReasoningOverride } from "../../lib/assistantTypes";
import { reasoningHint } from "../../lib/assistantFormat";

const OPTIONS: { value: ReasoningOverride; label: string; title: string }[] = [
  {
    value: null,
    label: "Modellstandard",
    title: "Wie in Einstellungen → KI für den Haushalt eingestellt",
  },
  { value: "on", label: "An", title: "Denken an: gründlicher, aber deutlich langsamer" },
  { value: "off", label: "Aus", title: "Denken aus: schnellere Antworten" },
];

interface ReasoningToggleProps {
  value: ReasoningOverride;
  capability: ReasoningCapability;
  onChange: (value: ReasoningOverride) => void;
  disabled?: boolean;
}

/**
 * Denken (Reasoning) je Chat: Modellstandard / An / Aus. Kann das geladene
 * Modell nicht denken oder denkt es immer, ist der Schalter gesperrt und sagt
 * warum.
 */
export function ReasoningToggle({ value, capability, onChange, disabled }: ReasoningToggleProps) {
  const id = useId();
  const hint = reasoningHint(value, capability);
  const locked = hint.locked || Boolean(disabled);
  return (
    <div className="assistant-reasoning">
      <span className="assistant-reasoning__label" id={`${id}-label`}>
        Denken
      </span>
      <div
        className="assistant-segmented"
        role="group"
        aria-labelledby={`${id}-label`}
        aria-describedby={`${id}-hint`}
      >
        {OPTIONS.map((option) => (
          <button
            key={option.label}
            type="button"
            className={`assistant-segmented__option${
              value === option.value ? " assistant-segmented__option--active" : ""
            }`}
            aria-pressed={value === option.value}
            title={hint.locked ? hint.text : option.title}
            disabled={locked}
            onClick={() => {
              if (value !== option.value) onChange(option.value);
            }}
          >
            {option.label}
          </button>
        ))}
      </div>
      <span
        className={`assistant-reasoning__hint${hint.locked ? " assistant-reasoning__hint--locked" : ""}`}
        id={`${id}-hint`}
      >
        {hint.text}
      </span>
    </div>
  );
}
