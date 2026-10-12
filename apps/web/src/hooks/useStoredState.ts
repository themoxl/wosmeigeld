import { useCallback, useState } from "react";

// Ansichtseinstellungen, die einen Reload ueberleben sollen, aber nicht in die
// URL gehoeren (Spaltenwahl, Filterzeile ein/aus).
//
// Bewusst getrennt vom URL-Zustand: eine geteilte Explorer-URL soll dieselbe
// AUSWERTUNG zeigen, nicht die Bildschirmvorlieben des Absenders erzwingen.
//
// Der Zugriff ist gekapselt, weil localStorage in gehaerteten Browserprofilen
// werfen kann (deaktivierte Speicherung, privater Modus). Ein Fehler beim
// Merken einer Spaltenbreite darf die Seite nicht abstuerzen lassen.

function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function useStoredState<T>(key: string, fallback: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => read(key, fallback));

  const store = useCallback(
    (next: T) => {
      setValue(next);
      try {
        window.localStorage.setItem(key, JSON.stringify(next));
      } catch {
        // Nicht speichern zu koennen ist kein Grund, die Aenderung zu verwerfen.
      }
    },
    [key],
  );

  return [value, store];
}
