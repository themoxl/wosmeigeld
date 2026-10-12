import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { JoinHouseholdPage } from "../pages/JoinHouseholdPage";

const authState = vi.hoisted(() => ({
  session: {
    access_token: "test-access-token",
    user: { id: "user-1", email: "test@example.com" },
  },
}));

vi.mock("../lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: vi.fn(() => Promise.resolve({ data: { session: authState.session } })),
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
      signInWithPassword: vi.fn(),
      signUp: vi.fn(),
      signOut: vi.fn(),
    },
  },
}));

const meResponse = {
  user_id: "user-1",
  display_name: null,
  language: "de",
  timezone: "Europe/Vienna",
  currency: "EUR",
  households: [],
};

const previewResponse = {
  household_id: "household-1",
  household_name: "Mustermann-Haushalt",
  role: "editor",
  expires_at: "2026-08-08T00:00:00Z",
};

const redeemResponse = {
  id: "household-1",
  name: "Mustermann-Haushalt",
  base_currency: "EUR",
  timezone: "Europe/Vienna",
  budget_method: "tracking",
  fiscal_year_start_month: 1,
  status: "active",
  role: "editor",
};

function renderJoinPage() {
  const redeemMock = vi.fn(() =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(redeemResponse) }),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/v1/households/invites/redeem/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(previewResponse),
        });
      }
      if (url.includes("/v1/households/invites/redeem") && init?.method === "POST") {
        return redeemMock();
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }),
  );

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/beitreten?code=ABCD1234"]}>
        <AuthProvider>
          <HouseholdProvider>
            <JoinHouseholdPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { redeemMock };
}

describe("JoinHouseholdPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt die Vorschau und loest den Code bei Bestaetigung ein", async () => {
    const { redeemMock } = renderJoinPage();

    await waitFor(() => {
      expect(screen.getByDisplayValue("ABCD1234")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Code prüfen" }));

    await waitFor(() => {
      expect(screen.getByText(/Mustermann-Haushalt/)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Beitreten" }));

    await waitFor(() => {
      expect(redeemMock).toHaveBeenCalled();
    });
  });
});
