import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/household-fixture";

// End-to-End aus docs/KATEGORISIERUNGSPLAN.md, Verifikation Punkt 10 (Web-Teil):
// Kategorie anlegen -> im Picker finden -> Buchung zuweisen -> archivieren ->
// Historie prüfen. Der Kern der Abnahme ist, dass Archivieren nichts löscht:
// die Buchung zeigt die Kategorie danach weiterhin, nur mit Badge.
test.describe("Kategorienverwaltung", () => {
  test("Kategorie anlegen, zuweisen, archivieren – Historie bleibt sichtbar", async ({
    page,
    seeded,
  }) => {
    const categoryName = `E2E Kategorie ${randomUUID().slice(0, 8)}`;
    const marker = `e2e-kategorie-${randomUUID()}`;

    await seeded.createTransaction({
      booked_date: new Date().toISOString().slice(0, 10),
      amount_minor: -4200,
      note: marker,
    });

    // --- 1. Anlegen ------------------------------------------------------
    await page.goto("/kategorien");
    await expect(page.getByRole("heading", { name: "Kategorien" })).toBeVisible();

    await page.getByRole("button", { name: "Kategorie hinzufügen", exact: true }).click();
    const createDialog = page.getByRole("dialog", { name: "Kategorie hinzufügen" });
    await createDialog.getByLabel("Name *").fill(categoryName);
    await createDialog.getByLabel("Synonyme (kommagetrennt)").fill("E2E Synonym");
    await createDialog.getByRole("button", { name: "Speichern" }).click();
    await expect(createDialog).toBeHidden();

    const categoryRow = page.locator(".category-row").filter({ hasText: categoryName });
    await expect(categoryRow).toBeVisible();

    // --- 2. Im Picker finden und einer Buchung zuweisen ------------------
    await page.goto("/buchungen");
    const txRow = page.locator("table.txn-table tbody tr").filter({ hasText: marker });
    await expect(txRow).toBeVisible();
    await txRow.locator("td.txn-td--category button").click();

    const picker = page.getByRole("combobox", { name: "Kategorie dieser Buchung" });
    // Gesucht wird über das Synonym - der Treffer trägt trotzdem den vollen
    // Pfad "Gruppe · Kategorie".
    await picker.fill("E2E Synonym");
    await page
      .getByRole("listbox")
      .getByRole("option")
      .filter({ hasText: categoryName })
      .first()
      .click();

    await expect(txRow.locator("td.txn-td--category")).toContainText(categoryName);

    // --- 3. Archivieren mit Servervorschau -------------------------------
    await page.goto("/kategorien");
    const rowToArchive = page.locator(".category-row").filter({ hasText: categoryName });
    await rowToArchive.getByRole("button", { name: "Archivieren" }).click();

    const archiveDialog = page.getByRole("dialog", { name: `„${categoryName}“ archivieren` });
    await expect(archiveDialog).toBeVisible();
    // Die Vorschau zählt die Referenzen, bevor irgendetwas passiert.
    await expect(archiveDialog.getByText("Historisch (bleibt erhalten)")).toBeVisible();
    const confirm = archiveDialog.getByRole("button", { name: "Archivieren" });
    await expect(confirm).toBeEnabled();
    await confirm.click();
    await expect(archiveDialog).toBeHidden();

    // Aktiv-Liste kennt sie nicht mehr, das Archiv schon - mit Badge.
    await expect(page.locator(".category-row").filter({ hasText: categoryName })).toHaveCount(0);
    await page.getByRole("button", { name: "Archiviert" }).click();
    const archivedRow = page.locator(".category-row").filter({ hasText: categoryName });
    await expect(archivedRow).toBeVisible();
    await expect(archivedRow.getByText("Archiviert")).toBeVisible();
    await expect(archivedRow.getByRole("button", { name: "Wiederherstellen" })).toBeVisible();

    // --- 4. Historie bleibt lesbar ---------------------------------------
    await page.goto("/buchungen");
    const historicRow = page.locator("table.txn-table tbody tr").filter({ hasText: marker });
    await expect(historicRow.locator("td.txn-td--category")).toContainText(categoryName);
    await expect(historicRow.locator("td.txn-td--category")).toContainText("Archiviert");

    // Neu auswählbar ist sie nicht mehr: die Suche findet keinen Treffer,
    // stattdessen wird die Schnellanlage angeboten.
    await historicRow.locator("td.txn-td--category button").click();
    const historicPicker = page.getByRole("combobox", { name: "Kategorie dieser Buchung" });
    await historicPicker.fill(categoryName);
    await expect(
      page.getByRole("listbox").getByRole("option").filter({ hasText: "Neue Kategorie" }),
    ).toBeVisible();
  });

  test("Kategorien sind über die Sidebar und die Einstellungen erreichbar", async ({ page }) => {
    await page.goto("/einstellungen");
    await page.getByRole("link", { name: "Kategorien verwalten" }).click();
    await expect(page).toHaveURL(/\/kategorien$/);
    await expect(page.getByRole("heading", { name: "Kategorien" })).toBeVisible();

    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Hauptnavigation" });
    await nav.getByRole("link", { name: "Kategorien" }).click();
    await expect(page).toHaveURL(/\/kategorien$/);
  });
});
