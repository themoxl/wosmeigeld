import { useState } from "react";

interface AssistantComposerProps {
  /** Ein Auftrag dieses Chats laeuft oder wartet - dann heisst der Knopf "Einreihen". */
  busy: boolean;
  /** Sendet den Text; wirft mit Klartext, wenn es nicht klappt (der Entwurf bleibt dann stehen). */
  onSend: (text: string) => Promise<void>;
  /** Bricht den laufenden Auftrag ab; fehlt, wenn nichts laeuft. */
  onCancel?: () => void;
  cancelPending?: boolean;
  disabled?: boolean;
}

/**
 * Eingabe des KI-Felds: Enter sendet, Shift+Enter bricht um. Laeuft schon
 * etwas, wird die Frage eingereiht statt verworfen.
 */
export function AssistantComposer({
  busy,
  onSend,
  onCancel,
  cancelPending = false,
  disabled = false,
}: AssistantComposerProps) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const text = draft.trim();
    if (!text || sending || disabled) return;
    setSending(true);
    setError(null);
    try {
      await onSend(text);
      setDraft("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="assistant-composer">
      <textarea
        className="assistant-composer__input"
        rows={2}
        value={draft}
        disabled={disabled}
        aria-label="Frage an die KI"
        placeholder="Frag die KI über deine Finanzen … (Enter senden · Shift+Enter Zeilenumbruch)"
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            void submit();
          }
        }}
      />
      <div className="assistant-composer__actions">
        {error && (
          <p className="assistant-composer__error" role="alert">
            {error}
          </p>
        )}
        {onCancel && (
          <button
            type="button"
            className="btn-secondary"
            onClick={onCancel}
            disabled={cancelPending}
          >
            {cancelPending ? "bricht ab …" : "Abbrechen"}
          </button>
        )}
        <button
          type="button"
          className="btn-primary"
          onClick={() => void submit()}
          disabled={disabled || sending || !draft.trim()}
          title={busy ? "Läuft schon etwas, kommt die Frage in die Warteschlange." : undefined}
        >
          {busy ? "Einreihen" : "Senden"}
        </button>
      </div>
    </div>
  );
}
