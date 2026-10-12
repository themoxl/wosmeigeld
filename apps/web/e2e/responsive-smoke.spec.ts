import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/household-fixture";

// Laeuft laut playwright.config.ts NUR in den beiden responsive-*-Projekten
// (1920x1080, 1280x800 "schmaler Laptop") - das primaere chromium-Projekt
// (1440x900) deckt die volle funktionale Suite ab, hier bewusst nur Layout/
// Screenshot/Tastatur-Stichprobe statt vollstaendiger CRUD-Wiederholung.
// Erfuellt Abschnitt 15 ("Screenshots bei 1440x900, 1920x1080 und schmalem
// Laptop") als tatsaechliches Artefakt, nicht nur bei Fehlern.
test.describe("Responsive Smoke-Test (Buchungsregister)", () => {
  test("Layout ohne horizontales Overflow, Tastaturnavigation funktioniert", async ({
    page,
    seeded,
  }, testInfo) => {
    const marker = `e2e-responsive-${randomUUID()}`;
    await seeded.createTransaction({
      booked_date: new Date().toISOString().slice(0, 10),
      amount_minor: -1234,
      note: marker,
    });

    await page.goto("/buchungen");
    const row = page.locator("table.txn-table tbody tr").filter({ hasText: marker });
    await expect(row).toBeVisible();

    const screenshot = await page.screenshot({ fullPage: false });
    await testInfo.attach(`buchungsregister-${testInfo.project.name}`, {
      body: screenshot,
      contentType: "image/png",
    });

    const viewport = page.viewportSize();
    expect(viewport).not.toBeNull();
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    // Kleine Toleranz fuer Scrollbar-Breite u.ae. - kein hartes Pixel-exact.
    expect(scrollWidth).toBeLessThanOrEqual((viewport?.width ?? 0) + 20);

    await page.locator("td.txn-td--date").first().click();
    await page.keyboard.press("ArrowRight");
    await expect(page.locator('td[tabindex="0"]')).toHaveClass(/txn-td--account/);
    await page.keyboard.press("ArrowLeft");
    await expect(page.locator('td[tabindex="0"]')).toHaveClass(/txn-td--date/);
  });
});
