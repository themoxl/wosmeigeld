// Bewusst @playwright/test direkt, NICHT die household-fixture aus
// fixtures/household-fixture.ts: dieser Test muss das echte Login-/
// Registrierungsformular UI-getrieben durchlaufen, nicht den dortigen
// storageState-Shortcut nutzen (der genau das umgeht).
import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { cleanupTestIdentity } from "./fixtures/test-cleanup";

const SUPABASE_URL = process.env.VITE_SUPABASE_URL ?? "http://127.0.0.1:55321";
const SUPABASE_ANON_KEY =
  process.env.VITE_SUPABASE_ANON_KEY ??
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0";

test.describe("Login", () => {
  test("neuer Nutzer registriert sich per UI und landet nach Onboarding auf dem Dashboard", async ({
    page,
  }) => {
    const email = `e2e-login-${randomUUID()}@finanzplaner.test`;
    try {
      await page.goto("/login");
      await expect(page.getByRole("heading", { name: "Anmelden" })).toBeVisible();

      await page.getByRole("button", { name: "Noch kein Konto? Registrieren" }).click();
      await expect(page.getByRole("heading", { name: "Konto erstellen" })).toBeVisible();

      await page.getByLabel("E-Mail").fill(email);
      await page.getByLabel("Passwort").fill("correct-horse-battery-staple-e2e");
      await page.getByRole("button", { name: "Registrieren" }).click();

      // Frischer Nutzer hat noch keinen Haushalt -> Onboarding statt Dashboard.
      await expect(page.getByRole("heading", { name: "Haushalt erstellen" })).toBeVisible();
      await page.getByLabel("Name des Haushalts").fill("E2E Haushalt");
      await page.getByRole("button", { name: "Haushalt erstellen" }).click();

      await expect(page.getByRole("heading", { name: "Übersicht" })).toBeVisible();
      await expect(page).toHaveURL(/\/$/);
    } finally {
      await cleanupTestIdentity(email);
    }
  });

  test("bestehender Nutzer meldet sich per UI an", async ({ page, request }) => {
    const email = `e2e-login-${randomUUID()}@finanzplaner.test`;
    const password = "correct-horse-battery-staple-e2e";
    try {
      const signupResponse = await request.post(`${SUPABASE_URL}/auth/v1/signup`, {
        headers: { apikey: SUPABASE_ANON_KEY },
        data: { email, password },
      });
      expect(signupResponse.ok()).toBe(true);

      await page.goto("/login");
      await page.getByLabel("E-Mail").fill(email);
      await page.getByLabel("Passwort").fill(password);
      await page.getByRole("button", { name: "Anmelden" }).click();

      // Auch dieser Nutzer ist frisch signt-up ohne Haushalt -> Onboarding.
      await expect(page.getByRole("heading", { name: "Haushalt erstellen" })).toBeVisible();
    } finally {
      await cleanupTestIdentity(email);
    }
  });

  test("falsches Passwort zeigt eine Fehlermeldung", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("E-Mail").fill(`e2e-nope-${randomUUID()}@finanzplaner.test`);
    await page.getByLabel("Passwort").fill("wrong-password-123");
    await page.getByRole("button", { name: "Anmelden" }).click();

    await expect(page.getByRole("alert")).toContainText(/falsch/i);
  });

  test("nicht erreichbarer Anmeldedienst nennt die Ursache statt der Browser-Meldung", async ({
    page,
  }) => {
    // Simuliert ein nicht gestartetes lokales Supabase: der fetch scheitert
    // auf Transportebene, GoTrue liefert also keinen Fehlercode.
    await page.route(`${SUPABASE_URL}/auth/v1/**`, (route) => route.abort("failed"));

    await page.goto("/login");
    await page.getByLabel("E-Mail").fill(`e2e-offline-${randomUUID()}@finanzplaner.test`);
    await page.getByLabel("Passwort").fill("egal-egal-123");
    await page.getByRole("button", { name: "Anmelden" }).click();

    const alert = page.getByRole("alert");
    await expect(alert).toContainText(/nicht erreichbar/i);
    await expect(alert).not.toContainText(/fetch/i);
    // Der Knopf darf nicht in "Bitte warten…" haengen bleiben.
    await expect(page.getByRole("button", { name: "Anmelden" })).toBeEnabled();
  });

  test("Passwort laesst sich zur Kontrolle einblenden", async ({ page }) => {
    await page.goto("/login");
    const password = page.getByLabel("Passwort");
    await password.fill("geheim-123");

    await expect(password).toHaveAttribute("type", "password");
    await page.getByRole("button", { name: "Anzeigen" }).click();
    await expect(password).toHaveAttribute("type", "text");
    await page.getByRole("button", { name: "Verbergen" }).click();
    await expect(password).toHaveAttribute("type", "password");
  });
});
