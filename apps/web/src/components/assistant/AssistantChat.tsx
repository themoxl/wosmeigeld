import { useRef, useState } from "react";
import {
  isActiveStatus,
  newAssistantRequestId,
  useAssistantChat,
  useAssistantLiveRun,
  useCancelAssistantRun,
  useCompactAssistantChat,
  useCreateAssistantChat,
  useRetryAssistantRun,
  useSendAssistantMessage,
  useUpdateAssistantChat,
} from "../../hooks/useAssistant";
import type { ReasoningOverride } from "../../lib/assistantTypes";
import { compactBlockReason, frontActiveMessage } from "../../lib/assistantFormat";
import { AssistantComposer } from "./AssistantComposer";
import { AssistantMessageList } from "./AssistantMessageList";
import { CompactField } from "./CompactField";
import { ContextMeter } from "./ContextMeter";
import { ReasoningToggle } from "./ReasoningToggle";

interface AssistantChatProps {
  /** null = neuer Chat; er entsteht mit der ersten Frage. */
  chatId: string | null;
  onChatCreated: (chatId: string) => void;
}

/**
 * Ein Chat des KI-Felds: Verlauf, darueber die Zeile mit Denken-Schalter,
 * Kontextbalken und "Verdichten…", darunter die Eingabe. Dieselbe Ansicht im
 * Seitenpanel und auf der Seite /assistent.
 */
export function AssistantChat({ chatId, onChatCreated }: AssistantChatProps) {
  const chatQuery = useAssistantChat(chatId);
  const chat = chatId ? (chatQuery.data ?? null) : null;
  const messages = chat?.messages ?? [];

  const createChat = useCreateAssistantChat();
  const updateChat = useUpdateAssistantChat();
  const sendMessage = useSendAssistantMessage();
  const retryRun = useRetryAssistantRun(chatId);
  const cancelRun = useCancelAssistantRun(chatId);
  const compact = useCompactAssistantChat(chatId);

  // Zustand je Chat: beim Wechsel zuruecksetzen (waehrend des Renderns,
  // nicht in einem Effekt).
  const [shownChatId, setShownChatId] = useState(chatId);
  const [compactRunId, setCompactRunId] = useState<string | null>(null);
  const [newChatReasoning, setNewChatReasoning] = useState<ReasoningOverride>(null);
  const [compactOpenSignal, setCompactOpenSignal] = useState(0);
  if (shownChatId !== chatId) {
    setShownChatId(chatId);
    setCompactRunId(null);
  }
  // Eine Verdichtung, die der Server als laufend meldet (nach dem Neuladen,
  // aus einem anderen Tab), wird uebernommen. Meldet er danach null, bleibt
  // der Lauf gemerkt - sonst verschwaende ein Fehler der Verdichtung mit dem
  // Neuladen des Chats, bevor ihn jemand sehen konnte.
  const serverCompactionRunId = chat?.active_compaction_run_id ?? null;
  if (serverCompactionRunId !== null && serverCompactionRunId !== compactRunId) {
    setCompactRunId(serverCompactionRunId);
  }

  // Gleicher Entwurf, gleiche request_id: ein doppelter Klick oder ein
  // Wiederholen nach Netzfehler legt keine zweite Frage an.
  const pendingRequest = useRef<{ text: string; id: string } | null>(null);

  const compactLive = useAssistantLiveRun(chatId, compactRunId, true);
  const compacting = compactRunId !== null && !compactLive.state?.final;
  const front = frontActiveMessage(messages);
  const answerLive = useAssistantLiveRun(chatId, front?.run_id ?? null, !compacting);
  const answerActive = front !== null && isActiveStatus(front.status);
  const busy = answerActive || compacting;

  const compactError =
    compactLive.state?.final?.kind === "error" ? compactLive.state.final.message : null;
  const blockedReason = compactBlockReason(chat, { answerActive, compacting });
  const meter = answerLive.state?.meter ?? chat?.context_meter ?? null;

  const handleSend = async (text: string) => {
    if (pendingRequest.current?.text !== text) {
      pendingRequest.current = { text, id: newAssistantRequestId() };
    }
    const requestId = pendingRequest.current.id;
    let targetId = chatId;
    if (!targetId) {
      const created = await createChat.mutateAsync({});
      targetId = created.id;
      if (newChatReasoning !== null) {
        await updateChat.mutateAsync({
          chatId: targetId,
          input: { reasoning_override: newChatReasoning },
        });
      }
      onChatCreated(targetId);
    }
    await sendMessage.mutateAsync({
      chatId: targetId,
      input: { content: text, request_id: requestId },
    });
    pendingRequest.current = null;
  };

  const handleCancel = () => {
    const runId = compacting ? compactRunId : front?.run_id;
    if (runId) cancelRun.mutate(runId);
  };

  const handleCompact = async (focus: string) => {
    const response = await compact.mutateAsync(focus ? { focus } : {});
    setCompactRunId(response.run_id);
  };

  if (chatId && chatQuery.isError) {
    return (
      <div className="assistant-chat">
        <p className="assistant-chat__error" role="alert">
          Der Chat lässt sich nicht laden: {chatQuery.error.message}
        </p>
      </div>
    );
  }

  return (
    <div className="assistant-chat">
      {chat ? (
        <AssistantMessageList
          chat={chat}
          live={answerLive.state}
          liveRunId={front?.run_id ?? null}
          onRetry={(runId, reasoningOverride) => retryRun.mutate({ runId, reasoningOverride })}
          retryPending={retryRun.isPending}
          retryError={retryRun.error?.message ?? null}
          onCancelRun={(runId) => cancelRun.mutate(runId)}
          onReconnect={answerLive.reconnect}
          onOpenCompact={() => setCompactOpenSignal((count) => count + 1)}
        />
      ) : (
        <div className="assistant-messages assistant-messages--empty">
          {chatId ? (
            <p className="assistant-empty">Chat wird geladen …</p>
          ) : (
            <p className="assistant-empty">
              Frag nach deinen Finanzen – z. B. „Wofür habe ich im September am meisten
              ausgegeben?“. Die KI liest Buchungen, Konten, Budgets und Verträge; ändern kann sie
              nichts. Jede Zahl der Antwort ist als belegt oder unbelegt markiert.
            </p>
          )}
        </div>
      )}

      {compacting && (
        <div className="assistant-compacting" aria-live="polite">
          <p className="assistant-live__status">
            <span className="assistant-live__pulse" aria-hidden="true" />
            verdichtet den Verlauf …
            {compactLive.state?.connectionNote ? ` ${compactLive.state.connectionNote}` : ""}
          </p>
        </div>
      )}

      <div className="assistant-toolbar">
        <ReasoningToggle
          value={chat ? chat.reasoning_override : newChatReasoning}
          capability={chat?.reasoning_capability ?? "unknown"}
          disabled={Boolean(chatId) && !chat}
          onChange={(value) => {
            if (chat) {
              updateChat.mutate({ chatId: chat.id, input: { reasoning_override: value } });
            } else {
              setNewChatReasoning(value);
            }
          }}
        />
        <span className="assistant-toolbar__spacer" />
        <ContextMeter meter={meter} />
        <CompactField
          key={`${chatId ?? "neu"}:${compactOpenSignal}`}
          blockedReason={blockedReason}
          running={compacting}
          onCompact={handleCompact}
          error={compactError}
          initiallyOpen={compactOpenSignal > 0}
        />
      </div>
      {updateChat.isError && (
        <p className="assistant-chat__error" role="alert">
          Denken nicht umgestellt: {updateChat.error.message}
        </p>
      )}

      <AssistantComposer
        busy={busy}
        onSend={handleSend}
        onCancel={busy ? handleCancel : undefined}
        cancelPending={cancelRun.isPending}
        disabled={Boolean(chatId) && !chat}
      />
      {cancelRun.isError && (
        <p className="assistant-chat__error" role="alert">
          Abbrechen fehlgeschlagen: {cancelRun.error.message}
        </p>
      )}
    </div>
  );
}
