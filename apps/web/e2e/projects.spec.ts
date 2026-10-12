import { test, expect } from "./fixtures/household-fixture";

// End-to-End aus docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md, Abschnitt 15.6 und
// P1-Abnahme (Abschnitt 16): Hausbau aus Vorlage anlegen, Kosten aendern, in
// Basis und Kosten-Stress vergleichen, Auswahl mit unvollstaendigem
// Zahlungsplan blockieren.
test.describe("Vorhaben", () => {
  test("Hausbau aus Vorlage anlegen, Kosten pflegen, Stress-Szenario vergleichen", async ({
    page,
  }) => {
    // --- 1. Anlegen aus Vorlage ------------------------------------------
    await page.goto("/vorhaben");
    await expect(page.getByRole("heading", { name: "Vorhaben", exact: true })).toBeVisible();

    await page.getByRole("button", { name: "+ Vorhaben" }).click();
    const wizard = page.getByRole("dialog", { name: "Neues Vorhaben" });
    await wizard.getByLabel("Name *").fill("E2E Hausbau Musterstrasse");
    await expect(wizard.getByLabel("Vorlage")).toHaveValue("home_build_de");
    await wizard.getByRole("button", { name: "Anlegen" }).click();

    await expect(page.getByRole("heading", { name: "E2E Hausbau Musterstrasse" })).toBeVisible();
    await expect(page.getByText("Idee")).toBeVisible();

    // --- 2. Kostenplan: Vorlage brachte alle zehn Abschnitte mit ---------
    await page.getByRole("tab", { name: "Kosten" }).click();
    await expect(page.getByRole("heading", { name: "Grundstück/Erwerb" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Rohbau" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Übergangskosten" })).toBeVisible();

    const grundstueckRow = page.locator("tr.txn-tr").filter({ hasText: "Grundstückskaufpreis" });
    await grundstueckRow.getByRole("button", { name: "Bearbeiten" }).click();
    const editDialog = page.getByRole("dialog", { name: "Position bearbeiten" });
    await editDialog.getByLabel("Schätzung (€)").fill("85000");
    await editDialog.getByRole("button", { name: "Speichern" }).click();
    await expect(editDialog).toBeHidden();
    await expect(grundstueckRow.getByText("85.000,00 €").first()).toBeVisible();

    // --- 3. Auswahl mit unvollstaendigem Zahlungsplan wird blockiert -----
    await page.getByRole("tab", { name: "Übersicht" }).click();
    await page.getByRole("button", { name: "Als Entscheidungsstand auswählen" }).click();
    await expect(
      page.getByText(/Zahlungsplan unvollständig|cost_schedule_incomplete/),
    ).toBeVisible();

    // --- 4. Stresstest-Klon: +15 % auf pufferfaehige Kosten ---------------
    await page.getByRole("button", { name: "Szenario klonen / Stresstest" }).click();
    const cloneDialog = page.getByRole("dialog", { name: "Szenario klonen" });
    await expect(
      cloneDialog.getByLabel("Kostenaufschlag auf pufferfähige Positionen (%)"),
    ).toHaveValue("15");
    await cloneDialog.getByRole("button", { name: "Klonen" }).click();
    await expect(cloneDialog).toBeHidden();
    await expect(page.getByLabel("Szenario")).toHaveValue(/.+/);

    // --- 5. Vergleich zeigt Basis vs. Kosten-Stress -----------------------
    await page.getByRole("tab", { name: "Vergleich" }).click();
    await expect(page.getByRole("checkbox", { name: "Basis" })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: "Kosten-Stress" })).toBeChecked();
    const comparisonTable = page.locator("table.txn-table");
    await expect(comparisonTable.getByText("Projektkosten (Cash)")).toBeVisible();
  });
});
