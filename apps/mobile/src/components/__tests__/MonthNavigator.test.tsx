import { StyleSheet } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { MonthNavigator } from "../MonthNavigator";

const REFERENCE_DATE = new Date(2026, 7, 9); // 9. August 2026

function renderNavigator(
  year: number,
  month: number,
  onChange: (year: number, month: number) => void,
  monthsBack?: number,
) {
  let tree: ReactTestRenderer | undefined;
  act(() => {
    tree = create(
      <MonthNavigator
        year={year}
        month={month}
        onChange={onChange}
        referenceDate={REFERENCE_DATE}
        monthsBack={monthsBack}
      />,
    );
  });
  if (!tree) throw new Error("render fehlgeschlagen");
  return tree;
}

function findModal(tree: ReactTestRenderer) {
  return tree.root.findByProps({ animationType: "slide", transparent: true });
}

function openPicker(tree: ReactTestRenderer, label: string) {
  const monthButton = tree.root.findByProps({
    accessibilityLabel: `Monat wählen, aktuell ${label}`,
  });
  act(() => {
    monthButton.props.onPress();
  });
}

describe("MonthNavigator", () => {
  it("zeigt den ausgewaehlten Monat als lokalisierten Titel", () => {
    const tree = renderNavigator(2026, 8, () => {});
    const monthButton = tree.root.findByProps({
      accessibilityLabel: "Monat wählen, aktuell August 2026",
    });
    expect(monthButton).toBeTruthy();
  });

  it("blaettert mit den Pfeilen einen Monat zurueck bzw. vor", () => {
    const onChange = jest.fn();
    const tree = renderNavigator(2026, 8, onChange);

    const prevButton = tree.root.findByProps({ accessibilityLabel: "Vorheriger Monat" });
    act(() => {
      prevButton.props.onPress();
    });
    expect(onChange).toHaveBeenLastCalledWith(2026, 7);

    const nextButton = tree.root.findByProps({ accessibilityLabel: "Nächster Monat" });
    act(() => {
      nextButton.props.onPress();
    });
    expect(onChange).toHaveBeenLastCalledWith(2026, 9);
  });

  it("wechselt beim Blaettern korrekt ueber Jahresgrenzen", () => {
    const onChangeBackward = jest.fn();
    const januaryTree = renderNavigator(2026, 1, onChangeBackward);
    act(() => {
      januaryTree.root.findByProps({ accessibilityLabel: "Vorheriger Monat" }).props.onPress();
    });
    expect(onChangeBackward).toHaveBeenCalledWith(2025, 12);

    const onChangeForward = jest.fn();
    const decemberTree = renderNavigator(2026, 12, onChangeForward);
    act(() => {
      decemberTree.root.findByProps({ accessibilityLabel: "Nächster Monat" }).props.onPress();
    });
    expect(onChangeForward).toHaveBeenCalledWith(2027, 1);
  });

  it("oeffnet das Monatswahl-Sheet erst nach Antippen des Monatsnamens", () => {
    const tree = renderNavigator(2026, 8, () => {});
    expect(findModal(tree).props.visible).toBe(false);

    openPicker(tree, "August 2026");
    expect(findModal(tree).props.visible).toBe(true);
  });

  it("listet im Sheet die letzten Monate ab dem Referenzdatum, neuester zuerst", () => {
    const tree = renderNavigator(2026, 8, () => {}, 3);
    openPicker(tree, "August 2026");

    const optionLabels = ["August 2026", "Juli 2026", "Juni 2026"];
    for (const optionLabel of optionLabels) {
      const matches = tree.root.findAll(
        (node) =>
          node.props.accessibilityLabel === optionLabel &&
          node.props.accessibilityRole === "button",
      );
      expect(matches.length).toBeGreaterThan(0);
    }
  });

  it("markiert die aktuell ausgewaehlte Monatszeile im Sheet als selected", () => {
    const tree = renderNavigator(2026, 7, () => {}, 3);
    openPicker(tree, "Juli 2026");

    const selectedOption = tree.root.findByProps({ accessibilityLabel: "Juli 2026" });
    expect(selectedOption.props.accessibilityState).toEqual({ selected: true });

    const otherOption = tree.root.findByProps({ accessibilityLabel: "August 2026" });
    expect(otherOption.props.accessibilityState).toEqual({ selected: false });
  });

  it("uebernimmt eine Sheet-Auswahl per onChange und schliesst danach das Sheet", () => {
    const onChange = jest.fn();
    const tree = renderNavigator(2026, 8, onChange, 3);
    openPicker(tree, "August 2026");

    const juneOption = tree.root.findByProps({ accessibilityLabel: "Juni 2026" });
    act(() => {
      juneOption.props.onPress();
    });

    expect(onChange).toHaveBeenCalledWith(2026, 6);
    expect(findModal(tree).props.visible).toBe(false);
  });

  it("erfuellt fuer Pfeile und Monatsname ein Touch-Ziel von mindestens 44dp", () => {
    const tree = renderNavigator(2026, 8, () => {});
    const prevButton = tree.root.findByProps({ accessibilityLabel: "Vorheriger Monat" });
    const nextButton = tree.root.findByProps({ accessibilityLabel: "Nächster Monat" });
    const monthButton = tree.root.findByProps({
      accessibilityLabel: "Monat wählen, aktuell August 2026",
    });

    for (const node of [prevButton, nextButton, monthButton]) {
      expect(flattenPressableStyle(node.props.style).minHeight).toBeGreaterThanOrEqual(44);
    }
    expect(flattenPressableStyle(prevButton.props.style).minWidth).toBeGreaterThanOrEqual(44);
    expect(flattenPressableStyle(nextButton.props.style).minWidth).toBeGreaterThanOrEqual(44);
  });
});

/**
 * `Pressable` reicht die eigene `style`-Prop als Funktion `({pressed}) =>
 * [...]` an die zugrundeliegende View weiter (siehe
 * react-native/Libraries/Components/Pressable/Pressable.js). `findByProps`
 * liefert hier die Pressable-Instanz selbst (deep:false stoppt vor der
 * dahinterliegenden Host-View) - deshalb einmal mit `pressed: false`
 * aufloesen, bevor `StyleSheet.flatten` greift.
 */
function flattenPressableStyle(style: unknown): Record<string, number> {
  const resolved = typeof style === "function" ? style({ pressed: false }) : style;
  return StyleSheet.flatten(resolved);
}
