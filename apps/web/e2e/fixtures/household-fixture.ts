import { test as base, expect, type APIRequestContext } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { cleanupTestIdentity } from "./test-cleanup";

const SUPABASE_URL = process.env.VITE_SUPABASE_URL ?? "http://127.0.0.1:55321";
const SUPABASE_ANON_KEY =
  process.env.VITE_SUPABASE_ANON_KEY ??
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0";
const API_BASE = process.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// Spiegelt den internen Default von @supabase/supabase-js (SupabaseClient-
// Konstruktor): `sb-<erstes Hostname-Label>-auth-token`. Verifiziert gegen
// das in apps/web/package.json gepinnte @supabase/supabase-js@2.111.0
// (dist/umd/supabase.js: `sb-${r.hostname.split(".")[0]}-auth-token`).
function storageKeyFor(url: string): string {
  return `sb-${new URL(url).hostname.split(".")[0]}-auth-token`;
}

interface SignupResponse {
  access_token: string;
  refresh_token: string;
}

interface Seeded {
  token: string;
  refreshToken: string;
  householdId: string;
  accountId: string;
  // POST /v1/households seedet automatisch Standard-Kategoriegruppen (SQL-
  // Funktion create_household(), siehe supabase/migrations/0001_schema.sql) -
  // die erste davon steht Tests, die irgendeine gueltige Kategorie brauchen,
  // ohne weiteres Setup zur Verfuegung.
  firstCategory: { id: string; name: string };
  request: APIRequestContext;
  createTransaction: (input: Record<string, unknown>) => Promise<{ id: string }>;
  createAccount: (input: Record<string, unknown>) => Promise<{ id: string }>;
}

export const test = base.extend<{ seeded: Seeded }>({
  seeded: async ({ playwright }, use) => {
    const api = await playwright.request.newContext();
    const email = `e2e-${randomUUID()}@finanzplaner.test`;
    try {
      const signupResponse = await api.post(`${SUPABASE_URL}/auth/v1/signup`, {
        headers: { apikey: SUPABASE_ANON_KEY },
        data: { email, password: "correct-horse-battery-staple-e2e" },
      });
      if (!signupResponse.ok()) {
        throw new Error(
          `Supabase-Signup fuer E2E-Testnutzer fehlgeschlagen (${signupResponse.status()}). ` +
            "Laeuft 'supabase start' im Ordner supabase/?",
        );
      }
      const session = (await signupResponse.json()) as SignupResponse;
      const authHeaders = { Authorization: `Bearer ${session.access_token}` };

      const householdResponse = await api.post(`${API_BASE}/v1/households`, {
        headers: authHeaders,
        data: { name: "E2E Haushalt" },
      });
      if (!householdResponse.ok()) {
        throw new Error(
          `Haushalt-Anlage fuer E2E-Test fehlgeschlagen (${householdResponse.status()}). ` +
            "Laeuft services/api (uvicorn) auf VITE_API_BASE_URL?",
        );
      }
      const household = (await householdResponse.json()) as { id: string };

      const accountResponse = await api.post(`${API_BASE}/v1/accounts`, {
        headers: { ...authHeaders, "X-Household-Id": household.id },
        data: { name: "Girokonto", account_type: "checking" },
      });
      if (!accountResponse.ok()) {
        throw new Error(`Konto-Anlage fuer E2E-Test fehlgeschlagen (${accountResponse.status()}).`);
      }
      const account = (await accountResponse.json()) as { id: string };

      const categoriesResponse = await api.get(`${API_BASE}/v1/categories`, {
        headers: { ...authHeaders, "X-Household-Id": household.id },
      });
      if (!categoriesResponse.ok()) {
        throw new Error(
          `Kategorien-Abruf fuer E2E-Test fehlgeschlagen (${categoriesResponse.status()}).`,
        );
      }
      type CategoryGroup = { categories: { id: string; name: string }[] };
      const categoryGroups = (await categoriesResponse.json()) as CategoryGroup[];
      const firstCategory = categoryGroups.flatMap((group) => group.categories)[0];
      if (!firstCategory) {
        throw new Error(
          "Kein Standard-Kategorie nach Haushalt-Anlage gefunden - seedet create_household() " +
            "(0001_schema.sql) noch Kategorien?",
        );
      }

      await use({
        token: session.access_token,
        refreshToken: session.refresh_token,
        householdId: household.id,
        accountId: account.id,
        firstCategory,
        request: api,
        createTransaction: async (input) => {
          const response = await api.post(`${API_BASE}/v1/transactions`, {
            headers: { ...authHeaders, "X-Household-Id": household.id },
            data: { account_id: account.id, ...input },
          });
          if (!response.ok()) {
            throw new Error(
              `Transaktions-Seed fuer E2E-Test fehlgeschlagen (${response.status()}).`,
            );
          }
          return (await response.json()) as { id: string };
        },
        createAccount: async (input) => {
          const response = await api.post(`${API_BASE}/v1/accounts`, {
            headers: { ...authHeaders, "X-Household-Id": household.id },
            data: input,
          });
          if (!response.ok()) {
            throw new Error(`Konto-Seed fuer E2E-Test fehlgeschlagen (${response.status()}).`);
          }
          return (await response.json()) as { id: string };
        },
      });
    } finally {
      try {
        await api.dispose();
      } finally {
        await cleanupTestIdentity(email);
      }
    }
  },

  storageState: async ({ seeded, baseURL, browser }, use) => {
    const sessionPayload = {
      access_token: seeded.token,
      refresh_token: seeded.refreshToken,
      token_type: "bearer",
      expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    };

    const context = await browser.newContext();
    await context.addInitScript(([key, value]) => window.localStorage.setItem(key, value), [
      storageKeyFor(SUPABASE_URL),
      JSON.stringify(sessionPayload),
    ] as [string, string]);
    const page = await context.newPage();
    await page.goto(baseURL ?? "http://localhost:5173");
    // Schlaegt HIER laut fehl, statt dass jeder abhaengige Test spaeter mit
    // einem verwirrenden "immer noch auf /login"-Timeout scheitert - z.B.
    // falls eine kuenftige supabase-js-Version die Storage-Key-Formel aendert.
    await expect(page.getByRole("heading", { name: "Übersicht" })).toBeVisible();
    const state = await context.storageState();
    await context.close();
    await use(state);
  },
});

export { expect };
