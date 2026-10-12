import { useEffect, useRef, type ReactNode } from "react";

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

interface ReimbursementModalProps {
  titleId: string;
  descriptionId?: string;
  onClose: () => void;
  className?: string;
  children: ReactNode;
}

/**
 * Gemeinsame Modalhülle für Gesundheitsvorgänge und Extraktionsprüfung.
 *
 * Der sichtbare Vertrag besteht aus `reimbursement-dialog-overlay` und
 * `reimbursement-dialog-panel`; Tastatur- und Fokusverhalten leben bewusst
 * ebenfalls hier, damit `aria-modal` nie nur ein leeres Versprechen ist.
 */
export function ReimbursementModal({
  titleId,
  descriptionId,
  onClose,
  className,
  children,
}: ReimbursementModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    const dialog = dialogRef.current;
    document.body.style.overflow = "hidden";

    const preferredFocus = dialog?.querySelector<HTMLElement>(
      "[data-reimbursement-modal-initial-focus]",
    );
    const firstFocus = dialog?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
    (preferredFocus ?? firstFocus ?? dialog)?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialog) return;

      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      const first = focusable[0];
      const last = focusable.at(-1);
      if (!first || !last) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  return (
    <div
      className="reimbursement-dialog-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCloseRef.current();
      }}
    >
      <div
        ref={dialogRef}
        className={["reimbursement-dialog-panel", className].filter(Boolean).join(" ")}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
      >
        {children}
      </div>
    </div>
  );
}
