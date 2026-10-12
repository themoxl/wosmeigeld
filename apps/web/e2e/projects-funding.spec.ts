import { test, expect } from "./fixtures/household-fixture";

// Ergaenzt projects.spec.ts um die P3-Abnahme aus
// docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md Abschnitt 15.6: Foerderprofil
// pflegen, Foerdercheck laufen lassen, mindestens ein Treffer mit
// Status-Badge. Der Hausbau-Vorlage-Standardtyp "home_build" plus
// Standardland "DE" (major_projects.country_code default, 0001_schema.sql)
// matcht deterministisch das KfW-Wohneigentumsprogramm (124) - dessen
// einzige Bedingung ist Selbstnutzung (0001_schema.sql, ~Zeile 2118), ohne
// Einkommens- oder Regionsabhaengigkeit.
test.describe("Vorhaben Foerderung", () => {
  test("Foerderprofil speichern, Foerdercheck liefert Treffer mit Status", async ({ page }) => {
    await page.goto("/vorhaben");
    await page.getByRole("button", { name: "+ Vorhaben" }).click();
    const wizard = page.getByRole("dialog", { name: "Neues Vorhaben" });
    await wizard.getByLabel("Name *").fill("E2E Foerderung Musterstrasse");
    await wizard.getByRole("button", { name: "Anlegen" }).click();
    await expect(page.getByRole("heading", { name: "E2E Foerderung Musterstrasse" })).toBeVisible();

    await page.getByRole("tab", { name: "Förderung" }).click();
    await expect(page.getByRole("heading", { name: "Förderprofil" })).toBeVisible();

    // --- 1. Foerderprofil pflegen -------------------------------------------
    await page.getByLabel("Jahreshaushaltseinkommen (€)").fill("80000");
    await page.getByLabel("Anzahl Kinder im Haushalt").fill("2");
    await page.getByLabel("Immobilie wird nach Erwerb/Bau selbst genutzt").check();

    await Promise.all([
      page.waitForResponse(
        (response) => response.url().includes("/eligibility-profile") && response.ok(),
      ),
      page.getByRole("button", { name: "Profil speichern" }).click(),
    ]);

    // --- 2. Foerdercheck ausfuehren ------------------------------------------
    await Promise.all([
      page.waitForResponse(
        (response) => response.url().includes("/funding-check") && response.ok(),
      ),
      page.getByRole("button", { name: "Förderung prüfen" }).click(),
    ]);

    // --- 3. Mindestens ein Treffer mit Status-Badge -------------------------
    const kfwRow = page.locator("tr.txn-tr").filter({ hasText: "KfW-Wohneigentumsprogramm (124)" });
    await expect(kfwRow).toBeVisible();
    await expect(kfwRow.locator(".metric-hint")).toHaveText("KfW");
    await expect(kfwRow.locator("span.status-badge")).toHaveText("Förderfähig");
    await expect(kfwRow.locator("td.txn-td--amount-wide").nth(1)).toHaveText("100.000,00 €");

    // --- 4. Treffer als Finanzierungsquelle uebernehmen ---------------------
    await kfwRow.getByRole("button", { name: "Als Quelle übernehmen" }).click();
    await expect(page.getByText(/Übernommen als Finanzierungsquelle/)).toBeVisible();
  });
});
