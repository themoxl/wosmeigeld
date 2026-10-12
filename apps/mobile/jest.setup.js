// Zur Konfiguration in package.json: "testTimeout" steht dort auf 20000 statt
// auf den 5000 der Vorgabe. Die Suiten hier warten auf nichts - sie rendern,
// und ein einzelnes create() zieht bei den Sheet- und Screen-Tests den halben
// Modulgraph nach. Unter acht parallelen Workern reissen genau diese Tests die
// 5-Sekunden-Grenze, einzeln laufen sie in Millisekunden; der erhoehte Wert
// deckt die Lastspitze ab. Ein echtes Haengen faellt weiterhin auf, nur
// 15 Sekunden spaeter. (package.json erlaubt keinen Kommentar, deshalb hier.)
//
// Jest-Setup fuer die Expo-App. Die drei nativen Module, die der
// Offline-Kern aus Phase 9 verwendet (AsyncStorage, NetInfo, expo-crypto),
// haben in der Node-Testumgebung keine native Gegenseite und werden hier
// zentral durch die offiziellen bzw. minimale eigene Mocks ersetzt.

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"),
);

jest.mock("@react-native-community/netinfo", () =>
  require("@react-native-community/netinfo/jest/netinfo-mock"),
);

// react-native-safe-area-context liest die Insets ueber ein natives Modul.
// Ohne <SafeAreaProvider> wirft useSafeAreaInsets() - und den Provider
// bringt in der App die Wurzel mit, nicht der einzelne Bildschirm. Der
// offizielle Mock der Bibliothek liefert stattdessen Nullwerte und einen
// vereinfachten Provider; gebraucht wird er, seit SheetModal die untere
// Safe-Area selbst freihaelt.
jest.mock("react-native-safe-area-context", () => {
  const actual = jest.requireActual("react-native-safe-area-context");
  const insets = { top: 0, right: 0, bottom: 0, left: 0 };
  const frame = { x: 0, y: 0, width: 320, height: 640 };
  return {
    ...actual,
    initialWindowMetrics: { insets, frame },
    useSafeAreaInsets: () => insets,
    useSafeAreaFrame: () => frame,
  };
});

// expo-crypto liefert nur randomUUID(); ein zaehlender Ersatz reicht fuer
// Tests und macht erzeugte IDs zusaetzlich vorhersagbar.
jest.mock("expo-crypto", () => {
  let counter = 0;
  return {
    randomUUID: () => {
      counter += 1;
      return `00000000-0000-4000-8000-${String(counter).padStart(12, "0")}`;
    },
  };
});
