import type { ComponentProps } from "react";
import type { Ionicons } from "@expo/vector-icons";
import type { Href } from "expo-router";

type IconName = ComponentProps<typeof Ionicons>["name"];

export type MehrEntry = {
  key: string;
  label: string;
  href: Href;
  icon: IconName;
};

export type MehrSection = {
  key: string;
  label: string;
  data: MehrEntry[];
};

// Mehr-Menue (docs/MOBILE-APP-REDESIGN-PLAN.md Abschnitt 8): genau vier
// fachlich verstaendliche Gruppen statt der vorherigen fuenf technischen
// Abschnitte. "Konto" wird bewusst nicht mehr als Abschnittsbegriff benutzt,
// weil das Wort sonst gleichzeitig fuer Bankkonten (Konten) und das
// Benutzerkonto (Fusszeile "Angemeldet als") stehen wuerde. Lange
// Beschreibungstexte entfallen zugunsten von Ionicon + Label + echtem
// Chevron (ListRow-Standard).
//
// Bewusst hier statt im Screen: die Liste ist reine Konfiguration, laesst sich
// so ohne Renderer testen, und der Screen bleibt auf Darstellung beschraenkt.
export const MEHR_SECTIONS: MehrSection[] = [
  {
    key: "finanzen",
    label: "Finanzen verwalten",
    data: [
      // Ganz oben, weil die Buchungsliste seit dem Redesign keinen eigenen Tab
      // mehr hat (siehe app/(tabs)/_layout.tsx: "Berichte" steht auf ihrem
      // Platz, /buchungen bleibt per href:null nur aus der Leiste ausgeblendet).
      // Ohne diesen Eintrag waere der haeufigste Weg zur vollstaendigen Liste
      // nur noch ein Umweg ueber den Konten-Strip auf "Heute".
      { key: "buchungen", label: "Buchungen", href: "/buchungen", icon: "receipt-outline" },
      // Gleich danach, obwohl es die einzige rein lesende Zeile der Gruppe ist: die
      // Vorschau beantwortet "wann wird mein Geld knapp" und ist damit das
      // haeufigste Ziel dieser Gruppe - und das Ziel jeder
      // Vorschau-Benachrichtigung
      // (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 5).
      { key: "vorschau", label: "Vorschau", href: "/mehr/vorschau", icon: "analytics-outline" },
      // "Berichte" steht seit dem Redesign in der Tableiste und deshalb nicht
      // mehr hier (docs/MOBILE-DESIGNSYSTEM.md, Abschnitt 6): der Rueckblick
      // auf die letzten Monate ist der haeufigste Grund, die App zu oeffnen,
      // und gehoert damit auf den kuerzesten Weg statt in ein Untermenue.
      { key: "konten", label: "Konten", href: "/mehr/konten", icon: "wallet-outline" },
      // Direkt hinter "Konten": beides sind Verzeichnisse dessen, was man hat,
      // und beide werden nachgeschlagen statt bearbeitet. Weiter unten, bei den
      // Buchungswerkzeugen (Kategorien, Import, Pruefen), stuende der Eintrag
      // in einer Gruppe, deren Zeilen alle etwas veraendern
      // (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 7.1).
      {
        key: "versicherungen",
        label: "Versicherungen",
        href: "/mehr/versicherungen",
        icon: "shield-checkmark-outline",
      },
      {
        key: "kategorien",
        label: "Kategorien",
        href: "/mehr/kategorien",
        icon: "pricetags-outline",
      },
      { key: "import", label: "Import", href: "/mehr/import", icon: "cloud-upload-outline" },
      {
        key: "review",
        label: "Buchungen prüfen",
        href: "/mehr/review",
        icon: "checkmark-done-outline",
      },
    ],
  },
  {
    key: "ziele-vermoegen",
    label: "Ziele & Vermögen",
    data: [
      {
        key: "sparziele",
        label: "Sparen & Wünsche",
        href: "/mehr/sparziele",
        icon: "flag-outline",
      },
      { key: "vorhaben", label: "Vorhaben", href: "/mehr/vorhaben", icon: "hammer-outline" },
      {
        key: "vermoegen",
        label: "Vermögen & Schulden",
        href: "/mehr/vermoegen",
        icon: "trending-up-outline",
      },
    ],
  },
  {
    key: "alltag",
    label: "Alltag",
    data: [
      {
        key: "essensplan",
        label: "Essensplan",
        href: "/mehr/essensplan",
        icon: "restaurant-outline",
      },
      {
        key: "einkaufsliste",
        label: "Einkaufsliste",
        href: "/mehr/einkaufsliste",
        icon: "cart-outline",
      },
    ],
  },
  {
    key: "app",
    label: "App",
    data: [
      {
        key: "einstellungen",
        label: "Einstellungen",
        href: "/mehr/einstellungen",
        icon: "settings-outline",
      },
      { key: "status", label: "Systemstatus", href: "/mehr/status", icon: "pulse-outline" },
    ],
  },
];
