import { useEffect, useState } from "react";
import { displayedBox } from "../../lib/pdfGeometry";

export interface FieldLocation {
  page_number: number;
  box: [number, number, number, number];
  method?: string;
}

export function EvidencePageViewer({
  pageCount,
  location,
  rotation,
  loadPage,
  onPage,
}: {
  pageCount: number;
  location: FieldLocation;
  rotation: number;
  loadPage: (page: number) => Promise<Blob>;
  onPage: (page: number) => void;
}) {
  const [image, setImage] = useState<{ page: number; url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    let url: string | null = null;
    loadPage(location.page_number)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setImage({ page: location.page_number, url });
        setError(null);
      })
      .catch(() => {
        if (!cancelled)
          setError(
            "Die Seite konnte nicht geladen werden. Das Original lässt sich in einem neuen Tab öffnen.",
          );
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [loadPage, location.page_number]);
  const [x0, y0, x1, y1] = displayedBox(location.box, rotation);
  return (
    <div className="reimbursement-evidence-viewer">
      <nav aria-label="PDF-Seiten" className="reimbursement-evidence-nav">
        <button
          type="button"
          className="btn-secondary"
          disabled={location.page_number <= 1}
          onClick={() => onPage(location.page_number - 1)}
        >
          Vorherige Seite
        </button>
        <span>
          Seite {location.page_number} von {pageCount}
        </span>
        <button
          type="button"
          className="btn-secondary"
          disabled={location.page_number >= pageCount}
          onClick={() => onPage(location.page_number + 1)}
        >
          Nächste Seite
        </button>
      </nav>
      {error && <p role="alert">{error}</p>}
      {image?.page === location.page_number ? (
        <div className="reimbursement-evidence-sheet">
          <img src={image.url} alt={`PDF-Seite ${location.page_number}`} />
          {x1 > x0 && y1 > y0 && (
            <span
              className="reimbursement-evidence-highlight"
              aria-label="Fundstelle im Original"
              style={{
                left: `${x0 * 100}%`,
                top: `${y0 * 100}%`,
                width: `${(x1 - x0) * 100}%`,
                height: `${(y1 - y0) * 100}%`,
              }}
            />
          )}
        </div>
      ) : (
        !error && <p role="status">PDF-Seite wird geladen …</p>
      )}
      {location.method?.startsWith("vision") && (
        <p>Die Bildlesung markiert eine ungefähre Fundstelle. Bitte am Original prüfen.</p>
      )}
    </div>
  );
}
