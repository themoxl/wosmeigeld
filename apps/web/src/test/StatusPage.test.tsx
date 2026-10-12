import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { SystemHealth } from "@finanzplaner/contracts";
import { StatusPage } from "../pages/StatusPage";

vi.mock("../lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: vi.fn(() => Promise.resolve({ data: { session: null } })),
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
    },
  },
}));

function renderStatusPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <StatusPage />
    </QueryClientProvider>,
  );
}

const sampleHealth: SystemHealth = {
  status: "degraded",
  services: [
    { service: "api", status: "ok", checkedAt: "2026-07-31T10:00:00.000Z" },
    { service: "database", status: "ok", checkedAt: "2026-07-31T10:00:00.000Z" },
    {
      service: "redis",
      status: "degraded",
      detail: "Hohe Latenz",
      checkedAt: "2026-07-31T10:00:00.000Z",
    },
    { service: "worker", status: "down", checkedAt: "2026-07-31T10:00:00.000Z" },
    { service: "storage", status: "unknown", checkedAt: "2026-07-31T10:00:00.000Z" },
    { service: "lm_studio", status: "unknown", checkedAt: "2026-07-31T10:00:00.000Z" },
  ],
};

describe("StatusPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt zunaechst einen Ladezustand und danach den Status jedes Dienstes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(sampleHealth),
      }),
    );

    renderStatusPage();

    expect(screen.getByRole("status")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Datenbank")).toBeInTheDocument();
    });

    expect(screen.getByText("LM Studio (lokale KI)")).toBeInTheDocument();
    expect(screen.getByText("Hohe Latenz")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Lokale KI (LM Studio)" })).toBeInTheDocument();
  });

  it("zeigt einen Fehlerzustand, wenn die API nicht erreichbar ist", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));

    renderStatusPage();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });
});
