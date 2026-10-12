/// <reference types="jest" />
import { Text } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { ImportRevertPreview, ImportRun } from "../../api/types";
import { useImportRevertPreview, useImportRuns, useRevertImportRun } from "../../hooks/useImport";
import ImportScreen from "../../../app/mehr/import";

// Component-Tests fuer die Importhistorie und das Zuruecknehmen eines bereits
// uebernommenen Laufs. Bis dahin konnte die App einen Import nach der
// Uebernahme nicht mehr anfassen und zeigte ihn nicht einmal an - der Weg
// zurueck existierte nur in der Weboberflaeche.

jest.mock("../../hooks/useImport", () => ({
  useImportRun: jest.fn(),
  useImportRows: jest.fn(),
  useImportRuns: jest.fn(),
  useImportRevertPreview: jest.fn(),
  useRevertImportRun: jest.fn(),
  useUploadImport: jest.fn(),
  useCommitImportRun: jest.fn(),
}));

jest.mock("../../hooks/useAccounts", () => ({
  useAccounts: jest.fn(),
}));

jest.mock("expo-router", () => ({
  Stack: { Screen: () => null },
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock("expo-document-picker", () => ({
  getDocumentAsync: jest.fn(),
}));

const mockUseImportRuns = useImportRuns as jest.Mock;
const mockUseImportRevertPreview = useImportRevertPreview as jest.Mock;
const mockUseRevertImportRun = useRevertImportRun as jest.Mock;

// eslint-disable-next-line @typescript-eslint/no-require-imports
const importHooks = require("../../hooks/useImport");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const accountHooks = require("../../hooks/useAccounts");

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

function makeRun(overrides: Partial<ImportRun> = {}): ImportRun {
  return {
    id: "run-1",
    account_id: "account-1",
    filename: "umsaetze-april.csv",
    file_size_bytes: 2048,
    detected_format: "csv_generic",
    status: "completed",
    total_rows: 5,
    new_count: 5,
    duplicate_count: 0,
    transfer_candidate_count: 0,
    error_count: 0,
    error_message: null,
    created_at: "2026-04-30T10:00:00Z",
    completed_at: "2026-04-30T10:01:00Z",
    ...overrides,
  } as ImportRun;
}

function makePreview(overrides: Partial<ImportRevertPreview> = {}): ImportRevertPreview {
  return {
    import_run_id: "run-1",
    filename: "umsaetze-april.csv",
    transactions_total: 5,
    revertable: 4,
    sum_minor: -12500,
    from_date: "2026-04-02",
    to_date: "2026-04-30",
    blocked: [{ reason: "bearbeitet", label: "nachtraeglich bearbeitet", count: 1 }],
    ...overrides,
  } as ImportRevertPreview;
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

/**
 * Sucht eine Schaltflaeche ueber ihre sichtbare Beschriftung.
 *
 * PrimaryButton setzt kein accessibilityLabel - die Beschriftung steht als
 * Text-Kind im Pressable. Gesucht wird deshalb ueber die gerenderten Texte
 * innerhalb des Knotens mit accessibilityRole="button".
 */
function findButton(tree: ReactTestRenderer, label: string) {
  const buttons = tree.root.findAll((node) => node.props?.accessibilityRole === "button");
  for (const button of buttons) {
    const labels = button.findAllByType(Text).map((node) => String(node.props.children));
    if (labels.includes(label)) return button;
  }
  throw new Error(`Keine Schaltflaeche "${label}" gefunden.`);
}

function pressButton(tree: ReactTestRenderer, label: string): void {
  const button = findButton(tree, label);
  act(() => {
    button.props.onPress?.();
  });
}

let renderedTrees: ReactTestRenderer[] = [];

function renderScreen(): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<ImportScreen />);
  });
  renderedTrees.push(tree!);
  return tree!;
}

describe("ImportScreen - Historie und Zuruecknehmen", () => {
  beforeEach(() => {
    // Alle Mocks hier setzen und nicht in der Fabrik von jest.mock: das
    // jest.resetAllMocks() im afterEach loescht auch die dort hinterlegte
    // Implementierung, und der zweite Test liefe gegen undefined.
    accountHooks.useAccounts.mockReturnValue({ data: [], isLoading: false });
    importHooks.useImportRun.mockReturnValue(query());
    importHooks.useImportRows.mockReturnValue(query());
    importHooks.useUploadImport.mockReturnValue({ mutateAsync: jest.fn(), isPending: false });
    importHooks.useCommitImportRun.mockReturnValue({ mutateAsync: jest.fn(), isPending: false });
    mockUseImportRuns.mockReturnValue(query({ data: [makeRun()] }));
    mockUseImportRevertPreview.mockReturnValue(query({ data: makePreview() }));
    mockUseRevertImportRun.mockReturnValue({ mutateAsync: jest.fn(), isPending: false });
  });

  afterEach(() => {
    for (const tree of renderedTrees) {
      act(() => {
        tree.unmount();
      });
    }
    renderedTrees = [];
    jest.resetAllMocks();
  });

  it("listet uebernommene Importe mit Dateiname und Umfang", () => {
    const texte = textsOf(renderScreen());
    expect(texte).toContain("umsaetze-april.csv");
    expect(texte.some((t) => t.includes("5 Zeilen"))).toBe(true);
  });

  it("zeigt noch nicht uebernommene Laeufe nicht in der Historie", () => {
    mockUseImportRuns.mockReturnValue(query({ data: [makeRun({ status: "ready" })] }));

    const texte = textsOf(renderScreen());
    expect(texte).not.toContain("Übernommene Importe");
  });

  it("nennt vor dem Zuruecknehmen Umfang, Zeitraum und was stehen bleibt", () => {
    const tree = renderScreen();
    pressButton(tree, "Zurücknehmen");

    const texte = textsOf(tree);
    expect(texte.some((t) => t.includes("4 von 5"))).toBe(true);
    expect(texte.some((t) => t.includes("2026-04-02 bis 2026-04-30"))).toBe(true);
    expect(texte.some((t) => t.includes("nachtraeglich bearbeitet"))).toBe(true);
  });

  it("nimmt den Lauf zurueck und meldet, was stehen blieb", async () => {
    const mutateAsync = jest.fn().mockResolvedValue({
      import_run_id: "run-1",
      reverted: 4,
      kept: 1,
      blocked: [],
    });
    mockUseRevertImportRun.mockReturnValue({ mutateAsync, isPending: false });

    const tree = renderScreen();
    pressButton(tree, "Zurücknehmen");
    await act(async () => {
      pressButton(tree, "4 Buchungen zurücknehmen");
    });

    expect(mutateAsync).toHaveBeenCalledWith("run-1");
    const texte = textsOf(tree);
    expect(texte.some((t) => t.includes("wurden 4 Buchungen zurückgenommen"))).toBe(true);
    expect(texte.some((t) => t.includes("1 blieben stehen"))).toBe(true);
  });

  it("sperrt die Bestaetigung, wenn nichts mehr im Bestand steht", () => {
    mockUseImportRevertPreview.mockReturnValue(
      query({
        data: makePreview({
          transactions_total: 0,
          revertable: 0,
          sum_minor: 0,
          from_date: null,
          to_date: null,
          blocked: [],
        }),
      }),
    );

    const tree = renderScreen();
    pressButton(tree, "Zurücknehmen");

    const texte = textsOf(tree);
    expect(texte.some((t) => t.includes("Es gibt nichts zurückzunehmen"))).toBe(true);
    expect(findButton(tree, "0 Buchungen zurücknehmen").props.accessibilityState.disabled).toBe(
      true,
    );
  });
});
