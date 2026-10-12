import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantPage } from "../pages/AssistantPage";
import type { ChatDetail, ChatListItem } from "../lib/assistantTypes";

const api = vi.hoisted(() => ({
  listAssistantChats: vi.fn(),
  getAssistantChat: vi.fn(),
  deleteAssistantChat: vi.fn(),
  openAssistantRunStream: vi.fn(),
}));

vi.mock("../lib/assistantApi", () => api);
vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ session: { access_token: "tok" } }),
}));
vi.mock("../household/HouseholdProvider", () => ({
  useHousehold: () => ({ selectedHouseholdId: "hh-1" }),
}));

const chats: ChatListItem[] = [
  {
    id: "chat-2",
    title: "Fixkosten im Herbst",
    created_at: "2026-10-02T09:00:00Z",
    updated_at: "2026-10-02T09:30:00Z",
    last_message_at: "2026-10-02T09:30:00Z",
    message_count: 4,
  },
  {
    id: "chat-1",
    title: "Autokosten",
    created_at: "2026-09-20T09:00:00Z",
    updated_at: "2026-09-20T09:10:00Z",
    last_message_at: "2026-09-20T09:10:00Z",
    message_count: 2,
  },
];

function detail(id: string, answer: string): ChatDetail {
  return {
    id,
    title: id,
    reasoning_override: null,
    reasoning_capability: "switchable",
    summary: null,
    active_compaction_run_id: null,
    context_meter: null,
    messages: [
      {
        id: `${id}-a`,
        seq: 2,
        role: "assistant",
        content: answer,
        status: "done",
        run_id: null,
        created_at: "2026-10-02T09:30:00Z",
        error_code: null,
        error_message: null,
        numbers: null,
        context_meter: null,
        run_summary: null,
      },
    ],
  };
}

beforeEach(() => {
  api.listAssistantChats.mockResolvedValue(chats);
  api.getAssistantChat.mockImplementation((_t: string, _h: string, id: string) =>
    Promise.resolve(detail(id, id === "chat-2" ? "Antwort zu Fixkosten" : "Antwort zum Auto")),
  );
  api.deleteAssistantChat.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPage(path = "/assistent") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AssistantPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Seite KI-Assistent", () => {
  it("listet die eigenen Chats und oeffnet ohne Angabe den neuesten", async () => {
    renderPage();
    const list = await screen.findByRole("navigation", { name: "Chats" });
    const items = await within(list).findAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("Fixkosten im Herbst"),
      expect.stringContaining("Autokosten"),
    ]);
    expect(await screen.findByText("Antwort zu Fixkosten")).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: /^Fixkosten im Herbst/ })).toHaveAttribute(
      "aria-current",
      "true",
    );

    fireEvent.click(within(list).getByRole("button", { name: /^Autokosten/ }));
    expect(await screen.findByText("Antwort zum Auto")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Neuer Chat" }));
    expect(await screen.findByText(/Frag nach deinen Finanzen/)).toBeInTheDocument();
  });

  it("loescht einen Chat erst nach Rueckfrage", async () => {
    const confirm = vi
      .spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    renderPage("/assistent?chat=chat-1");
    const remove = await screen.findByRole("button", { name: "Chat „Autokosten“ löschen" });

    fireEvent.click(remove);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(api.deleteAssistantChat).not.toHaveBeenCalled();

    fireEvent.click(remove);
    await waitFor(() =>
      expect(api.deleteAssistantChat).toHaveBeenCalledWith("tok", "hh-1", "chat-1"),
    );
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /^Autokosten/ })).not.toBeInTheDocument(),
    );
  });
});
