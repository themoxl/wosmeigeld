/// <reference types="jest" />
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { Alert, Text } from "react-native";
import { resolveBookedDuplicate, scanBookedDuplicates } from "../../api/client";
import type { ReviewItemBookedDuplicate } from "../../api/types";
import { BookedDuplicatesPanel } from "../BookedDuplicatesPanel";
import { PrimaryButton } from "../PrimaryButton";

jest.mock("../../api/client", () => ({
  scanBookedDuplicates: jest.fn(),
  resolveBookedDuplicate: jest.fn(),
}));
jest.mock("../../auth/AuthProvider", () => ({ useAuth: () => ({ accessToken: "token" }) }));
jest.mock("../../household/HouseholdProvider", () => ({
  useHousehold: () => ({ selectedHouseholdId: "household", selectedHousehold: { role: "owner" } }),
}));

const pair: ReviewItemBookedDuplicate = {
  match: "duplicate_possible",
  first: {
    transaction_id: "first",
    version: 1,
    currency: "EUR",
    booked_date: "2026-04-02",
    amount_minor: -1999,
    payee_label: "Musterkasse AG",
    account_name: "Girokonto",
    purpose: "Beitrag April",
    import_filename: "april.csv",
  },
  second: {
    transaction_id: "second",
    version: 2,
    currency: "EUR",
    booked_date: "2026-04-02",
    amount_minor: -1999,
    payee_label: null,
    account_name: "Girokonto",
    purpose: "Beitrag April",
    import_filename: "april.sta",
  },
};
let tree: ReactTestRenderer;
let client: QueryClient;
function mount(items = [pair]) {
  client = new QueryClient({ defaultOptions: { mutations: { retry: false, gcTime: 0 } } });
  act(() => {
    tree = create(
      <QueryClientProvider client={client}>
        <BookedDuplicatesPanel items={items} total={items.length} />
      </QueryClientProvider>,
    );
  });
}

afterEach(() => {
  act(() => tree?.unmount());
  client?.clear();
  jest.restoreAllMocks();
  jest.resetAllMocks();
});

it("can scan an empty inbox and show the checked booking count", async () => {
  (scanBookedDuplicates as jest.Mock).mockResolvedValue({
    scanned_count: 471,
    total_count: 0,
    items: [],
  });
  mount([]);
  await act(async () => {
    tree.root.findByType(PrimaryButton).props.onPress();
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(scanBookedDuplicates).toHaveBeenCalledWith("token", "household");
  expect(JSON.stringify(tree.toJSON())).toContain("471");
  expect(JSON.stringify(tree.toJSON())).toContain("Keine möglichen Duplikate gefunden.");
});

it("shows both imports and waits for confirmation before removing the chosen copy", async () => {
  const alert = jest.spyOn(Alert, "alert").mockImplementation(() => {});
  (resolveBookedDuplicate as jest.Mock).mockResolvedValue(undefined);
  mount();
  const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
  expect(texts).toContain("april.csv");
  expect(texts).toContain("april.sta");
  act(() => {
    tree.root
      .findAllByType(PrimaryButton)
      .filter((node) => node.props.label === "Diese Buchung entfernen")[1]!
      .props.onPress();
  });
  expect(resolveBookedDuplicate).not.toHaveBeenCalled();
  await act(async () => {
    alert.mock.calls[0]![2]!.find((button) => button.style === "destructive")!.onPress!();
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(resolveBookedDuplicate).toHaveBeenCalledWith("token", "household", {
    first_transaction_id: "first",
    first_version: 1,
    second_transaction_id: "second",
    second_version: 2,
    action: "archive_second",
  });
});

it("sends an explicit keep-both decision", async () => {
  (resolveBookedDuplicate as jest.Mock).mockResolvedValue(undefined);
  mount();
  await act(async () => {
    tree.root
      .findAllByType(PrimaryButton)
      .find((node) => node.props.label === "Beide behalten")!
      .props.onPress();
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(resolveBookedDuplicate).toHaveBeenCalledWith(
    "token",
    "household",
    expect.objectContaining({ action: "keep_both" }),
  );
});
