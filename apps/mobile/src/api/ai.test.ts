import {
  cancelClassificationRun,
  classifyWithAI,
  createClassificationRun,
  getAIHealth,
  getLatestClassificationRun,
} from "./client";

function makeRun(overrides: Record<string, unknown> = {}) {
  return {
    id: "run-1",
    status: "running",
    run_type: "classification",
    current_stage: "classifying",
    since_date: "2026-05-01",
    total_count: 42,
    processed_count: 17,
    success_count: 15,
    error_count: 2,
    error_message: null,
    created_at: "2026-08-01T00:00:00Z",
    completed_at: null,
    heartbeat_at: "2026-08-01T00:05:00Z",
    cancel_requested: false,
    is_stale: false,
    ...overrides,
  };
}

describe("AI API client", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("fetches AI health successfully", async () => {
    const mockHealth = {
      status: "ok",
      is_connected: true,
      base_url: "http://localhost:1234/v1",
      active_model: "qwen2.5-7b-instruct",
      canary_passed: true,
      models_count: 1,
      message: null,
    };

    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockHealth,
    } as Response);

    const result = await getAIHealth("fake-token", "household-123");
    expect(result).toEqual(mockHealth);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/v1/ai/health"),
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer fake-token",
          "X-Household-Id": "household-123",
        }),
      }),
    );
  });

  it("classifies with AI successfully", async () => {
    const mockResult = {
      category_id: "cat-groceries",
      category_name: "Lebensmittel",
      confidence: 0.92,
      reasoning: "Matched Edeka to Groceries",
    };

    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockResult,
    } as Response);

    const input = {
      transaction_id: "tx-123",
      payee_raw: "EDEKA SAGT DANKE",
      purpose: "Einkauf",
      amount_minor: -3450,
    };

    const result = await classifyWithAI("fake-token", "household-123", input);
    expect(result).toEqual(mockResult);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/v1/ai/classify"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(input),
        headers: expect.objectContaining({
          Authorization: "Bearer fake-token",
          "X-Household-Id": "household-123",
          "Content-Type": "application/json",
        }),
      }),
    );
  });

  // Ausbaustufe 0 des KI-Plans: der laufende Lauf wird vom Server geholt,
  // nicht in der App gemerkt - sonst ist er nach einem App-Neustart weg.
  it("liest den juengsten Lauf des Haushalts", async () => {
    const run = makeRun();
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [run],
    } as Response);

    const result = await getLatestClassificationRun("fake-token", "household-123");
    expect(result).toEqual(run);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/v1/ai/classify-runs?limit=1"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("liefert null, wenn der Haushalt noch nie einen Lauf hatte", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [],
    } as Response);

    await expect(getLatestClassificationRun("fake-token", "household-123")).resolves.toBeNull();
  });

  it("schickt beim Start einen Idempotenzschluessel mit", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => makeRun({ status: "pending" }),
    } as Response);

    await createClassificationRun("fake-token", "household-123", "req-42");
    const [, init] = (globalThis.fetch as jest.Mock).mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ request_id: "req-42" });
  });

  it("bricht einen Lauf ueber den Cancel-Endpunkt ab", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => makeRun({ cancel_requested: true }),
    } as Response);

    const result = await cancelClassificationRun("fake-token", "household-123", "run-1");
    expect(result.cancel_requested).toBe(true);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/v1/ai/classify-runs/run-1/cancel"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
