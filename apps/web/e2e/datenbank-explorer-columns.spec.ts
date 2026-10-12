import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Der Datenbank-Explorer zeigt beliebige Tabellen - `transactions` hat zwei
// Dutzend Spalten. Genau daran ist die Ansicht zerbrochen: `.txn-table` steht
// auf `table-layout: fixed`, das verteilt die Breite gleichmaessig, und bei 24
// Spalten blieben je rund 55 px. Die Kopfzeilen ueberlappten sich gegenseitig
// (jede traegt einen deckenden Hintergrund und uebermalte die vorherige), von
// den Werten war nichts mehr zu sehen.
//
// Geprueft wird ohne Server und ohne Anmeldung: das Markup der Seite plus das
// echte Stylesheet - dieselbe Bauart wie reports-ledger-responsive.spec.ts.
// Ein Layoutfehler dieser Art faellt in jsdom grundsaetzlich nicht auf, weil es
// dort kein Layout gibt.

const COLUMNS = [
  "id",
  "household_id",
  "account_id",
  "booked_date",
  "value_date",
  "amount_minor",
  "currency",
  "payee_label",
  "payee_raw",
  "note",
  "status",
  "source",
  "is_transfer",
  "is_archived",
  "created_at",
  "created_by",
  "updated_at",
  "import_row_id",
  "external_ref",
  "fingerprint",
  "version",
  "payee_id",
  "purpose_raw",
  "bank_transaction_code",
];

const VALUE_BY_COLUMN: Record<string, string> = {
  id: "8f3a1c0e-2b44-4d19-9a77-1c2e5b6d7f80",
  booked_date: "2026-07-14",
  amount_minor: "-12500",
  currency: "EUR",
  payee_label: "Allianz Lebensservice",
  status: "booked",
  bank_transaction_code: "N105",
};

async function mountExplorerTable(page: Page) {
  const header = COLUMNS.map((name) => `<th class="txn-th">${name}</th>`).join("");
  const body = Array.from({ length: 12 }, (_unused, index) => {
    const cells = COLUMNS.map((name) => {
      const value = VALUE_BY_COLUMN[name] ?? `${name}-${index}`;
      return `<td class="txn-td explorer-cell"><span title="${value}">${value}</span></td>`;
    }).join("");
    return `<tr class="txn-tr"><td class="txn-td explorer-check"><input type="checkbox" /></td>${cells}</tr>`;
  }).join("");

  await page.setContent(`
    <style>
      :root {
        --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-5: 20px;
        --color-border: #e4e4e1; --color-surface: #fff; --color-surface-raised: #fbfaf7;
        --color-background: #f7f7f5; --color-text-primary: #161614; --color-text-secondary: #54544f;
        --color-focus-ring: #1f6d4c; --color-highlight: #c9a34e;
        --radius-sm: 2px; --radius-md: 4px; --font-mono: monospace; --font-ui: sans-serif;
      }
      body { margin: 0; padding: 16px; background: #f7f7f5; }
    </style>
    <div class="txn-table-wrapper explorer-rows">
      <table class="txn-table">
        <thead><tr><th class="txn-th explorer-check"><input type="checkbox" /></th>${header}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `);
  await page.addStyleTag({ path: path.resolve(process.cwd(), "src/styles/global.css") });
}

test.describe("Datenbank-Explorer: Spaltenbreiten", () => {
  test("jede Spaltenbeschriftung passt in ihre Zelle", async ({ page }) => {
    await mountExplorerTable(page);

    // scrollWidth > clientWidth heisst: der Text passt nicht und laeuft aus der
    // Zelle heraus - genau der Zustand, in dem sich die Koepfe uebermalt haben.
    const clipped = await page.evaluate(() =>
      Array.from(document.querySelectorAll("thead th.txn-th"))
        .map((cell) => ({
          text: cell.textContent?.trim() ?? "",
          overflow: cell.scrollWidth - cell.clientWidth,
        }))
        .filter((entry) => entry.overflow > 1),
    );

    expect(clipped).toEqual([]);
  });

  test("Zellwerte sind lesbar breit und die Tabelle scrollt waagerecht", async ({ page }) => {
    await mountExplorerTable(page);

    const firstValueCell = page.locator("tbody tr:first-child td.explorer-cell").first();
    const box = await firstValueCell.boundingBox();
    expect(box).not.toBeNull();
    // Bei gleichmaessig verteilter Breite blieben rund 55 px, davon 24 Polsterung.
    expect(box?.width ?? 0).toBeGreaterThan(80);

    // Zwei Dutzend Spalten passen nicht nebeneinander - die Tabelle darf ueber
    // den Rahmen hinauswachsen, gescrollt wird im Wrapper.
    const scroll = await page.locator(".explorer-rows").evaluate((element) => ({
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
    }));
    expect(scroll.scrollWidth).toBeGreaterThan(scroll.clientWidth);
  });

  test("lange Rohwerte werden gekuerzt statt die Zeile zu sprengen", async ({ page }) => {
    await mountExplorerTable(page);

    // Die UUID-Spalte ist die laengste; sie wird bei 260 px gedeckelt und der
    // volle Wert steht im title-Attribut.
    const idCell = page.locator("tbody tr:first-child td.explorer-cell").first();
    const width = (await idCell.boundingBox())?.width ?? 0;
    expect(width).toBeLessThanOrEqual(261);
    await expect(idCell.locator("span")).toHaveAttribute(
      "title",
      "8f3a1c0e-2b44-4d19-9a77-1c2e5b6d7f80",
    );
  });
});
