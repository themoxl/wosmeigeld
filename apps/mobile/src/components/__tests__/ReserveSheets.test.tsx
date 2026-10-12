/// <reference types="jest" />
import { Text, TextInput } from "react-native";
import { act, create, type ReactTestInstance, type ReactTestRenderer } from "react-test-renderer";
import type { ReservePotResponse } from "../../api/types";
import { ReserveBulkDepositSheet } from "../reserves/ReserveBulkDepositSheet";
import { ReserveDepositSheet } from "../reserves/ReserveDepositSheet";
import { ReserveDistributionSheet } from "../reserves/ReserveDistributionSheet";
import { ReserveReallocationSheet } from "../reserves/ReserveReallocationSheet";
import { ReserveWithdrawalSheet } from "../reserves/ReserveWithdrawalSheet";

function makePot(overrides: Partial<ReservePotResponse> = {}): ReservePotResponse {
  return {
    id: "contract-pot",
    kind: "contract",
    source_id: "contract-1",
    name: "Jahresversicherung",
    frequency: "yearly",
    target_amount_minor: 120000,
    next_due_date: "2027-01-01",
    recommended_minor: 10000,
    deposited_minor: 0,
    withdrawn_minor: 0,
    open_minor: 10000,
    balance_minor: 25000,
    next_month_recommended_minor: 10000,
    status: "open",
    is_included_in_planning: true,
    movements: [],
    ...overrides,
  };
}

const FREE_POT = makePot({
  id: "free-pot",
  kind: "free",
  source_id: null,
  name: "Freie Rücklage",
  frequency: null,
  target_amount_minor: null,
  next_due_date: null,
  recommended_minor: 0,
  open_minor: 0,
  balance_minor: 10000,
  next_month_recommended_minor: 0,
  status: "done",
  is_included_in_planning: false,
});

function findButton(tree: ReactTestRenderer, label: string): ReactTestInstance {
  const candidates = tree.root.findAll(
    (node) =>
      typeof node.props.onPress === "function" &&
      node.findAll((child) => child.type === Text && child.props.children === label).length > 0,
  );
  const button = candidates[candidates.length - 1];
  if (!button) throw new Error(`Button "${label}" nicht gefunden.`);
  return button;
}

function findInput(tree: ReactTestRenderer, accessibilityLabel: string): ReactTestInstance {
  return tree.root.find(
    (node) =>
      node.type === TextInput &&
      node.props.accessibilityLabel === accessibilityLabel &&
      typeof node.props.onChangeText === "function",
  );
}

function textOf(tree: ReactTestRenderer): string {
  return tree.root
    .findAllByType(Text)
    .map((node) => {
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
    })
    .join(" ");
}

describe("safe native reserve sheets", () => {
  it("opens bulk confirmation with selected editable pots and never writes before confirmation", async () => {
    const onConfirm = jest.fn().mockResolvedValue({});
    let tree: ReactTestRenderer;
    await act(async () => {
      tree = create(
        <ReserveBulkDepositSheet
          visible
          pots={[makePot()]}
          year={2026}
          month={8}
          loading={false}
          onClose={jest.fn()}
          onConfirm={onConfirm}
        />,
      );
    });

    expect(onConfirm).not.toHaveBeenCalled();
    expect(textOf(tree!)).toContain("Das Öffnen dieses Sheets schreibt noch nichts");
    expect(findInput(tree!, "Einzahlungsbetrag für Jahresversicherung").props.value).toBe("100,00");

    await act(async () => findButton(tree!, "Verbindlich bestätigen").props.onPress());
    expect(onConfirm).toHaveBeenCalledWith(
      expect.objectContaining({
        total_minor: 10000,
        allocations: [{ pot_id: "contract-pot", amount_minor: 10000 }],
      }),
    );
    act(() => tree!.unmount());
  });

  it("keeps a 20k deposit fully free until the explicit suggestion action", async () => {
    const onPreview = jest.fn().mockResolvedValue({
      total_minor: 2000000,
      allocations: [{ pot_id: "contract-pot", amount_minor: 10000 }],
      free_remainder_minor: 1990000,
    });
    const onConfirm = jest.fn().mockResolvedValue({});
    let tree: ReactTestRenderer;
    await act(async () => {
      tree = create(
        <ReserveDistributionSheet
          visible
          pots={[makePot(), FREE_POT]}
          year={2026}
          month={8}
          previewLoading={false}
          confirmLoading={false}
          onClose={jest.fn()}
          onPreview={onPreview}
          onConfirm={onConfirm}
        />,
      );
    });

    const totalInput = tree!.root.find(
      (node) => node.type === TextInput && node.props.value === "" && node.props.autoFocus,
    );
    act(() => totalInput.props.onChangeText("20.000,00"));

    expect(onPreview).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
    expect(findInput(tree!, "Betrag für Freie Rücklage").props.value).toBe("20.000,00");

    await act(async () =>
      findButton(tree!, "Nach offenen/fälligen Töpfen vorschlagen").props.onPress(),
    );
    expect(onPreview).toHaveBeenCalledWith({ year: 2026, month: 8, total_minor: 2000000 });
    expect(findInput(tree!, "Betrag für Freie Rücklage").props.value).toBe("19900,00");

    await act(async () => findButton(tree!, "Verteilung verbindlich bestätigen").props.onPress());
    expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ total_minor: 2000000 }));
    act(() => tree!.unmount());
  });

  it("blocks a reallocation that would overdraw the free reserve", async () => {
    const onConfirm = jest.fn().mockResolvedValue({});
    let tree: ReactTestRenderer;
    await act(async () => {
      tree = create(
        <ReserveReallocationSheet
          visible
          pots={[makePot(), FREE_POT]}
          year={2026}
          month={8}
          loading={false}
          onClose={jest.fn()}
          onConfirm={onConfirm}
        />,
      );
    });

    act(() => findInput(tree!, "Betrag für Jahresversicherung").props.onChangeText("200,00"));

    expect(textOf(tree!)).toContain("Die freie Rücklage kann nicht überzogen werden.");
    expect(
      tree!.root.findAll(
        (node) =>
          node.props.accessibilityState?.disabled === true &&
          node.findAll(
            (child) => child.type === Text && child.props.children === "Umverteilung bestätigen",
          ).length > 0,
      ),
    ).not.toHaveLength(0);
    expect(onConfirm).not.toHaveBeenCalled();
    act(() => tree!.unmount());
  });

  it("confirms an edited single deposit and blocks a withdrawal above the real balance", async () => {
    const depositConfirm = jest.fn().mockResolvedValue({});
    let depositTree: ReactTestRenderer;
    await act(async () => {
      depositTree = create(
        <ReserveDepositSheet
          visible
          pot={makePot()}
          year={2026}
          month={8}
          loading={false}
          onClose={jest.fn()}
          onConfirm={depositConfirm}
        />,
      );
    });
    const depositAmount = depositTree!.root.find(
      (node) => node.type === TextInput && node.props.keyboardType === "decimal-pad",
    );
    act(() => depositAmount.props.onChangeText("75,00"));
    await act(async () => findButton(depositTree!, "Verbindlich bestätigen").props.onPress());
    expect(depositConfirm).toHaveBeenCalledWith(
      expect.objectContaining({
        total_minor: 7500,
        allocations: [{ pot_id: "contract-pot", amount_minor: 7500 }],
      }),
    );

    const withdrawConfirm = jest.fn().mockResolvedValue({});
    let withdrawTree: ReactTestRenderer;
    await act(async () => {
      withdrawTree = create(
        <ReserveWithdrawalSheet
          visible
          pot={makePot({ balance_minor: 2500 })}
          year={2026}
          month={8}
          loading={false}
          onClose={jest.fn()}
          onConfirm={withdrawConfirm}
        />,
      );
    });
    const withdrawalAmount = withdrawTree!.root.find(
      (node) => node.type === TextInput && node.props.keyboardType === "decimal-pad",
    );
    act(() => withdrawalAmount.props.onChangeText("50,00"));
    await act(async () => findButton(withdrawTree!, "Entnahme bestätigen").props.onPress());
    expect(withdrawConfirm).not.toHaveBeenCalled();
    expect(textOf(withdrawTree!)).toContain("Der Topf kann nicht überzogen werden.");
    act(() => {
      depositTree!.unmount();
      withdrawTree!.unmount();
    });
  });
});
