import type { ComponentProps } from "react";
import { Text } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { InlineQueryState } from "./InlineQueryState";

function renderState(props: ComponentProps<typeof InlineQueryState>) {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<InlineQueryState {...props} />);
  });
  return tree!;
}

describe("InlineQueryState", () => {
  it("zeigt im Ladezustand Skeleton-Zeilen ohne Fehler-/Leertext", () => {
    const tree = renderState({ status: "loading" });

    const texts = tree.root.findAllByType(Text);
    expect(texts).toHaveLength(0);

    // Standardmaessig drei Skeleton-Zeilen.
    const skeletonRows = tree.root.findAll(
      (node) => typeof node.type === "string" && node.props.importantForAccessibility === "no",
    );
    expect(skeletonRows).toHaveLength(3);
  });

  it("respektiert eine eigene Anzahl an Skeleton-Zeilen", () => {
    const tree = renderState({ status: "loading", skeletonRows: 5 });
    const skeletonRows = tree.root.findAll(
      (node) => typeof node.type === "string" && node.props.importantForAccessibility === "no",
    );
    expect(skeletonRows).toHaveLength(5);
  });

  it("zeigt im Fehlerzustand einen kurzen Text mit 'Erneut versuchen'-Button und ruft onRetry auf", () => {
    const onRetry = jest.fn();
    const tree = renderState({
      status: "error",
      message: "Server nicht erreichbar.",
      onRetry,
    });

    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toContain("Server nicht erreichbar.");
    expect(texts).toContain("Erneut versuchen");

    const alert = tree.root.findByProps({ accessibilityRole: "alert" });
    expect(alert).toBeTruthy();

    const retryButton = tree.root.findByProps({ accessibilityLabel: "Erneut versuchen" });
    act(() => {
      retryButton.props.onPress();
    });
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("verwendet einen verstaendlichen Standardtext im Fehlerzustand ohne eigene message", () => {
    const tree = renderState({ status: "error" });
    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toContain("Daten konnten nicht geladen werden");
    expect(texts).toContain("Bitte prüfe deine Verbindung und versuche es erneut.");
  });

  it("zeigt ohne onRetry keinen 'Erneut versuchen'-Button", () => {
    const tree = renderState({ status: "error", message: "Fehler." });
    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).not.toContain("Erneut versuchen");
  });

  it("zeigt im Leerzustand einen eigenen, vom Fehlerzustand unterscheidbaren Text ohne Retry-Button", () => {
    const tree = renderState({
      status: "empty",
      title: "Keine Buchungen im August",
      message: "Fuer diesen Monat liegen keine Buchungen vor.",
    });

    const texts = tree.root.findAllByType(Text).map((node) => node.props.children);
    expect(texts).toEqual([
      "Keine Buchungen im August",
      "Fuer diesen Monat liegen keine Buchungen vor.",
    ]);
    expect(texts).not.toContain("Erneut versuchen");

    const alerts = tree.root.findAll(
      (node) => typeof node.type === "string" && node.props.accessibilityRole === "alert",
    );
    expect(alerts).toHaveLength(0);
  });

  it("Leer- und Fehlerzustand fuer denselben Titel bleiben unterscheidbare Zustaende", () => {
    const empty = renderState({ status: "empty", title: "Keine Treffer fuer diese Filter" });
    const error = renderState({ status: "error", title: "Keine Treffer fuer diese Filter" });

    const emptyRetry = empty.root.findAll(
      (node) =>
        typeof node.type === "string" && node.props.accessibilityLabel === "Erneut versuchen",
    );
    const errorRetry = error.root.findAll(
      (node) =>
        typeof node.type === "string" && node.props.accessibilityLabel === "Erneut versuchen",
    );
    expect(emptyRetry).toHaveLength(0);
    // Fehlerzustand ohne uebergebenes onRetry zeigt ebenfalls keinen Button,
    // unterscheidet sich aber durch accessibilityRole="alert" vom Leerzustand.
    expect(errorRetry).toHaveLength(0);
    expect(
      error.root.findAll(
        (node) => typeof node.type === "string" && node.props.accessibilityRole === "alert",
      ),
    ).toHaveLength(1);
  });
});
