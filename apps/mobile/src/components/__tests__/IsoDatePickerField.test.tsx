/// <reference types="jest" />
import type { ElementType } from "react";
import { Platform } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import {
  dateToIso,
  formatIsoDateForDisplay,
  IsoDatePickerField,
  isoToLocalDate,
} from "../IsoDatePickerField";

jest.mock("@expo/vector-icons", () => ({
  Ionicons: () => null,
}));

jest.mock("@react-native-community/datetimepicker", () => {
  return {
    __esModule: true,
    default: "NativeDatePicker",
  };
});

describe("IsoDatePickerField", () => {
  it("converts local dates without timezone drift and formats German display text", () => {
    const local = isoToLocalDate("2027-01-15");
    expect(local.getFullYear()).toBe(2027);
    expect(local.getMonth()).toBe(0);
    expect(local.getDate()).toBe(15);
    expect(dateToIso(local)).toBe("2027-01-15");
    expect(formatIsoDateForDisplay("2027-01-15")).toContain("Januar 2027");
  });

  it("opens the native picker and emits the same ISO server format", () => {
    if (Platform.OS === "web") return;
    const onChange = jest.fn();
    let tree: ReactTestRenderer;
    act(() => {
      tree = create(
        <IsoDatePickerField label="Zieldatum" value="2026-08-10" onChange={onChange} />,
      );
    });

    const trigger = tree!.root.find(
      (node) =>
        typeof node.props.accessibilityLabel === "string" &&
        node.props.accessibilityLabel.startsWith("Zieldatum:") &&
        typeof node.props.onPress === "function",
    );
    act(() => trigger.props.onPress());
    const picker = tree!.root.findByType("NativeDatePicker" as unknown as ElementType);
    act(() => picker.props.onChange({ type: "set" }, new Date(2027, 0, 15, 12)));

    expect(onChange).toHaveBeenCalledWith("2027-01-15");
    act(() => tree!.unmount());
  });
});
