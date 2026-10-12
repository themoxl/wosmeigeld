import { StyleSheet } from "react-native";
import { act, create, type ReactTestInstance, type ReactTestRenderer } from "react-test-renderer";
import { SegmentedControl, type SegmentedControlOption } from "../SegmentedControl";

/**
 * `ReactTestInstance["type"]` ist ueber `ElementType` an die
 * Web-JSX-Intrinsics (DOM-Tagnamen) gebunden, nicht an React-Native-Host-
 * Komponenten - ein direkter Vergleich mit dem Stringnamen "View" waere fuer
 * TypeScript deshalb ein (vermeintlich) unmoeglicher Vergleich, obwohl
 * react-test-renderer host-Komponenten zur Laufzeit exakt so benennt.
 */
function isHostTabView(node: ReactTestInstance): boolean {
  return (node.type as unknown) === "View" && node.props.accessibilityRole === "tab";
}

type Direction = "expense" | "income";

const OPTIONS: SegmentedControlOption<Direction>[] = [
  { value: "expense", label: "Ausgaben" },
  { value: "income", label: "Einnahmen" },
];

function renderControl(value: Direction, onChange: (value: Direction) => void) {
  let tree: ReactTestRenderer | undefined;
  act(() => {
    tree = create(
      <SegmentedControl
        options={OPTIONS}
        value={value}
        onChange={onChange}
        accessibilityLabel="Ausgaben oder Einnahmen"
      />,
    );
  });
  if (!tree) throw new Error("render fehlgeschlagen");
  return tree;
}

describe("SegmentedControl", () => {
  it("meldet die Gruppe als tablist und jede Option als tab mit Auswahlzustand", () => {
    const tree = renderControl("expense", () => {});

    const tablist = tree.root.findByProps({ accessibilityRole: "tablist" });
    expect(tablist.props.accessibilityLabel).toBe("Ausgaben oder Einnahmen");

    const tabs = tree.root.findAll(isHostTabView);
    expect(tabs).toHaveLength(2);

    const expenseTab = tree.root.findByProps({ accessibilityLabel: "Ausgaben" });
    const incomeTab = tree.root.findByProps({ accessibilityLabel: "Einnahmen" });
    expect(expenseTab.props.accessibilityState).toEqual({ selected: true });
    expect(incomeTab.props.accessibilityState).toEqual({ selected: false });
  });

  it("ruft onChange mit dem Wert der angetippten Option auf", () => {
    const onChange = jest.fn();
    const tree = renderControl("expense", onChange);

    const incomeTab = tree.root.findByProps({ accessibilityLabel: "Einnahmen" });
    act(() => {
      incomeTab.props.onPress();
    });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("income");
  });

  it("erfuellt fuer jede Option ein Touch-Ziel von mindestens 44dp Hoehe", () => {
    const tree = renderControl("expense", () => {});
    const tabs = tree.root.findAll(isHostTabView);

    for (const tab of tabs) {
      expect(StyleSheet.flatten(tab.props.style).minHeight).toBeGreaterThanOrEqual(44);
    }
  });
});
