/// <reference types="jest" />
import type { ReactElement } from "react";
import { Modal, Text } from "react-native";
import { act, create } from "react-test-renderer";
import { FilterSheet } from "./FilterSheet";
import { PrimaryButton } from "./PrimaryButton";

// react-test-renderer exportiert seinen Rueckgabetyp nicht zuverlaessig als
// benannten Typ (je nach aufgeloester @types-Version). ReturnType<typeof
// create> ist deshalb robuster als ein direkter Typ-Import.
type Renderer = ReturnType<typeof create>;

// FilterSheet komponiert nur SheetModal + PrimaryButton, keine Ionicons -
// hier reicht ein synchrones act() ohne Font-Ladewartezeit.
function render(element: ReactElement): Renderer {
  let tree!: Renderer;
  act(() => {
    tree = create(element);
  });
  return tree;
}

// react-native exportiert Pressable ueber React.memo(); die auf einem Fiber
// hinterlegte Instanz weicht deshalb vom importierten Symbol ab. Statt
// gegen den internen Pressable-Typ zu pruefen, wird hier direkt auf den
// PrimaryButton-Props geprueft - das testet ausserdem genau das, was
// FilterSheet selbst verantwortet: die richtige Verdrahtung von Label,
// Callback und Deaktivierung an PrimaryButton.
function findButton(tree: Renderer, label: string) {
  const button = tree.root.findAllByType(PrimaryButton).find((node) => node.props.label === label);
  if (!button) {
    throw new Error(`PrimaryButton mit Label "${label}" nicht gefunden`);
  }
  return button;
}

describe("FilterSheet", () => {
  it("zeigt Titel und den vom Screen uebergebenen Inhalt", () => {
    const tree = render(
      <FilterSheet
        visible
        title="Buchungen filtern"
        onClose={jest.fn()}
        onReset={jest.fn()}
        onApply={jest.fn()}
      >
        <Text>Kontofilter</Text>
      </FilterSheet>,
    );

    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toContain("Buchungen filtern");
    expect(texts).toContain("Kontofilter");
  });

  it("verwendet 'Filter' als Standardtitel und die Standardbeschriftungen", () => {
    const tree = render(
      <FilterSheet visible onClose={jest.fn()} onReset={jest.fn()} onApply={jest.fn()}>
        <Text>Inhalt</Text>
      </FilterSheet>,
    );
    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toContain("Filter");
    expect(() => findButton(tree, "Zurücksetzen")).not.toThrow();
    expect(() => findButton(tree, "Anwenden")).not.toThrow();
  });

  it("verdrahtet 'Zurücksetzen' mit onReset und die Sekundaervariante", () => {
    const onReset = jest.fn();
    const tree = render(
      <FilterSheet visible onClose={jest.fn()} onReset={onReset} onApply={jest.fn()}>
        <Text>Inhalt</Text>
      </FilterSheet>,
    );

    const button = findButton(tree, "Zurücksetzen");
    expect(button.props.variant).toBe("secondary");
    expect(button.props.disabled).toBe(false);

    act(() => {
      button.props.onPress();
    });
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it("verdrahtet 'Anwenden' mit onApply und der Primaervariante", () => {
    const onApply = jest.fn();
    const tree = render(
      <FilterSheet visible onClose={jest.fn()} onReset={jest.fn()} onApply={onApply}>
        <Text>Inhalt</Text>
      </FilterSheet>,
    );

    const button = findButton(tree, "Anwenden");
    expect(button.props.variant).toBe("primary");

    act(() => {
      button.props.onPress();
    });
    expect(onApply).toHaveBeenCalledTimes(1);
  });

  it("gibt resetDisabled unveraendert an den 'Zurücksetzen'-Button weiter", () => {
    const tree = render(
      <FilterSheet
        visible
        onClose={jest.fn()}
        onReset={jest.fn()}
        onApply={jest.fn()}
        resetDisabled
      >
        <Text>Inhalt</Text>
      </FilterSheet>,
    );

    expect(findButton(tree, "Zurücksetzen").props.disabled).toBe(true);
    // "Anwenden" bleibt von resetDisabled unberuehrt.
    expect(findButton(tree, "Anwenden").props.disabled).toBeUndefined();
  });

  it("verwendet eigene Beschriftungen, wenn angegeben", () => {
    const tree = render(
      <FilterSheet
        visible
        onClose={jest.fn()}
        onReset={jest.fn()}
        onApply={jest.fn()}
        resetLabel="Alles zurücksetzen"
        applyLabel="Filter übernehmen"
      >
        <Text>Inhalt</Text>
      </FilterSheet>,
    );

    expect(() => findButton(tree, "Alles zurücksetzen")).not.toThrow();
    expect(() => findButton(tree, "Filter übernehmen")).not.toThrow();
  });

  it("meldet Schliessen, wenn die Kopfzeile des Sheets geschlossen wird", () => {
    const onClose = jest.fn();
    const tree = render(
      <FilterSheet visible onClose={onClose} onReset={jest.fn()} onApply={jest.fn()}>
        <Text>Inhalt</Text>
      </FilterSheet>,
    );

    const closeButton = tree.root.findByProps({ accessibilityLabel: "Auswahl schließen" });
    act(() => {
      closeButton.props.onPress();
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("gibt 'visible' unveraendert an das native Modal weiter", () => {
    const tree = render(
      <FilterSheet visible={false} onClose={jest.fn()} onReset={jest.fn()} onApply={jest.fn()}>
        <Text>Inhalt</Text>
      </FilterSheet>,
    );

    expect(tree.root.findByType(Modal).props.visible).toBe(false);
  });
});
