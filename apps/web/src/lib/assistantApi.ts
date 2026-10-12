// Endpunkte des KI-Felds (docs/KI-FELD-SCHNITTSTELLE.md, Abschnitt
// "Endpunkte"). Kopfzeilen, Basis-URL und Fehlertexte kommen aus lib/api.ts;
// hier stehen nur Pfade und Koerper.

import { apiFetch, apiFetchResponse } from "./api";
import type {
  ChatCreateInput,
  ChatDetail,
  ChatListItem,
  ChatUpdateInput,
  CompactInput,
  MessageStatus,
  RetryInput,
  RunLog,
  SendMessageInput,
  SendResponse,
} from "./assistantTypes";

const BASE = "/v1/assistant";

const chatPath = (chatId: string) => `${BASE}/chats/${encodeURIComponent(chatId)}`;
const runPath = (runId: string) => `${BASE}/runs/${encodeURIComponent(runId)}`;

export function listAssistantChats(token: string, householdId: string): Promise<ChatListItem[]> {
  return apiFetch<ChatListItem[]>(`${BASE}/chats`, { token, householdId });
}

export function createAssistantChat(
  token: string,
  householdId: string,
  input: ChatCreateInput = {},
): Promise<ChatDetail> {
  return apiFetch<ChatDetail>(`${BASE}/chats`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getAssistantChat(
  token: string,
  householdId: string,
  chatId: string,
): Promise<ChatDetail> {
  return apiFetch<ChatDetail>(chatPath(chatId), { token, householdId });
}

export function updateAssistantChat(
  token: string,
  householdId: string,
  chatId: string,
  input: ChatUpdateInput,
): Promise<ChatDetail> {
  return apiFetch<ChatDetail>(chatPath(chatId), {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteAssistantChat(
  token: string,
  householdId: string,
  chatId: string,
): Promise<void> {
  return apiFetch<void>(chatPath(chatId), { token, householdId, method: "DELETE" });
}

/** Gleiche request_id liefert dieselbe Antwort (idempotent). */
export function sendAssistantMessage(
  token: string,
  householdId: string,
  chatId: string,
  input: SendMessageInput,
): Promise<SendResponse> {
  return apiFetch<SendResponse>(`${chatPath(chatId)}/messages`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** 409, solange Auftraege des Chats laufen oder der Chat weniger als zwei Fragen hat. */
export function compactAssistantChat(
  token: string,
  householdId: string,
  chatId: string,
  input: CompactInput = {},
): Promise<{ run_id: string }> {
  return apiFetch<{ run_id: string }>(`${chatPath(chatId)}/compact`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getAssistantRun(
  token: string,
  householdId: string,
  runId: string,
): Promise<RunLog> {
  return apiFetch<RunLog>(runPath(runId), { token, householdId });
}

export function cancelAssistantRun(
  token: string,
  householdId: string,
  runId: string,
): Promise<{ status: MessageStatus }> {
  return apiFetch<{ status: MessageStatus }>(`${runPath(runId)}/cancel`, {
    token,
    householdId,
    method: "POST",
  });
}

/** Neue Antwortnachricht zur selben Frage. */
export function retryAssistantRun(
  token: string,
  householdId: string,
  runId: string,
  input: RetryInput = {},
): Promise<SendResponse> {
  return apiFetch<SendResponse>(`${runPath(runId)}/retry`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * Oeffnet den NDJSON-Strom eines Laufs. fetch statt EventSource, weil der
 * Authorization-Header noetig ist. `after` = letzte gesehene seq; der Server
 * spielt alles danach nach.
 */
export function openAssistantRunStream(
  token: string,
  householdId: string,
  runId: string,
  after: number | null,
  signal: AbortSignal,
): Promise<Response> {
  const query = after === null ? "" : `?after=${after}`;
  return apiFetchResponse(`${runPath(runId)}/stream${query}`, {
    token,
    householdId,
    signal,
    cache: "no-store",
    headers: { Accept: "application/x-ndjson" },
  });
}
