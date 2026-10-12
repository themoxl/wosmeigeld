import { useId, useState } from "react";

interface CompactFieldProps {
  /** Warum Verdichten gerade nicht geht; null = es geht. */
  blockedReason: string | null;
  /** Eine Verdichtung dieses Chats laeuft. */
  running: boolean;
  /** Startet die Verdichtung; wirft mit Klartext, wenn der Server ablehnt. */
  onCompact: (focus: string) => Promise<void>;
  /** Fehler der letzten Verdichtung (aus dem Lauf), sonst null. */
  error?: string | null;
  /** Feld gleich offen zeigen (z. B. aus einer Fehlerantwort heraus). */
  initiallyOpen?: boolean;
}

/**
 * "Verdichten…" wie /compact: fasst den Verlauf vor der letzten Frage
 * zusammen, optional mit Fokus. Das Feld steht eingebettet unter der Zeile
 * (kein Dialog). Ist Verdichten gesperrt, laesst sich das Feld trotzdem
 * oeffnen - es nennt dann den Grund, statt nur einen grauen Knopf zu zeigen.
 */
export function CompactField({
  blockedReason,
  running,
  onCompact,
  error,
  initiallyOpen = false,
}: CompactFieldProps) {
  const id = useId();
  const [open, setOpen] = useState(initiallyOpen);
  const [focus, setFocus] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const blocked = blockedReason !== null;
  const start = async () => {
    if (blocked || submitting) return;
    setSubmitting(true);
    setRequestError(null);
    try {
      await onCompact(focus.trim());
      setOpen(false);
      setFocus("");
    } catch (failure) {
      setRequestError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSubmitting(false);
    }
  };

  const close = () => {
    setOpen(false);
    setRequestError(null);
  };

  return (
    <>
      <button
        type="button"
        className="btn-secondary assistant-compact-toggle"
        aria-expanded={open}
        aria-controls={`${id}-field`}
        title={
          blockedReason ??
          "Den Verlauf vor der letzten Frage zusammenfassen (wie /compact), optional mit Fokus. Die Originale bleiben gespeichert."
        }
        onClick={() => (open ? close() : setOpen(true))}
      >
        {running ? "verdichtet …" : "Verdichten…"}
      </button>
      {!open && error && (
        <p className="assistant-compact__error" role="alert">
          Nicht verdichtet: {error}
        </p>
      )}
      {open && (
        <div className="assistant-compact" id={`${id}-field`} role="group" aria-label="Verdichten">
          <p className="assistant-compact__intro">
            <strong>Verdichten</strong> fasst den Verlauf vor der letzten Frage zusammen; die letzte
            Frage und ihre Antwort bleiben im Wortlaut, die Originale bleiben gespeichert.
          </p>
          <label className="assistant-compact__label" htmlFor={`${id}-focus`}>
            Worauf soll die Zusammenfassung achten? (optional)
          </label>
          <input
            id={`${id}-focus`}
            type="text"
            className="form-input"
            autoComplete="off"
            value={focus}
            disabled={blocked || submitting}
            onChange={(event) => setFocus(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void start();
              } else if (event.key === "Escape") {
                close();
              }
            }}
          />
          {blockedReason && (
            <p className="assistant-compact__blocked" data-testid="assistant-compact-blocked">
              {blockedReason}
            </p>
          )}
          {(requestError || error) && (
            <p className="assistant-compact__error" role="alert">
              Nicht verdichtet: {requestError ?? error}
            </p>
          )}
          <div className="assistant-compact__actions">
            <button type="button" className="btn-secondary" onClick={close}>
              Schließen
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={blocked || submitting}
              onClick={() => void start()}
            >
              {submitting ? "startet …" : "Jetzt verdichten"}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
