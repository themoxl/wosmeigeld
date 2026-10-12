import * as LocalAuthentication from "expo-local-authentication";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";
import { AppState } from "react-native";
import { getAppLockEnabledPreference, setAppLockEnabledPreference } from "./appLockPreference";

export type AppLockEnableResult =
  { success: true } | { success: false; reason: "not_supported" | "auth_failed" };

type AppLockContextValue = {
  /** Persistierte Nutzerpraeferenz ("App-Sperre aktiviert"). */
  isEnabled: boolean;
  /** Ob die Praeferenz bereits aus AsyncStorage geladen wurde. */
  isHydrated: boolean;
  /** Ob der Sperr-Overlay gerade angezeigt werden soll. */
  isLocked: boolean;
  /** Vom Lock-Screen nach erfolgreicher Authentifizierung aufgerufen. */
  unlock: () => void;
  /** Fuehrt eine Bestaetigungs-Authentifizierung durch und aktiviert die Sperre erst bei Erfolg. */
  enable: () => Promise<AppLockEnableResult>;
  /** Deaktiviert die Sperre ohne erneute Authentifizierung (ein gesperrtes Geraet hat den Zugriff bereits geschuetzt). */
  disable: () => Promise<void>;
};

const AppLockContext = createContext<AppLockContextValue | undefined>(undefined);

export function AppLockProvider({ children }: PropsWithChildren) {
  const [isEnabled, setIsEnabled] = useState(false);
  const [isHydrated, setIsHydrated] = useState(false);
  // "background" setzt locked=true; der Lock-Screen selbst setzt es via
  // unlock() wieder zurueck. Bewusst nur auf "background" reagieren, nicht
  // auf "inactive": iOS durchlaeuft active->inactive->active auch rund um
  // den Biometrie-Prompt selbst und System-UI (Control Center,
  // Benachrichtigungsbanner) - ein Lock bei "inactive" wuerde eine
  // Doppel-Lock-Schleife erzeugen.
  const [locked, setLocked] = useState(false);

  useEffect(() => {
    let active = true;
    getAppLockEnabledPreference().then((enabled) => {
      if (!active) return;
      setIsEnabled(enabled);
      // Beim Kaltstart mit aktivierter Sperre direkt gesperrt starten -
      // nicht erst beim ersten Backgrounding.
      setLocked(enabled);
      setIsHydrated(true);
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!isEnabled) return;
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "background") {
        setLocked(true);
      }
    });
    return () => subscription.remove();
  }, [isEnabled]);

  const unlock = useCallback(() => setLocked(false), []);

  const enable = useCallback(async (): Promise<AppLockEnableResult> => {
    const level = await LocalAuthentication.getEnrolledLevelAsync();
    if (level === LocalAuthentication.SecurityLevel.NONE) {
      // Weder Biometrie noch Geraete-PIN eingerichtet - eine Sperre waere
      // nicht durchsetzbar und damit ein Aussperr-Risiko statt Sicherheit.
      return { success: false, reason: "not_supported" };
    }

    const result = await LocalAuthentication.authenticateAsync({
      promptMessage: "Bestätige, um die App-Sperre zu aktivieren",
      disableDeviceFallback: false,
    });
    if (!result.success) {
      return { success: false, reason: "auth_failed" };
    }

    await setAppLockEnabledPreference(true);
    setIsEnabled(true);
    return { success: true };
  }, []);

  const disable = useCallback(async () => {
    await setAppLockEnabledPreference(false);
    setIsEnabled(false);
    setLocked(false);
  }, []);

  const value = useMemo<AppLockContextValue>(
    () => ({
      isEnabled,
      isHydrated,
      isLocked: isHydrated && isEnabled && locked,
      unlock,
      enable,
      disable,
    }),
    [isEnabled, isHydrated, locked, unlock, enable, disable],
  );

  return <AppLockContext.Provider value={value}>{children}</AppLockContext.Provider>;
}

/** Zustand und Aktionen des biometrischen App-Locks (Architekturplan Abschnitt 5 "Mobile Sicherheit"). */
export function useAppLock(): AppLockContextValue {
  const ctx = useContext(AppLockContext);
  if (!ctx) {
    throw new Error("useAppLock() muss innerhalb von <AppLockProvider> verwendet werden.");
  }
  return ctx;
}
