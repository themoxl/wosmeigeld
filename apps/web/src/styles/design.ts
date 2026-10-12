import { useSyncExternalStore } from "react";

// Anbindung an den Designkasten (public/designkasten/, Quelle sia_public).
//
// designs.js laeuft in index.html VOR React: es setzt das gemerkte Design am
// <html>, bevor irgendetwas gezeichnet wird. React liest hier nur noch, welches
// Design gilt, und reicht Aufrufe an das Designfenster (designwahl.js) weiter.
// Laedt der Kasten nicht (z. B. in Tests), bleibt Klassisch aus global.css
// stehen und die Knoepfe zur Designwahl sind gesperrt.

export interface KastenDesign {
  id: string;
  name: string;
  text: string;
  art: "Dunkel" | "Hell";
  herkunft: "kasten" | "eigen";
}

interface DesignkastenApi {
  readonly aktuell: string;
  readonly standard: string;
  readonly meldung: string | null;
  readonly merkt: boolean;
  /** Rollen, die ein eigenes Design fuer das Designfenster setzen muss. */
  readonly fensterrollen: string[];
  designs(): KastenDesign[];
  design(id?: string): KastenDesign | null;
  setze(id: string): string;
}

interface DesignwahlApi {
  oeffne(anker: HTMLElement): void;
  schliesse(): void;
  umschalten(anker: HTMLElement): void;
  offen(): boolean;
  einbetten(ziel: HTMLElement): { aktualisiere(): void; entferne(): void };
}

declare global {
  interface Window {
    Designkasten?: DesignkastenApi;
    Designwahl?: DesignwahlApi;
  }
}

/** window-Ereignis, das designs.js nach jedem Wechsel ausloest. */
export const DESIGN_EVENT = "design-gewechselt";

function subscribe(onChange: () => void): () => void {
  window.addEventListener(DESIGN_EVENT, onChange);
  return () => window.removeEventListener(DESIGN_EVENT, onChange);
}

function currentDesignId(): string | null {
  return window.Designkasten?.aktuell ?? null;
}

/**
 * Kennung des geltenden Designs; aendert sich mit jeder Wahl im Designfenster.
 * Komponenten, die Farben per getComputedStyle lesen (Diagramme), haengen
 * daran, um nach einem Wechsel neu zu zeichnen.
 */
export function useDesignId(): string | null {
  return useSyncExternalStore(subscribe, currentDesignId, () => null);
}

export function designwahl(): DesignwahlApi | null {
  return window.Designkasten && window.Designwahl ? window.Designwahl : null;
}
