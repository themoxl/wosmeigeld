import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/household-fixture";

test.describe("Buchung bearbeiten", () => {
  test("Status und Notiz lassen sich über das Bearbeiten-Modal ändern", async ({
    page,
    seeded,
  }) => {
    const originalMarker = `e2e-edit-orig-${randomUUID()}`;
    const updatedMarker = `e2e-edit-updated-${randomUUID()}`;

    await seeded.createTransaction({
      booked_date: new Date().toISOString().slice(0, 10),
      amount_minor: -1500,
      note: originalMarker,
    });

    await page.goto("/buchungen");

    const row = page.locator("table.txn-table tbody tr").filter({ hasText: originalMarker });
    await expect(row).toBeVisible();
    // Frisch angelegte Buchungen starten laut Migration mit status="booked".
    await expect(row.getByText("Gebucht")).toBeVisible();

    await row.getByRole("button", { name: "Buchung bearbeiten" }).click();

    // Modal-Felder haben teils identischen Label-Text wie das "Neue Buchung"-
    // Formular darunter ("Datum", "Konto", "Notiz" ist Teilstring von
    // "Notiz (optional)") - ohne Scoping auf den Dialog waere getByLabel(...)
    // mehrdeutig, solange die Seite dahinter im DOM bleibt.
    const dialog = page.getByRole("dialog", { name: "Buchung bearbeiten" });
    await expect(dialog).toBeVisible();

    await dialog.getByLabel("Notiz").fill(updatedMarker);

    const statusGroup = dialog.getByRole("group", { name: "Buchungsstatus" });
    await statusGroup.getByRole("button", { name: "Vorgemerkt" }).click();
    await expect(statusGroup.getByRole("button", { name: "Vorgemerkt" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await dialog.getByRole("button", { name: "Speichern" }).click();
    await expect(dialog).toBeHidden();

    const updatedRow = page.locator("table.txn-table tbody tr").filter({ hasText: updatedMarker });
    await expect(updatedRow).toBeVisible();
    await expect(updatedRow.getByText("Vorgemerkt")).toBeVisible();
  });
});
