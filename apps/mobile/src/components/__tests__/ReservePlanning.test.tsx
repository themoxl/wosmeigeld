/// <reference types="jest" />
import { Text } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { BudgetSummary, ReservePotResponse } from "../../api/types";
import { ReserveCompactStrip } from "../reserves/ReserveLedger";
import { ReservePotCard, shouldStackReserveSequence } from "../reserves/ReservePotCard";

function textsOf(tree: ReactTestRenderer): string[] {
  return tree.root.findAllByType(Text).map((node) => {
    const children = node.props.children;
    if (typeof children === "string" || typeof children === "number") return String(children);
    if (Array.isArray(children)) {
      return children
        .filter(
          (child): child is string | number =>
            typeof child === "string" || typeof child === "number",
        )
        .join("");
    }
    return "";
  });
}

function makePot(overrides: Partial<ReservePotResponse> = {}): ReservePotResponse {
  return {
    id: "pot-1",
    kind: "contract",
    source_id: "contract-1",
    name: "Kfz-Versicherung",
    frequency: "yearly",
    target_amount_minor: 120000,
    target_date: null,
    next_due_date: "2027-01-01",
    recommended_minor: 10000,
    deposited_minor: 4000,
    withdrawn_minor: 0,
    open_minor: 6000,
    balance_minor: 24000,
    next_month_recommended_minor: 10600,
    status: "partial",
    is_included_in_planning: true,
    movements: [],
    ...overrides,
  };
}

describe("native reserve planning components", () => {
  it("uses a 2x2 sequence on narrow screens and with Dynamic Type", () => {
    expect(shouldStackReserveSequence(360, 1)).toBe(true);
    expect(shouldStackReserveSequence(390, 1)).toBe(true);
    expect(shouldStackReserveSequence(768, 1.2)).toBe(true);
    expect(shouldStackReserveSequence(768, 1)).toBe(false);
  });

  it("keeps monthly budget to the compact server-calculated strip", () => {
    let tree: ReactTestRenderer;
    act(() => {
      tree = create(
        <ReserveCompactStrip
          budget={
            {
              planning_truly_free_minor: 123456,
              total_calculational_reserves_minor: 34567,
            } as BudgetSummary
          }
          onOpen={jest.fn()}
        />,
      );
    });

    const text = textsOf(tree!).join(" ");
    expect(text).toContain("Wirklich frei nach Rücklagen");
    expect(text).toContain("Diesen Monat empfohlen");
    expect(text).toContain("Rücklagen öffnen");
    expect(text).not.toContain("Erwartete Einnahmen");
  });

  it("renders the canonical Soll-to-next-month sequence and an explicit skip action", () => {
    let tree: ReactTestRenderer;
    act(() => {
      tree = create(
        <ReservePotCard
          pot={makePot({
            recommended_minor: 12345678,
            next_month_recommended_minor: 23456789,
          })}
          canEdit
          actionsDisabled={false}
          onDeposit={jest.fn()}
          onWithdraw={jest.fn()}
          onSkip={jest.fn()}
          onUndoSkip={jest.fn()}
        />,
      );
    });

    const text = textsOf(tree!).join(" ");
    expect(text).toContain("Soll");
    expect(text).toContain("Bestätigt");
    expect(text).toContain("Stand");
    expect(text).toContain("Nächster Monat");
    expect(text).toContain("123.456,78 €");
    expect(text).toContain("234.567,89 €");
    expect(text).toContain("Diesen Monat nicht");
  });

  it("offers undo for a skipped month and hides all write actions from viewers", () => {
    let editor: ReactTestRenderer;
    let viewer: ReactTestRenderer;
    act(() => {
      editor = create(
        <ReservePotCard
          pot={makePot({ status: "skipped" })}
          canEdit
          actionsDisabled={false}
          onDeposit={jest.fn()}
          onWithdraw={jest.fn()}
          onSkip={jest.fn()}
          onUndoSkip={jest.fn()}
        />,
      );
      viewer = create(
        <ReservePotCard
          pot={makePot({ status: "skipped" })}
          canEdit={false}
          actionsDisabled={false}
          onDeposit={jest.fn()}
          onWithdraw={jest.fn()}
          onSkip={jest.fn()}
          onUndoSkip={jest.fn()}
        />,
      );
    });

    expect(textsOf(editor!).join(" ")).toContain("Auslassen rückgängig");
    expect(textsOf(viewer!).join(" ")).not.toContain("Auslassen rückgängig");
    expect(textsOf(viewer!).join(" ")).not.toContain("Entnehmen");
  });
});
