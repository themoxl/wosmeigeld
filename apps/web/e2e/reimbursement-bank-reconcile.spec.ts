import { test, expect } from "./fixtures/household-fixture";

const API_BASE = process.env.VITE_API_BASE_URL ?? "http://localhost:8000";

/**
 * Der eine durchgaengige Weg des Erstattungsmoduls.
 *
 * Bewusst genau ein E2E-Test und keine Sammlung: die Einzelteile sind in
 * services/api/tests/test_reimbursement_allocations.py und in
 * src/test/BankReconciliationPanel.test.tsx abgedeckt. Was dort prinzipiell
 * fehlt, ist die Frage, ob die Teile im Browser tatsaechlich zusammenpassen -
 * genau die Luecke, durch die der Bankabgleich monatelang unbemerkt
 * unerreichbar war: die API konnte ihn, die Oberflaeche rief ihn nie auf.
 *
 * Geprueft wird deshalb der Uebergang, nicht die Rechnung:
 * markierte Ausgabe -> Vorgang -> eingegangene Zahlung -> Zuordnung ->
 * der Vorgang zeigt den Eingang.
 */
test.describe("Erstattung: von der Markierung bis zum Bankabgleich", () => {
  test("eine markierte Zahlung wird zum Vorgang und der Geldeingang wird zugeordnet", async ({
    page,
    seeded,
  }) => {
    const headers = {
      Authorization: `Bearer ${seeded.token}`,
      "X-Household-Id": seeded.householdId,
    };

    // --- Vorbereitung ueber die API: Person, bezahlte Arztrechnung, Geldeingang.
    const personResponse = await seeded.request.post(`${API_BASE}/v1/reimbursements/people`, {
      headers,
      data: { display_name: "Testkind" },
    });
    expect(personResponse.ok()).toBeTruthy();
    const person = (await personResponse.json()) as { id: string };

    const arztrechnung = await seeded.createTransaction({
      booked_date: "2026-07-01",
      amount_minor: -20000,
      payee_label: "Praxis Beispiel",
    });
    const geldeingang = await seeded.createTransaction({
      booked_date: "2026-07-20",
      amount_minor: 16000,
      payee_label: "Krankenversicherung AG",
    });
    expect(geldeingang.id).toBeTruthy();

    // Markieren und in einen Vorgang wandeln - das ist der Teil, den es schon
    // gab; er steht hier als Ausgangslage, nicht als Pruefgegenstand.
    const markResponse = await seeded.request.put(
      `${API_BASE}/v1/reimbursements/marks/${arztrechnung.id}`,
      { headers, data: { expectation_mode: "quota", expectation_rate: "0.8" } },
    );
    expect(markResponse.ok()).toBeTruthy();

    const caseResponse = await seeded.request.post(
      `${API_BASE}/v1/reimbursements/marks/${arztrechnung.id}/case`,
      { headers, data: { insured_person_id: person.id } },
    );
    expect(caseResponse.ok()).toBeTruthy();

    // --- Ab hier ausschliesslich ueber die Oberflaeche.
    await page.goto("/erstattungen");

    await page.getByRole("tab", { name: /Bank/ }).click();

    const vorgangszeile = page
      .locator("table.txn-table tbody tr")
      .filter({ hasText: "Praxis Beispiel" })
      .first();
    await expect(vorgangszeile).toBeVisible();
    // 80 % von 200,00 EUR - die Erwartung stammt aus der Marke.
    await expect(vorgangszeile).toContainText(/160,00\s?€/);

    await vorgangszeile.getByRole("button", { name: "Zahlungen zuordnen" }).click();

    // Der Geldeingang muss als Kandidat erscheinen, mit Begruendung.
    const kandidatenzeile = page
      .locator("table.txn-table tbody tr")
      .filter({ hasText: "Krankenversicherung AG" })
      .first();
    await expect(kandidatenzeile).toBeVisible();

    const betragsfeld = kandidatenzeile.getByLabel(/Betrag für/);
    await expect(betragsfeld).toHaveValue("160,00");

    await kandidatenzeile.getByRole("button", { name: "Zuordnen" }).click();

    // --- Die eigentliche Zusage: der Vorgang zeigt den Eingang, ohne Neuladen.
    await expect(page.getByText("Bereits zugeordnet")).toBeVisible();
    const zuordnungszeile = page
      .locator("table.txn-table tbody tr")
      .filter({ hasText: "Krankenversicherung AG" })
      .first();
    await expect(zuordnungszeile).toContainText(/160,00\s?€/);

    // Und der Vorgang selbst ist nicht mehr offen: die Liste der Vorgaenge mit
    // Rest fuehrt ihn nicht mehr. Genau das war der Bruch - die Zuordnung
    // wirkte in der Datenbank, aber nicht in der Ansicht.
    await expect(
      page
        .locator("table.txn-table tbody tr")
        .filter({ hasText: "Praxis Beispiel" })
        .filter({ has: page.getByRole("button", { name: "Zahlungen zuordnen" }) }),
    ).toHaveCount(0);
  });
});
