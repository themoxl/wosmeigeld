import { Link } from "react-router-dom";
import { useAssistantChats } from "../../hooks/useAssistant";
import { formatDateTimeShort } from "../../lib/assistantFormat";
import { AssistantChat } from "./AssistantChat";
import "./assistant.css";

interface AssistantPanelProps {
  /** null = neuer Chat */
  chatId: string | null;
  onSelectChat: (chatId: string | null) => void;
  onClose: () => void;
}

/**
 * Seitenpanel des KI-Felds (docs/KI-FELD-SCHNITTSTELLE.md, "Oberflaeche").
 * Liegt in Layout.tsx ausserhalb von <Outlet>, damit ein laufender Strom die
 * Navigation uebersteht. Ab 1280 px schiebt es den Inhalt zur Seite, darunter
 * liegt es darueber (Regeln in assistant.css).
 */
export function AssistantPanel({ chatId, onSelectChat, onClose }: AssistantPanelProps) {
  const chats = useAssistantChats();
  const list = chats.data ?? [];
  const knownSelection = chatId === null || list.some((chat) => chat.id === chatId);

  return (
    <aside
      className="assistant-panel"
      aria-label="KI-Assistent"
      data-testid="assistant-panel"
      onKeyDown={(event) => {
        // Escape schliesst das Feld - ausser in Eingaben, die es selbst nutzen
        // (Entwurf, Fokus der Verdichtung, Chatauswahl).
        const target = event.target as HTMLElement;
        if (event.key === "Escape" && !target.closest("input, textarea, select")) onClose();
      }}
    >
      <header className="assistant-panel__head">
        <h2 className="assistant-panel__title">KI-Assistent</h2>
        <div className="assistant-panel__actions">
          <Link
            className="assistant-icon-btn"
            to={chatId ? `/assistent?chat=${encodeURIComponent(chatId)}` : "/assistent"}
            title="Breite Ansicht mit Chatliste"
            aria-label="Breite Ansicht öffnen"
          >
            ⤢
          </Link>
          <button
            type="button"
            className="assistant-icon-btn"
            onClick={onClose}
            aria-label="KI-Feld schließen"
            title="Schließen"
          >
            ×
          </button>
        </div>
      </header>
      <div className="assistant-panel__chats">
        <select
          className="form-input assistant-panel__select"
          aria-label="Chat wählen"
          value={chatId ?? ""}
          onChange={(event) => onSelectChat(event.target.value || null)}
        >
          <option value="">Neuer Chat</option>
          {!knownSelection && chatId && <option value={chatId}>Aktueller Chat</option>}
          {list.map((chat) => (
            <option key={chat.id} value={chat.id}>
              {chat.title || "Ohne Titel"} ·{" "}
              {formatDateTimeShort(chat.last_message_at ?? chat.updated_at)}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => onSelectChat(null)}
          disabled={chatId === null}
        >
          Neuer Chat
        </button>
      </div>
      {chats.isError && (
        <p className="assistant-chat__error" role="alert">
          Chatliste nicht geladen: {chats.error.message}
        </p>
      )}
      <AssistantChat chatId={chatId} onChatCreated={(id) => onSelectChat(id)} />
    </aside>
  );
}
