// Abschnitt 10.2 des Architekturplans: linke Sidebar.
// Die 20 Eintraege liegen weiterhin unveraendert vor (gleiche Pfade, gleiche
// Beschriftungen), sind aber in sechs thematische Gruppen gegliedert, damit die
// Seitenleiste nicht als flache Liste erscheint.
// "phase" verweist auf Abschnitt 17 (Umsetzung in Phasen) - nur gesetzt, wo
// aus dem Plan eindeutig hervorgeht, in welcher Phase die Funktion entsteht.
export interface NavItem {
  label: string;
  path: string;
  phase?: number;
  icon?: string;
}

export interface NavGroup {
  /** Stabile ID fuer die aria-labelledby-Verknuepfung zwischen Ueberschrift und Liste. */
  id: string;
  label: string;
  code: string;
  items: NavItem[];
}

export const navGroups: NavGroup[] = [
  {
    id: "ueberblick",
    label: "Überblick",
    code: "01",
    items: [
      { label: "Übersicht", path: "/", icon: "dashboard" },
      { label: "Berichte", path: "/berichte", phase: 5, icon: "reports" },
      { label: "Empfehlungen", path: "/empfehlungen", icon: "sparkles" },
      { label: "Review-Inbox", path: "/review", phase: 4, icon: "inbox" },
      // Breite Ansicht des KI-Felds mit Chatliste; dasselbe Feld liegt als
      // Seitenpanel ueber jeder Seite ("KI fragen", docs/KI-FELD-SCHNITTSTELLE.md).
      { label: "KI-Assistent", path: "/assistent", icon: "message-circle" },
    ],
  },
  {
    id: "geld",
    label: "Geld",
    code: "02",
    items: [
      { label: "Alle Buchungen", path: "/buchungen", icon: "list" },
      { label: "Explorer", path: "/explorer", icon: "search" },
      { label: "Konten", path: "/konten", icon: "credit-card" },
      { label: "Erstattungen", path: "/erstattungen", icon: "shield" },
    ],
  },
  {
    id: "planung",
    label: "Planung",
    code: "03",
    items: [
      { label: "Budget", path: "/budget", phase: 5, icon: "pie-chart" },
      { label: "Verträge", path: "/fixkosten", phase: 7, icon: "file-text" },
      // Fachlich der naechste Nachbar der Vertraege: beide haengen an Fristen
      // und Fixkosten (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 6). Nicht in die
      // Gruppe "Geld", weil das Modul kein Geld bewegt.
      { label: "Versicherungen", path: "/versicherungen", icon: "umbrella" },
      // Kein eigener Eintrag "Einnahmen": Einnahmen sind Vertraege mit
      // direction='income' und liegen unter /fixkosten?richtung=einnahmen
      // (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Abschnitt 3).
      { label: "Vorschau", path: "/vorschau", icon: "trending-up" },
      { label: "Lebensmittel", path: "/lebensmittel", phase: 8, icon: "shopping-cart" },
    ],
  },
  {
    id: "vorhaben",
    label: "Vorhaben",
    code: "04",
    items: [
      { label: "Vorhaben", path: "/vorhaben", icon: "home" },
      { label: "Förderungen", path: "/foerderungen", icon: "award" },
    ],
  },
  {
    id: "ziele-vermoegen",
    label: "Ziele & Vermögen",
    code: "05",
    items: [
      { label: "Sparen & Wünsche", path: "/sparen", phase: 7, icon: "target" },
      { label: "Vermögen", path: "/vermoegen", phase: 7, icon: "trending-up" },
      { label: "Schulden", path: "/schulden", phase: 7, icon: "trending-down" },
    ],
  },
  {
    id: "system",
    label: "System",
    code: "06",
    items: [
      { label: "Importe", path: "/importe", phase: 3, icon: "upload" },
      { label: "Regeln", path: "/regeln", phase: 4, icon: "sliders" },
      // Eigener Bereich statt Nebenpunkt der Einstellungen
      // (docs/KATEGORISIERUNGSPLAN.md, "Zieloberflaeche: Kategorien verwalten").
      { label: "Kategorien", path: "/kategorien", icon: "folder" },
      // Rohzustand der Haushaltstabellen samt Loeschwegen - gehoert neben die
      // uebrigen Werkzeuge, nicht zwischen die fachlichen Ansichten.
      { label: "Datenbank", path: "/datenbank", icon: "database" },
      { label: "Einstellungen", path: "/einstellungen", icon: "settings" },
    ],
  },
];

// Flache Sicht auf dieselben Eintraege: die Routentabelle in App.tsx leitet
// ihre Routen weiterhin hieraus ab, unabhaengig von der Gruppierung.
export const navItems: NavItem[] = navGroups.flatMap((group) => group.items);
