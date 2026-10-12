/// <reference types="node" />
// Liest Dateien des Web-Pakets (index.html, Designkasten-Kopien) - daher Node-Typen.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  chartCategorical,
  chartDiverging,
  chartSequential,
  type ThemeTokens,
} from "@finanzplaner/design-tokens";
import { OWN_DESIGN_TOKENS } from "../styles/theme";
import { resolveCssVars } from "../components/charts/chartTheme";

// Schranke fuer die Designwahl (Designkasten aus sia_public):
//   1. Die Kopien in public/designkasten/ sind unveraendert (Pruefsumme Zeile 1).
//   2. index.html startet den Kasten mit gueltiger Auswahl, merkt die Wahl und
//      uebernimmt die alte Hell/Dunkel-Wahl genau einmal.
//   3. Die eigenen Designs in global.css tragen genau die Design-Tokens.
//   4. Die Bruecke setzt jede Finanzplaner-Farbe fuer Kasten-Designs, nur aus
//      Rollen, die es gibt; Diagrammfarben nur aus #rrggbb-Rollen.
//   5. Jede benutzte Farbvariable ist definiert.

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const read = (rel: string) => readFileSync(path.join(WEB, rel), "utf8").replace(/\r\n/g, "\n");

const KASTEN_FILES = ["designs.css", "designs.js", "designwahl.js"];
const globalCss = read("src/styles/global.css");
const kastenCss = read("public/designkasten/designs.css");
const indexHtml = read("index.html");

/** Rumpf des ersten Blocks, dessen Selektor genau `selector` ist. */
function block(css: string, selector: string): Map<string, string> {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const start = withoutComments.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`Block fehlt: ${selector}`);
  const open = withoutComments.indexOf("{", start);
  const close = withoutComments.indexOf("}", open);
  const props = new Map<string, string>();
  for (const declaration of withoutComments.slice(open + 1, close).split(";")) {
    const colon = declaration.indexOf(":");
    if (colon < 0) continue;
    const name = declaration.slice(0, colon).trim();
    if (name.startsWith("--")) props.set(name, declaration.slice(colon + 1).trim());
  }
  return props;
}

const norm = (value: string) => value.replace(/\s+/g, "").toLowerCase();
const kebab = (key: string) => key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

const rootBlock = block(globalCss, ":root");
const darkBlock = block(globalCss, ':root[data-design="klassisch-dunkel"]');
const bridge = block(globalCss, ':root[data-design-herkunft="kasten"]');
const ownWindowRoles = block(globalCss, ':root[data-design-herkunft="eigen"]');

const kastenBlocks = new Map<string, Map<string, string>>();
for (const [selector, id] of kastenCss.matchAll(/:root\[data-design="([a-z0-9_-]+)"\]/g)) {
  if (id) kastenBlocks.set(id, block(kastenCss, selector));
}

function loadScript(rel: string) {
  // Die Kasten-Dateien sind klassische Skripte fuer window; wie im Browser
  // global ausfuehren. Jeder Lauf baut einen frischen Kasten.
  new Function(read(rel))();
}

function runIndexStartScript() {
  const scripts = [...indexHtml.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  expect(scripts).toHaveLength(1);
  new Function(scripts[0]?.[1] ?? "")();
}

describe("Designkasten-Kopien", () => {
  it.each(KASTEN_FILES)("%s ist nicht von Hand geaendert", (file) => {
    const content = read(`public/designkasten/${file}`);
    const newline = content.indexOf("\n");
    const header = /^\/\* Designkasten-Stand ([0-9a-f]{12}) /.exec(content.slice(0, newline));
    expect(header, `${file}: keine Stand-Zeile`).not.toBeNull();
    const sum = createHash("sha256")
      .update(content.slice(newline + 1), "utf8")
      .digest("hex")
      .slice(0, 12);
    expect(sum).toBe(header![1]);
  });
});

describe("Start in index.html", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-design");
    document.documentElement.removeAttribute("data-theme");
    loadScript("public/designkasten/designs.js");
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("startet ohne gemerkte Wahl mit Klassisch (hell)", () => {
    runIndexStartScript();
    const root = document.documentElement;
    expect(root.dataset.design).toBe("klassisch");
    expect(root.dataset.designHerkunft).toBe("eigen");
    expect(root.dataset.theme).toBe("light");
    expect(window.Designkasten?.meldung).toBeNull();
  });

  it("bietet alle Kasten-Designs und beide eigenen an", () => {
    runIndexStartScript();
    const ids = window.Designkasten!.designs().map((d) => d.id);
    expect(new Set(ids)).toEqual(
      new Set(["klassisch", "klassisch-dunkel", ...kastenBlocks.keys()]),
    );
  });

  it("gilt das gemerkte Design und spiegelt hell/dunkel", () => {
    window.localStorage.setItem("finanzplaner:web:design", "daemmerung");
    runIndexStartScript();
    const root = document.documentElement;
    expect(root.dataset.design).toBe("daemmerung");
    expect(root.dataset.designHerkunft).toBe("kasten");
    expect(root.dataset.theme).toBe("dark");
  });

  it("uebernimmt die alte Wahl Dunkel einmal und raeumt den alten Schluessel weg", () => {
    window.localStorage.setItem("finanzplaner:web:theme", "dark");
    runIndexStartScript();
    expect(document.documentElement.dataset.design).toBe("klassisch-dunkel");
    expect(window.localStorage.getItem("finanzplaner:web:design")).toBe("klassisch-dunkel");
    expect(window.localStorage.getItem("finanzplaner:web:theme")).toBeNull();
  });

  it("laesst eine neue Wahl vor der alten gelten", () => {
    window.localStorage.setItem("finanzplaner:web:design", "oled");
    window.localStorage.setItem("finanzplaner:web:theme", "light");
    runIndexStartScript();
    expect(document.documentElement.dataset.design).toBe("oled");
    expect(window.localStorage.getItem("finanzplaner:web:theme")).toBeNull();
  });

  it("merkt eine Wahl im Fenster", () => {
    runIndexStartScript();
    window.Designkasten!.setze("warm");
    expect(window.localStorage.getItem("finanzplaner:web:design")).toBe("warm");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("nennt in der Probe der eigenen Designs ihre CSS-Werte", () => {
    runIndexStartScript();
    for (const [id, tokens] of Object.entries(OWN_DESIGN_TOKENS)) {
      const design = window.Designkasten!.design(id) as unknown as {
        probe: { kopf: string; flaeche: string; a: string; b: string };
        kopfFarbe: string;
      };
      expect(norm(design.probe.kopf)).toBe(norm(tokens.surface));
      expect(norm(design.probe.flaeche)).toBe(norm(tokens.background));
      expect(norm(design.probe.a)).toBe(norm(tokens.accent));
      expect(norm(design.probe.b)).toBe(norm(tokens.accentSecondary));
      expect(norm(design.kopfFarbe)).toBe(norm(tokens.surface));
    }
  });
});

describe("Eigene Designs = Design-Tokens", () => {
  const keys = Object.keys(OWN_DESIGN_TOKENS.klassisch) as (keyof ThemeTokens)[];

  it.each(keys)("Klassisch: %s", (key) => {
    expect(norm(rootBlock.get(`--color-${kebab(key)}`) ?? "")).toBe(
      norm(OWN_DESIGN_TOKENS.klassisch[key]),
    );
  });

  it.each(keys)("Klassisch Dunkel: %s", (key) => {
    const name = `--color-${kebab(key)}`;
    expect(norm(darkBlock.get(name) ?? rootBlock.get(name) ?? "")).toBe(
      norm(OWN_DESIGN_TOKENS["klassisch-dunkel"][key]),
    );
  });

  it("traegt die Diagrammpaletten der Tokens", () => {
    chartCategorical.forEach((hex, i) =>
      expect(norm(rootBlock.get(`--color-chart-${i + 1}`) ?? "")).toBe(norm(hex)),
    );
    chartSequential.forEach((hex, i) =>
      expect(norm(rootBlock.get(`--color-chart-seq-${i + 1}`) ?? "")).toBe(norm(hex)),
    );
    chartDiverging.forEach((hex, i) =>
      expect(norm(rootBlock.get(`--color-chart-div-${i + 1}`) ?? "")).toBe(norm(hex)),
    );
  });
});

describe("Bruecke fuer Kasten-Designs", () => {
  const roles = new Set(kastenBlocks.values().next().value!.keys());
  const hexInEveryDesign = (role: string) =>
    [...kastenBlocks.values()].every((b) => /^#[0-9a-f]{6}$/i.test(b.get(role) ?? ""));

  it("kennt mindestens die sechs Kasten-Designs", () => {
    expect([...kastenBlocks.keys()]).toEqual(
      expect.arrayContaining(["warm", "ember", "dark", "daemmerung", "oled", "light"]),
    );
  });

  it("setzt jede Finanzplaner-Farbe, nur aus vorhandenen Rollen", () => {
    for (const name of rootBlock.keys()) {
      if (!name.startsWith("--color-")) continue;
      const value = bridge.get(name);
      expect(value, `${name} fehlt in der Bruecke`).toBeDefined();
      const used = [...value!.matchAll(/var\((--[\w-]+)\)/g)].map((m) => m[1]);
      expect(used.length, `${name}: kein Wert aus einer Rolle`).toBeGreaterThan(0);
      for (const role of used)
        expect(roles, `${name}: Rolle ${role} gibt es nicht`).toContain(role);
      expect(value).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
    }
  });

  it("nimmt Diagrammfarben nur aus Rollen, die in jedem Design #rrggbb sind", () => {
    for (const [name, value] of bridge) {
      if (!name.startsWith("--color-chart-")) continue;
      const role = /^var\((--[\w-]+)\)$/.exec(value)?.[1];
      expect(role, `${name} ist kein einfacher Rollenverweis`).toBeDefined();
      expect(hexInEveryDesign(role!), `${name}: ${role} ist nicht ueberall #rrggbb`).toBe(true);
    }
  });

  it("setzt in eigenen Designs alle Fensterrollen des Designfensters", () => {
    loadScript("public/designkasten/designs.js");
    for (const role of window.Designkasten!.fensterrollen) {
      expect(ownWindowRoles.has(`--d-${role}`), `--d-${role}`).toBe(true);
    }
  });
});

describe("Farbvariablen", () => {
  function sources(dir: string): string[] {
    return readdirSync(dir).flatMap((entry: string) => {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) return entry === "test" ? [] : sources(full);
      return /\.(css|tsx?)$/.test(entry) ? [full] : [];
    });
  }

  it("jede benutzte --color-* ist im Grundblock definiert", () => {
    const missing = new Set<string>();
    for (const file of sources(path.join(WEB, "src"))) {
      const text = readFileSync(file, "utf8");
      for (const [, name] of text.matchAll(/var\((--color-[\w-]+)/g)) {
        if (name && !rootBlock.has(name)) missing.add(`${name} (${path.basename(file)})`);
      }
    }
    expect([...missing]).toEqual([]);
  });
});

describe("Diagrammfarben", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--color-chart-1");
  });

  it("loest Verweise auf, laesst Funktionen und Unbekanntes stehen", () => {
    document.documentElement.style.setProperty("--color-chart-1", "#123456");
    const formatter = () => "x";
    const resolved = resolveCssVars({
      color: ["var(--color-chart-1)"],
      series: [{ itemStyle: { color: "var(--color-chart-1)" }, label: { formatter } }],
      other: "var(--gibt-es-nicht)",
      plain: "Text",
    });
    expect(resolved.color).toEqual(["#123456"]);
    expect(resolved.series[0]?.itemStyle.color).toBe("#123456");
    expect(resolved.series[0]?.label.formatter).toBe(formatter);
    expect(resolved.other).toBe("var(--gibt-es-nicht)");
    expect(resolved.plain).toBe("Text");
  });
});
