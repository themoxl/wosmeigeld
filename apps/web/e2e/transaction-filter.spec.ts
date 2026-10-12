import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/household-fixture";

// Das Buchungsregister filtert auf zwei Wegen, und die Trennung ist die
// eigentliche Zusage der Seite:
//
// - Konto, Zeitraum und Volltextsuche gehen an den Server und wirken auf den
//   GESAMTEN Bestand.
// - Spaltenfilter, Sortierung und Gruppierung wirken auf die geladenen Zeilen.
//
// Hier laufen beide serverseitigen Wege; die clientseitigen deckt
// src/test/TransactionsPage.test.tsx ab.
test.describe("Konto-Filter", () => {
  test("Konto-Filter blendet Buchungen anderer Konten aus", async ({ page, seeded }) => {
    const girokontoMarker = `e2e-filter-giro-${randomUUID()}`;
    const sparkontoMarker = `e2e-filter-spar-${randomUUID()}`;
    const today = new Date().toISOString().slice(0, 10);

    const sparkonto = await seeded.createAccount({ name: "Sparkonto", account_type: "savings" });

    await seeded.createTransaction({
      booked_date: today,
      amount_minor: -1000,
      note: girokontoMarker,
    });
    // createTransaction seedet standardmaessig auf dem Fixture-Konto
    // (Girokonto) - account_id in input ueberschreibt das per Object-Spread.
    await seeded.createTransaction({
      account_id: sparkonto.id,
      booked_date: today,
      amount_minor: -2000,
      note: sparkontoMarker,
    });

    await page.goto("/buchungen");

    const girokontoRow = page
      .locator("table.txn-table tbody tr")
      .filter({ hasText: girokontoMarker });
    const sparkontoRow = page
      .locator("table.txn-table tbody tr")
      .filter({ hasText: sparkontoMarker });
    await expect(girokontoRow).toBeVisible();
    await expect(sparkontoRow).toBeVisible();

    await page.getByLabel("Konto-Filter").selectOption({ label: "Girokonto" });
    await expect(girokontoRow).toBeVisible();
    await expect(sparkontoRow).toHaveCount(0);

    await page.getByLabel("Konto-Filter").selectOption({ label: "Alle Konten" });
    await expect(girokontoRow).toBeVisible();
    await expect(sparkontoRow).toBeVisible();
  });

  test("Volltextsuche findet ueber Empfaenger und Notiz", async ({ page, seeded }) => {
    const marker = randomUUID().slice(0, 8);
    const today = new Date().toISOString().slice(0, 10);

    await seeded.createTransaction({
      booked_date: today,
      amount_minor: -1100,
      payee_label: `Nordlicht ${marker}`,
    });
    await seeded.createTransaction({
      booked_date: today,
      amount_minor: -2200,
      note: `Suedwind ${marker}`,
    });

    await page.goto("/buchungen");

    const nordlichtRow = page
      .locator("table.txn-table tbody tr")
      .filter({ hasText: `Nordlicht ${marker}` });
    const suedwindRow = page
      .locator("table.txn-table tbody tr")
      .filter({ hasText: `Suedwind ${marker}` });
    await expect(nordlichtRow).toBeVisible();
    await expect(suedwindRow).toBeVisible();

    await page.getByLabel("Buchungen durchsuchen").fill(`Nordlicht ${marker}`);
    await page.getByRole("button", { name: "Suchen" }).click();

    await expect(nordlichtRow).toBeVisible();
    await expect(suedwindRow).toHaveCount(0);

    // Die Notiz ist ebenfalls abgedeckt - die Suche laeuft ueber Empfaenger,
    // Verwendungszweck und Notiz, nicht nur ueber die sichtbare Spalte.
    await page.getByLabel("Buchungen durchsuchen").fill(`Suedwind ${marker}`);
    await page.getByRole("button", { name: "Suchen" }).click();

    await expect(suedwindRow).toBeVisible();
    await expect(nordlichtRow).toHaveCount(0);
  });

  test("Spaltenmenue filtert und gruppiert die geladenen Zeilen", async ({ page, seeded }) => {
    const marker = randomUUID().slice(0, 8);
    const today = new Date().toISOString().slice(0, 10);

    const sparkonto = await seeded.createAccount({
      name: "Sparkonto",
      account_type: "savings",
    });
    await seeded.createTransaction({
      booked_date: today,
      amount_minor: -3300,
      payee_label: `Ausgabe ${marker}`,
    });
    await seeded.createTransaction({
      account_id: sparkonto.id,
      booked_date: today,
      amount_minor: 4400,
      payee_label: `Einnahme ${marker}`,
    });

    await page.goto("/buchungen");
    const ausgabe = page
      .locator("table.txn-table tbody tr")
      .filter({ hasText: `Ausgabe ${marker}` });
    const einnahme = page
      .locator("table.txn-table tbody tr")
      .filter({ hasText: `Einnahme ${marker}` });
    await expect(ausgabe).toBeVisible();

    // Spaltenfilter: nur Einnahmen.
    await page.getByRole("button", { name: "Betrag: filtern, sortieren, gruppieren" }).click();
    const menu = page.getByRole("dialog", { name: "Spaltenmenü Betrag" });
    await menu.getByLabel("Filteroperator für Betrag").selectOption("gt");
    await menu.getByLabel("Wert für Betrag").fill("0");
    await menu.getByRole("button", { name: "Filter übernehmen" }).click();

    await expect(einnahme).toBeVisible();
    await expect(ausgabe).toHaveCount(0);

    // Chip zurueckgenommen -> beide wieder da.
    await page.getByRole("button", { name: /^Betrag >/ }).click();
    await expect(ausgabe).toBeVisible();

    // Gruppierung nach Konto: die Gruppenzeile traegt Anzahl und Summe.
    await page.getByLabel("Gruppierungsebene hinzufügen").selectOption("account");
    await expect(page.getByRole("button", { name: /Girokonto (zu|auf)klappen/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Sparkonto (zu|auf)klappen/ })).toBeVisible();
  });
});
