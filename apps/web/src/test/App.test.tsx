import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "../App";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { navGroups, navItems } from "../nav";

const authState = vi.hoisted(() => ({
  session: null as { access_token: string; user: { id: string; email: string } } | null,
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

const fakeSession = {
  access_token: "test-access-token",
  user: { id: "user-1", email: "test@example.com" },
};

const meResponse = {
  user_id: "user-1",
  display_name: null,
  language: "de",
  timezone: "Europe/Vienna",
  currency: "EUR",
  households: [{ household_id: "household-1", name: "Testhaushalt", role: "owner" }],
};

function renderApp(initialPath = "/") {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      // endsWith statt includes: "/v1/me" ist sonst ein Praefix-Substring von
      // "/v1/meal-weeks" und wuerde dessen Requests faelschlich abfangen.
      if (url.endsWith("/v1/me")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(meResponse),
        });
      }
      if (url.includes("/v1/reports/overview")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              current_year: 2026,
              current_month: 8,
              total_income_minor: 0,
              total_expense_minor: 0,
              net_savings_minor: 0,
              savings_rate_percent: 0,
              budget_remaining_minor: 0,
              open_review_count: 0,
              top_expense_categories: [],
              cashflow_last_6_months: [],
            }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }),
  );

  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AuthProvider>
          <HouseholdProvider>
            <App />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App-Shell", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    authState.session = null;
  });

  it("zeigt die Sidebar-Navigation und die Übersicht-Seite fuer angemeldete Nutzer", async () => {
    authState.session = fakeSession;
    renderApp("/");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Übersicht" })).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "Budget" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Einstellungen" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("Einnahmen")).toBeInTheDocument();
    });
  });

  it("gliedert die Navigation in Gruppen und verlinkt weiterhin alle 21 Ziele", async () => {
    authState.session = fakeSession;
    renderApp("/");

    const nav = await screen.findByRole("navigation", { name: "Hauptnavigation" });

    // Jede Gruppe ist als echte Struktur ausgezeichnet: Ueberschrift plus
    // darueber benannte Liste.
    expect(navGroups).toHaveLength(6);
    navGroups.forEach((group) => {
      expect(within(nav).getByRole("heading", { name: group.label })).toBeInTheDocument();
      const list = within(nav).getByRole("list", { name: group.label });
      expect(within(list).getAllByRole("link")).toHaveLength(group.items.length);
    });

    // Regressionsschutz: die Umstrukturierung darf kein Navigationsziel
    // verlieren oder eine URL veraendern. 23 seit den Versicherungen
    // (/versicherungen in der Gruppe "Planung", neben den Vertraegen), 24 seit
    // dem KI-Assistenten (/assistent in der Gruppe "Überblick").
    expect(navItems).toHaveLength(24);
    const hrefs = within(nav)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));
    expect(hrefs).toHaveLength(24);
    navItems.forEach((item) => {
      expect(hrefs).toContain(item.path);
      expect(within(nav).getByRole("link", { name: item.label })).toBeInTheDocument();
    });
    expect(within(nav).getByRole("link", { name: "Sparen & Wünsche" })).toHaveAttribute(
      "href",
      "/sparen",
    );

    // Der aktive Eintrag bleibt erkennbar hervorgehoben.
    const activeLink = within(nav).getByRole("link", { name: "Übersicht" });
    expect(activeLink).toHaveAttribute("aria-current", "page");
    expect(activeLink.className).toContain("sidebar-link--active");
  });

  it("ordnet Vorhaben und Förderungen einer eigenen Navigationsgruppe zu", () => {
    const vorhaben = navGroups.find((group) => group.id === "vorhaben");
    const zieleVermoegen = navGroups.find((group) => group.id === "ziele-vermoegen");

    expect(vorhaben?.items.map((item) => item.path)).toEqual(["/vorhaben", "/foerderungen"]);
    expect(zieleVermoegen?.items.map((item) => item.path)).not.toContain("/vorhaben");
    expect(zieleVermoegen?.items.map((item) => item.path)).not.toContain("/foerderungen");
  });

  it("zeigt die Lebensmittelplanung statt einer Platzhalterseite", async () => {
    authState.session = fakeSession;
    renderApp("/lebensmittel");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Lebensmittel" })).toBeInTheDocument();
    });
    // Ohne geplante Woche steht die laufende Kalenderwoche bereits in der
    // Navigation und laesst sich mit einem Klick anlegen - kein Dialog noetig.
    // Die Ueberschrift nennt den Zeitraum, der vom heutigen Datum abhaengt.
    expect(screen.getByRole("heading", { name: /ist noch nicht geplant$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Diese Woche planen" })).toBeInTheDocument();
  });

  it.each([
    ["/sparziele", "Spartöpfe"],
    ["/wunschliste", "Wünsche"],
  ])("leitet den Legacy-Einstieg %s auf den passenden gemeinsamen Tab", async (path, tab) => {
    authState.session = fakeSession;
    renderApp(path);

    expect(await screen.findByRole("heading", { name: "Sparen & Wünsche" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: tab })).toHaveAttribute("aria-current", "page");
  });

  it("leitet nicht angemeldete Nutzer zur Login-Seite um", async () => {
    authState.session = null;
    renderApp("/");

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Anmelden" })).toBeInTheDocument();
    });
  });

  it("oeffnet das KI-Feld per ?assistent=, laesst es die Navigation ueberstehen und schliesst es ueber KI fragen", async () => {
    authState.session = fakeSession;
    renderApp("/?assistent=");

    const panel = await screen.findByTestId("assistant-panel");
    expect(panel).toHaveAccessibleName("KI-Assistent");
    expect(document.querySelector(".app-shell")).toHaveClass("app-shell--assistant-open");

    // Das Feld liegt ausserhalb von <Outlet>: ein Seitenwechsel laesst es stehen.
    const nav = screen.getByRole("navigation", { name: "Hauptnavigation" });
    fireEvent.click(within(nav).getByRole("link", { name: "Lebensmittel" }));
    expect(await screen.findByRole("heading", { name: "Lebensmittel" })).toBeInTheDocument();
    expect(screen.getByTestId("assistant-panel")).toBe(panel);

    const toggle = screen.getByTestId("assistant-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(screen.queryByTestId("assistant-panel")).not.toBeInTheDocument();
    expect(document.querySelector(".app-shell")).not.toHaveClass("app-shell--assistant-open");

    fireEvent.click(screen.getByTestId("assistant-toggle"));
    expect(await screen.findByTestId("assistant-panel")).toBeInTheDocument();
  });
});
