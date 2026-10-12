/**
 * Duplikatpruefung im Regelimport.
 *
 * Ohne sie liess sich dasselbe Paket beliebig oft installieren: die Vorschau
 * zeigte zwar eine Zahl "Duplikate", hakte aber trotzdem alles an. Diese Tests
 * halten fest, dass bereits bekannte Regeln abgewaehlt ankommen, dass der Grund
 * dabeisteht und dass der Import genau die abgewaehlten Positionen auslaesst.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RuleImportExportModal } from "../components/RuleImportExportModal";
import type { Category, CategoryGroup } from "../lib/types";

const packageMetas = [
  {
    id: "online_shopping",
    name: "Online-Shopping",
    description: "Amazon, eBay, Zalando und mehr",
    icon: "📦",
    rule_count: 2,
    installed_count: 0,
  },
  {
    id: "supermarkt",
    name: "Supermärkte",
    description: "Rewe, Edeka und mehr",
    icon: "🛒",
    rule_count: 1,
    installed_count: 1,
  },
];

const onlineShoppingPackage = {
  format_version: "1.0",
  name: "Online-Shopping",
  description: "Amazon, eBay, Zalando und mehr",
  author: "",
  rules: [
    {
      name: "Amazon",
      match_payee_raw: "(?i)\\b(AMAZON|EBAY)\\b",
      match_payee_exact_word: false,
      match_purpose: null,
      match_purpose_exact_word: false,
      exclude_payee_raw: null,
      exclude_purpose: null,
      match_min_amount_minor: null,
      match_max_amount_minor: null,
      match_amount_mode: "inside" as const,
      action_category_key: "shopping",
      action_set_transfer: false,
      action_note: null,
      priority: 10,
      is_active: true,
    },
    {
      name: "Zalando",
      match_payee_raw: "(?i)\\b(ZALANDO)\\b",
      match_payee_exact_word: false,
      match_purpose: null,
      match_purpose_exact_word: false,
      exclude_payee_raw: null,
      exclude_purpose: null,
      match_min_amount_minor: null,
      match_max_amount_minor: null,
      match_amount_mode: "inside" as const,
      action_category_key: "clothing",
      action_set_transfer: false,
      action_note: null,
      priority: 10,
      is_active: true,
    },
  ],
};

// Regel 0 gibt es schon (Duplikat), Regel 1 ist neu.
const previewResponse = {
  package_name: "Online-Shopping",
  total_rules: 2,
  mappable: 2,
  unmapped: 0,
  duplicates: 1,
  conflicts: 0,
  overlaps: 0,
  recommended: 1,
  items: [
    {
      index: 0,
      rule_name: "Amazon",
      match_payee_raw: "(?i)\\b(AMAZON|EBAY)\\b",
      category_key: "shopping",
      resolved_category_id: "cat-1",
      resolved_category_name: "Online-Shopping",
      status: "duplicate" as const,
      is_duplicate: true,
      duplicate_rule_id: "rule-1",
      existing_rule_name: "Amazon & eBay",
      existing_category_name: "Online-Shopping",
      shared_terms: [],
      in_package_duplicate: false,
      recommended: false,
    },
    {
      index: 1,
      rule_name: "Zalando",
      match_payee_raw: "(?i)\\b(ZALANDO)\\b",
      category_key: "clothing",
      resolved_category_id: "cat-2",
      resolved_category_name: "Kleidung & Schuhe",
      status: "new" as const,
      is_duplicate: false,
      duplicate_rule_id: null,
      existing_rule_name: null,
      existing_category_name: null,
      shared_terms: [],
      in_package_duplicate: false,
      recommended: true,
    },
  ],
};

function category(id: string, name: string, sortOrder: number): Category {
  return {
    id,
    group_id: "group-1",
    name,
    default_type: "expense",
    budget_bucket: "wants",
    is_archived: false,
    icon: null,
    color: null,
    is_budgeted: true,
    sort_order: sortOrder,
    synonyms: [],
    description: null,
    semantic_key: null,
    version: 1,
    is_tax_deductible: false,
  };
}

const categoryGroups: CategoryGroup[] = [
  {
    id: "group-1",
    name: "Lifestyle",
    sort_order: 0,
    is_archived: false,
    icon: null,
    color: null,
    semantic_key: null,
    version: 1,
    categories: [
      category("cat-1", "Online-Shopping", 0),
      category("cat-2", "Kleidung & Schuhe", 1),
    ],
  },
];

function renderModal() {
  // `init` bleibt in der Signatur, obwohl der Mock es nicht auswertet: nur so
  // taucht der Request-Body in mock.calls auf und die Tests koennen ihn lesen.
  const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/v1/rules/packages/online_shopping")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(onlineShoppingPackage),
      });
    }
    if (url.includes("/v1/rules/packages")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(packageMetas) });
    }
    if (url.includes("/v1/rules/import/preview")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(previewResponse),
      });
    }
    if (url.includes("/v1/rules/import")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            created: 1,
            skipped: 1,
            skipped_duplicates: 1,
            skipped_deselected: 0,
            errors: [],
          }),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });
  vi.stubGlobal("fetch", fetchMock);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RuleImportExportModal
        onClose={vi.fn()}
        token="test-token"
        householdId="household-1"
        rules={[]}
        categoryGroups={categoryGroups}
      />
    </QueryClientProvider>,
  );
  return fetchMock;
}

describe("RuleImportExportModal", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt an, welche Pakete bereits installiert sind", async () => {
    renderModal();

    await waitFor(() => {
      expect(screen.getByText("Online-Shopping")).toBeInTheDocument();
    });
    expect(screen.getByText(/1 Regeln · bereits installiert/)).toBeInTheDocument();
    // Ein vollstaendig installiertes Paket laedt nicht mehr zum Installieren
    // ein, bleibt aber pruefbar.
    expect(screen.getByRole("button", { name: "Prüfen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Installieren" })).toBeInTheDocument();
  });

  it("waehlt bekannte Regeln ab und benennt den Grund", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(await screen.findByRole("button", { name: "Installieren" }));

    const duplicateCheckbox = await screen.findByRole("checkbox", {
      name: "Amazon importieren",
    });
    const newCheckbox = screen.getByRole("checkbox", { name: "Zalando importieren" });
    expect(duplicateCheckbox).not.toBeChecked();
    expect(newCheckbox).toBeChecked();

    expect(screen.getByText(/Gibt es bereits als „Amazon & eBay“/)).toBeInTheDocument();
    expect(screen.getByText(/1 von 2 Regeln kennt dieser Haushalt schon/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1 Regeln importieren" })).toBeInTheDocument();
  });

  it("laesst genau die abgewaehlten Positionen aus dem Import heraus", async () => {
    const user = userEvent.setup();
    const fetchMock = renderModal();

    await user.click(await screen.findByRole("button", { name: "Installieren" }));
    await screen.findByRole("checkbox", { name: "Amazon importieren" });

    await user.click(screen.getByRole("button", { name: "1 Regeln importieren" }));

    await waitFor(() => {
      const importCall = fetchMock.mock.calls.find(
        ([input]) =>
          String(input).includes("/v1/rules/import") && !String(input).includes("/preview"),
      );
      expect(importCall).toBeDefined();
      const body = JSON.parse(String((importCall![1] as RequestInit).body)) as {
        skip_indices: number[];
      };
      expect(body.skip_indices).toEqual([0]);
    });

    // Das Ergebnis benennt die uebersprungenen Duplikate getrennt.
    const result = await screen.findByText("Duplikate übersprungen");
    expect(within(result.parentElement!).getByText("1")).toBeInTheDocument();
  });

  it("importiert ein Duplikat, wenn es ausdruecklich angehakt wird", async () => {
    const user = userEvent.setup();
    const fetchMock = renderModal();

    await user.click(await screen.findByRole("button", { name: "Installieren" }));
    await user.click(await screen.findByRole("checkbox", { name: "Amazon importieren" }));

    await user.click(screen.getByRole("button", { name: "2 Regeln importieren" }));

    await waitFor(() => {
      const importCall = fetchMock.mock.calls.find(
        ([input]) =>
          String(input).includes("/v1/rules/import") && !String(input).includes("/preview"),
      );
      const body = JSON.parse(String((importCall![1] as RequestInit).body)) as {
        skip_indices: number[];
      };
      expect(body.skip_indices).toEqual([]);
    });
  });
});
