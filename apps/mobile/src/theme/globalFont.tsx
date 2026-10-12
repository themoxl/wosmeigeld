import { createElement, type ComponentType } from "react";
import { StyleSheet, type TextStyle } from "react-native";
import { resolveFontStyle } from "./fonts";

// Inter fuer die gesamte Oberflaeche, JetBrains Mono fuer Geldbetraege - ohne
// in ueber zweihundert Dateien jede einzelne Textzeile anzufassen.
//
// React Native kennt keine App-weite Standardschrift. Der frueher uebliche
// Weg (Text.defaultProps) ist mit React 19 fort, und <Text> ist seit React
// Native 0.81 eine schlichte Funktionskomponente ohne .render, an die sich
// nichts mehr anhaengen laesst.
//
// Was bleibt, ist der Modulzugang: react-native/index.js exportiert Text und
// TextInput als LAZY GETTER ("get Text() { return require(...).default }").
// Der Getter laeuft bei jedem Zugriff neu. Tauscht man den Default-Export des
// dahinterliegenden Moduls einmal beim Start aus, bekommen alle Importeure ab
// dem naechsten Zugriff die umhuellte Fassung - auch die, die react-native
// bereits importiert haben, weil Babel `import { Text }` als Zugriff
// `_reactNative.Text` an der Verwendungsstelle stehen laesst.
//
// Der Eingriff ist bewusst vollstaendig abgesichert: schlaegt er fehl (andere
// React-Native-Fassung, react-native-web, Testumgebung), bleibt die
// Systemschrift stehen und die App laeuft unveraendert weiter.

type StyledProps = { style?: unknown };

// Einmal beim Laden aufloesen statt bei jedem Rendern: react-native/index.js
// exportiert StyleSheet als Getter mit require() dahinter, und dieser require
// darf nicht im Renderpfad jedes einzelnen <Text> stehen.
const flattenStyle = StyleSheet.flatten;

function withAppFont<P extends StyledProps>(Component: ComponentType<P>): ComponentType<P> {
  function AppFontComponent(props: P) {
    const flattened = flattenStyle(props.style as TextStyle | undefined) as TextStyle | undefined;
    const patch = resolveFontStyle(flattened?.fontFamily, flattened?.fontWeight);
    if (!patch) {
      return createElement(Component, props);
    }
    return createElement(Component, { ...props, style: [props.style, patch] });
  }

  const originalName =
    (Component as { displayName?: string; name?: string }).displayName ??
    (Component as { name?: string }).name ??
    "Component";
  AppFontComponent.displayName = originalName;

  // Statische Felder mitnehmen (z. B. TextInput.State), sonst brechen
  // Aufrufer, die sie erwarten.
  for (const key of Object.keys(Component)) {
    if (key === "displayName") continue;
    (AppFontComponent as unknown as Record<string, unknown>)[key] = (
      Component as unknown as Record<string, unknown>
    )[key];
  }

  return AppFontComponent as ComponentType<P>;
}

type PatchableModule = { default?: unknown; __appFontPatched?: boolean };

function patchModule(moduleObject: unknown): boolean {
  try {
    const target = moduleObject as PatchableModule | undefined;
    if (!target || typeof target.default !== "function" || target.__appFontPatched) {
      return false;
    }
    const wrapped = withAppFont(target.default as ComponentType<StyledProps>);
    target.default = wrapped;
    target.__appFontPatched = true;
    return true;
  } catch {
    return false;
  }
}

let applied = false;

/**
 * Einmal beim Start aufrufen, BEVOR der erste Bildschirm rendert (siehe
 * app/_layout.tsx). Mehrfachaufrufe sind wirkungslos.
 *
 * @returns true, wenn der Aufsatz greift und die App-Schrift App-weit gilt.
 */
export function applyGlobalFont(): boolean {
  if (applied) return true;
  let ok = false;
  try {
    // Statische Pfade: Metro loest require() nur mit Zeichenkettenliteral auf.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    ok = patchModule(require("react-native/Libraries/Text/Text")) || ok;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    ok = patchModule(require("react-native/Libraries/Components/TextInput/TextInput")) || ok;
  } catch {
    ok = false;
  }
  applied = ok;
  return ok;
}
