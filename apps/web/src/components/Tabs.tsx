import { useRef, type ReactNode } from "react";

/**
 * Generisches, barrierefreies Registermuster fuer Seiten mit mehreren
 * gleichrangigen Inhaltsbereichen (z. B. Vertragsdialog, Einstellungen).
 *
 * Warum eine eigene Komponente und nicht das vorhandene `filter-tabs`-Muster
 * aus ReportsPage/ContractsPage direkt inline: die Seitenregister dort sind
 * Knopfleisten mit `role="tab"`, aber ohne Pfeiltastenbedienung, ohne
 * `aria-controls` und ohne `role="tabpanel"` - fuer wenige Filterschalter
 * reicht das, fuer mehrere Register mit umfangreichem Inhalt nicht mehr. Hier
 * gilt das volle Muster: eine einzige Tabstopp-Stelle in der Leiste
 * (rovierender Tabindex), Links/Rechts/Pos1/Ende wechseln, und jeder Bereich
 * nennt sein Register.
 *
 * Die Optik bleibt bewusst `filter-tabs`/`filter-tab` - die Register sollen
 * aussehen wie die uebrigen Register der Anwendung, nur besser bedienbar sein.
 * (Die Stilblaetter liegen in einem fremden Arbeitsstrang; hier kommt deshalb
 * keine neue Klasse dazu.)
 */

export interface TabDefinition<K extends string> {
  key: K;
  label: string;
}

interface TabsProps<K extends string> {
  /** Eindeutiges Praefix fuer die id-Paare zwischen Register und Bereich. */
  idPrefix: string;
  /** Beschriftung der Leiste fuer Screenreader. */
  label: string;
  tabs: TabDefinition<K>[];
  active: K;
  onChange: (key: K) => void;
}

export function Tabs<K extends string>({ idPrefix, label, tabs, active, onChange }: TabsProps<K>) {
  const listRef = useRef<HTMLDivElement>(null);

  /**
   * Bewegt Auswahl UND Fokus.
   *
   * Automatische Aktivierung (der Bereich wechselt beim Fokussieren, nicht erst
   * bei Enter) ist fuer Register ohne Ladeverzoegerung das empfohlene Verhalten
   * - alle Bereiche halten ihre Daten in eigenen Abfragen, das Wechseln kostet
   * nichts.
   */
  const moveTo = (index: number) => {
    const target = tabs[index];
    if (!target) return;
    onChange(target.key);
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    buttons?.[index]?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const current = tabs.findIndex((tab) => tab.key === active);
    if (current < 0) return;
    if (event.key === "ArrowRight") {
      event.preventDefault();
      moveTo((current + 1) % tabs.length);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      moveTo((current - 1 + tabs.length) % tabs.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      moveTo(0);
    } else if (event.key === "End") {
      event.preventDefault();
      moveTo(tabs.length - 1);
    }
  };

  return (
    <div
      ref={listRef}
      className="filter-tabs"
      role="tablist"
      aria-label={label}
      onKeyDown={handleKeyDown}
    >
      {tabs.map((tab) => {
        const selected = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${tab.key}`}
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel-${tab.key}`}
            // Rovierender Tabindex: die ganze Leiste ist EINE Tabstopp-Stelle,
            // von dort geht es mit den Pfeiltasten weiter. Mehrere einzelne
            // Tabstopps zwischen Kopfzeile und Inhalt waeren mit der Tastatur
            // eine Zumutung.
            tabIndex={selected ? 0 : -1}
            className={`filter-tab ${selected ? "filter-tab--active" : ""}`}
            onClick={() => onChange(tab.key)}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

interface TabPanelProps<K extends string> {
  idPrefix: string;
  tabKey: K;
  active: K;
  children: ReactNode;
}

/**
 * Ein Registerbereich. Rendert nichts, solange sein Register nicht gewaehlt ist
 * - `hidden` allein wuerde die Abfragen aller Bereiche gleichzeitig starten,
 * obwohl die meisten davon niemand sieht.
 */
export function TabPanel<K extends string>({
  idPrefix,
  tabKey,
  active,
  children,
}: TabPanelProps<K>) {
  if (tabKey !== active) return null;
  return (
    <div
      role="tabpanel"
      id={`${idPrefix}-panel-${tabKey}`}
      aria-labelledby={`${idPrefix}-tab-${tabKey}`}
      // Der Bereich selbst ist anfahrbar: nach dem Registerwechsel landet der
      // naechste Tabdruck im Inhalt und nicht hinter ihm.
      tabIndex={0}
    >
      {children}
    </div>
  );
}
