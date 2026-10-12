import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/household-fixture";

test.describe("Split-Editor", () => {
  test("Speichern bleibt blockiert bis die Restsumme 0,00 € ergibt", async ({ page, seeded }) => {
    const marker = `e2e-split-${randomUUID()}`;

    await seeded.createTransaction({
      booked_date: new Date().toISOString().slice(0, 10),
      amount_minor: -2500,
      note: marker,
    });

    await page.goto("/buchungen");

    const row = page.locator("table.txn-table tbody tr").filter({ hasText: marker });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Splits bearbeiten" }).click();

    const dialog = page.getByRole("dialog", { name: "Splits bearbeiten" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("Gesamtbetrag")).toHaveValue("-25,00");

    // Neu angelegte, nicht gesplittete Buchung startet mit genau einer
    // leeren Split-Zeile -> Restsumme entspricht dem vollen Betrag, Speichern
    // ist deshalb ohne weiteres Zutun bereits blockiert.
    const saveButton = dialog.getByRole("button", { name: "Speichern" });
    await expect(dialog.locator(".split-remaining--error")).toContainText(/-25,00\s?€/);
    await expect(saveButton).toBeDisabled();

    // split-row-amount hat kein <label> (nur placeholder="0,00") - "0,00" ist
    // aber auch der Placeholder des "Betrag"-Felds im Formular darunter, also
    // auf den Dialog scopen, wo es bei genau einer Split-Zeile eindeutig ist.
    await dialog.getByPlaceholder("0,00").fill("-25,00");

    // Jede Split-Zeile verwendet denselben durchsuchbaren Kategoriepicker
    // (role="combobox" + role="listbox") wie der Rest der Anwendung; der
    // Treffer traegt immer den vollen Pfad "Gruppe · Kategorie".
    const rowPicker = dialog.getByRole("combobox", { name: "Kategorie dieses Splits" });
    await rowPicker.click();
    await rowPicker.fill(seeded.firstCategory.name);
    await page
      .getByRole("listbox")
      .getByRole("option")
      .filter({ hasText: seeded.firstCategory.name })
      .first()
      .click();
    await expect(rowPicker).toHaveValue(
      new RegExp(`·\\s*${escapeForRegExp(seeded.firstCategory.name)}$`),
    );

    await expect(dialog.locator(".split-remaining--ok")).toContainText(/0,00\s?€/);
    await expect(saveButton).toBeEnabled();

    await saveButton.click();
    await expect(dialog).toBeHidden();

    await expect(row.getByText(seeded.firstCategory.name)).toBeVisible();
  });
});

function escapeForRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
