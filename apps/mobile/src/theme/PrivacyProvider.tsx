import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";

// Privat-Modus aus dem Showcase (Auge-Schalter in der Kopfzeile): alle
// Geldbetraege werden durch Punkte ersetzt, damit man die App im Zug oder im
// Grossraumbuero oeffnen kann, ohne sein Vermoegen offenzulegen.
//
// Bewusst App-weit statt je Bildschirm: der Nutzen entsteht erst, wenn der
// Schalter auch beim Wechsel auf "Berichte" oder "Vermoegen" noch greift.
// Die Wahl wird gespeichert - wer sie einschaltet, will sie meistens behalten.

const STORAGE_KEY = "finanzplaner.privacyMode.v1";
/** Ersatzdarstellung fuer einen verdeckten Betrag. */
export const MASKED_AMOUNT = "•••• €";
/** Ersatzdarstellung ohne Waehrungszeichen (Fliesstext, Legenden). */
export const MASKED_PLAIN = "••••";

type PrivacyApi = {
  isPrivacyMode: boolean;
  toggle: () => void;
  setPrivacyMode: (next: boolean) => void;
};

const PrivacyContext = createContext<PrivacyApi>({
  isPrivacyMode: false,
  toggle: () => {},
  setPrivacyMode: () => {},
});

export function PrivacyProvider({ children }: PropsWithChildren) {
  const [isPrivacyMode, setState] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void AsyncStorage.getItem(STORAGE_KEY)
      .then((stored) => {
        if (!cancelled && stored === "1") setState(true);
      })
      .catch(() => {
        // Nicht lesbar: sichtbar bleiben (kein stiller Datenverlust im UI).
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setPrivacyMode = useCallback((next: boolean) => {
    setState(next);
    void AsyncStorage.setItem(STORAGE_KEY, next ? "1" : "0").catch(() => {});
  }, []);

  const toggle = useCallback(() => {
    setState((current) => {
      const next = !current;
      void AsyncStorage.setItem(STORAGE_KEY, next ? "1" : "0").catch(() => {});
      return next;
    });
  }, []);

  const value = useMemo<PrivacyApi>(
    () => ({ isPrivacyMode, toggle, setPrivacyMode }),
    [isPrivacyMode, toggle, setPrivacyMode],
  );

  return <PrivacyContext.Provider value={value}>{children}</PrivacyContext.Provider>;
}

export function usePrivacy(): PrivacyApi {
  return useContext(PrivacyContext);
}

/**
 * Wendet den Privat-Modus auf einen bereits formatierten Betrag an.
 *
 * `override` erlaubt es Bildschirmen und Tests, den globalen Zustand gezielt
 * zu uebersteuern (die bestehenden Komponenten reichen `isPrivacyMode` als
 * Eigenschaft durch).
 */
export function useMaskedAmount(): (formatted: string, override?: boolean) => string {
  const { isPrivacyMode } = usePrivacy();
  return useCallback(
    (formatted: string, override?: boolean) =>
      (override ?? isPrivacyMode) ? MASKED_AMOUNT : formatted,
    [isPrivacyMode],
  );
}
