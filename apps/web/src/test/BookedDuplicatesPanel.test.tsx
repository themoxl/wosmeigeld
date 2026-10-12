import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BookedDuplicatesPanel } from "../components/BookedDuplicatesPanel";
import {
  assessBookedDuplicates,
  resolveBookedDuplicatesBulk,
  scanBookedDuplicates,
} from "../lib/api";
import type {
  BookedDuplicateAssessment,
  BookedDuplicateAssessmentResult,
  BookedDuplicatePairInput,
  ReviewInboxResponse,
  ReviewItemBookedDuplicate,
} from "../lib/types";

const state = vi.hoisted(() => ({ role: "owner", householdId: "household" }));
vi.mock("../auth/AuthProvider", () => ({ useAuth: () => ({ accessToken: "token" }) }));
vi.mock("../household/HouseholdProvider", () => ({
  useHousehold: () => ({
    selectedHouseholdId: state.householdId,
    selectedHousehold: { role: state.role },
  }),
}));
vi.mock("../lib/api", () => ({
  assessBookedDuplicates: vi.fn(),
  scanBookedDuplicates: vi.fn(),
  resolveBookedDuplicatesBulk: vi.fn(),
}));

const pair: ReviewItemBookedDuplicate = {
  match: "duplicate_possible",
  first: {
    transaction_id: "older",
    version: 2,
    booked_date: "2026-04-02",
    amount_minor: -1999,
    currency: "EUR",
    payee_label: "Musterkasse AG",
    purpose: "Beitrag April",
    account_name: "Girokonto",
    import_filename: "april.csv",
  },
  second: {
    transaction_id: "newer",
    version: 3,
    booked_date: "2026-04-02",
    amount_minor: -1999,
    currency: "EUR",
    payee_label: null,
    purpose: "Beitrag April",
    account_name: "Girokonto",
    import_filename: "april.sta",
  },
};
const exactPair: ReviewItemBookedDuplicate = {
  match: "duplicate_exact",
  first: {
    ...pair.first,
    transaction_id: "older-exact",
    payee_label: "Stadtwerke",
    import_filename: "strom.csv",
  },
  second: {
    ...pair.second,
    transaction_id: "newer-exact",
    payee_label: "Stadtwerke",
    import_filename: "strom.sta",
  },
};
const emptyInbox: ReviewInboxResponse = {
  total_count: 8,
  unclassified_open_count: 8,
  unclassified_total_count: 8,
  unclassified_offset: 0,
  unclassified_limit: 50,
  unclassified: [],
  possible_duplicates: [],
  transfer_candidates: [],
  recurring_suggestions: [],
  booked_duplicates: [],
  booked_duplicate_count: 0,
};

function mount(items: ReviewItemBookedDuplicate[] = [pair]) {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const view = (next: ReviewItemBookedDuplicate[]) => (
    <QueryClientProvider client={queryClient}>
      <BookedDuplicatesPanel items={next} total={next.length} />
    </QueryClientProvider>
  );
  const rendered = render(view(items));
  return {
    queryClient,
    unmount: rendered.unmount,
    rerender: (next: ReviewItemBookedDuplicate[]) => rendered.rerender(view(next)),
  };
}

const rowChecks = () => screen.getAllByRole("checkbox", { name: /^Paar auswählen:/ });
const selectAll = () => screen.getByRole("checkbox", { name: "Alle sichtbaren Paare auswählen" });
const actionSelect = () => screen.getByRole("combobox", { name: "Aktion für ausgewählte Paare" });

afterEach(() => {
  vi.resetAllMocks();
  state.role = "owner";
  state.householdId = "household";
});

describe("Duplikate gemeinsam bearbeiten", () => {
  it("submits selected pairs in one request without opening any dialog", async () => {
    vi.mocked(resolveBookedDuplicatesBulk).mockResolvedValue({
      processed_count: 2,
      archived_count: 2,
      kept_count: 0,
    });
    const { queryClient } = mount([pair, exactPair]);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    expect(selectAll()).not.toBeChecked();
    expect(screen.getByRole("button", { name: "0 Buchungen entfernen" })).toBeDisabled();
    expect(screen.getByText("april.csv")).toBeInTheDocument();
    expect(screen.getByText("april.sta")).toBeInTheDocument();
    await userEvent.click(selectAll());
    expect(screen.getAllByText("Entfernen")).toHaveLength(2);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(resolveBookedDuplicatesBulk).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "2 Buchungen entfernen" }));
    await waitFor(() => expect(resolveBookedDuplicatesBulk).toHaveBeenCalledTimes(1));
    expect(resolveBookedDuplicatesBulk).toHaveBeenCalledWith("token", "household", {
      items: [
        {
          first_transaction_id: "older",
          first_version: 2,
          second_transaction_id: "newer",
          second_version: 3,
          action: "archive_second",
        },
        {
          first_transaction_id: "older-exact",
          first_version: 2,
          second_transaction_id: "newer-exact",
          second_version: 3,
          action: "archive_second",
        },
      ],
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "2 Buchungen entfernt · 2 Paare erledigt.",
    );
    expect(screen.getByText("0 ausgewählt")).toBeInTheDocument();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["review-inbox", "household"] });
    expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({ refetchType: "none" }));
    expect(invalidate).not.toHaveBeenCalledWith();
  });

  it("selects only filtered rows and exposes selections hidden by another filter", async () => {
    mount([pair, exactPair]);
    await userEvent.click(rowChecks()[0]!);
    expect(selectAll()).toBePartiallyChecked();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Übereinstimmung" }),
      "duplicate_exact",
    );
    expect(rowChecks()).toHaveLength(1);
    expect(selectAll()).not.toBeChecked();
    expect(screen.getByText("davon 1 ausgeblendet")).toBeInTheDocument();
    await userEvent.click(selectAll());
    expect(screen.getByText("2 ausgewählt")).toBeInTheDocument();
    await userEvent.click(selectAll());
    expect(screen.getByText("1 ausgewählt")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Auswahl aufheben" }));
    expect(screen.getByText("0 ausgewählt")).toBeInTheDocument();
  });

  it("searches source filenames and expands purposes inline", async () => {
    mount([pair, exactPair]);
    await userEvent.type(
      screen.getByRole("searchbox", { name: "In Duplikaten suchen" }),
      "strom.sta",
    );
    expect(rowChecks()).toHaveLength(1);
    expect(screen.queryByText("april.sta")).not.toBeInTheDocument();
    const row = rowChecks()[0]!.closest("tr")!;
    await userEvent.click(within(row).getAllByText("Verwendungszweck")[0]!);
    expect(row.querySelector("details")).toHaveAttribute("open");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("preserves selection and versions after a rejected batch", async () => {
    vi.mocked(resolveBookedDuplicatesBulk).mockRejectedValue(
      new Error("Eine Buchung wurde geändert. Bitte erneut prüfen."),
    );
    mount([pair, exactPair]);
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "2 Buchungen entfernen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Eine Buchung wurde geändert.");
    expect(rowChecks().every((checkbox) => (checkbox as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByRole("button", { name: "2 Buchungen entfernen" })).toBeEnabled();
  });

  it("clears changed versions without silently selecting their replacement", async () => {
    const { rerender } = mount([pair, exactPair]);
    await userEvent.click(selectAll());
    rerender([{ ...pair, second: { ...pair.second, version: 4 } }, exactPair]);
    expect(rowChecks()[0]).not.toBeChecked();
    expect(rowChecks()[1]).toBeChecked();
    expect(screen.getByText("1 ausgewählt")).toBeInTheDocument();
  });

  it("can keep all selected pairs without archiving", async () => {
    vi.mocked(resolveBookedDuplicatesBulk).mockResolvedValue({
      processed_count: 2,
      archived_count: 0,
      kept_count: 2,
    });
    mount([pair, exactPair]);
    await userEvent.click(selectAll());
    await userEvent.selectOptions(actionSelect(), "keep_both");
    expect(screen.queryByText("Entfernen bei Auswahl")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "2 Paare behalten" }));
    await waitFor(() =>
      expect(resolveBookedDuplicatesBulk).toHaveBeenCalledWith("token", "household", {
        items: [
          expect.objectContaining({ action: "keep_both" }),
          expect.objectContaining({ action: "keep_both" }),
        ],
      }),
    );
  });

  it("shows and submits earlier bookings when the chosen action changes", async () => {
    vi.mocked(resolveBookedDuplicatesBulk).mockResolvedValue({
      processed_count: 1,
      archived_count: 1,
      kept_count: 0,
    });
    mount();
    await userEvent.click(selectAll());
    await userEvent.selectOptions(actionSelect(), "archive_first");
    expect(screen.getByText("april.csv").closest("td")).toHaveClass("duplicates-booking--remove");
    expect(screen.getByText("april.sta").closest("td")).toHaveClass("duplicates-booking--keep");
    await userEvent.click(screen.getByRole("button", { name: "1 Buchung entfernen" }));
    await waitFor(() =>
      expect(resolveBookedDuplicatesBulk).toHaveBeenCalledWith("token", "household", {
        items: [
          expect.objectContaining({ action: "archive_first", first_transaction_id: "older" }),
        ],
      }),
    );
  });

  it("counts a triple copy only once per removed booking and marks overlapping outcomes", async () => {
    const third = { ...pair.second, transaction_id: "third", import_filename: "april-extra.csv" };
    mount([
      pair,
      { first: pair.first, second: third, match: "duplicate_exact" },
      { first: pair.second, second: third, match: "duplicate_exact" },
    ]);
    await userEvent.click(selectAll());
    expect(screen.getByText("3 ausgewählt")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "2 Buchungen entfernen" })).toBeEnabled();
    const lastRow = rowChecks()[2]!.closest("tr")!;
    expect(within(lastRow).getAllByText("Entfernen")).toHaveLength(2);
    expect(within(lastRow).queryByText("Bleibt")).not.toBeInTheDocument();
  });

  it("blocks removing every booking in a contradictory group inline", async () => {
    mount([pair, { first: pair.second, second: pair.first, match: "duplicate_exact" }]);
    await userEvent.click(selectAll());
    expect(screen.getByRole("alert")).toHaveTextContent(
      "alle Buchungen einer Duplikatgruppe entfernen",
    );
    expect(screen.getByRole("button", { name: "2 Buchungen entfernen" })).toBeDisabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("disables selection and actions while a batch is in flight", async () => {
    vi.mocked(resolveBookedDuplicatesBulk).mockImplementation(() => new Promise(() => {}));
    mount();
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "1 Buchung entfernen" }));
    expect(screen.getByRole("button", { name: "Bearbeite Auswahl…" })).toBeDisabled();
    expect(selectAll()).toBeDisabled();
    expect(rowChecks()[0]).toBeDisabled();
    expect(actionSelect()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Duplikate erneut prüfen" })).toBeDisabled();
  });

  it("patches every matching inbox cache after one scan without requesting another scan", async () => {
    vi.mocked(scanBookedDuplicates).mockResolvedValue({
      scanned_count: 471,
      total_count: 1,
      items: [pair],
    });
    const { queryClient } = mount([]);
    queryClient.setQueryData(["review-inbox", "household", "", 0], emptyInbox);
    queryClient.setQueryData(["review-inbox", "household", "Strom", 0], {
      ...emptyInbox,
      total_count: 9,
      booked_duplicate_count: 1,
      booked_duplicates: [exactPair],
    });
    queryClient.setQueryData(["review-inbox", "another-household"], emptyInbox);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await userEvent.click(screen.getByRole("button", { name: "Duplikate erneut prüfen" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "471 Buchungen geprüft · 1 mögliches Duplikatpaar gefunden.",
    );
    expect(scanBookedDuplicates).toHaveBeenCalledTimes(1);
    expect(invalidate).not.toHaveBeenCalled();
    for (const key of [
      ["review-inbox", "household", "", 0],
      ["review-inbox", "household", "Strom", 0],
    ]) {
      expect(queryClient.getQueryData(key)).toEqual(
        expect.objectContaining({
          booked_duplicates: [pair],
          booked_duplicate_count: 1,
          total_count: 9,
        }),
      );
    }
    expect(queryClient.getQueryData(["review-inbox", "another-household"])).toEqual(emptyInbox);
  });

  it("can scan an empty inbox and report zero matches explicitly", async () => {
    vi.mocked(scanBookedDuplicates).mockResolvedValue({
      scanned_count: 471,
      total_count: 0,
      items: [],
    });
    mount([]);
    await userEvent.click(screen.getByRole("button", { name: "Duplikate erneut prüfen" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "471 Buchungen geprüft · Keine möglichen Duplikate gefunden.",
    );
  });

  it("keeps failed scans visible as errors rather than claiming zero matches", async () => {
    vi.mocked(scanBookedDuplicates).mockRejectedValue(new Error("Prüfung fehlgeschlagen"));
    mount([]);
    await userEvent.click(screen.getByRole("button", { name: "Duplikate erneut prüfen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Prüfung fehlgeschlagen");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("lets viewers scan and filter without offering selection or write actions", () => {
    state.role = "viewer";
    mount();
    expect(screen.getByRole("button", { name: "Duplikate erneut prüfen" })).toBeEnabled();
    expect(screen.getByRole("combobox", { name: "Übereinstimmung" })).toBeEnabled();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "Aktion für ausgewählte Paare" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /entfernen|behalten/ })).not.toBeInTheDocument();
  });
});

function assessmentPair(item: ReviewItemBookedDuplicate): BookedDuplicatePairInput {
  return {
    first_transaction_id: item.first.transaction_id,
    first_version: item.first.version,
    second_transaction_id: item.second.transaction_id,
    second_version: item.second.version,
  };
}

function manyPairs(count: number): ReviewItemBookedDuplicate[] {
  return Array.from({ length: count }, (_, index) => ({
    ...pair,
    first: { ...pair.first, transaction_id: `first-${index}` },
    second: { ...pair.second, transaction_id: `second-${index}` },
  }));
}

function assessmentResult(
  pairs: BookedDuplicatePairInput[],
  decide: (index: number) => BookedDuplicateAssessment["action"] = () => "archive_second",
): BookedDuplicateAssessmentResult & { assessments: BookedDuplicateAssessment[] } {
  return {
    available: true,
    reason: "ok",
    message: "Bewertung abgeschlossen",
    model_name: "test-model",
    prompt_version: "v1",
    minimum_confidence: 0.9,
    assessments: pairs.map((item, index) => ({
      ...item,
      verdict: decide(index) === "keep_both" ? "different" : decide(index) ? "same" : "unsure",
      reason: "Datum, Betrag und Referenz wurden verglichen.",
      confidence: decide(index) ? 0.99 : 0.5,
      action: decide(index),
    })),
  };
}

describe("KI bewertet ausgewählte Duplikatpaare", () => {
  it("assesses all selections sequentially in chunks of 25 and reports progress without writes", async () => {
    const items = manyPairs(26);
    let finishFirst!: (result: BookedDuplicateAssessmentResult) => void;
    let finishSecond!: (result: BookedDuplicateAssessmentResult) => void;
    vi.mocked(assessBookedDuplicates)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishSecond = resolve;
          }),
      );
    const { queryClient } = mount(items);
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    expect(assessBookedDuplicates).toHaveBeenCalledTimes(1);
    expect(vi.mocked(assessBookedDuplicates).mock.calls[0]![2].items).toHaveLength(25);
    expect(screen.getByRole("status")).toHaveTextContent("0 / 26 bewertet");
    await act(async () => finishFirst(assessmentResult(items.slice(0, 25).map(assessmentPair))));
    expect(assessBookedDuplicates).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("status")).toHaveTextContent("25 / 26 bewertet");
    expect(vi.mocked(assessBookedDuplicates).mock.calls[1]![2].items).toEqual([
      assessmentPair(items[25]!),
    ]);
    await act(async () => finishSecond(assessmentResult([assessmentPair(items[25]!)])));
    expect(screen.getByRole("status")).toHaveTextContent(
      "26 / 26 bewertet · KI-Prüfung abgeschlossen",
    );
    expect(actionSelect()).toHaveValue("ai");
    expect(screen.getByRole("button", { name: "26 KI-Entscheidungen übernehmen" })).toBeEnabled();
    expect(resolveBookedDuplicatesBulk).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stops after the current request while retaining completed recommendations", async () => {
    const items = manyPairs(51);
    let finishSecond!: (result: BookedDuplicateAssessmentResult) => void;
    vi.mocked(assessBookedDuplicates)
      .mockResolvedValueOnce(assessmentResult(items.slice(0, 25).map(assessmentPair)))
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishSecond = resolve;
          }),
      );
    mount(items);
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    await waitFor(() => expect(assessBookedDuplicates).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole("button", { name: "KI-Prüfung stoppen" }));
    expect(screen.getByRole("button", { name: "Stoppt nach aktuellem Paket…" })).toBeDisabled();
    await act(async () => finishSecond(assessmentResult(items.slice(25, 50).map(assessmentPair))));
    expect(assessBookedDuplicates).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("status")).toHaveTextContent("50 / 51 bewertet · KI-Prüfung beendet");
    expect(screen.getByRole("button", { name: "50 KI-Entscheidungen übernehmen" })).toBeEnabled();
    expect(screen.getByText(/1 unklare oder unbewertete Paare bleiben offen/)).toBeInTheDocument();
  });

  it("applies only concrete mixed recommendations in one batch and leaves unclear pairs selected", async () => {
    const items = manyPairs(4);
    vi.mocked(assessBookedDuplicates).mockResolvedValue(
      assessmentResult(
        items.map(assessmentPair),
        (index) =>
          ["archive_first", "archive_second", "keep_both", null][
            index
          ] as BookedDuplicateAssessment["action"],
      ),
    );
    vi.mocked(resolveBookedDuplicatesBulk).mockResolvedValue({
      processed_count: 3,
      archived_count: 2,
      kept_count: 1,
    });
    mount(items);
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    const apply = await screen.findByRole("button", { name: "3 KI-Entscheidungen übernehmen" });
    expect(screen.getByText("3 von 4 KI-Entscheidungen anwendbar")).toBeInTheDocument();
    expect(
      within(rowChecks()[0]!.closest("tr")!).getByText("Früher erfasste entfernen"),
    ).toBeInTheDocument();
    expect(rowChecks()[0]!.closest("tr")!.querySelectorAll(".duplicates-booking--remove")[0]).toBe(
      rowChecks()[0]!.closest("tr")!.querySelectorAll(".duplicates-booking")[0],
    );
    expect(screen.getByText("Unklar")).toBeInTheDocument();
    await userEvent.click(apply);
    expect(resolveBookedDuplicatesBulk).toHaveBeenCalledTimes(1);
    expect(resolveBookedDuplicatesBulk).toHaveBeenCalledWith("token", "household", {
      items: [
        { ...assessmentPair(items[0]!), action: "archive_first" },
        { ...assessmentPair(items[1]!), action: "archive_second" },
        { ...assessmentPair(items[2]!), action: "keep_both" },
      ],
    });
    await waitFor(() => expect(screen.getByText("1 ausgewählt")).toBeInTheDocument());
    expect(rowChecks()[3]).toBeChecked();
    expect(rowChecks()[0]).not.toBeChecked();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("never infers an action from confidence or verdict and ignores stale response versions", async () => {
    const items = manyPairs(3);
    const result = assessmentResult(items.map(assessmentPair), () => null);
    result.assessments[0] = { ...result.assessments[0]!, verdict: "same", confidence: 1 };
    result.assessments[1] = { ...result.assessments[1]!, verdict: "same", confidence: 0.4 };
    result.assessments[2] = {
      ...result.assessments[2]!,
      first_version: 999,
      action: "archive_first",
    };
    vi.mocked(assessBookedDuplicates).mockResolvedValue(result);
    mount(items);
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    expect(
      await screen.findByRole("button", { name: "0 KI-Entscheidungen übernehmen" }),
    ).toBeDisabled();
    expect(resolveBookedDuplicatesBulk).not.toHaveBeenCalled();
  });

  it("invalidates a recommendation immediately when its displayed version changes", async () => {
    vi.mocked(assessBookedDuplicates).mockResolvedValue(assessmentResult([assessmentPair(pair)]));
    const { rerender } = mount();
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    expect(
      await screen.findByRole("button", { name: "1 KI-Entscheidungen übernehmen" }),
    ).toBeEnabled();
    rerender([{ ...pair, first: { ...pair.first, version: 7 } }]);
    await userEvent.click(selectAll());
    expect(screen.getByRole("button", { name: "0 KI-Entscheidungen übernehmen" })).toBeDisabled();
    expect(screen.getByText("Noch nicht bewertet")).toBeInTheDocument();
  });

  it("retains partial results on failure and allows manual actions afterwards", async () => {
    const items = manyPairs(26);
    vi.mocked(assessBookedDuplicates)
      .mockResolvedValueOnce(assessmentResult(items.slice(0, 25).map(assessmentPair)))
      .mockRejectedValueOnce(new Error("KI-Verbindung unterbrochen"));
    mount(items);
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("KI-Verbindung unterbrochen");
    expect(screen.getByRole("button", { name: "25 KI-Entscheidungen übernehmen" })).toBeEnabled();
    await userEvent.selectOptions(actionSelect(), "archive_second");
    expect(screen.getByRole("button", { name: "26 Buchungen entfernen" })).toBeEnabled();
  });

  it("stops on service unavailability without applying recommendations", async () => {
    const items = manyPairs(26);
    vi.mocked(assessBookedDuplicates).mockResolvedValue({
      ...assessmentResult([]),
      available: false,
      message: "KI ist nicht aktiviert.",
    });
    mount(items);
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("KI ist nicht aktiviert.");
    expect(assessBookedDuplicates).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "26 Buchungen entfernen" })).toBeEnabled();
    expect(resolveBookedDuplicatesBulk).not.toHaveBeenCalled();
  });

  it("counts only really assessed pairs of a partial result and names the reason", async () => {
    const items = manyPairs(30);
    const partial = assessmentResult(items.slice(0, 25).map(assessmentPair));
    // Der Server hat nach dem ersten Stapel abgebrochen: die uebrigen Paare
    // kommen als "Nicht bewertet: …" zurueck - kein Urteil.
    for (let index = 10; index < 25; index += 1) {
      partial.assessments[index] = {
        ...partial.assessments[index]!,
        verdict: "unsure",
        confidence: 0,
        action: null,
        reason:
          "Nicht bewertet: Der Modellaufruf hat das Zeitbudget überschritten. Bitte manuell prüfen.",
      };
    }
    vi.mocked(assessBookedDuplicates).mockResolvedValue({
      ...partial,
      reason: "model_timeout",
      message:
        "10 von 25 Paaren bewertet (aufgeteilt in 2 Stapel nach Platz im Kontextfenster), 10 Entscheidungen übernehmbar. Die übrigen nicht: Der Modellaufruf hat das Zeitbudget überschritten.",
    });
    mount(items);
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("10 von 25 Paaren bewertet");
    expect(alert).toHaveTextContent("Zeitbudget überschritten");
    expect(within(alert).getByRole("link", { name: /Einstellungen › KI/ })).toHaveAttribute(
      "href",
      "/einstellungen?tab=ki",
    );
    expect(screen.getByRole("status")).toHaveTextContent("10 / 30 bewertet · KI-Prüfung beendet");
    // Derselbe Grund traefe das naechste Paket - es wird nicht mehr gefragt.
    expect(assessBookedDuplicates).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText("Nicht bewertet")).toHaveLength(15);
    expect(screen.getByRole("button", { name: "10 KI-Entscheidungen übernehmen" })).toBeEnabled();
  });

  it("names the reason when the AI is unavailable before any pair was assessed", async () => {
    vi.mocked(assessBookedDuplicates).mockResolvedValue({
      ...assessmentResult([]),
      available: false,
      reason: "ai_disabled",
      message: "Die KI ist für diesen Haushalt ausgeschaltet.",
    });
    mount(manyPairs(2));
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Die KI ist für diesen Haushalt ausgeschaltet.");
    expect(
      within(alert).getByRole("link", { name: "KI unter Einstellungen › KI einschalten" }),
    ).toBeInTheDocument();
    expect(alert).not.toHaveTextContent("ai_disabled");
  });

  it("prevents removing a booking that another recommendation explicitly keeps", async () => {
    const items = [pair, { ...pair, second: { ...pair.second, transaction_id: "third" } }];
    vi.mocked(assessBookedDuplicates).mockResolvedValue(
      assessmentResult(items.map(assessmentPair), (index) =>
        index === 0 ? "archive_first" : "keep_both",
      ),
    );
    mount(items);
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "zugleich entfernt und mit „Beide behalten“ erhalten",
    );
    expect(screen.getByRole("button", { name: "2 KI-Entscheidungen übernehmen" })).toBeDisabled();
  });

  it("discards in-flight results and stops future chunks when the household changes", async () => {
    const items = manyPairs(26);
    let finish!: (result: BookedDuplicateAssessmentResult) => void;
    vi.mocked(assessBookedDuplicates).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { rerender } = mount(items);
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    state.householdId = "another-household";
    rerender(items);
    await act(async () => finish(assessmentResult(items.slice(0, 25).map(assessmentPair))));
    expect(assessBookedDuplicates).toHaveBeenCalledTimes(1);
    expect(screen.getByText("0 ausgewählt")).toBeInTheDocument();
    expect(screen.queryByText("Doppelt")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("stops future chunks after unmount", async () => {
    const items = manyPairs(26);
    let finish!: (result: BookedDuplicateAssessmentResult) => void;
    vi.mocked(assessBookedDuplicates).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { unmount } = mount(items);
    await userEvent.click(selectAll());
    await userEvent.click(screen.getByRole("button", { name: "KI bewerten" }));
    unmount();
    await act(async () => finish(assessmentResult(items.slice(0, 25).map(assessmentPair))));
    expect(assessBookedDuplicates).toHaveBeenCalledTimes(1);
  });
});
