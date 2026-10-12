// Konvention laut Architekturplan: Expo-Variablen mit dem Praefix
// EXPO_PUBLIC_ sind zur Laufzeit im Client verfuegbar. Der Wert wird zentral
// in der Repo-Root-.env gepflegt (siehe apps/mobile/README.md und
// metro.config.js, das diese Datei zusaetzlich laedt). Fallback fuer die
// lokale Entwicklung ohne .env: http://localhost:8882. Bei Tests auf einem
// echten Geraet muss statt "localhost" die Rechner-IP im lokalen Netz
// verwendet werden, da das Geraet sonst sich selbst statt den Entwicklungs-
// rechner anspricht.
export const DEFAULT_API_BASE_URL = "http://localhost:8882";

export function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, "");
}

// "Cloud-Preset": der zur Build-Zeit eingebackene Wert. Bleibt die Adresse,
// die "Finanzplaner Cloud" im Server-Setup verwendet (siehe
// src/config/serverConfig.ts). Fuer "Eigener Server" wird stattdessen die
// vom Nutzer eingegebene Adresse per setApiBaseUrl() aktiv gesetzt.
export const CLOUD_API_BASE_URL = normalizeBaseUrl(
  process.env.EXPO_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL,
);

// Adressen, die nur auf dem Entwicklungsrechner oder im lokalen Netz gelten.
// Ein Build, in dem eine solche Adresse steckt, hat schlicht kein
// Cloud-Angebot - egal was der Knopf verspricht.
const _PRIVATE_HOST =
  /^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|169\.254\.\d+\.\d+|\[?::1\]?|host\.docker\.internal|.+\.local)$/i;

/** Gibt es in diesem Build ueberhaupt eine betriebene Instanz?
 *
 * `EXPO_PUBLIC_API_BASE_URL` wird zur Buildzeit eingebacken und zeigt in der
 * Entwicklung auf localhost oder die Rechner-IP. Ein Store-Build mit einem
 * solchen Wert darf keine "Cloud" anbieten: der Nutzer tippt darauf und landet
 * bei einem Server, den nur der Entwickler erreicht. Die Serverauswahl blendet
 * die Schaltflaeche deshalb aus, wenn hier keine oeffentliche Adresse steht -
 * uebrig bleibt der Weg ueber die selbst eingegebene Adresse. */
export const HAS_CLOUD_PRESET: boolean = (() => {
  try {
    const host = new URL(CLOUD_API_BASE_URL).hostname;
    return host.length > 0 && !_PRIVATE_HOST.test(host);
  } catch {
    return false;
  }
})();

// Laufzeit-veraenderlich statt fixer Konstante: src/api/client.ts und
// src/api/health.ts lesen ueber getApiBaseUrl() statt eine Konstante direkt
// zu importieren, damit ServerConfigProvider die Adresse beim Start bzw.
// bei einem Serverwechsel umschalten kann, ohne einen Neustart der App zu
// erzwingen.
let currentApiBaseUrl = CLOUD_API_BASE_URL;

export function getApiBaseUrl(): string {
  return currentApiBaseUrl;
}

export function setApiBaseUrl(url: string): void {
  currentApiBaseUrl = normalizeBaseUrl(url);
}
