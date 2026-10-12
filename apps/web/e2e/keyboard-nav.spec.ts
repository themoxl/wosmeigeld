import { test, expect } from "./fixtures/household-fixture";

const ROW_COUNT = 50;
const ARROW_DOWN_PRESSES = 45;
const DAY_MS = 24 * 60 * 60 * 1000;

function isoDateOffsetFromToday(daysAgo: number): string {
  return new Date(Date.now() - daysAgo * DAY_MS).toISOString().slice(0, 10);
}

async function seedManyTransactions(
  seeded: { createTransaction: (input: Record<string, unknown>) => Promise<{ id: string }> },
  count: number,
) {
  const indexes = Array.from({ length: count }, (_, i) => i);
  const chunkSize = 10;
  for (let start = 0; start < indexes.length; start += chunkSize) {
    const chunk = indexes.slice(start, start + chunkSize);
    await Promise.all(
      chunk.map((i) =>
        seeded.createTransaction({
          booked_date: isoDateOffsetFromToday(i),
          amount_minor: -100 * (i + 1),
        }),
      ),
    );
  }
}

test.describe("Tastatursteuerung (virtualisierte Liste)", () => {
  test("ArrowDown erreicht eine anfangs virtualisiert-unsichtbare Zeile und fokussiert sie", async ({
    page,
    seeded,
  }) => {
    // Jede Buchung hat ein eigenes booked_date (heute - i Tage), damit die
    // clientseitige Sortierung (TransactionsPage.tsx: sortedTransactions,
    // absteigend nach booked_date) eine deterministische Zeilenreihenfolge
    // ergibt, unabhaengig von der Erzeugungsreihenfolge beim Seeden.
    await seedManyTransactions(seeded, ROW_COUNT);

    await page.goto("/buchungen");

    const dateCells = page.locator("td.txn-td--date");
    await expect(dateCells.first()).toBeVisible();

    // Setzt echten DOM-Fokus UND den focusedCell-React-State (siehe den
    // "Bewusst onClick statt onFocus"-Kommentar in TransactionsPage.tsx) -
    // ab hier reichen reine Tastatur-Events.
    await dateCells.first().click();
    await expect(dateCells.first()).toBeFocused();

    for (let i = 0; i < ARROW_DOWN_PRESSES; i += 1) {
      await page.keyboard.press("ArrowDown");
    }

    // Beweist den scrollToIndex()+Retry-Fokus-Fix aus TransactionsPage.tsx:
    // bei 50 Zeilen und einem ueberschaubaren Viewport ist Zeile 45 anfangs
    // nicht gemountet (virtualisiert) - wenn der Fix nicht greift, bleibt
    // entweder der Fokus haengen oder es existiert kein td[tabindex="0"].
    const focusedCells = page.locator('td[tabindex="0"]');
    await expect(focusedCells).toHaveCount(1);
    await expect(focusedCells).toBeFocused();
    await expect(focusedCells).toHaveClass(/txn-td--date/);
    await expect(focusedCells).toHaveText(isoDateOffsetFromToday(ARROW_DOWN_PRESSES));
    await expect(focusedCells).toBeInViewport();
  });

  test("ArrowRight/ArrowLeft folgen der Spaltenreihenfolge (NAV_COLUMN_IDS)", async ({
    page,
    seeded,
  }) => {
    await seeded.createTransaction({
      booked_date: isoDateOffsetFromToday(0),
      amount_minor: -500,
    });

    await page.goto("/buchungen");

    const focusedCell = page.locator('td[tabindex="0"]');
    await page.locator("td.txn-td--date").first().click();
    await expect(focusedCell).toHaveClass(/txn-td--date/);

    // NAV_COLUMN_IDS: select, date, account, payeeNote, category, amount,
    // status, transfer, actions - von "date" bis "actions" sind 7 Schritte.
    const expectedOrder = [
      "account",
      "payeeNote",
      "category",
      "amount",
      "status",
      "transfer",
      "actions",
    ];
    for (const expectedColumn of expectedOrder) {
      await page.keyboard.press("ArrowRight");
      await expect(focusedCell).toHaveClass(new RegExp(`txn-td--${expectedColumn}\\b`));
    }

    await page.keyboard.press("ArrowLeft");
    await expect(focusedCell).toHaveClass(/txn-td--transfer/);
  });

  test("Enter in der Kategoriespalte öffnet den Suchpicker, der rein per Tastatur bedienbar ist", async ({
    page,
    seeded,
  }) => {
    await seeded.createTransaction({
      booked_date: isoDateOffsetFromToday(0),
      amount_minor: -1234,
    });

    await page.goto("/buchungen");

    // Von der Datumszelle vier Spalten nach rechts: date -> account ->
    // payeeNote -> category (NAV_COLUMN_IDS in TransactionsPage.tsx).
    await page.locator("td.txn-td--date").first().click();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await expect(page.locator('td[tabindex="0"]')).toHaveClass(/txn-td--category/);

    // Enter startet die Inline-Bearbeitung: statt des frueheren nativen
    // <select> erscheint die Combobox mit aria-expanded/aria-controls.
    await page.keyboard.press("Enter");
    const picker = page.getByRole("combobox", { name: "Kategorie dieser Buchung" });
    await expect(picker).toBeFocused();
    await expect(picker).toHaveAttribute("aria-expanded", "false");

    await page.keyboard.press("ArrowDown");
    await expect(picker).toHaveAttribute("aria-expanded", "true");
    const listbox = page.getByRole("listbox");
    await expect(listbox).toBeVisible();

    // Index 0 ist "Ohne Kategorie" - ein weiteres ArrowDown steht auf der
    // ersten echten Kategorie, Enter uebernimmt sie.
    await page.keyboard.press("ArrowDown");
    await expect(picker).toHaveAttribute("aria-activedescendant", /-option-1$/);
    await page.keyboard.press("Enter");

    await expect(page.getByRole("listbox")).toBeHidden();
    await expect(
      page.locator("table.txn-table tbody tr").first().locator("td.txn-td--category"),
    ).not.toContainText("– Kategorie wählen –");
  });
});
