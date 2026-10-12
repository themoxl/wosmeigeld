import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/household-fixture";

test.describe("Buchung anlegen", () => {
  // "seeded" wird nicht direkt gelesen, laeuft aber automatisch: die
  // ueberschriebene storageState-Fixture (household-fixture.ts) haengt von
  // ihr ab, und "page" haengt wiederum von storageState ab.
  test("neue Buchung erscheint nach dem Anlegen in der Liste", async ({ page }) => {
    const marker = `e2e-create-${randomUUID()}`;
    const todayIso = new Date().toISOString().slice(0, 10);

    await page.goto("/buchungen");

    // Ausdruecklich auf das Anlegeformular gescopet: "Konto" und "Betrag"
    // kommen auf der Seite mehrfach vor (Konto-Filter der Werkzeugleiste,
    // Spaltenkopf "Betrag" mit Sortier- und Filterschaltflaechen). Ein
    // ungescopeter getByLabel trifft mehrere Elemente und laeuft in einen
    // Strict-Mode-Fehler.
    const form = page.locator("form.inline-form");

    await form.getByLabel("Konto").selectOption({ label: "Girokonto" });
    await form.getByLabel("Betrag").fill("25,00");
    await form.getByLabel("Notiz (optional)").fill(marker);
    await page.getByRole("button", { name: "Buchung anlegen" }).click();

    // Formular leert sich nach erfolgreichem Anlegen (Notiz/Betrag zurueckgesetzt).
    await expect(form.getByLabel("Notiz (optional)")).toHaveValue("");
    await expect(form.getByLabel("Betrag")).toHaveValue("");

    const row = page.locator("table.txn-table tbody tr").filter({ hasText: marker });
    await expect(row).toBeVisible();
    // Default-Richtung im Formular ist "Ausgabe" -> negativer Betrag.
    await expect(row).toContainText(/-25,00\s?€/);
    await expect(row).toContainText(todayIso);
  });
});
