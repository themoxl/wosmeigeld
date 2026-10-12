import path from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";

function ledger(id: string, rows = 48): string {
  const body = Array.from(
    { length: rows },
    (_, index) => `
    <tr class="report-monthly-table__row">
      <th class="report-monthly-table__label" scope="row">Zeile ${index + 1}</th>
      <td class="report-monthly-table__value">-${index + 1},00 €</td>
      <td class="report-monthly-table__value">-${index + 2},00 €</td>
      <td class="report-monthly-table__value">-${index + 3},00 €</td>
      <td class="report-monthly-table__summary report-monthly-table__average">-${index + 2},00 €</td>
      <td class="report-monthly-table__summary report-monthly-table__sum">-${index + 6},00 €</td>
      <td class="report-monthly-table__summary report-monthly-table__share">10 %</td>
      <td class="report-monthly-table__summary report-monthly-table__count">3</td>
    </tr>`,
  ).join("");
  return `
    <div class="report-table-shell">
      <p class="report-table-scroll-hint"><span>↔</span> Tabelle seitlich scrollen für weitere Spalten</p>
      <div class="report-table-scroll" tabindex="0">
        <table id="${id}" class="report-monthly-table" style="--report-count-width:52px;--report-share-width:72px">
          <thead><tr>
            <th class="report-monthly-table__label">Gruppe / Kategorie</th>
            <th class="report-monthly-table__month">Jan 26</th>
            <th class="report-monthly-table__month">Feb 26</th>
            <th class="report-monthly-table__month">März 26</th>
            <th class="report-monthly-table__summary report-monthly-table__average">Ø / Monat</th>
            <th class="report-monthly-table__summary report-monthly-table__sum">Summe</th>
            <th class="report-monthly-table__summary report-monthly-table__share">Anteil</th>
            <th class="report-monthly-table__summary report-monthly-table__count">#</th>
          </tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </div>`;
}

async function mountLedgers(page: Page) {
  await page.setContent(`
    <style>
      :root {
        --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
        --color-border: #d0d0cb; --color-surface: #fff; --color-surface-raised: #f7f7f5;
        --color-text-primary: #161614; --color-text-secondary: #54544f;
        --radius-sm: 2px; --font-mono: monospace; --font-heading: Georgia, serif;
      }
      body { margin: 0; padding: 16px; background: #f7f7f5; }
      main { display: grid; gap: 24px; }
    </style>
    <main>
      <section id="cashflow-section">${ledger("cashflow")}</section>
      <section id="category-section">${ledger("category")}</section>
      <section id="payee-section" class="report-payee-disclosure">${ledger("payee")}</section>
    </main>
  `);
  await page.addStyleTag({ path: path.resolve(process.cwd(), "src/styles/global.css") });
}

async function expectStickyHeader(table: Locator) {
  const wrapper = table.locator("xpath=ancestor::div[contains(@class,'report-table-scroll')][1]");
  const header = table.locator("thead th.report-monthly-table__month").first();
  await expect(wrapper).toBeVisible();
  const before = await header.boundingBox();
  expect(before).not.toBeNull();
  await wrapper.evaluate((element) => {
    element.scrollTop = 240;
  });
  await expect.poll(() => wrapper.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  const after = await header.boundingBox();
  expect(after).not.toBeNull();
  expect(Math.abs((after?.y ?? 0) - (before?.y ?? 0))).toBeLessThanOrEqual(1);
}

async function expectFullFirstMonth(table: Locator) {
  const wrapper = table.locator("xpath=ancestor::div[contains(@class,'report-table-scroll')][1]");
  await table.locator("tbody tr").first().scrollIntoViewIfNeeded();
  await wrapper.evaluate((element) => {
    element.scrollLeft = 0;
    element.scrollTop = 0;
  });
  const wrapperBox = await wrapper.boundingBox();
  const labelBox = await table
    .locator("tbody tr")
    .first()
    .locator(".report-monthly-table__label")
    .boundingBox();
  const monthCell = table
    .locator("tbody tr")
    .first()
    .locator(".report-monthly-table__value")
    .first();
  const monthBox = await monthCell.boundingBox();
  expect(wrapperBox && labelBox && monthBox).toBeTruthy();
  expect(monthBox!.x).toBeGreaterThanOrEqual(labelBox!.x + labelBox!.width - 1);
  expect(monthBox!.x + monthBox!.width).toBeLessThanOrEqual(wrapperBox!.x + wrapperBox!.width + 1);
  const hit = await pageElementAtCenter(monthCell);
  expect(hit).toContain("report-monthly-table__value");
}

async function pageElementAtCenter(locator: Locator): Promise<string> {
  return locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return (
      document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)?.className ?? ""
    );
  });
}

for (const viewport of [
  {
    width: 1440,
    height: 900,
    hintDisplay: "none",
    summaryPosition: "sticky",
    labelMin: 230,
    labelMax: 500,
  },
  {
    width: 768,
    height: 900,
    hintDisplay: "flex",
    summaryPosition: "sticky",
    labelMin: 230,
    labelMax: 300,
  },
  {
    width: 375,
    height: 844,
    hintDisplay: "flex",
    summaryPosition: "static",
    labelMin: 149,
    labelMax: 151,
  },
]) {
  test(`Monatsledger bleiben bei ${viewport.width}px lesbar und der Kopf bleibt stehen`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await mountLedgers(page);

    for (const id of ["cashflow", "category", "payee"]) {
      const table = page.locator(`#${id}`);
      await expectStickyHeader(table);
      await expectFullFirstMonth(table);
    }

    const hint = page.locator("#category-section .report-table-scroll-hint");
    await expect(hint).toHaveCSS("display", viewport.hintDisplay);
    const hintBeforeViewport = await page
      .locator("#category-section .report-table-shell")
      .evaluate((shell) => {
        const hintNode = shell.querySelector(".report-table-scroll-hint");
        const scrollNode = shell.querySelector(".report-table-scroll");
        return Boolean(
          hintNode &&
          scrollNode &&
          hintNode.compareDocumentPosition(scrollNode) & Node.DOCUMENT_POSITION_FOLLOWING,
        );
      });
    expect(hintBeforeViewport).toBe(true);

    const bodySummary = page.locator("#category tbody .report-monthly-table__summary").first();
    await expect(bodySummary).toHaveCSS("position", viewport.summaryPosition);
    const labelBox = await page
      .locator("#category .report-monthly-table__label")
      .first()
      .boundingBox();
    expect(labelBox?.width).toBeGreaterThanOrEqual(viewport.labelMin);
    expect(labelBox?.width).toBeLessThanOrEqual(viewport.labelMax);
  });
}
