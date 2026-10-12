/// <reference types="jest" />
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { StyleSheet, Text, TextInput } from "react-native";
import { applyGlobalFont } from "../globalFont";
import { fontFamily, resolveFontStyle, setFontsReady } from "../fonts";

// Der App-weite Schriftaufsatz greift in ein Interna von React Native ein
// (Austausch des Default-Exports von Libraries/Text/Text). Genau deshalb wird
// er hier geprueft: bricht die Technik in einer kuenftigen React-Native-
// Fassung, faellt das hier auf und nicht erst als Systemschrift in der APK.

function flattenedFontOf(element: unknown): { fontFamily?: string; fontWeight?: string } {
  let renderer: ReactTestRenderer | undefined;
  // React 19 rendert ueber den Scheduler; ohne act() liest toJSON() den Baum,
  // bevor er ueberhaupt gebaut ist.
  act(() => {
    renderer = create(element as React.ReactElement);
  });
  const tree = renderer?.toJSON() ?? null;
  const node = Array.isArray(tree) ? tree[0] : tree;
  const style = StyleSheet.flatten(
    (node as { props?: { style?: unknown } } | null)?.props?.style as never,
  ) as { fontFamily?: string; fontWeight?: string } | undefined;
  return style ?? {};
}

describe("resolveFontStyle", () => {
  afterEach(() => setFontsReady(false));

  it("faellt ohne geladene Schriften auf die Systemschrift zurueck", () => {
    setFontsReady(false);
    expect(resolveFontStyle(fontFamily.ui, "700")).toEqual({
      fontFamily: undefined,
      fontWeight: "700",
    });
    expect(resolveFontStyle(fontFamily.mono, "700")?.fontFamily).toMatch(/mono|Menlo/i);
  });

  it("waehlt den passenden Inter-Schnitt und entfernt die Staerke", () => {
    setFontsReady(true);
    expect(resolveFontStyle(undefined, "800")).toEqual({
      fontFamily: "Inter_800ExtraBold",
      fontWeight: undefined,
    });
    expect(resolveFontStyle(fontFamily.ui, "500")).toEqual({
      fontFamily: "Inter_500Medium",
      fontWeight: undefined,
    });
  });

  it("waehlt JetBrains Mono fuer Geldbetraege", () => {
    setFontsReady(true);
    expect(resolveFontStyle(fontFamily.mono, "700")).toEqual({
      fontFamily: "JetBrainsMono_700Bold",
      fontWeight: undefined,
    });
  });

  it("laesst fremde Schriftfamilien unangetastet", () => {
    setFontsReady(true);
    expect(resolveFontStyle("Ionicons", "400")).toBeNull();
  });
});

describe("applyGlobalFont", () => {
  it("haengt sich erfolgreich in Text und TextInput ein", () => {
    expect(applyGlobalFont()).toBe(true);
  });

  it("setzt Inter auf jeden Text, ohne dass die Aufrufstelle es angibt", () => {
    applyGlobalFont();
    setFontsReady(true);
    try {
      expect(
        flattenedFontOf(<Text style={{ fontWeight: "700" }}>Guten Morgen</Text>),
      ).toMatchObject({ fontFamily: "Inter_700Bold" });
      expect(
        flattenedFontOf(
          <TextInput style={{ fontWeight: "400" }} value="x" onChangeText={() => {}} />,
        ),
      ).toMatchObject({ fontFamily: "Inter_400Regular" });
      expect(
        flattenedFontOf(
          <Text style={{ fontFamily: fontFamily.mono, fontWeight: "800" }}>42,50 €</Text>,
        ),
      ).toMatchObject({ fontFamily: "JetBrainsMono_800ExtraBold" });
    } finally {
      setFontsReady(false);
    }
  });
});
