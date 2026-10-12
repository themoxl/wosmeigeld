/// <reference types="jest" />
import { Text } from "react-native";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { ReviewItemDuplicate } from "../../api/types";
import { useAcceptDuplicatesBulk, useReviewInbox } from "../../hooks/useReview";
import ReviewScreen from "../../../app/mehr/review";

// Die Dublettenliste auf dem Handy. Sie war lange unbrauchbar, und zwar aus
// zwei Gruenden, die zusammengehoeren:
//
// 1. Sie nannte nur die NEUE Zeile und verlangte "Dublette oder nicht", ohne
//    zu sagen, wovon. Wer das beantworten wollte, musste in "Alle Buchungen"
//    nachschlagen - am Telefon noch muehsamer als am Rechner.
// 2. Jede Zeile war einzeln zu bestaetigen. Ein Import bringt sie zu Dutzenden.

jest.mock("../../hooks/useReview", () => ({
  useReviewInbox: jest.fn(),
  useAcceptReview: jest.fn(),
  useCorrectReview: jest.fn(),
  useAcceptDuplicatesBulk: jest.fn(),
}));

jest.mock("../../hooks/useCategories", () => ({ useCategories: jest.fn() }));

// Muster aus SavingsWishlistScreen.test.tsx.
jest.mock("../../auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("../../household/HouseholdProvider", () => ({ useHousehold: jest.fn() }));

// Vollstaendig ersetzt, samt der reinen Hilfsfunktion: ein Teilmock ueber
// requireActual zoege die echten Hooks mit herein, und die brauchen einen
// QueryClient und eine Anmeldung. Beides hat mit Dubletten nichts zu tun - der
// Bildschirm scheiterte dann an einer Stelle, die dieser Test nicht prueft.
jest.mock("../../hooks/useAI", () => ({
  useAIDecisions: jest.fn(),
  useCancelClassificationRun: jest.fn(),
  useClassifyWithAI: jest.fn(),
  useLatestClassificationRun: jest.fn(),
  useStartClassificationRun: jest.fn(),
  isClassificationRunActive: () => false,
}));

jest.mock("expo-router", () => ({
  Stack: { Screen: () => null },
  useRouter: () => ({ push: jest.fn() }),
}));

const mockUseReviewInbox = useReviewInbox as jest.Mock;
const mockUseBulk = useAcceptDuplicatesBulk as jest.Mock;

// eslint-disable-next-line @typescript-eslint/no-require-imports
const reviewHooks = require("../../hooks/useReview");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const categoryHooks = require("../../hooks/useCategories");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const aiHooks = require("../../hooks/useAI");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const authModule = require("../../auth/AuthProvider");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const householdModule = require("../../household/HouseholdProvider");

function query(overrides: Record<string, unknown> = {}) {
  return {
    data: undefined,
    isLoading: false,
    isPending: false,
    isError: false,
    error: null,
    refetch: jest.fn(),
    ...overrides,
  };
}

function mutation(overrides: Record<string, unknown> = {}) {
  return {
    mutate: jest.fn(),
    mutateAsync: jest.fn().mockResolvedValue({ confirmed: 0, skipped: 0 }),
    isPending: false,
    ...overrides,
  };
}

function makeDuplicate(overrides: Partial<ReviewItemDuplicate> = {}): ReviewItemDuplicate {
  return {
    type: "possible_duplicate",
    import_row_id: "row-1",
    import_run_id: "run-1",
    booked_date: "2026-06-10",
    amount_minor: -450,
    payee_raw: "PAYPAL",
    purpose: "PP.1234.PP Musterdienst",
    duplicate_of_transaction_id: "tx-1",
    duplicate_of: {
      transaction_id: "tx-1",
      booked_date: "2026-06-10",
      amount_minor: -450,
      payee_label: "PayPal Europe S.a.r.l.",
      purpose: "1000000000001/PP.1234.PP/. Musterdienst",
      account_name: "Girokonto",
      source: "import",
    },
    ...overrides,
  } as ReviewItemDuplicate;
}

function textsOf(tree: ReactTestRenderer): string[] {
  return tree.root.findAllByType(Text).map((node) => {
    const children = node.props.children;
    if (typeof children === "string" || typeof children === "number") return String(children);
    if (Array.isArray(children)) {
      return children
        .map((child) =>
          typeof child === "string" || typeof child === "number" ? String(child) : "",
        )
        .join("");
    }
    return "";
  });
}

function findButton(tree: ReactTestRenderer, label: string) {
  const buttons = tree.root.findAll((node) => node.props?.accessibilityRole === "button");
  for (const button of buttons) {
    const labels = button.findAllByType(Text).map((node) => String(node.props.children));
    if (labels.includes(label)) return button;
  }
  throw new Error(`Keine Schaltflaeche "${label}" gefunden.`);
}

function findCheckboxes(tree: ReactTestRenderer) {
  return tree.root.findAll((node) => node.props?.accessibilityRole === "checkbox");
}

function inbox(duplicates: ReviewItemDuplicate[]) {
  return {
    total_count: duplicates.length,
    unclassified_open_count: 0,
    unclassified_total_count: 0,
    unclassified_offset: 0,
    unclassified_limit: 100,
    unclassified: [],
    possible_duplicates: duplicates,
    transfer_candidates: [],
    recurring_suggestions: [],
    contract_ended_checks: [],
    contract_suggestions: [],
    contract_duplicate_suggestions: [],
    contract_amount_change_checks: [],
  };
}

function render(duplicates: ReviewItemDuplicate[], bulk = mutation()) {
  authModule.useAuth.mockReturnValue({ accessToken: "token", session: {} });
  householdModule.useHousehold.mockReturnValue({ selectedHouseholdId: "haushalt-1" });
  mockUseReviewInbox.mockReturnValue(query({ data: inbox(duplicates) }));
  reviewHooks.useAcceptReview.mockReturnValue(mutation());
  reviewHooks.useCorrectReview.mockReturnValue(mutation());
  mockUseBulk.mockReturnValue(bulk);
  categoryHooks.useCategories.mockReturnValue(query({ data: [] }));
  aiHooks.useAIDecisions.mockReturnValue(query());
  aiHooks.useCancelClassificationRun.mockReturnValue(mutation());
  aiHooks.useClassifyWithAI.mockReturnValue(mutation());
  aiHooks.useLatestClassificationRun.mockReturnValue(query());
  aiHooks.useStartClassificationRun.mockReturnValue(mutation());

  // Echter QueryClient statt noch eines Mocks: der Bildschirm zieht ueber
  // Nebenwege (Offline-Warteschlange) weitere Abfragen herein. Sie einzeln zu
  // ersetzen hiesse, den Test an jede kuenftige Ergaenzung des Bildschirms zu
  // binden - ein Client ohne Wiederholungen kostet nichts und haelt.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  let tree!: ReactTestRenderer;
  act(() => {
    tree = create(
      <QueryClientProvider client={queryClient}>
        <ReviewScreen />
      </QueryClientProvider>,
    );
  });
  return tree;
}

afterEach(() => jest.clearAllMocks());

it("zeigt die Buchung, gegen die eine Zeile als Dublette gilt", () => {
  const tree = render([makeDuplicate()]);
  const texte = textsOf(tree).join(" | ");

  expect(texte).toContain("Bereits vorhanden");
  expect(texte).toContain("PayPal Europe S.a.r.l.");
  // Konto und Zweck der Gegenbuchung gehoeren dazu: dieselbe Buchung auf einem
  // anderen Konto waere eine ganz andere Lage.
  expect(texte).toContain("Girokonto");
  expect(texte).toContain("1000000000001/PP.1234.PP/. Musterdienst");
});

it("sagt es, wenn die verglichene Buchung nicht mehr im Bestand steht", () => {
  const tree = render([makeDuplicate({ duplicate_of: null })]);
  expect(textsOf(tree).join(" | ")).toContain("nicht mehr im Bestand");
});

it("bestätigt mehrere Dubletten in einem Zug", async () => {
  const bulk = mutation({
    mutateAsync: jest.fn().mockResolvedValue({ confirmed: 2, skipped: 0 }),
  });
  const tree = render([makeDuplicate(), makeDuplicate({ import_row_id: "row-2" })], bulk);

  await act(async () => {
    findButton(tree, "Alle auswählen").props.onPress();
  });
  await act(async () => {
    findButton(tree, "2 bestätigen").props.onPress();
  });

  expect(bulk.mutateAsync).toHaveBeenCalledWith(["row-1", "row-2"]);
});

it("nimmt einzelne Zeilen in die Auswahl auf und wieder heraus", async () => {
  const tree = render([makeDuplicate(), makeDuplicate({ import_row_id: "row-2" })]);

  await act(async () => {
    findCheckboxes(tree)[0]!.props.onPress();
  });
  expect(findButton(tree, "1 bestätigen")).toBeTruthy();

  await act(async () => {
    findCheckboxes(tree)[0]!.props.onPress();
  });
  expect(findButton(tree, "0 bestätigen")).toBeTruthy();
});

it("meldet, wenn Zeilen inzwischen schon entschieden waren", async () => {
  const bulk = mutation({
    mutateAsync: jest.fn().mockResolvedValue({ confirmed: 1, skipped: 1 }),
  });
  const tree = render([makeDuplicate(), makeDuplicate({ import_row_id: "row-2" })], bulk);

  await act(async () => {
    findButton(tree, "Alle auswählen").props.onPress();
  });
  await act(async () => {
    findButton(tree, "2 bestätigen").props.onPress();
  });

  // Eine veraltete Liste soll den Nutzer nicht raten lassen, was passiert ist.
  expect(textsOf(tree).join(" | ")).toContain("schon entschieden");
});
