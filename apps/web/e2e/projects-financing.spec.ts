import { test, expect } from "./fixtures/household-fixture";

// Ergaenzt projects.spec.ts (P1-Abnahme) um die P2-Abnahme aus
// docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md Abschnitt 15.6: zwei
// Finanzierungsquellen (Darlehen + Eigenkapital), das Darlehen mit einem
// Tilgungsplan verknuepft, "Finanzierung"-Tab zeigt beide mit korrekten
// Betraegen.
test.describe("Vorhaben Finanzierung", () => {
  test("Darlehen mit Tilgungsplan und Eigenkapitalquelle anlegen", async ({ page }) => {
    // --- 1. Vorhaben aus Vorlage anlegen (wie projects.spec.ts) -----------
    await page.goto("/vorhaben");
    await page.getByRole("button", { name: "+ Vorhaben" }).click();
    const wizard = page.getByRole("dialog", { name: "Neues Vorhaben" });
    await wizard.getByLabel("Name *").fill("E2E Finanzierung Musterstrasse");
    await wizard.getByRole("button", { name: "Anlegen" }).click();
    await expect(
      page.getByRole("heading", { name: "E2E Finanzierung Musterstrasse" }),
    ).toBeVisible();

    await page.getByRole("tab", { name: "Finanzierung" }).click();
    await expect(page.getByRole("button", { name: "+ Finanzierungsquelle" })).toBeVisible();

    // --- 2. Darlehensquelle anlegen ----------------------------------------
    await page.getByRole("button", { name: "+ Finanzierungsquelle" }).click();
    const loanDialog = page.getByRole("dialog", { name: "Neue Finanzierungsquelle" });
    await loanDialog.getByLabel("Name *").fill("Bankdarlehen Hauptbank");
    await loanDialog.getByLabel("Typ").selectOption({ label: "Bankdarlehen" });
    await loanDialog.getByLabel("Geplanter Betrag (€)").fill("300000");
    await loanDialog.getByRole("button", { name: "Speichern" }).click();
    await expect(loanDialog).toBeHidden();

    // --- 3. Eigenkapitalquelle, an das Girokonto gebunden ------------------
    await page.getByRole("button", { name: "+ Finanzierungsquelle" }).click();
    const equityDialog = page.getByRole("dialog", { name: "Neue Finanzierungsquelle" });
    await equityDialog.getByLabel("Name *").fill("Eigenkapital Girokonto");
    await equityDialog.getByLabel("Typ").selectOption({ label: "Eigenkapital (Konto)" });
    await equityDialog.getByLabel("Geplanter Betrag (€)").fill("50000");
    await equityDialog.getByLabel("Konto *").selectOption({ label: "Girokonto" });
    await equityDialog.getByRole("button", { name: "Speichern" }).click();
    await expect(equityDialog).toBeHidden();

    // --- 4. Tabelle zeigt beide Quellen mit korrekten Betraegen ------------
    const loanRow = page.locator("tr.txn-tr").filter({ hasText: "Bankdarlehen Hauptbank" });
    await expect(loanRow).toBeVisible();
    await expect(loanRow.getByText("Bankdarlehen", { exact: true })).toBeVisible();
    await expect(loanRow.locator("td.txn-td--amount-wide")).toHaveText("300.000,00 €");

    const equityRow = page.locator("tr.txn-tr").filter({ hasText: "Eigenkapital Girokonto" });
    await expect(equityRow).toBeVisible();
    await expect(equityRow.getByText("Eigenkapital (Konto)")).toBeVisible();
    await expect(equityRow.locator("td.txn-td--amount-wide")).toHaveText("50.000,00 €");
    // Kontoname wird als Hinweis unter dem Quellennamen angezeigt.
    await expect(equityRow.getByText("Girokonto", { exact: true })).toBeVisible();

    // --- 5. Darlehen mit Tilgungsplan verknuepfen ---------------------------
    await loanRow.getByRole("button", { name: "Tilgungsplan anlegen" }).click();
    const debtDialog = page.getByRole("dialog", { name: /Tilgungsplan für/ });
    await expect(debtDialog.getByLabel("Darlehenssumme (€) *")).toHaveValue("300000");
    await debtDialog.getByLabel("Monatsrate (€) *").fill("1200");
    await debtDialog.getByRole("button", { name: "Anlegen" }).click();
    await expect(debtDialog).toBeHidden({ timeout: 10_000 });

    // Nach erfolgreicher Verknuepfung verschwindet der Button fuer diese
    // Zeile (has_debt_plan=true, FinancingPanel.tsx Zeile ~163).
    await expect(loanRow.getByRole("button", { name: "Tilgungsplan anlegen" })).toBeHidden();
  });
});
