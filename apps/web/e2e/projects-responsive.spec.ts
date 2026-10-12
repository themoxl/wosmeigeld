import { randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures/household-fixture";

// Ergaenzt responsive-smoke.spec.ts (Buchungsregister) um die in
// docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md Abschnitt 15.6 geforderte
// Responsive-Pruefung der Vorhaben-Kostentabelle: 1280/1440 Desktop und eine
// schmale (Handy-)Ansicht. Anders als responsive-smoke.spec.ts laeuft dieser
// Test bewusst NICHT ueber eigene Playwright-"projects" (die sind laut
// playwright.config.ts fest auf responsive-smoke.spec.ts gemappt), sondern
// setzt die Viewportgroesse je Test direkt per page.setViewportSize() - so
// bleibt er im normalen "chromium"-Projekt lauffaehig.
//
// Die Kostentabelle hat zehn feste/breite Spalten (CostPlanTable.tsx:
// Position, Berechnung 96px, Status 110px, sechs Betragsspalten je 130px,
// Aktionen 220px) und braucht deshalb schon bei schmaler Ansicht
// horizontales Scrollen innerhalb von .txn-table-wrapper (global.css) -
// Plan-Anforderung "schwere Tabelle darf horizontal scrollen, Aktionen
// bleiben erreichbar".
async function createHomeBuildProject(page: Page, name: string) {
  await page.goto("/vorhaben");
  await page.getByRole("button", { name: "+ Vorhaben" }).click();
  const wizard = page.getByRole("dialog", { name: "Neues Vorhaben" });
  await wizard.getByLabel("Name *").fill(name);
  await wizard.getByRole("button", { name: "Anlegen" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();

  await page.getByRole("tab", { name: "Kosten" }).click();
  // Vorlage home_build_de bringt zehn Abschnitte mit mehreren Positionen je
  // Abschnitt mit - "mehrere Kostenpositionen" ist damit ohne manuelles
  // Anlegen erfuellt (siehe projects.spec.ts).
  await expect(page.getByRole("heading", { name: "Grundstück/Erwerb" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Rohbau" })).toBeVisible();
}

test.describe("Vorhaben Kostentabelle responsiv", () => {
  for (const viewport of [
    { width: 1280, height: 800, label: "1280x800" },
    { width: 1440, height: 900, label: "1440x900" },
  ]) {
    test(`Desktop ${viewport.label}: Kostentabelle und Aktionen sichtbar`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await createHomeBuildProject(page, `E2E Responsiv ${viewport.label} ${randomUUID()}`);

      const wrapper = page.locator(".txn-table-wrapper").first();
      await expect(wrapper).toBeVisible();

      const firstRow = page.locator("tr.txn-tr").first();
      const editButton = firstRow.getByRole("button", { name: "Bearbeiten" });
      await expect(editButton).toBeVisible();
      await editButton.click();
      await expect(page.getByRole("dialog", { name: "Position bearbeiten" })).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog", { name: "Position bearbeiten" })).toBeHidden();

      const screenshot = await page.screenshot({ fullPage: false });
      await testInfo.attach(`vorhaben-kosten-${viewport.label}`, {
        body: screenshot,
        contentType: "image/png",
      });
    });
  }

  test("Schmale Ansicht (390x844): Kostentabelle scrollt horizontal, Aktionen bleiben erreichbar", async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await createHomeBuildProject(page, `E2E Responsiv schmal ${randomUUID()}`);

    const wrapper = page.locator(".txn-table-wrapper").first();
    await expect(wrapper).toBeVisible();

    // Die zehn Spalten der Kostentabelle passen bei 390px Breite nicht -
    // der Wrapper muss deshalb selbst scrollbar sein.
    const overflow = await wrapper.evaluate((el) => el.scrollWidth > el.clientWidth);
    expect(overflow).toBe(true);

    // Das horizontale Scrollen bleibt auf den Wrapper begrenzt - die Seite
    // selbst bekommt keine horizontale Scrollleiste (Muster aus
    // responsive-smoke.spec.ts).
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(scrollWidth).toBeLessThanOrEqual(390 + 20);

    // Aktionen der ersten Zeile bleiben trotz Scroll-Notwendigkeit
    // erreichbar: in den sichtbaren Bereich scrollen, klicken, Dialog
    // oeffnet sich tatsaechlich.
    const firstRow = page.locator("tr.txn-tr").first();
    const editButton = firstRow.getByRole("button", { name: "Bearbeiten" });
    await editButton.scrollIntoViewIfNeeded();
    await expect(editButton).toBeVisible();
    const box = await editButton.boundingBox();
    expect(box).not.toBeNull();
    expect(box?.width ?? 0).toBeGreaterThan(0);
    expect(box?.height ?? 0).toBeGreaterThan(0);

    await editButton.click();
    const editDialog = page.getByRole("dialog", { name: "Position bearbeiten" });
    await expect(editDialog).toBeVisible();
    await expect(editDialog.getByLabel("Schätzung (€)")).toBeVisible();

    const screenshot = await page.screenshot({ fullPage: false });
    await testInfo.attach("vorhaben-kosten-390x844", {
      body: screenshot,
      contentType: "image/png",
    });
  });
});
