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
import { useColorScheme } from "react-native";
import { darkTheme, lightTheme, type MobileThemeTokens } from "./palette";
import { applyGlobalFont } from "./globalFont";
import { useAppFonts } from "./useAppFonts";

// Handy-Designsystem, aufgebaut nach docs/mobile_redesign_showcase.html.
// Die Farbwerte stehen in palette.ts, Masse in layout.ts, Schriften in
// fonts.ts. Hier wird nur entschieden, WELCHES der beiden Themes gilt.
//
// Gegenueber der frueheren Fassung kommt eine ausdrueckliche Wahl dazu: der
// Showcase hat einen Umschalter in der Kopfzeile, und der dunkle Entwurf ist
// die eigentliche Gestaltungsabsicht. "System" bleibt die Vorgabe, aber wer
// sein Telefon hell betreibt und die App trotzdem dunkel will, kann das jetzt
// festlegen (Einstellungen -> Erscheinungsbild).

export type ThemeTokens = MobileThemeTokens;
export type ThemeMode = "system" | "light" | "dark";

const STORAGE_KEY = "finanzplaner.themeMode.v1";

// Beim LADEN des Moduls, nicht in einem Effekt: ThemeProvider sitzt ganz
// aussen, und ein Effekt liefe erst NACH dem ersten Rendern - der erste Frame
// stuende dann noch in der Systemschrift und der Baum waere kurzzeitig
// gemischt.
applyGlobalFont();

const ThemeContext = createContext<ThemeTokens>(darkTheme);

type ThemeModeApi = {
  mode: ThemeMode;
  /** Tatsaechlich angezeigtes Schema, nachdem "system" aufgeloest wurde. */
  resolved: "light" | "dark";
  setMode: (mode: ThemeMode) => void;
};

const ThemeModeContext = createContext<ThemeModeApi>({
  mode: "system",
  resolved: "dark",
  setMode: () => {},
});

function isThemeMode(value: unknown): value is ThemeMode {
  return value === "system" || value === "light" || value === "dark";
}

export function ThemeProvider({ children }: PropsWithChildren) {
  const colorScheme = useColorScheme();
  const [mode, setModeState] = useState<ThemeMode>("system");

  // Schriften laden und den App-weiten Schriftaufsatz einhaengen. Beides
  // gehoert hierher, weil der Aufsatz greifen muss, bevor der erste Text
  // gerendert wird - und ThemeProvider sitzt in app/_layout.tsx ganz aussen.
  useAppFonts();

  useEffect(() => {
    let cancelled = false;
    void AsyncStorage.getItem(STORAGE_KEY)
      .then((stored) => {
        if (!cancelled && isThemeMode(stored)) setModeState(stored);
      })
      .catch(() => {
        // Keine gespeicherte Wahl lesbar: "system" bleibt stehen.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    void AsyncStorage.setItem(STORAGE_KEY, next).catch(() => {
      // Nicht speicherbar ist kein Grund, die Anzeige zu verweigern.
    });
  }, []);

  const resolved: "light" | "dark" =
    mode === "system" ? (colorScheme === "light" ? "light" : "dark") : mode;

  const theme = useMemo<ThemeTokens>(
    () => (resolved === "light" ? lightTheme : darkTheme),
    [resolved],
  );

  const modeApi = useMemo<ThemeModeApi>(
    () => ({ mode, resolved, setMode }),
    [mode, resolved, setMode],
  );

  return (
    <ThemeModeContext.Provider value={modeApi}>
      <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>
    </ThemeModeContext.Provider>
  );
}

/** Liefert die aktuellen semantischen Farb-Tokens (hell/dunkel). */
export function useTheme(): ThemeTokens {
  return useContext(ThemeContext);
}

/** Erscheinungsbild lesen und umstellen (Einstellungen, Kopfzeilen-Schalter). */
export function useThemeMode(): ThemeModeApi {
  return useContext(ThemeModeContext);
}
