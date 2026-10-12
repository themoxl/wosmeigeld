import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

interface RowMenuProps {
  label: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}

// Punktmenue fuer Tabellenzeilen. Oeffnet standardmaessig nach unten, klappt
// aber nach oben um, wenn unterhalb des Buttons nicht genug Platz im
// Viewport ist - sonst ragt das Menue bei Zeilen am unteren Bildschirmrand
// (letzte Zeilen einer langen Tabelle) ueber den sichtbaren Bereich hinaus
// und wirkt dort winzig/abgeschnitten, bis man weiterscrollt.
export function RowMenu({ label, open, onToggle, children }: RowMenuProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [openUpward, setOpenUpward] = useState(false);

  useLayoutEffect(() => {
    if (!open || !wrapperRef.current) {
      return;
    }
    const rect = wrapperRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    setOpenUpward(spaceBelow < 220 && spaceAbove > spaceBelow);
  }, [open]);

  return (
    <div className="category-menu" ref={wrapperRef}>
      <button
        type="button"
        className="icon-btn"
        aria-label={label}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={onToggle}
      >
        ⋯
      </button>
      {open && (
        <div
          className={`category-menu-popup category-menu-popup--end${
            openUpward ? " category-menu-popup--up" : ""
          }`}
          role="menu"
        >
          {children}
        </div>
      )}
    </div>
  );
}
