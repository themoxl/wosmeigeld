/// <reference types="jest" />
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { Text } from "react-native";
import { Badge, CardLabel } from "../Badge";
import { Card } from "../Card";
import { DataRow } from "../DataRow";
import { DonutChart } from "../DonutChart";
import { Money } from "../Money";
import { Numpad } from "../Numpad";
import { PacingBar } from "../PacingBar";
import { SplitMeter } from "../SplitMeter";
import { TrendChart } from "../TrendChart";
import {
  PrivacyProvider,
  ThemeProvider,
  darkTheme,
  lightTheme,
  toneForLabel,
} from "../../../theme";
import { initialsForLabel } from "../../../theme/palette";

// Die Bausteine aus src/components/ui sind die Grundlage jedes Bildschirms.
// Geprueft wird hier nicht das Aussehen, sondern das, worauf sich die
// Bildschirme verlassen: dass sie ueberhaupt rendern (react-native-svg,
// expo-linear-gradient und expo-haptics sind native Module), dass der
// Privat-Modus greift und dass die Ableitungen aus einem Namen stabil sind.

// ThemeProvider laedt Schriften und liest die gespeicherte Themewahl - beides
// loest nach dem ersten Rendern noch einen Zustandswechsel aus. Ohne das
// asynchrone act() liest der Test den Baum davor und sieht nichts.
async function render(element: React.ReactElement): Promise<ReactTestRenderer> {
  let renderer: ReactTestRenderer | undefined;
  await act(async () => {
    renderer = create(
      <ThemeProvider>
        <PrivacyProvider>{element}</PrivacyProvider>
      </ThemeProvider>,
    );
  });
  if (!renderer) throw new Error("Renderer wurde nicht erzeugt");
  return renderer;
}

// Ueber den gerenderten Baum statt ueber findAllByType(Text): der
// Schriftaufsatz aus src/theme/globalFont.tsx tauscht die Text-Komponente aus,
// wodurch ein Typvergleich je nach Ladezeitpunkt ins Leere laeuft.
type JsonNode =
  { type?: string; children?: (JsonNode | string | number | null)[] } | string | number | null;

function textsOf(renderer: ReactTestRenderer): string[] {
  const collected: string[] = [];

  function walk(node: JsonNode): void {
    if (node == null || typeof node === "string" || typeof node === "number") return;
    const children = node.children ?? [];
    const ownText = children
      .filter((child) => typeof child === "string" || typeof child === "number")
      .join("");
    if (ownText) collected.push(ownText);
    for (const child of children) {
      if (child && typeof child === "object") walk(child);
    }
  }

  const tree = renderer.toJSON();
  for (const root of Array.isArray(tree) ? tree : [tree]) walk(root as JsonNode);
  return collected;
}

describe("Money", () => {
  it("schreibt Betraege mit Waehrungszeichen und Monospace", async () => {
    const tree = await render(<Money minorUnits={-2840} />);
    expect(textsOf(tree)).toContain("-28,40 €");
  });

  it("setzt bei Einnahmen auf Wunsch ein Pluszeichen", async () => {
    const tree = await render(<Money minorUnits={320000} showPlusSign />);
    expect(textsOf(tree)).toContain("+3.200,00 €");
  });

  it("verdeckt den Betrag im Privat-Modus", async () => {
    const tree = await render(<Money minorUnits={245080} isPrivacyMode />);
    expect(textsOf(tree)).toContain("•••• €");
    expect(textsOf(tree)).not.toContain("2.450,80 €");
  });

  it("kuerzt grosse Betraege in der Kompaktform", async () => {
    const tree = await render(<Money minorUnits={2845000} compact />);
    expect(textsOf(tree)).toContain("28,5k €");
  });
});

describe("Badge und CardLabel", () => {
  it("stellt den Zustand immer auch als Text dar, nicht nur als Farbe", async () => {
    const tree = await render(<Badge label="Im Plan" tone="positive" />);
    expect(textsOf(tree)).toContain("Im Plan");
  });

  it("schreibt Kartenlabel in Grossbuchstaben", async () => {
    const tree = await render(<CardLabel>Konten und Salden</CardLabel>);
    const label = tree.root.findAllByType(Text)[0];
    expect(label?.props.style).toEqual(
      expect.arrayContaining([expect.objectContaining({ textTransform: "uppercase" })]),
    );
  });
});

describe("PacingBar", () => {
  it("meldet Verbrauch und Sollwert an den Screenreader", async () => {
    const tree = await render(
      <PacingBar progress={0.64} pace={0.48} accessibilityLabel="Budget Lebensmittel" />,
    );
    const bar = tree.root.findByProps({ accessibilityRole: "progressbar" });
    expect(bar.props.accessibilityValue.now).toBe(64);
    expect(bar.props.accessibilityValue.text).toContain("planmaessig waeren 48");
  });

  it("zeichnet ohne Sollwert keine Nadel", async () => {
    const tree = await render(<PacingBar progress={0.5} testID="pb" />);
    expect(tree.root.findAllByProps({ testID: "pb-needle" })).toHaveLength(0);
  });
});

describe("SplitMeter", () => {
  it("nennt jeden Abschnitt mit Anteil in der Legende", async () => {
    const tree = await render(
      <SplitMeter
        segments={[
          { label: "Fixkosten", value: 1450, tone: "info" },
          { label: "Variabel", value: 1340, tone: "purple" },
        ]}
      />,
    );
    const texts = textsOf(tree).join(" ");
    expect(texts).toContain("Fixkosten 52%");
    expect(texts).toContain("Variabel 48%");
  });

  it("stuerzt bei einer leeren Aufteilung nicht ab", async () => {
    await expect(render(<SplitMeter segments={[]} />)).resolves.toBeDefined();
  });
});

describe("Diagramme", () => {
  it("rendert den Ring mit Gesamtsumme in der Mitte", async () => {
    const tree = await render(
      <DonutChart
        slices={[
          { label: "Wohnen", value: 980, tone: "info" },
          { label: "Lebensmittel", value: 614, tone: "positive" },
        ]}
        centerValue="2.790 €"
        centerLabel="Ausgaben"
      />,
    );
    const texts = textsOf(tree);
    expect(texts).toContain("2.790 €");
    expect(texts).toContain("AUSGABEN");
  });

  it("weist bei zu wenig Punkten auf fehlende Daten hin statt zu zeichnen", async () => {
    const tree = await render(<TrendChart points={[{ x: 0, value: 100 }]} />);
    expect(textsOf(tree).join(" ")).toContain("zu wenig Daten");
  });

  it("zeichnet die Verlaufskurve mit Pufferlinie", async () => {
    const tree = await render(
      <TrendChart
        points={[
          { x: 0, value: 245080 },
          { x: 1, value: 233580 },
          { x: 2, value: 142000, marker: "low" },
          { x: 3, value: 462000, marker: "high" },
        ]}
        thresholdMinor={100000}
        thresholdLabel="Sicherheits-Puffer 1.000 €"
      />,
    );
    expect(textsOf(tree)).toContain("Sicherheits-Puffer 1.000 €");
  });
});

describe("DataRow", () => {
  it("bringt Titel, Untertitel und Fussnote unter", async () => {
    const tree = await render(
      <DataRow
        title="Spotify Premium"
        subtitle="Morgen · Dauerauftrag"
        trailing={<Money minorUnits={-1499} />}
        trailingCaption="Danach: 2.435,00 €"
      />,
    );
    const texts = textsOf(tree);
    expect(texts).toContain("Spotify Premium");
    expect(texts).toContain("Morgen · Dauerauftrag");
    expect(texts).toContain("-14,99 €");
    expect(texts).toContain("Danach: 2.435,00 €");
  });

  it("ist als Knopf ansagbar, sobald sie antippbar ist", async () => {
    const onPress = jest.fn();
    const tree = await render(<DataRow title="Rewe Center" onPress={onPress} />);
    const row = tree.root.findByProps({ accessibilityRole: "button" });
    expect(row.props.accessibilityLabel).toBe("Rewe Center");
    act(() => row.props.onPress());
    expect(onPress).toHaveBeenCalledTimes(1);
  });
});

describe("Numpad", () => {
  it("meldet jede Taste einzeln und mit sprechendem Label", async () => {
    const onPress = jest.fn();
    const tree = await render(<Numpad onPress={onPress} />);
    const komma = tree.root.findByProps({ accessibilityLabel: "Komma" });
    act(() => komma.props.onPress());
    expect(onPress).toHaveBeenCalledWith(",");

    const back = tree.root.findByProps({ accessibilityLabel: "Letzte Ziffer löschen" });
    act(() => back.props.onPress());
    expect(onPress).toHaveBeenCalledWith("back");
  });
});

describe("Card", () => {
  it("bleibt ohne onPress eine reine Flaeche ohne Knopf-Rolle", async () => {
    const tree = await render(
      <Card>
        <Text>Inhalt</Text>
      </Card>,
    );
    expect(tree.root.findAllByProps({ accessibilityRole: "button" })).toHaveLength(0);
  });
});

describe("Ableitungen aus Namen", () => {
  it("gibt demselben Haendler immer dieselbe Farbe", () => {
    expect(toneForLabel("Rewe Center")).toBe(toneForLabel("Rewe Center"));
    expect(toneForLabel("")).toBeDefined();
  });

  it("bildet Initialen aus ein oder zwei Woertern", () => {
    expect(initialsForLabel("Rewe Center")).toBe("RC");
    expect(initialsForLabel("Netflix")).toBe("NE");
    expect(initialsForLabel("   ")).toBe("?");
  });
});

describe("Palette", () => {
  it("belegt in beiden Themes dieselben Rollen", () => {
    expect(Object.keys(lightTheme).sort()).toEqual(Object.keys(darkTheme).sort());
  });

  it("hat keine leeren Farbwerte", () => {
    for (const [key, value] of Object.entries(darkTheme)) {
      expect(`${key}=${value}`).toMatch(/=(#|rgba?\()/);
    }
    for (const [key, value] of Object.entries(lightTheme)) {
      expect(`${key}=${value}`).toMatch(/=(#|rgba?\()/);
    }
  });
});
