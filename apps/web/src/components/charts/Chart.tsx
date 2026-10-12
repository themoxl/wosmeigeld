import { useEffect, useRef, useState, type ReactNode } from "react";
import * as echarts from "echarts/core";
import { BarChart, HeatmapChart, LineChart, PieChart } from "echarts/charts";
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";
import { useDesignId } from "../../styles/design";
import { resolveCssVars } from "./chartTheme";

// Ein einziger ECharts-Wrapper (docs/ANALYSE-EXPLORER-PLAN.md, D.1).
//
// Import ueber echarts/core mit ausdruecklicher Registrierung statt des
// Gesamtpakets: sonst waechst das Bundle um rund 1 MB, von dem der groesste
// Teil nie gebraucht wird.
echarts.use([
  BarChart,
  LineChart,
  PieChart,
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DatasetComponent,
  VisualMapComponent,
  // Schwellenlinien und getoente Zonen (Nulllinie, Sicherheitspuffer in der
  // Liquiditaetsvorschau). Ohne die beiden Komponenten ignoriert ECharts
  // `markLine`/`markArea` stillschweigend - der Fehler waere nur im Bild
  // sichtbar, nicht im Code.
  MarkLineComponent,
  MarkAreaComponent,
  CanvasRenderer,
]);

/**
 * Kann diese Umgebung ueberhaupt auf Canvas zeichnen?
 *
 * Einmal beim Modulladen geprueft statt im Effect: `setState` synchron in
 * einem Effect ist unter den React-Compiler-Regeln dieses Projekts ein
 * Fehler (react-hooks/set-state-in-effect), und die Antwort aendert sich zur
 * Laufzeit ohnehin nicht. Trifft in jsdom (Tests, SSR) zu - dort faellt jedes
 * Diagramm auf seine Tabelle zurueck, statt die ganze Seite mitzureissen.
 */
function detectCanvasSupport(): boolean {
  if (typeof document === "undefined") return false;
  try {
    const probe = document.createElement("canvas");
    return typeof probe.getContext === "function" && probe.getContext("2d") !== null;
  } catch {
    return false;
  }
}

const CANVAS_SUPPORTED = detectCanvasSupport();

export interface ChartProps {
  option: EChartsOption;
  height?: number;
  /** Wird mit dem Datenindex der angeklickten Serie aufgerufen. */
  onSegmentClick?: (dataIndex: number, seriesIndex: number) => void;
  /** Barrierefreie Beschreibung; erscheint als aria-label. */
  description: string;
  /**
   * Tabellarische Entsprechung. Canvas ist weder vorlesbar noch in
   * Playwright pruefbar - deshalb hat jedes Diagramm einen Umschalter auf
   * eine echte Tabelle. Das ist gleichzeitig Barrierefreiheit und
   * Testoberflaeche.
   */
  tableFallback?: ReactNode;
}

export function Chart({
  option,
  height = 280,
  onSegmentClick,
  description,
  tableFallback,
}: ChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);
  const clickRef = useRef(onSegmentClick);
  const [showTable, setShowTable] = useState(false);
  // Nach jedem Designwechsel neu setzen: die Farben der Option sind Verweise
  // auf CSS-Variablen (chartTheme.ts) und werden erst hier aufgeloest. Auch
  // nach dem Zurueckschalten von der Tabelle: dann gibt es eine neue Instanz,
  // aber oft keine neue Option (useMemo im Aufrufer).
  const designId = useDesignId();

  // Der Klick-Handler laeuft ueber ein Ref, das im Effect nachgezogen wird:
  // sonst muesste das Chart bei jeder neuen Callback-Identitaet neu
  // aufgebaut werden, und eine Zuweisung waehrend des Renderns waere ein
  // Seiteneffekt (react-hooks/purity).
  useEffect(() => {
    clickRef.current = onSegmentClick;
  }, [onSegmentClick]);

  useEffect(() => {
    const container = containerRef.current;
    if (showTable || !CANVAS_SUPPORTED || !container) return;

    let instance: echarts.ECharts;
    try {
      instance = echarts.init(container, undefined, { renderer: "canvas" });
    } catch {
      // Trotz Canvas-Unterstuetzung gescheitert (z. B. Container mit
      // Groesse 0): das Diagramm bleibt leer, die Tabelle ist weiterhin
      // erreichbar - aber die Seite laeuft weiter.
      return;
    }
    instanceRef.current = instance;

    instance.on("click", (params) => {
      // seriesIndex ist in ECharts optional (Klicks koennen auch auf
      // Nicht-Serien-Elemente wie die Legende fallen); ohne Datenindex gibt
      // es nichts aufzuklappen.
      if (typeof params.dataIndex !== "number") return;
      clickRef.current?.(params.dataIndex, params.seriesIndex ?? 0);
    });

    // ResizeObserver fehlt in aelteren Umgebungen und in jsdom.
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => instance.resize());
    observer?.observe(container);

    return () => {
      observer?.disconnect();
      try {
        instance.dispose();
      } catch {
        // Eine werfende Aufraeumfunktion bricht Reacts Unmount ab und reisst
        // die ganze Seite mit. Ein nicht freigegebenes Chart ist demgegenueber
        // das kleinere Uebel.
      }
      instanceRef.current = null;
    };
  }, [showTable]);

  useEffect(() => {
    if (!instanceRef.current) return;
    try {
      // notMerge: sonst bleiben Serien einer vorherigen Abfrage stehen, wenn
      // die neue weniger Kategorien hat.
      instanceRef.current.setOption(resolveCssVars(option), { notMerge: true });
    } catch {
      // Eine unpassende Option darf die Seite nicht mitnehmen; das Diagramm
      // behaelt dann seinen letzten gueltigen Zustand.
    }
  }, [option, designId, showTable]);

  // Ohne tabellarische Entsprechung bleibt bei einem Fehler nur ein Hinweis -
  // eine leere Flaeche waere schlimmer als eine ehrliche Meldung.
  const fallbackOnly = !CANVAS_SUPPORTED || (showTable && Boolean(tableFallback));

  return (
    <figure className="chart">
      <div className="chart-toolbar">
        {CANVAS_SUPPORTED && (
          <button
            type="button"
            className="chart-toggle"
            onClick={() => setShowTable((value) => !value)}
            aria-pressed={showTable}
          >
            {showTable ? "Als Diagramm anzeigen" : "Als Tabelle anzeigen"}
          </button>
        )}
      </div>

      {fallbackOnly ? (
        <div className="chart-table">
          {tableFallback ?? (
            <p className="placeholder-note">
              Das Diagramm konnte nicht gezeichnet werden. Die Zahlen stehen in der Tabelle
              darunter.
            </p>
          )}
        </div>
      ) : (
        <div
          ref={containerRef}
          className="chart-canvas"
          style={{ height }}
          role="img"
          aria-label={description}
        />
      )}
      <figcaption className="chart-description">{description}</figcaption>
    </figure>
  );
}
