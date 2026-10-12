import { useEffect, useRef } from "react";
import { designwahl } from "../styles/design";

/**
 * Einstellungen > Anzeige: dieselbe Designliste wie im Fenster am ◐, hier
 * eingebettet (designwahl.js baut sie selbst und haelt den Haken aktuell).
 * Die Wahl gilt fuer diesen Browser und wird dort gemerkt - wie das Design
 * eines Programms, nicht eines Haushalts.
 */
export function DesignCard() {
  const listRef = useRef<HTMLDivElement | null>(null);
  const wahl = designwahl();

  useEffect(() => {
    const target = listRef.current;
    if (!target || !wahl) return;
    const embedded = wahl.einbetten(target);
    return () => embedded.entferne();
  }, [wahl]);

  return (
    <div className="card design-card" style={{ marginTop: "var(--space-4)" }}>
      <h2 className="card-heading">Design</h2>
      <p className="card-body" style={{ marginBottom: "var(--space-3)" }}>
        Farben der ganzen App. Die Wahl gilt für diesen Browser und wird dort gemerkt; dasselbe
        Fenster öffnet auch der Knopf ◐ oben in der Seitenleiste.
      </p>
      {wahl ? (
        <div ref={listRef} className="design-card__list" data-testid="design-list" />
      ) : (
        <p className="form-error">
          Die Designwahl ist nicht geladen (designkasten/designs.js fehlt). Es gilt Klassisch.
        </p>
      )}
    </div>
  );
}
