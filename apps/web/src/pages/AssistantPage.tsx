import { useSearchParams } from "react-router-dom";
import { AssistantChat } from "../components/assistant/AssistantChat";
import { useAssistantChats, useDeleteAssistantChat } from "../hooks/useAssistant";
import { formatDateTimeShort, plural } from "../lib/assistantFormat";
import "../components/assistant/assistant.css";

const NEW_CHAT = "neu";

/**
 * KI-Assistent in breit: links die eigenen Chats (neueste zuerst), rechts der
 * Chat. `?chat=<id>` waehlt einen Chat, `?chat=neu` beginnt einen neuen;
 * ohne Angabe steht der neueste Chat offen.
 */
export function AssistantPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const chats = useAssistantChats();
  const deleteChat = useDeleteAssistantChat();
  const list = chats.data ?? [];

  const param = searchParams.get("chat");
  const selectedId =
    param === NEW_CHAT ? null : (param ?? (chats.isSuccess ? (list[0]?.id ?? null) : null));
  const waitingForList = param === null && chats.isPending;

  const select = (chatId: string | null) => {
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("chat", chatId ?? NEW_CHAT);
        return next;
      },
      { replace: true },
    );
  };

  const remove = (chatId: string, title: string) => {
    if (!window.confirm(`Chat „${title || "Ohne Titel"}“ samt Laufprotokollen löschen?`)) return;
    deleteChat.mutate(chatId, {
      onSuccess: () => {
        if (chatId === selectedId) select(null);
      },
    });
  };

  return (
    <section className="page assistant-page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">KI-Assistent</h1>
          <p className="placeholder-note">
            Fragen zu deinen Finanzen. Die KI liest nur – ändern kann sie nichts. Jede Zahl der
            Antwort ist als belegt oder unbelegt markiert, jeder Lauf lässt sich nachvollziehen.
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={() => select(null)}>
          Neuer Chat
        </button>
      </div>

      <div className="assistant-page__layout">
        <nav className="assistant-chatlist" aria-label="Chats">
          {chats.isPending && <p className="assistant-empty">Chats werden geladen …</p>}
          {chats.isError && (
            <p className="assistant-chat__error" role="alert">
              Chatliste nicht geladen: {chats.error.message}
            </p>
          )}
          {chats.isSuccess && list.length === 0 && (
            <p className="assistant-empty">Noch keine Chats.</p>
          )}
          <ul>
            {list.map((chat) => (
              <li
                key={chat.id}
                className={`assistant-chatlist__item${
                  chat.id === selectedId ? " assistant-chatlist__item--active" : ""
                }`}
              >
                <button
                  type="button"
                  className="assistant-chatlist__open"
                  aria-current={chat.id === selectedId ? "true" : undefined}
                  onClick={() => select(chat.id)}
                >
                  <span className="assistant-chatlist__title">{chat.title || "Ohne Titel"}</span>
                  <span className="assistant-chatlist__meta">
                    {formatDateTimeShort(chat.last_message_at ?? chat.updated_at)} ·{" "}
                    {plural(chat.message_count, "Nachricht", "Nachrichten")}
                  </span>
                </button>
                <button
                  type="button"
                  className="assistant-icon-btn assistant-chatlist__delete"
                  aria-label={`Chat „${chat.title || "Ohne Titel"}“ löschen`}
                  title="Chat löschen"
                  disabled={deleteChat.isPending}
                  onClick={() => remove(chat.id, chat.title)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
          {deleteChat.isError && (
            <p className="assistant-chat__error" role="alert">
              Nicht gelöscht: {deleteChat.error.message}
            </p>
          )}
        </nav>
        <div className="assistant-page__chat">
          {waitingForList ? (
            <p className="assistant-empty">Chats werden geladen …</p>
          ) : (
            <AssistantChat chatId={selectedId} onChatCreated={(id) => select(id)} />
          )}
        </div>
      </div>
    </section>
  );
}
