/// <reference types="jest" />
import {
  createReserveDeposit,
  createReserveReallocation,
  createReserveWithdrawal,
  getReserveOverview,
  previewReserveDistribution,
  skipReserveMonth,
  startWishlistSavingsPlan,
  undoSkipReserveMonth,
} from "./client";
import { getApiBaseUrl, setApiBaseUrl } from "./config";

type FetchMock = jest.Mock<Promise<Response>, [string, RequestInit?]>;

function responseFor(init?: RequestInit): Response {
  const status = init?.method === "DELETE" ? 204 : 200;
  return {
    ok: true,
    status,
    json: async () => ({}),
  } as Response;
}

describe("typed reserve API client", () => {
  const originalFetch = globalThis.fetch;
  const originalBaseUrl = getApiBaseUrl();
  let fetchMock: FetchMock;

  beforeEach(() => {
    setApiBaseUrl("http://reserve-api.test");
    fetchMock = jest.fn(async (_url, init) => responseFor(init)) as FetchMock;
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterAll(() => {
    setApiBaseUrl(originalBaseUrl);
    globalThis.fetch = originalFetch;
  });

  it("uses the household-scoped overview path and forwards its AbortSignal", async () => {
    const controller = new AbortController();

    await getReserveOverview("token", "household-1", 2026, 8, controller.signal);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://reserve-api.test/v1/reserves/overview?year=2026&month=8",
      expect.objectContaining({
        method: "GET",
        signal: controller.signal,
        headers: expect.objectContaining({
          Authorization: "Bearer token",
          "X-Household-Id": "household-1",
        }),
      }),
    );
  });

  it.each([
    ["deposit", createReserveDeposit, "/v1/reserves/deposits", "POST"],
    ["withdrawal", createReserveWithdrawal, "/v1/reserves/withdrawals", "POST"],
    ["reallocation", createReserveReallocation, "/v1/reserves/reallocations", "POST"],
    ["skip", skipReserveMonth, "/v1/reserves/month-decisions/skip", "PUT"],
    ["undo skip", undoSkipReserveMonth, "/v1/reserves/month-decisions/skip", "DELETE"],
    [
      "distribution preview",
      previewReserveDistribution,
      "/v1/reserves/distribution-preview",
      "POST",
    ],
  ])("sends %s only to its typed ledger endpoint", async (_label, call, path, method) => {
    const body = { pot_id: "pot-1", year: 2026, month: 8 };

    await call("token", "household-1", body as never);

    expect(fetchMock).toHaveBeenCalledWith(
      `http://reserve-api.test${path}`,
      expect.objectContaining({
        method,
        body: JSON.stringify(body),
        headers: expect.objectContaining({
          Authorization: "Bearer token",
          "X-Household-Id": "household-1",
          "Content-Type": "application/json",
        }),
      }),
    );
  });

  it("creates a savings plan from a wishlist item with the planning payload unchanged", async () => {
    const input = {
      planning_mode: "monthly_amount" as const,
      target_date: null,
      monthly_contribution_minor: 5000,
      starting_amount_minor: 12000,
      include_in_planning: true,
    };

    await startWishlistSavingsPlan("token", "household-1", "wish-1", input);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://reserve-api.test/v1/wishlist/wish-1/start-savings-plan",
      expect.objectContaining({ method: "POST", body: JSON.stringify(input) }),
    );
  });
});
