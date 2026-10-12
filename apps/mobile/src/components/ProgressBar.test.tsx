import type { ComponentProps } from "react";
import { StyleSheet } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
// Die Handy-App hat seit dem Redesign eine EIGENE Palette (src/theme/palette.ts)
// und nicht mehr die Web-Tokens aus @finanzplaner/design-tokens - siehe die
// Begruendung oben in palette.ts. Ohne ThemeProvider gilt der Vorgabewert des
// Kontexts, also darkTheme.
import { darkTheme } from "../theme";
import { ProgressBar } from "./ProgressBar";
import { isHostElementOfType } from "./testUtils";

function renderBar(props: ComponentProps<typeof ProgressBar>) {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<ProgressBar {...props} />);
  });
  return tree!;
}

/**
 * Die Balkenfuellung ist das einzige View mit prozentualer Breite UND eigener
 * Flaechenfarbe: der aeussere Rahmen hat zwar ebenfalls width "100%", aber
 * keinen backgroundColor, die Schiene umgekehrt eine Farbe ohne feste Breite.
 * Bewusst nicht ueber `Array.isArray(style)` gefiltert - ob ein Style als
 * Objekt oder als Array durchgereicht wird, ist ein Umsetzungsdetail.
 */
function findFills(tree: ReactTestRenderer, width: string) {
  return tree.root.findAll((node) => {
    if (!isHostElementOfType(node, "View")) return false;
    const style = StyleSheet.flatten(node.props.style);
    return style?.width === width && typeof style?.backgroundColor === "string";
  });
}

describe("ProgressBar", () => {
  it("rendert die bestehende Standardnutzung (nur progress) optisch unveraendert", () => {
    const tree = renderBar({ progress: 0.5 });

    // Balkenfuellung: 50 % Breite, Gruen als Flaechenfarbe (positiveStrong -
    // `positive` ist im neuen Tokensatz die TEXTfarbe).
    const fills = findFills(tree, "50%");
    expect(fills).toHaveLength(1);
    expect(StyleSheet.flatten(fills[0]!.props.style).backgroundColor).toBe(
      darkTheme.positiveStrong,
    );

    // Keine Ueberziehungsmarkierung bei einem Wert unter 100 %.
    expect(tree.root.findAllByProps({ testID: "progress-bar-overflow" })).toHaveLength(0);
  });

  it("meldet Rolle, Wert und lesbaren Text fuer Screenreader", () => {
    const tree = renderBar({ progress: 0.42, accessibilityLabel: "Budget Lebensmittel" });

    const container = tree.root.findByProps({ accessibilityRole: "progressbar" });
    expect(container.props.accessibilityLabel).toBe("Budget Lebensmittel");
    expect(container.props.accessibilityValue).toEqual({
      min: 0,
      max: 100,
      now: 42,
      text: "42 Prozent",
    });
  });

  it("verwendet ein neutrales Standardlabel ohne accessibilityLabel-Prop", () => {
    const tree = renderBar({ progress: 0.2 });
    const container = tree.root.findByProps({ accessibilityRole: "progressbar" });
    expect(container.props.accessibilityLabel).toBe("Fortschritt");
  });

  it("zeigt bei Ueberziehung (> 100 %) eine sichtbare Markierung zusaetzlich zur Warnfarbe", () => {
    const tree = renderBar({ progress: 1.2 });

    const overflowMarkers = tree.root.findAll(
      (node) => isHostElementOfType(node, "View") && node.props.testID === "progress-bar-overflow",
    );
    expect(overflowMarkers).toHaveLength(1);
    expect(StyleSheet.flatten(overflowMarkers[0]!.props.style).borderLeftColor).toBe(
      darkTheme.warningStrong,
    );

    // Fuellung bleibt bei 100 % gekappt, aber in Warnfarbe statt Gruen.
    const fills = findFills(tree, "100%");
    expect(fills).toHaveLength(1);
    expect(StyleSheet.flatten(fills[0]!.props.style).backgroundColor).toBe(darkTheme.warningStrong);

    const container = tree.root.findByProps({ accessibilityRole: "progressbar" });
    expect(container.props.accessibilityValue.text).toBe("120 Prozent, 20 Prozent ueber dem Ziel");
    expect(container.props.accessibilityValue.now).toBe(100);
  });

  it("respektiert eine eigene Farbe und blendet die Ueberziehungsmarkierung dann nicht ab", () => {
    const tree = renderBar({ progress: 1.5, color: "#123456" });

    const overflowMarkers = tree.root.findAll(
      (node) => isHostElementOfType(node, "View") && node.props.testID === "progress-bar-overflow",
    );
    expect(overflowMarkers).toHaveLength(1);

    const fills = findFills(tree, "100%");
    expect(fills).toHaveLength(1);
    expect(StyleSheet.flatten(fills[0]!.props.style).backgroundColor).toBe("#123456");
  });
});
