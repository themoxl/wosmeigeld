import { useState } from "react";
import { Outlet, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { useAiTaskCompletionEffects, useAiTasks } from "../hooks/useAiTaskQueue";
import { Sidebar } from "./Sidebar";
import { AiTaskQueueChip } from "./AiTaskQueueChip";
import { AiTaskQueuePanel } from "./AiTaskQueuePanel";
import { AssistantPanel } from "./assistant/AssistantPanel";

/** ?assistent=<chatId> oeffnet das KI-Feld (leer = neuer Chat). */
const ASSISTANT_PARAM = "assistent";

interface AssistantState {
  open: boolean;
  chatId: string | null;
  /** Zuletzt gesehener Wert des URL-Parameters */
  seenParam: string | null;
}

export function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [aiQueueOpen, setAiQueueOpen] = useState(false);
  const aiTasks = useAiTasks();
  useAiTaskCompletionEffects(aiTasks.data);

  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const canAsk = Boolean(session && selectedHouseholdId);

  // Das KI-Feld lebt hier, ausserhalb von <Outlet>: ein laufender Strom
  // uebersteht so jede Navigation. Der URL-Parameter oeffnet es nur - die
  // Links der Seitenleiste tragen ihn nicht weiter, das Feld soll beim
  // Seitenwechsel aber offen bleiben. Ein neuer Wert wird waehrend des
  // Renderns uebernommen (kein Effekt, der Zustand nachzieht).
  const [searchParams, setSearchParams] = useSearchParams();
  const assistantParam = searchParams.get(ASSISTANT_PARAM);
  const [assistant, setAssistant] = useState<AssistantState>({
    open: assistantParam !== null,
    chatId: assistantParam || null,
    seenParam: assistantParam,
  });
  if (assistantParam !== assistant.seenParam) {
    setAssistant(
      assistantParam === null
        ? { ...assistant, seenParam: null }
        : { open: true, chatId: assistantParam || null, seenParam: assistantParam },
    );
  }
  const assistantVisible = assistant.open && canAsk;

  const toggleSidebar = () => {
    setSidebarOpen((open) => !open);
  };

  const closeSidebar = () => {
    setSidebarOpen(false);
  };

  const toggleAssistant = () => {
    setAssistant((current) => ({ ...current, open: !current.open }));
    closeSidebar();
  };

  const closeAssistant = () => {
    setAssistant((current) => ({ ...current, open: false }));
    if (assistantParam !== null) {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete(ASSISTANT_PARAM);
          return next;
        },
        { replace: true },
      );
    }
  };

  const selectAssistantChat = (chatId: string | null) => {
    setAssistant((current) => ({ ...current, chatId }));
  };

  const shellClass = [
    "app-shell",
    sidebarOpen ? "app-shell--sidebar-open" : null,
    assistantVisible ? "app-shell--assistant-open" : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={shellClass}>
      <header className="mobile-topbar" data-testid="mobile-topbar">
        <button
          type="button"
          className="mobile-menu-btn"
          aria-label="Navigation öffnen"
          onClick={toggleSidebar}
          data-testid="mobile-menu-toggle"
        >
          Menü
        </button>
        <span className="mobile-topbar-brand">Finanzplaner</span>
        {canAsk && (
          <button
            type="button"
            className="assistant-open-btn assistant-open-btn--compact"
            aria-expanded={assistantVisible}
            onClick={toggleAssistant}
            data-testid="mobile-assistant-toggle"
          >
            KI fragen
          </button>
        )}
        <AiTaskQueueChip compact onOpen={() => setAiQueueOpen(true)} />
      </header>
      <Sidebar
        onNavigate={closeSidebar}
        onOpenAiTasks={() => setAiQueueOpen(true)}
        onToggleAssistant={canAsk ? toggleAssistant : undefined}
        assistantOpen={assistantVisible}
      />
      <button
        type="button"
        className="mobile-backdrop"
        aria-label="Navigation schließen"
        onClick={closeSidebar}
      />
      <main className="app-content" data-testid="app-content" onClick={closeSidebar}>
        <Outlet />
      </main>
      {assistantVisible && (
        <AssistantPanel
          chatId={assistant.chatId}
          onSelectChat={selectAssistantChat}
          onClose={closeAssistant}
        />
      )}
      <AiTaskQueuePanel open={aiQueueOpen} onClose={() => setAiQueueOpen(false)} />
    </div>
  );
}
