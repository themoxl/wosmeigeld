import { test, expect } from "./fixtures/household-fixture";

// Abnahme aus docs/FOERDERKATALOG-IMPORT-PLAN.md, Abschnitt 7: Paket
// importieren -> Programm erscheint im Register -> der Foerdercheck eines
// Vorhabens findet es.
//
// Das Paket "kfw_sanierung" ist mitgeliefert (app/data/funding_packages/) und
// enthaelt zwei Programme fuer project_type = "renovation". Deshalb legt der
// Test ein Sanierungsvorhaben an, nicht das Standard-Hausbauvorhaben.
test.describe("Foerderkatalog", () => {
  test("Paket einspielen, im Register sehen, im Foerdercheck finden", async ({ page }) => {
    // --- 1. Register zeigt zunaechst nur mitgelieferte Programme ------------
    await page.goto("/foerderungen");
    await expect(page.getByRole("heading", { name: "Förderprogramme" })).toBeVisible();

    const curatedRow = page
      .locator("tr.txn-tr")
      .filter({ hasText: "KfW-Wohneigentumsprogramm (124)" });
    await expect(curatedRow).toBeVisible();
    await expect(curatedRow.getByText("Mitgeliefert")).toBeVisible();

    // --- 2. Paketvorschau: schreibt nichts ----------------------------------
    await page.getByRole("button", { name: "Importieren / Exportieren" }).click();
    const dialog = page.getByRole("dialog");
    const packageCard = dialog
      .locator(".fie-package-card")
      .filter({ hasText: "KfW Sanierung & Effizienz" });
    await packageCard.getByRole("button", { name: "Installieren" }).click();

    await expect(dialog.getByText("Vorschau — noch nichts gespeichert")).toBeVisible();
    const previewRow = dialog
      .locator("label.fie-preview-item")
      .filter({ hasText: "Wohngebäude Kredit (261)" });
    await expect(previewRow).toBeVisible();
    await expect(previewRow.getByText("Neu")).toBeVisible();

    // --- 3. Einspielen ------------------------------------------------------
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/v1/funding/import") &&
          !response.url().includes("/import/preview") &&
          response.ok(),
      ),
      dialog.getByRole("button", { name: /Programm\(e\) einspielen/ }).click(),
    ]);
    await page
      .getByRole("button", { name: "Schließen" })
      .click()
      .catch(() => {});
    await page.goto("/foerderungen");

    const importedRow = page.locator("tr.txn-tr").filter({ hasText: "Wohngebäude Kredit (261)" });
    await expect(importedRow).toBeVisible();
    await expect(importedRow.getByText("Eigener Eintrag")).toBeVisible();

    // --- 4. Der Foerdercheck eines Sanierungsvorhabens findet es ------------
    await page.goto("/vorhaben");
    await page.getByRole("button", { name: "+ Vorhaben" }).click();
    const wizard = page.getByRole("dialog", { name: "Neues Vorhaben" });
    await wizard.getByLabel("Name *").fill("E2E Sanierung Katalog");
    await wizard.getByLabel("Art").selectOption("renovation");
    await wizard.getByRole("button", { name: "Anlegen" }).click();
    await expect(page.getByRole("heading", { name: "E2E Sanierung Katalog" })).toBeVisible();

    await page.getByRole("tab", { name: "Förderung" }).click();
    await expect(page.getByRole("heading", { name: "Förderprofil" })).toBeVisible();
    await page.getByLabel("Immobilie wird nach Erwerb/Bau selbst genutzt").check();
    await Promise.all([
      page.waitForResponse(
        (response) => response.url().includes("/eligibility-profile") && response.ok(),
      ),
      page.getByRole("button", { name: "Profil speichern" }).click(),
    ]);

    await Promise.all([
      page.waitForResponse(
        (response) => response.url().includes("/funding-check") && response.ok(),
      ),
      page.getByRole("button", { name: "Förderung prüfen" }).click(),
    ]);

    const matchRow = page.locator("tr.txn-tr").filter({ hasText: "Wohngebäude Kredit (261)" });
    await expect(matchRow).toBeVisible();
    // Das Programm traegt zwei manual_check-Bedingungen: sie muessen als
    // anhakbare Zeilen erscheinen, sonst bliebe der Treffer dauerhaft auf
    // "moeglicherweise foerderfaehig" stehen.
    await expect(matchRow.getByText("Selbst zu prüfen", { exact: false })).toBeVisible();
  });
});
