import { useEffect, type ReactNode } from "react";

// Kleiner wiederverwendbarer Dialog fuer Phase 2 (Split-Editor,
// Buchung bearbeiten, ...). Bewusst ohne <dialog>-Element, damit das
// Verhalten (Overlay-Klick + Escape schliessen, Fokus bleibt im Panel)
// in jsdom-Tests ohne Polyfill funktioniert.
interface ModalProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** "wide" fuer Dialoge mit Kategoriepicker - dessen Aufklappliste braucht
   *  spuerbar mehr Platz als die 520px des Standardpanels. "table" ist fuer
   *  breite, horizontal scrollbar bleibende Datentabellen gedacht. */
  size?: "default" | "wide" | "table";
  className?: string;
}

export function Modal({ title, onClose, children, size = "default", className }: ModalProps) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="modal-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        className={[
          "modal-panel",
          size === "default" ? "" : `modal-panel--${size}`,
          className ?? "",
        ]
          .filter(Boolean)
          .join(" ")}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-header">
          <h2 className="modal-title">{title}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Schließen">
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
