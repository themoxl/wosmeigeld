import { useFonts } from "expo-font";
import { useEffect } from "react";
// Jeder Schnitt einzeln ueber seinen Unterpfad, NICHT ueber den Paketstamm:
// @expo-google-fonts/inter/index.js macht ein require() auf alle 18 Dateien
// (neun Staerken mal aufrecht/kursiv). Metro folgt jedem davon und legt sie
// ins Erzeugnis - ueber den Stamm importiert wandern rund 6 MB Schriftdateien
// in die APK, von denen wir fuenf brauchen.
import { Inter_400Regular } from "@expo-google-fonts/inter/400Regular";
import { Inter_500Medium } from "@expo-google-fonts/inter/500Medium";
import { Inter_600SemiBold } from "@expo-google-fonts/inter/600SemiBold";
import { Inter_700Bold } from "@expo-google-fonts/inter/700Bold";
import { Inter_800ExtraBold } from "@expo-google-fonts/inter/800ExtraBold";
import { JetBrainsMono_400Regular } from "@expo-google-fonts/jetbrains-mono/400Regular";
import { JetBrainsMono_500Medium } from "@expo-google-fonts/jetbrains-mono/500Medium";
import { JetBrainsMono_700Bold } from "@expo-google-fonts/jetbrains-mono/700Bold";
import { JetBrainsMono_800ExtraBold } from "@expo-google-fonts/jetbrains-mono/800ExtraBold";
import { setFontsReady } from "./fonts";

// Genau die neun Schnitte, die das Designsystem verwendet - jeder weitere
// waere eine Schriftdatei mehr im APK ohne Gegenwert. Die Zuordnung
// Staerke -> Datei steht in fonts.ts (UI_BY_WEIGHT / MONO_BY_WEIGHT); beide
// Listen muessen zusammenpassen.
const APP_FONTS = {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
  Inter_800ExtraBold,
  JetBrainsMono_400Regular,
  JetBrainsMono_500Medium,
  JetBrainsMono_700Bold,
  JetBrainsMono_800ExtraBold,
};

/**
 * Laedt Inter und JetBrains Mono.
 *
 * Bewusst KEIN Aufhalten des Starts: bis die Dateien da sind, rendert die App
 * in der Systemschrift (resolveFontStyle() faellt darauf zurueck) und tauscht
 * sie danach aus. Ein weisser Bildschirm beim Kaltstart waere der schlechtere
 * Tausch - zumal ein fehlgeschlagener Ladevorgang die App sonst dauerhaft
 * blockieren wuerde.
 */
export function useAppFonts(): boolean {
  const [loaded, error] = useFonts(APP_FONTS);

  useEffect(() => {
    if (loaded) setFontsReady(true);
  }, [loaded]);

  useEffect(() => {
    if (error) {
      console.warn("[theme] App-Schriften konnten nicht geladen werden:", error.message);
    }
  }, [error]);

  return loaded;
}
