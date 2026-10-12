// Datenschicht des KI-Felds: React Query fuer Chatliste, Chat und
// Laufprotokoll, dazu der Live-Zustand eines laufenden Laufs.
//
// Live-Zustand: Die Strom-Ereignisse landen im Query-Cache unter
// assistantKeys.live(...). Den Strom selbst haelt ein Register je
// QueryClient - zeigen Seitenpanel und Seite /assistent denselben Chat, gibt es
// trotzdem nur EINE Verbindung. Wer den Lauf zeigt, haelt den Strom
// (Referenzzaehlung); geht der Letzte, endet er nach kurzer Gnadenfrist
// (React StrictMode baut Effekte doppelt auf, ein Panel schliesst und oeffnet).
// Je Chat wird nur der vorderste aktive Lauf gestreamt; die Browser erlauben
// nur wenige offene Verbindungen je Server.

import { useCallback, useEffect } from "react";
import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  cancelAssistantRun,
  compactAssistantChat,
  createAssistantChat,
  deleteAssistantChat,
  getAssistantChat,
  getAssistantRun,
  listAssistantChats,
  openAssistantRunStream,
  retryAssistantRun,
  sendAssistantMessage,
  updateAssistantChat,
} from "../lib/assistantApi";
import {
  type LiveRunState,
  applyConnection,
  applyOutcome,
  applyStreamEvent,
  createLiveRun,
} from "../lib/assistantLive";
import { type AssistantStreamOutcome, readAssistantStream } from "../lib/assistantStream";
import type {
  AssistantStreamEvent,
  ChatCreateInput,
  ChatDetail,
  ChatListItem,
  ChatMessage,
  ChatUpdateInput,
  MessageStatus,
  ReasoningOverride,
  RunLog,
  SendMessageInput,
  SendResponse,
} from "../lib/assistantTypes";

export const assistantKeys = {
  chats: (householdId: string) => ["assistant", householdId, "chats"] as const,
  chat: (householdId: string, chatId: string) =>
    ["assistant", householdId, "chat", chatId] as const,
  run: (householdId: string, runId: string) => ["assistant", householdId, "run", runId] as const,
  live: (householdId: string, runId: string) => ["assistant", householdId, "live", runId] as const,
};

/** Gnadenfrist, bevor ein nicht mehr gezeigter Strom geschlossen wird. */
const RELEASE_GRACE_MS = 2_000;
/** Strom-Ereignisse werden gebuendelt gezeichnet, nicht je Token. */
const LIVE_FLUSH_MS = 80;

export function isActiveStatus(status: MessageStatus | null | undefined): boolean {
  return status === "queued" || status === "running";
}

export function newAssistantRequestId(): string {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function useAssistantContext() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const token = session?.access_token ?? "";
  const householdId = selectedHouseholdId ?? "";
  return { token, householdId, enabled: Boolean(token && householdId) };
}

// ---------------------------------------------------------------------------
// Cache-Helfer
// ---------------------------------------------------------------------------

function mergeMessages(messages: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const byId = new Map(messages.map((message) => [message.id, message]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort((left, right) => left.seq - right.seq);
}

function patchChat(
  queryClient: QueryClient,
  householdId: string,
  chatId: string,
  update: (chat: ChatDetail) => ChatDetail,
) {
  queryClient.setQueryData<ChatDetail>(assistantKeys.chat(householdId, chatId), (current) =>
    current ? update(current) : current,
  );
}

async function applySendResponse(
  queryClient: QueryClient,
  householdId: string,
  chatId: string,
  response: SendResponse,
) {
  // Ein GET des Chats, das vor dem Senden losging, darf die neuen Nachrichten
  // nicht mit seinem aelteren Stand ueberschreiben.
  await queryClient.cancelQueries({ queryKey: assistantKeys.chat(householdId, chatId) });
  patchChat(queryClient, householdId, chatId, (chat) => ({
    ...chat,
    messages: mergeMessages(chat.messages, [response.user_message, response.assistant_message]),
  }));
  // Warteplatz aus der Antwort, bis der Strom den ersten Stand liefert.
  queryClient.setQueryData<LiveRunState | null>(
    assistantKeys.live(householdId, response.run_id),
    (current) =>
      current ??
      createLiveRun(response.run_id, {
        status: response.assistant_message.status,
        queuePosition: response.queue_position,
      }),
  );
}

// ---------------------------------------------------------------------------
// Abfragen
// ---------------------------------------------------------------------------

export function useAssistantChats(active = true) {
  const { token, householdId, enabled } = useAssistantContext();
  return useQuery<ChatListItem[]>({
    queryKey: assistantKeys.chats(householdId),
    queryFn: () => listAssistantChats(token, householdId),
    enabled: enabled && active,
  });
}

export function useAssistantChat(chatId: string | null) {
  const { token, householdId, enabled } = useAssistantContext();
  return useQuery<ChatDetail>({
    queryKey: assistantKeys.chat(householdId, chatId ?? ""),
    queryFn: () => getAssistantChat(token, householdId, chatId as string),
    enabled: enabled && Boolean(chatId),
  });
}

/** Laufprotokoll - erst laden, wenn jemand die Laufkarte aufklappt. */
export function useAssistantRunLog(runId: string | null, open: boolean) {
  const { token, householdId, enabled } = useAssistantContext();
  return useQuery<RunLog>({
    queryKey: assistantKeys.run(householdId, runId ?? ""),
    queryFn: () => getAssistantRun(token, householdId, runId as string),
    enabled: enabled && open && Boolean(runId),
    staleTime: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Aenderungen
// ---------------------------------------------------------------------------

export function useCreateAssistantChat() {
  const { token, householdId } = useAssistantContext();
  const queryClient = useQueryClient();
  return useMutation<ChatDetail, Error, ChatCreateInput>({
    mutationFn: (input) => createAssistantChat(token, householdId, input),
    onSuccess: (chat) => {
      queryClient.setQueryData(assistantKeys.chat(householdId, chat.id), chat);
      void queryClient.invalidateQueries({ queryKey: assistantKeys.chats(householdId) });
    },
  });
}

export function useDeleteAssistantChat() {
  const { token, householdId } = useAssistantContext();
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (chatId) => deleteAssistantChat(token, householdId, chatId),
    onSuccess: (_result, chatId) => {
      queryClient.setQueryData<ChatListItem[]>(assistantKeys.chats(householdId), (current) =>
        current?.filter((chat) => chat.id !== chatId),
      );
      queryClient.removeQueries({ queryKey: assistantKeys.chat(householdId, chatId) });
    },
  });
}

/** PATCH eines Chats; der Denken-Schalter springt sofort um und faellt bei Fehler zurueck. */
export function useUpdateAssistantChat() {
  const { token, householdId } = useAssistantContext();
  const queryClient = useQueryClient();
  return useMutation<
    ChatDetail,
    Error,
    { chatId: string; input: ChatUpdateInput },
    { previous: ChatDetail | undefined }
  >({
    mutationFn: ({ chatId, input }) => updateAssistantChat(token, householdId, chatId, input),
    onMutate: ({ chatId, input }) => {
      const key = assistantKeys.chat(householdId, chatId);
      const previous = queryClient.getQueryData<ChatDetail>(key);
      if (previous) queryClient.setQueryData<ChatDetail>(key, { ...previous, ...input });
      return { previous };
    },
    onError: (_error, { chatId }, context) => {
      if (context?.previous) {
        queryClient.setQueryData(assistantKeys.chat(householdId, chatId), context.previous);
      }
    },
    onSuccess: (chat) => {
      queryClient.setQueryData(assistantKeys.chat(householdId, chat.id), chat);
      void queryClient.invalidateQueries({ queryKey: assistantKeys.chats(householdId) });
    },
  });
}

export function useSendAssistantMessage() {
  const { token, householdId } = useAssistantContext();
  const queryClient = useQueryClient();
  return useMutation<SendResponse, Error, { chatId: string; input: SendMessageInput }>({
    mutationFn: ({ chatId, input }) => sendAssistantMessage(token, householdId, chatId, input),
    onSuccess: async (response, { chatId }) => {
      await applySendResponse(queryClient, householdId, chatId, response);
      void queryClient.invalidateQueries({ queryKey: assistantKeys.chats(householdId) });
    },
  });
}

export function useRetryAssistantRun(chatId: string | null) {
  const { token, householdId } = useAssistantContext();
  const queryClient = useQueryClient();
  return useMutation<SendResponse, Error, { runId: string; reasoningOverride?: ReasoningOverride }>(
    {
      mutationFn: ({ runId, reasoningOverride }) =>
        retryAssistantRun(
          token,
          householdId,
          runId,
          reasoningOverride === undefined ? {} : { reasoning_override: reasoningOverride },
        ),
      onSuccess: async (response) => {
        if (chatId) await applySendResponse(queryClient, householdId, chatId, response);
      },
    },
  );
}

export function useCancelAssistantRun(chatId: string | null) {
  const { token, householdId } = useAssistantContext();
  const queryClient = useQueryClient();
  return useMutation<{ status: MessageStatus }, Error, string>({
    mutationFn: (runId) => cancelAssistantRun(token, householdId, runId),
    onSuccess: ({ status }, runId) => {
      if (!chatId) return;
      patchChat(queryClient, householdId, chatId, (chat) => ({
        ...chat,
        messages: chat.messages.map((message) =>
          message.run_id === runId ? { ...message, status } : message,
        ),
      }));
      void queryClient.invalidateQueries({ queryKey: assistantKeys.chat(householdId, chatId) });
    },
  });
}

export function useCompactAssistantChat(chatId: string | null) {
  const { token, householdId } = useAssistantContext();
  return useMutation<{ run_id: string }, Error, { focus?: string }>({
    mutationFn: (input) => compactAssistantChat(token, householdId, chatId as string, input),
  });
}

// ---------------------------------------------------------------------------
// Live-Strom
// ---------------------------------------------------------------------------

interface StreamAuth {
  token: string;
  householdId: string;
  chatId: string;
}

interface StreamEntry {
  refs: number;
  /** Zuletzt gesehene Anmeldung; ein erneuerter Token gilt fuer die naechste Verbindung. */
  auth: StreamAuth;
  /** null = gerade keine Verbindung (verloren, abgeschlossen oder freigegeben) */
  controller: AbortController | null;
  releaseTimer: ReturnType<typeof setTimeout> | null;
}

const registries = new WeakMap<QueryClient, Map<string, StreamEntry>>();

function registryFor(queryClient: QueryClient): Map<string, StreamEntry> {
  let registry = registries.get(queryClient);
  if (!registry) {
    registry = new Map();
    registries.set(queryClient, registry);
  }
  return registry;
}

const entryId = (householdId: string, runId: string) => `${householdId}:${runId}`;

function finishRun(
  queryClient: QueryClient,
  auth: StreamAuth,
  runId: string,
  final: ChatMessage | null,
) {
  const { householdId, chatId } = auth;
  if (final) {
    // Nur eine Nachricht ersetzen, die der Chat schon kennt; alles andere
    // (z. B. eine Verdichtung) kommt mit dem Neuladen.
    patchChat(queryClient, householdId, chatId, (chat) =>
      chat.messages.some((message) => message.id === final.id)
        ? {
            ...chat,
            messages: mergeMessages(chat.messages, [final]),
            context_meter: final.context_meter ?? chat.context_meter,
          }
        : chat,
    );
  }
  void queryClient.invalidateQueries({ queryKey: assistantKeys.chat(householdId, chatId) });
  void queryClient.invalidateQueries({ queryKey: assistantKeys.chats(householdId) });
  void queryClient.invalidateQueries({ queryKey: assistantKeys.run(householdId, runId) });
}

function startRunStream(queryClient: QueryClient, runId: string, entry: StreamEntry) {
  const controller = new AbortController();
  entry.controller = controller;
  const key = assistantKeys.live(entry.auth.householdId, runId);
  const update = (change: (state: LiveRunState) => LiveRunState) =>
    queryClient.setQueryData<LiveRunState | null>(key, (current) =>
      change(current ?? createLiveRun(runId)),
    );

  // Token-Stroeme kommen schneller, als es sich zu zeichnen lohnt: sammeln
  // und hoechstens alle LIVE_FLUSH_MS in den Cache schreiben.
  let pending: AssistantStreamEvent[] = [];
  let flushTimer: ReturnType<typeof setTimeout> | null = null;
  const flush = () => {
    if (flushTimer) clearTimeout(flushTimer);
    flushTimer = null;
    if (!pending.length) return;
    const batch = pending;
    pending = [];
    update((state) => batch.reduce(applyStreamEvent, state));
  };

  const current = queryClient.getQueryData<LiveRunState | null>(key);
  void readAssistantStream({
    after: current?.lastSeq ?? null,
    signal: controller.signal,
    open: (after, signal) =>
      openAssistantRunStream(entry.auth.token, entry.auth.householdId, runId, after, signal),
    onEvent: (event) => {
      pending.push(event);
      if (event.type === "delta" || event.type === "think" || event.type === "targs") {
        flushTimer ??= setTimeout(flush, LIVE_FLUSH_MS);
      } else {
        flush();
      }
    },
    onConnection: (connection) => {
      flush();
      update((state) => applyConnection(state, connection));
    },
    onInvalidLine: () => update((state) => ({ ...state, invalidLines: state.invalidLines + 1 })),
  }).then((outcome: AssistantStreamOutcome) => {
    flush();
    if (entry.controller === controller) entry.controller = null;
    update((state) => applyOutcome(state, outcome));
    if (outcome.kind === "done" || outcome.kind === "error") {
      finishRun(
        queryClient,
        entry.auth,
        runId,
        outcome.kind === "done" ? outcome.message : outcome.messageObj,
      );
    }
  });
}

function retainRunStream(queryClient: QueryClient, runId: string, auth: StreamAuth): () => void {
  const registry = registryFor(queryClient);
  const id = entryId(auth.householdId, runId);
  let entry = registry.get(id);
  if (!entry) {
    entry = { refs: 0, auth, controller: null, releaseTimer: null };
    registry.set(id, entry);
  }
  const held = entry;
  held.refs += 1;
  held.auth = auth;
  if (held.releaseTimer) {
    clearTimeout(held.releaseTimer);
    held.releaseTimer = null;
  }
  const live = queryClient.getQueryData<LiveRunState | null>(
    assistantKeys.live(auth.householdId, runId),
  );
  if (!held.controller && !live?.final) startRunStream(queryClient, runId, held);

  return () => {
    held.refs -= 1;
    if (held.refs > 0) return;
    held.releaseTimer = setTimeout(() => {
      held.releaseTimer = null;
      if (held.refs > 0) return;
      held.controller?.abort();
      held.controller = null;
      registry.delete(id);
    }, RELEASE_GRACE_MS);
  };
}

export interface AssistantLiveHandle {
  state: LiveRunState | null;
  /** Neuer Verbindungsversuch nach "Verbindung verloren". */
  reconnect: () => void;
}

/**
 * Live-Zustand eines Laufs. Mit `retain` haelt die Komponente den Strom
 * offen; ohne liest sie nur, was schon im Cache steht.
 */
export function useAssistantLiveRun(
  chatId: string | null,
  runId: string | null,
  retain: boolean,
): AssistantLiveHandle {
  const { token, householdId } = useAssistantContext();
  const queryClient = useQueryClient();
  const { data } = useQuery<LiveRunState | null>({
    queryKey: assistantKeys.live(householdId, runId ?? ""),
    // Wird nie abgefragt: den Inhalt schreibt allein der Strom.
    queryFn: () => null,
    enabled: false,
    initialData: null,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (!retain || !runId || !chatId || !token || !householdId) return undefined;
    return retainRunStream(queryClient, runId, { token, householdId, chatId });
  }, [queryClient, retain, runId, chatId, token, householdId]);

  const reconnect = useCallback(() => {
    if (!runId) return;
    const entry = registryFor(queryClient).get(entryId(householdId, runId));
    if (!entry || entry.controller) return;
    queryClient.setQueryData<LiveRunState | null>(
      assistantKeys.live(householdId, runId),
      (current) =>
        current ? { ...current, connection: "connecting", connectionNote: null } : current,
    );
    startRunStream(queryClient, runId, entry);
  }, [queryClient, householdId, runId]);

  return { state: runId ? (data ?? null) : null, reconnect };
}
