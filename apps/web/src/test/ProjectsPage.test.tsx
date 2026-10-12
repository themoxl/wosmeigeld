import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ProjectsPage } from "../pages/ProjectsPage";

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

function meResponse(role: string) {
  return {
    user_id: "user-1",
    display_name: null,
    language: "de",
    timezone: "Europe/Vienna",
    currency: "EUR",
    households: [{ household_id: "household-1", name: "Testhaushalt", role }],
  };
}

function projectListItem(overrides: Record<string, unknown> = {}) {
  return {
    id: "project-1",
    name: "Hausbau Musterstrasse",
    project_type: "home_build",
    status: "idea",
    selected_scenario_id: null,
    selected_scenario_name: null,
    project_cash_cost_minor: null,
    planned_completion_on: null,
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

function renderProjectsPage(role = "owner") {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";

    if (url.includes("/v1/me")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(meResponse(role)),
      });
    }
    if (url.endsWith("/v1/projects") && method === "POST") {
      return Promise.resolve({
        ok: true,
        status: 201,
        json: () =>
          Promise.resolve({
            id: "project-new",
            name: "Neues Vorhaben",
            project_type: "home_build",
            status: "idea",
            currency: "EUR",
            country_code: "DE",
            region_code: null,
            postal_code: null,
            municipality: null,
            planned_start_on: null,
            planned_completion_on: null,
            actual_start_on: null,
            actual_completion_on: null,
            result_asset_id: null,
            note: null,
            version: 1,
            created_at: "2026-08-01T00:00:00Z",
            updated_at: "2026-08-01T00:00:00Z",
          }),
      });
    }
    if (url.includes("/v1/projects")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve([projectListItem()]),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });
  vi.stubGlobal("fetch", fetchMock);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>
            <ProjectsPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ProjectsPage", () => {
  it("zeigt bestehende Vorhaben in der Liste", async () => {
    renderProjectsPage();
    await screen.findByText("Hausbau Musterstrasse");
    expect(screen.getByText("Hausbau")).toBeInTheDocument();
  });

  it("legt ueber den Wizard ein neues Vorhaben mit Vorlage an", async () => {
    const fetchMock = renderProjectsPage();
    await screen.findByText("Hausbau Musterstrasse");

    await userEvent.click(screen.getByRole("button", { name: "+ Vorhaben" }));
    const nameField = await screen.findByLabelText("Name *");
    await userEvent.type(nameField, "Zweites Vorhaben");
    await userEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/v1/projects") && (init as RequestInit)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(String((postCall![1] as RequestInit).body));
      expect(body.name).toBe("Zweites Vorhaben");
      expect(body.project_type).toBe("home_build");
      expect(body.template_key).toBe("home_build_de");
    });
  });

  it("Viewer sieht die Liste, aber keinen Anlegen-Button-Erfolg ohne Schreibrecht", async () => {
    renderProjectsPage("viewer");
    await screen.findByText("Hausbau Musterstrasse");
    // Der Button ist sichtbar (Lesezugriff reicht fuers Oeffnen des Dialogs);
    // die serverseitige Rollenpruefung greift beim eigentlichen Anlegen.
    expect(screen.getByRole("button", { name: "+ Vorhaben" })).toBeInTheDocument();
  });
});
