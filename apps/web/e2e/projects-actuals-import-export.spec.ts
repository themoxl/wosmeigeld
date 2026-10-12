import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/household-fixture";

// Ergaenzt projects.spec.ts um die P4-Abnahme aus
// docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md Abschnitt 15.6: Transaktion einer
// Kostenposition zuordnen (Ist/Rest aktualisieren sich) sowie Excel-/CSV-
// Export des Kostenplans.
test.describe("Vorhaben Ist-Kosten und Export", () => {
  test("Buchung einer Kostenposition zuordnen, Ist/Rest aktualisieren sich", async ({
    page,
    seeded,
  }) => {
    const marker = `e2e-actual-${randomUUID()}`;
    const todayIso = new Date().toISOString().slice(0, 10);
    // Ausgabe ueber 4.000,00 EUR, die spaeter voll der Kostenposition
    // zugeordnet wird.
    await seeded.createTransaction({
      booked_date: todayIso,
      amount_minor: -400_000,
      note: marker,
    });

    await page.goto("/vorhaben");
    await page.getByRole("button", { name: "+ Vorhaben" }).click();
    const wizard = page.getByRole("dialog", { name: "Neues Vorhaben" });
    await wizard.getByLabel("Name *").fill("E2E Ist-Kosten Musterstrasse");
    await wizard.getByRole("button", { name: "Anlegen" }).click();
    await expect(page.getByRole("heading", { name: "E2E Ist-Kosten Musterstrasse" })).toBeVisible();

    // --- 1. Forecast der Position auf 30.000 EUR setzen ---------------------
    await page.getByRole("tab", { name: "Kosten" }).click();
    const grundstueckRow = page.locator("tr.txn-tr").filter({ hasText: "Grundstückskaufpreis" });
    await grundstueckRow.getByRole("button", { name: "Bearbeiten" }).click();
    const editDialog = page.getByRole("dialog", { name: "Position bearbeiten" });
    await editDialog.getByLabel("Schätzung (€)").fill("30000");
    await editDialog.getByRole("button", { name: "Speichern" }).click();
    await expect(editDialog).toBeHidden();

    // --- 2. Ist-Kosten-Dialog: Buchung suchen und zuordnen -------------------
    await grundstueckRow.getByRole("button", { name: "Ist-Kosten" }).click();
    const actualsDialog = page.getByRole("dialog", { name: /^Ist-Kosten:/ });
    await expect(actualsDialog.getByText("Noch keine Ist-Kosten zugeordnet.")).toBeVisible();

    await actualsDialog.getByLabel("Suche (Empfänger/Notiz)").fill(marker);
    const candidatesTable = actualsDialog.locator("table.txn-table").last();
    await expect(candidatesTable.locator("tr.txn-tr")).toHaveCount(1);
    await expect(candidatesTable.getByText(todayIso)).toBeVisible();
    await candidatesTable.getByRole("button", { name: "Zuordnen" }).click();

    // Nach der Zuordnung erscheint die "Vorhandene Zuordnungen"-Tabelle als
    // erste Tabelle im Dialog (DOM-Reihenfolge in ActualsPanel.tsx).
    const assignedTable = actualsDialog.locator("table.txn-table").first();
    const assignedRow = assignedTable.locator("tr.txn-tr").filter({ hasText: "Buchung" });
    await expect(assignedRow).toBeVisible();
    await expect(assignedRow.locator("td.txn-td--amount-wide")).toHaveText("4.000,00 €");

    await actualsDialog.getByRole("button", { name: "Schließen" }).click();
    await expect(actualsDialog).toBeHidden();

    // --- 3. Kostentabelle: Ist- und Rest-Spalte aktualisiert -----------------
    const amountCells = grundstueckRow.locator("td.txn-td--amount-wide");
    // Reihenfolge lt. CostPlanTable.tsx: Schätzung, Angebot, Auftrag,
    // Forecast, Ist, Rest.
    await expect(amountCells.nth(4)).toHaveText("4.000,00 €");
    await expect(amountCells.nth(5)).toHaveText("26.000,00 €");
  });

  test("Kostenplan als Excel und als CSV exportieren", async ({ page }) => {
    await page.goto("/vorhaben");
    await page.getByRole("button", { name: "+ Vorhaben" }).click();
    const wizard = page.getByRole("dialog", { name: "Neues Vorhaben" });
    await wizard.getByLabel("Name *").fill("E2E Kostenplandateien Musterstrasse");
    await wizard.getByRole("button", { name: "Anlegen" }).click();
    await expect(
      page.getByRole("heading", { name: "E2E Kostenplandateien Musterstrasse" }),
    ).toBeVisible();

    await page.getByRole("tab", { name: "Import/Export" }).click();
    await expect(page.getByRole("heading", { name: "Export", exact: true })).toBeVisible();

    const [xlsxDownload] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Als Excel exportieren" }).click(),
    ]);
    expect(xlsxDownload.suggestedFilename()).toBe("vorhaben-kostenplan.xlsx");

    const [csvDownload] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Kostenpositionen als CSV exportieren" }).click(),
    ]);
    expect(csvDownload.suggestedFilename()).toBe("kostenpositionen.csv");
  });
});
