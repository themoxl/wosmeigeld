import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

// Menue-Flaeche fuer Spaltenkoepfe.
//
// Bewusst ein Portal mit `position: fixed` statt des sonst im Projekt
// ueblichen `position: absolute` im relativ positionierten Elternelement
// (.facet-popover): Spaltenkoepfe stehen INNERHALB von .txn-table-wrapper,
// und der hat `overflow: auto`. Ein absolut positioniertes Menue wuerde dort
// am Rand des Scrollbereichs abgeschnitten - genau am oberen Rand, wo der
// Kopf klebt, waere also fast nichts davon sichtbar.
//
// Der Preis dafuer ist, dass die Position bei jedem Scrollen neu berechnet
// werden muss; das erledigt `reposition` an einem Capture-Listener, damit auch
// das Scrollen INNERER Container (der Tabellenwrapper selbst) ankommt.

interface GridPopoverProps {
  anchor: HTMLElement | null;
  onClose: () => void;
  /** Zugaengliche Beschriftung der Menueflaeche. */
  label: string;
  children: ReactNode;
  className?: string;
  /** Ausrichtung an der linken oder rechten Kante des Ankers. */
  align?: "start" | "end";
}

const VIEWPORT_MARGIN = 8;

export function GridPopover({
  anchor,
  onClose,
  label,
  children,
  className,
  align = "start",
}: GridPopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);

  const reposition = useCallback(() => {
    const panel = panelRef.current;
    if (!anchor || !panel) {
      return;
    }
    const anchorRect = anchor.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();

    let left = align === "end" ? anchorRect.right - panelRect.width : anchorRect.left;
    left = Math.min(left, window.innerWidth - panelRect.width - VIEWPORT_MARGIN);
    left = Math.max(VIEWPORT_MARGIN, left);

    // Nach unten aufklappen, solange Platz ist - sonst nach oben. Ein Menue,
    // das unten aus dem Fenster laeuft, waere ohne Scrollen nicht bedienbar.
    let top = anchorRect.bottom + 4;
    if (top + panelRect.height > window.innerHeight - VIEWPORT_MARGIN) {
      const above = anchorRect.top - panelRect.height - 4;
      top = above >= VIEWPORT_MARGIN ? above : VIEWPORT_MARGIN;
    }

    setPosition((current) =>
      current && current.top === top && current.left === left ? current : { top, left },
    );
  }, [anchor, align]);

  useLayoutEffect(() => {
    reposition();
  }, [reposition]);

  useEffect(() => {
    // Capture-Phase: Scrollen findet im Tabellenwrapper statt, nicht am
    // Fenster - ohne `capture` bekaeme das Menue davon nichts mit und bliebe
    // an einer Stelle stehen, an der der Kopf laengst nicht mehr ist.
    const handle = () => reposition();
    window.addEventListener("scroll", handle, true);
    window.addEventListener("resize", handle);
    return () => {
      window.removeEventListener("scroll", handle, true);
      window.removeEventListener("resize", handle);
    };
  }, [reposition]);

  useEffect(() => {
    const handlePointer = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target)) return;
      if (anchor?.contains(target)) return;
      onClose();
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("mousedown", handlePointer);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handlePointer);
      document.removeEventListener("keydown", handleKey);
    };
  }, [anchor, onClose]);

  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label={label}
      className={`grid-popover ${className ?? ""}`}
      style={{
        top: position?.top ?? 0,
        left: position?.left ?? 0,
        // Vor der ersten Messung wuerde das Menue sonst kurz oben links
        // aufblitzen.
        visibility: position ? "visible" : "hidden",
      }}
    >
      {children}
    </div>,
    document.body,
  );
}
