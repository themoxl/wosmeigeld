import { useAiTasks } from "../hooks/useAiTaskQueue";

export function AiTaskQueueChip({
  onOpen,
  compact = false,
}: {
  onOpen: () => void;
  compact?: boolean;
}) {
  const tasksQuery = useAiTasks();
  const tasks = tasksQuery.data ?? [];
  const running = tasks.filter((task) => task.status === "running").length;
  const queued = tasks.filter((task) => task.status === "queued").length;
  const review = tasks.filter((task) => task.status === "awaiting_confirmation").length;
  const latest = [...tasks].sort((left, right) =>
    right.created_at.localeCompare(left.created_at),
  )[0];
  const latestFailed = latest?.status === "failed";
  const hasAttention = running + queued + review > 0 || latestFailed;

  const label = compact
    ? `KI ${running ? "arbeitet" : queued ? `wartet ${queued}` : review ? `prüfen ${review}` : latestFailed ? "Fehler" : "bereit"}`
    : running
      ? `KI-Status · ${running} läuft${queued ? ` · ${queued} warten` : ""}`
      : queued
        ? `KI-Status · ${queued} warten`
        : review
          ? `KI-Status · ${review} zur Prüfung`
          : latestFailed
            ? "KI-Status · letzter Lauf fehlgeschlagen"
            : "KI-Status · bereit";

  return (
    <button
      type="button"
      className={`ai-queue-chip${hasAttention ? " ai-queue-chip--active" : ""}${latestFailed ? " ai-queue-chip--error" : ""}${compact ? " ai-queue-chip--compact" : ""}`}
      onClick={onOpen}
      aria-label={`${label}. Aktivitätsfenster öffnen`}
      data-testid={compact ? "mobile-ai-queue-chip" : "ai-queue-chip"}
    >
      <span className="ai-queue-chip__pulse" aria-hidden="true" />
      <span>{label}</span>
    </button>
  );
}
