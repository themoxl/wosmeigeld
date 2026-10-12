/// <reference types="jest" />
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Text } from "react-native";
import { act, create, type ReactTestInstance, type ReactTestRenderer } from "react-test-renderer";
import type { SavingsGoal, WishlistItem } from "../../api/types";
import { startWishlistSavingsPlan, updateSavingsGoal, updateWishlistItem } from "../../api/client";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { useSavingsGoals, useWishlistItems } from "../../hooks/useGoals";
import SavingsWishlistScreen from "../../../app/mehr/sparziele";

// Der Screen navigiert selbst ueber useRouter().push statt ueber <Link asChild>:
// expo-router reicht asChild ueber @radix-ui/react-slot durch, und dessen
// Slot-Merge macht aus einem Funktions- oder Array-"style" des Kindes ein
// leeres bzw. numerisch verschluesseltes Objekt und loescht damit dessen
// Styles. Der Mock bildet deshalb den Router nach, nicht mehr Link.
const mockPush = jest.fn();
jest.mock("expo-router", () => ({
  Stack: { Screen: () => null },
  useRouter: () => ({ push: mockPush }),
}));
jest.mock("../../api/client", () => ({
  startWishlistSavingsPlan: jest.fn(),
  updateSavingsGoal: jest.fn(),
  updateWishlistItem: jest.fn(),
}));
jest.mock("../../auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("../../household/HouseholdProvider", () => ({ useHousehold: jest.fn() }));
jest.mock("../../hooks/useGoals", () => ({
  useSavingsGoals: jest.fn(),
  useWishlistItems: jest.fn(),
}));

const mockUseAuth = useAuth as jest.MockedFunction<typeof useAuth>;
const mockUseHousehold = useHousehold as jest.MockedFunction<typeof useHousehold>;
const mockUseSavingsGoals = useSavingsGoals as jest.MockedFunction<typeof useSavingsGoals>;
const mockUseWishlistItems = useWishlistItems as jest.MockedFunction<typeof useWishlistItems>;
const mockStartPlan = startWishlistSavingsPlan as jest.MockedFunction<
  typeof startWishlistSavingsPlan
>;

const GOAL: SavingsGoal = {
  id: "goal-1",
  name: "Neues Handy",
  target_amount_minor: 70000,
  starting_amount_minor: 10000,
  current_amount_minor: 15000,
  target_date: "2027-08-01",
  monthly_required_minor: 4584,
  planning_mode: "target_date",
  monthly_contribution_minor: null,
  include_in_planning: true,
  priority: 1,
  effective_monthly_reserve_minor: 4584,
  projected_completion_date: "2027-08-01",
  planning_status: "on_track",
  account_id: null,
  category_id: null,
  status: "active",
  created_at: "2026-08-01T00:00:00Z",
};

const WISH: WishlistItem = {
  id: "wish-1",
  name: "Schreibtisch",
  price_minor: 50000,
  priority: 1,
  note: "Massivholz und mindestens 160 cm breit.",
  goal_id: null,
  status: "open",
  buy_traffic_light: "red",
  coverage_ratio: 0,
  shortfall_minor: 50000,
  earliest_purchase_date: null,
  created_at: "2026-08-01T00:00:00Z",
};

function queryResult<T>(data: T) {
  return {
    data,
    isLoading: false,
    isFetching: false,
    refetch: jest.fn(),
  } as unknown;
}

function findButton(tree: ReactTestRenderer, label: string): ReactTestInstance {
  const candidates = tree.root.findAll(
    (node) =>
      typeof node.props.onPress === "function" &&
      node.findAll((child) => child.type === Text && child.props.children === label).length > 0,
  );
  const button = candidates[candidates.length - 1];
  if (!button) {
    throw new Error(`Button "${label}" nicht gefunden.`);
  }
  return button;
}

function visibleText(tree: ReactTestRenderer): string {
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

describe("Sparen & Wünsche mobile", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUseAuth.mockReturnValue({ accessToken: "token" } as ReturnType<typeof useAuth>);
    mockUseHousehold.mockReturnValue({
      selectedHouseholdId: "household-1",
    } as unknown as ReturnType<typeof useHousehold>);
    mockUseSavingsGoals.mockReturnValue(queryResult([GOAL]) as ReturnType<typeof useSavingsGoals>);
    mockUseWishlistItems.mockReturnValue(
      queryResult([WISH]) as ReturnType<typeof useWishlistItems>,
    );
    mockStartPlan.mockResolvedValue({} as Awaited<ReturnType<typeof startWishlistSavingsPlan>>);
    (updateSavingsGoal as jest.Mock).mockResolvedValue({});
    (updateWishlistItem as jest.Mock).mockResolvedValue({});
  });

  it("zeigt echte Zielplanung und die Leistbarkeits-Ampel samt ausgeschriebenem Vorbehalt", () => {
    const client = new QueryClient();
    let tree: ReactTestRenderer;
    act(() => {
      tree = create(
        <QueryClientProvider client={client}>
          <SavingsWishlistScreen />
        </QueryClientProvider>,
      );
    });

    const text = visibleText(tree!);
    expect(text).toContain("Empfehlung");
    // Die Ampel bleibt (sie stand vor dem Redesign schon da und steht auch in
    // der Weboberflaeche), aber der Vorbehalt gehoert IN dieselbe Pille: eine
    // gruene Farbe allein liest sich sonst wie eine Zusage, obwohl dahinter
    // nur eine Rechnung aus Monatsbudget und Sparziel steckt.
    expect(text).toContain("Noch sparen · rechnerisch, keine Rücklage");
    expect(text).toContain("Massivholz und mindestens 160 cm breit.");
    // Die Deckung aus dem Monatsbudget wird bewusst NICHT als Betrag genannt -
    // sie waere keine reservierte Ruecklage.
    expect(text).not.toContain("gedeckt");
    act(() => tree!.unmount());
  });

  it("requires an explicit full-sheet confirmation before converting a wish", async () => {
    const client = new QueryClient();
    let tree: ReactTestRenderer;
    act(() => {
      tree = create(
        <QueryClientProvider client={client}>
          <SavingsWishlistScreen />
        </QueryClientProvider>,
      );
    });

    expect(mockStartPlan).not.toHaveBeenCalled();
    act(() => findButton(tree!, "Als Sparziel planen").props.onPress());
    expect(visibleText(tree!)).toContain("Aus dem Wunsch wird eine echte");
    expect(mockStartPlan).not.toHaveBeenCalled();

    await act(async () => {
      findButton(tree!, "Sparplan anlegen").props.onPress();
      await Promise.resolve();
    });

    expect(mockStartPlan).toHaveBeenCalledWith(
      "token",
      "household-1",
      "wish-1",
      expect.objectContaining({
        planning_mode: "target_date",
        include_in_planning: true,
        starting_amount_minor: 0,
      }),
    );
    act(() => tree!.unmount());
  });
});
