/// <reference types="jest" />
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import {
  createReserveDeposit,
  createReserveReallocation,
  createReserveWithdrawal,
  getReserveOverview,
  previewReserveDistribution,
  skipReserveMonth,
  undoSkipReserveMonth,
} from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { useReserveActions, useReserveOverview } from "./useReserves";

jest.mock("../api/client", () => ({
  createReserveDeposit: jest.fn(),
  createReserveReallocation: jest.fn(),
  createReserveWithdrawal: jest.fn(),
  getReserveOverview: jest.fn(),
  previewReserveDistribution: jest.fn(),
  skipReserveMonth: jest.fn(),
  undoSkipReserveMonth: jest.fn(),
}));
jest.mock("../auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("../household/HouseholdProvider", () => ({ useHousehold: jest.fn() }));

const mockUseAuth = useAuth as jest.MockedFunction<typeof useAuth>;
const mockUseHousehold = useHousehold as jest.MockedFunction<typeof useHousehold>;
const mockDeposit = createReserveDeposit as jest.MockedFunction<typeof createReserveDeposit>;
const mockOverview = getReserveOverview as jest.MockedFunction<typeof getReserveOverview>;

let latestActions: ReturnType<typeof useReserveActions> | null = null;

function ActionsHarness() {
  latestActions = useReserveActions(2026, 8);
  return null;
}

function OverviewHarness({ visible }: { visible: boolean }) {
  useReserveOverview(2026, 8, visible);
  return null;
}

function renderWithClient(node: React.ReactElement, client: QueryClient): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
  });
  return tree!;
}

describe("useReserves", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    latestActions = null;
    mockUseAuth.mockReturnValue({ accessToken: "token" } as ReturnType<typeof useAuth>);
    mockUseHousehold.mockReturnValue({
      selectedHouseholdId: "household-1",
      selectedHousehold: { id: "household-1", role: "editor" },
    } as unknown as ReturnType<typeof useHousehold>);
  });

  it("does not load the ledger while the reserve segment is hidden", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const tree = renderWithClient(<OverviewHarness visible={false} />, client);

    await act(async () => Promise.resolve());

    expect(mockOverview).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });

  it("keeps a ledger write online-only and invalidates all dependent planning views", async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const invalidate = jest.spyOn(client, "invalidateQueries").mockResolvedValue(undefined);
    mockDeposit.mockResolvedValue({} as Awaited<ReturnType<typeof createReserveDeposit>>);
    const tree = renderWithClient(<ActionsHarness />, client);

    await act(async () => {
      await latestActions!.deposit.mutateAsync({
        total_minor: 5000,
        allocations: [{ pot_id: "pot-1", amount_minor: 5000 }],
        occurred_on: "2026-08-10",
        note: "Bestätigt",
      });
    });

    const mutationOptions = client
      .getMutationCache()
      .getAll()
      .map((mutation) => mutation.options.networkMode);
    expect(mutationOptions).toEqual(["online"]);

    expect(mockDeposit).toHaveBeenCalledWith("token", "household-1", {
      total_minor: 5000,
      allocations: [{ pot_id: "pot-1", amount_minor: 5000 }],
      occurred_on: "2026-08-10",
      note: "Bestätigt",
    });
    expect(invalidate.mock.calls.map(([options]) => options?.queryKey)).toEqual(
      expect.arrayContaining([
        ["reserves", "household-1"],
        ["budget", "household-1"],
        ["goals", "household-1"],
        ["contracts", "household-1"],
        ["contract-summary", "household-1"],
      ]),
    );
    act(() => tree.unmount());
  });

  it("removes write capability for household viewers", () => {
    mockUseHousehold.mockReturnValue({
      selectedHouseholdId: "household-1",
      selectedHousehold: { id: "household-1", role: "viewer" },
    } as unknown as ReturnType<typeof useHousehold>);
    const client = new QueryClient();
    const tree = renderWithClient(<ActionsHarness />, client);

    expect(latestActions?.canEdit).toBe(false);
    act(() => tree.unmount());
  });
});

void createReserveReallocation;
void createReserveWithdrawal;
void previewReserveDistribution;
void skipReserveMonth;
void undoSkipReserveMonth;
