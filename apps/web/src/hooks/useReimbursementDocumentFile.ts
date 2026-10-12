import { useEffect, useState } from "react";
import { fetchReimbursementDocumentFile } from "../lib/reimbursementsApi";

export interface ReimbursementDocumentFile {
  url: string | null;
  loading: boolean;
  error: string | null;
}

interface SettledFile {
  documentId: string;
  url: string | null;
  error: string | null;
}

/**
 * Haelt genau eine Object-URL fuer das gerade geoeffnete Original.
 *
 * Der Download braucht Bearer-Token und Haushaltskopf, kann also nicht direkt
 * als `src` gesetzt werden. Jede vorherige URL wird beim Wechsel und beim
 * Unmount freigegeben - sonst bleiben die Blobs bis zum Neuladen des Tabs im
 * Speicher liegen, was bei privaten Gesundheitsdokumenten gerade nicht
 * gewuenscht ist.
 *
 * `loading` wird abgeleitet statt gesetzt: solange das Ergebnis nicht zum
 * angefragten Dokument gehoert, laeuft der Abruf noch. Das spart einen
 * Zustandswechsel im Effektkoerper und damit eine Kaskadenrenderung.
 */
export function useReimbursementDocumentFile(
  token: string | null,
  householdId: string | null,
  documentId: string | null,
): ReimbursementDocumentFile {
  const [settled, setSettled] = useState<SettledFile | null>(null);

  useEffect(() => {
    if (!token || !householdId || !documentId) return;

    let objectUrl: string | null = null;
    let cancelled = false;

    void fetchReimbursementDocumentFile(token, householdId, documentId)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSettled({ documentId, url: objectUrl, error: null });
      })
      .catch((failure: unknown) => {
        if (cancelled) return;
        setSettled({
          documentId,
          url: null,
          error:
            failure instanceof Error
              ? failure.message
              : "Das Dokument konnte nicht geladen werden.",
        });
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, householdId, token]);

  if (!token || !householdId || !documentId) {
    return { url: null, loading: false, error: null };
  }
  if (settled?.documentId !== documentId) {
    return { url: null, loading: true, error: null };
  }
  return { url: settled.url, loading: false, error: settled.error };
}
