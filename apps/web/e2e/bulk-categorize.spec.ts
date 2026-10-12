import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/household-fixture";

test.describe("Mehrfachzuordnung von Kategorien", () => {
  test("Vorschau und Anwenden ändern mehrere ausgewählte Buchungen gemeinsam", async ({
    page,
    seeded,
  }) => {
    const today = new Date().toISOString().slice(0, 10);
    const markers = [
      `e2e-bulk-a-${randomUUID()}`,
      `e2e-bulk-b-${randomUUID()}`,
      `e2e-bulk-c-${randomUUID()}`,
    ];

    for (const marker of markers) {
      await seeded.createTransaction({ booked_date: today, amount_minor: -500, note: marker });
    }

    await page.goto("/buchungen");

    const rows = markers.map((marker) =>
      page.locator("table.txn-table tbody tr").filter({ hasText: marker }),
    );
    for (const row of rows) {
      await expect(row).toBeVisible();
      await expect(row.getByText("– Kategorie wählen –")).toBeVisible();
      await row.getByRole("checkbox", { name: "Buchung auswählen" }).check();
    }

    const selectionBar = page.locator(".selection-bar");
    await expect(selectionBar).toBeVisible();
    await expect(selectionBar).toContainText(`${markers.length} ausgewählt`);

    // Der Geltungsbereich von "Alle auswählen" wird ausdruecklich benannt -
    // verdeckte Seiten oder Filtertreffer werden nie still einbezogen.
    await expect(
      selectionBar.getByRole("button", { name: /Alle \d+ sichtbaren auswählen/ }),
    ).toBeVisible();

    // Die Kategorieauswahl ist jetzt eine durchsuchbare Combobox mit
    // role="listbox"-Popup (kein natives <select> mehr) und traegt in der
    // Auswahl-Leiste ein eigenes aria-label.
    const picker = selectionBar.getByRole("combobox", {
      name: "Kategorie für die Mehrfachzuordnung",
    });
    await picker.click();
    await picker.fill(seeded.firstCategory.name);
    await page
      .getByRole("listbox")
      .getByRole("option")
      .filter({ hasText: seeded.firstCategory.name })
      .first()
      .click();

    // Erst Vorschau, dann Anwenden: die Vorschau nennt pro Buchung, was
    // passieren wuerde.
    await selectionBar.getByRole("button", { name: "Vorschau anzeigen" }).click();
    await expect(selectionBar).toContainText("Vorschau:");
    await expect(selectionBar).toContainText("wird geändert");

    await selectionBar.getByRole("button", { name: "Zuordnung anwenden" }).click();
    await expect(selectionBar).toContainText("Ergebnis:");

    for (const row of rows) {
      await expect(row.getByText(seeded.firstCategory.name)).toBeVisible();
    }
  });

  test("eine bereits zugeordnete Buchung meldet die Vorschau als unverändert", async ({
    page,
    seeded,
  }) => {
    const marker = `e2e-bulk-unchanged-${randomUUID()}`;
    await seeded.createTransaction({
      booked_date: new Date().toISOString().slice(0, 10),
      amount_minor: -750,
      note: marker,
      category_id: seeded.firstCategory.id,
    });

    await page.goto("/buchungen");

    const row = page.locator("table.txn-table tbody tr").filter({ hasText: marker });
    await expect(row).toBeVisible();
    await row.getByRole("checkbox", { name: "Buchung auswählen" }).check();

    const selectionBar = page.locator(".selection-bar");
    const picker = selectionBar.getByRole("combobox", {
      name: "Kategorie für die Mehrfachzuordnung",
    });
    await picker.click();
    await picker.fill(seeded.firstCategory.name);
    await page
      .getByRole("listbox")
      .getByRole("option")
      .filter({ hasText: seeded.firstCategory.name })
      .first()
      .click();

    await selectionBar.getByRole("button", { name: "Vorschau anzeigen" }).click();
    await expect(selectionBar).toContainText("bereits so zugeordnet");
  });
});
