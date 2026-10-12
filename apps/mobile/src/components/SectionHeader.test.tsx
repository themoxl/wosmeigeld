import type { ComponentProps } from "react";
import { StyleSheet, Text } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { letterSpacingLabel, typeScale } from "../theme";
import { SectionHeader } from "./SectionHeader";

function renderHeader(props: ComponentProps<typeof SectionHeader>) {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<SectionHeader {...props} />);
  });
  return tree!;
}

describe("SectionHeader", () => {
  it("rendert nur den Titel, wenn keine weiteren Props gesetzt sind", () => {
    const tree = renderHeader({ title: "Letzte Buchungen" });

    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toEqual(["Letzte Buchungen"]);
  });

  it("meldet den Titel als Ueberschrift fuer Screenreader", () => {
    const tree = renderHeader({ title: "Letzte Buchungen" });
    const heading = tree.root.findByProps({ accessibilityRole: "header" });
    expect(heading.props.children).toBe("Letzte Buchungen");
  });

  // Abschnittsueberschrift und CardLabel innerhalb einer Karte sollen sich
  // gleich lesen; die Sperrung kommt deshalb aus demselben Token.
  it("setzt den Titel als gesperrtes Grossbuchstaben-Label der Handy-Tokens", () => {
    const heading = renderHeader({ title: "Alltag" }).root.findByProps({
      accessibilityRole: "header",
    });
    const style = StyleSheet.flatten(heading.props.style);

    expect(style.fontSize).toBe(typeScale.caption);
    expect(style.textTransform).toBe("uppercase");
    expect(style.letterSpacing).toBe(letterSpacingLabel);
  });

  it("vergroessert den Titel bei size='sm' fuer reine Navigationslisten", () => {
    const heading = renderHeader({ title: "Alltag", size: "sm" }).root.findByProps({
      accessibilityRole: "header",
    });
    expect(StyleSheet.flatten(heading.props.style).fontSize).toBe(typeScale.body);
  });

  it("zeigt einen optionalen Betrag/Zaehler rechts vom Titel", () => {
    const tree = renderHeader({ title: "Budget", trailing: <Text>1.234,00 EUR</Text> });
    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toEqual(["Budget", "1.234,00 EUR"]);
  });

  it("zeigt eine Aktion rechts und ruft onActionPress beim Antippen auf", () => {
    const onActionPress = jest.fn();
    const tree = renderHeader({ title: "Letzte Buchungen", actionLabel: "Alle", onActionPress });

    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toEqual(["Letzte Buchungen", "Alle"]);

    const action = tree.root.findByProps({ accessibilityLabel: "Alle" });
    act(() => {
      action.props.onPress();
    });
    expect(onActionPress).toHaveBeenCalledTimes(1);
  });

  it("zeigt keine Aktion, wenn nur actionLabel ohne onActionPress gesetzt ist", () => {
    const tree = renderHeader({ title: "Letzte Buchungen", actionLabel: "Alle" });
    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toEqual(["Letzte Buchungen"]);
  });

  it("zeigt trailing und Aktion gleichzeitig, trailing zuerst", () => {
    const tree = renderHeader({
      title: "Budget",
      trailing: <Text>1.234,00 EUR</Text>,
      actionLabel: "Alle",
      onActionPress: () => {},
    });
    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toEqual(["Budget", "1.234,00 EUR", "Alle"]);
  });
});
