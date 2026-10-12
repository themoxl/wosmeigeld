/// <reference types="jest" />
import type { ReactElement } from "react";
import { Modal, Text } from "react-native";
import { act, create } from "react-test-renderer";
import { SortSheet } from "./SortSheet";

// react-test-renderer exportiert seinen Rueckgabetyp nicht zuverlaessig als
// benannten Typ (je nach aufgeloester @types-Version). ReturnType<typeof
// create> ist deshalb robuster als ein direkter Typ-Import.
type Renderer = ReturnType<typeof create>;

// SortSheet komponiert nur SheetModal, keine Ionicons - hier reicht ein
// synchrones act() ohne Font-Ladewartezeit.
function render(element: ReactElement): Renderer {
  let tree!: Renderer;
  act(() => {
    tree = create(element);
  });
  return tree;
}

describe("SortSheet", () => {
  it("zeigt Titel und den vom Screen uebergebenen Inhalt", () => {
    const tree = render(
      <SortSheet visible title="Buchungen sortieren" onClose={jest.fn()}>
        <Text>Neueste zuerst</Text>
      </SortSheet>,
    );

    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toContain("Buchungen sortieren");
    expect(texts).toContain("Neueste zuerst");
  });

  it("verwendet 'Sortieren' als Standardtitel", () => {
    const tree = render(
      <SortSheet visible onClose={jest.fn()}>
        <Text>Option</Text>
      </SortSheet>,
    );

    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toContain("Sortieren");
  });

  it("enthaelt keine eigene Fusszeile (anders als FilterSheet)", () => {
    const tree = render(
      <SortSheet visible onClose={jest.fn()}>
        <Text>Option</Text>
      </SortSheet>,
    );

    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).not.toContain("Anwenden");
    expect(texts).not.toContain("Zurücksetzen");
  });

  it("meldet Schliessen, wenn die Kopfzeile des Sheets geschlossen wird", () => {
    const onClose = jest.fn();
    const tree = render(
      <SortSheet visible onClose={onClose}>
        <Text>Option</Text>
      </SortSheet>,
    );

    const closeButton = tree.root.findByProps({ accessibilityLabel: "Auswahl schließen" });
    act(() => {
      closeButton.props.onPress();
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("meldet Schliessen, wenn der Hintergrund angetippt wird", () => {
    const onClose = jest.fn();
    const tree = render(
      <SortSheet visible onClose={onClose}>
        <Text>Option</Text>
      </SortSheet>,
    );

    // Der Backdrop traegt in SheetModal ein eigenes, vom Kopfzeilen-Button
    // ("Auswahl schliessen") unterscheidbares Accessibility-Label.
    const backdrop = tree.root.findByProps({ accessibilityLabel: "Schließen" });
    act(() => {
      backdrop.props.onPress();
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("gibt 'visible' unveraendert an das native Modal weiter", () => {
    const tree = render(
      <SortSheet visible={false} onClose={jest.fn()}>
        <Text>Option</Text>
      </SortSheet>,
    );

    expect(tree.root.findByType(Modal).props.visible).toBe(false);
  });
});
