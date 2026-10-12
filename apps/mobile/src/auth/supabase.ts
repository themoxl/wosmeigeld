import AsyncStorage from "@react-native-async-storage/async-storage";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { AppState, Platform } from "react-native";
import { secureSessionStorage } from "./tokenStore";

// Auth laeuft NICHT ueber die eigene API, sondern client-seitig direkt gegen
// Supabase Auth (siehe FINANZ_APP_ARCHITEKTURPLAN.md Abschnitt 2: "JWT wird
// durch die BFF-API validiert", nicht dort ausgestellt). Die Werte unten sind
// dieselben Demo-Zugangsdaten, die `supabase start` fuer JEDES lokale Projekt
// gleich erzeugt und die in .env.example ausdruecklich als "kein echtes
// Secret" dokumentiert sind - deshalb hier als Fallback vertretbar, exakt
// analog zum Muster in ../api/config.ts (EXPO_PUBLIC_API_BASE_URL /
// DEFAULT_API_BASE_URL).
export const DEFAULT_SUPABASE_URL = "http://127.0.0.1:55321";
export const DEFAULT_SUPABASE_ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0";

// "Cloud-Preset": der zur Build-Zeit eingebackene Wert, analog zu
// CLOUD_API_BASE_URL in ../api/config.ts. Fuer "Eigener Server" liefert
// stattdessen die Discovery (../api/discovery.ts) die Werte der jeweiligen
// Installation.
export const CLOUD_SUPABASE_URL = process.env.EXPO_PUBLIC_SUPABASE_URL ?? DEFAULT_SUPABASE_URL;
export const CLOUD_SUPABASE_ANON_KEY =
  process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ?? DEFAULT_SUPABASE_ANON_KEY;

// `npx expo export` rendert die Web-Ausgabe statisch vor (Static Rendering,
// siehe app.json "web.output": "static") - dafuer wird jede Route inklusive
// dieses Root-Layouts einmal in einer Node.js-Umgebung ausgefuehrt, in der es
// kein globales `window` gibt. In React Native (iOS/Android) UND im echten
// Browser ist `window` dagegen immer vorhanden (RN aliast es auf `global`).
// createClient() ruft intern sofort (nicht erst in einem Effect) die
// Storage-Adapter-Methoden auf, um eine vorhandene Session zu laden; die
// Web-Implementierung von AsyncStorage greift dabei synchron auf
// `window.localStorage` zu und wuerde den Node-Static-Rendering-Durchlauf
// mit "window is not defined" zum Absturz bringen. Deshalb bekommt der
// Client dort einen No-Op-Storage statt eines echten Storage-Adapters -
// Session-Restore auf dem echten Geraet/Browser ist davon nicht betroffen.
const isStaticRenderingPass = typeof window === "undefined";

const noopStorage = {
  getItem: async () => null,
  setItem: async () => {},
  removeItem: async () => {},
};

// expo-secure-store nutzt auf iOS/Android den geraeteeigenen sicheren
// Schluesselspeicher (Keychain/Keystore) und ist dort dem bisherigen
// unverschluesselten AsyncStorage vorzuziehen (Architekturplan Abschnitt 5
// "Mobile Sicherheit"). Im Web-Build existiert kein Keychain-Aequivalent,
// das aus JavaScript erreichbar waere - expo-secure-store unterstuetzt Web
// nicht, daher bleibt dort AsyncStorage (dessen Web-Implementierung
// `window.localStorage` nutzt) die einzig sinnvolle Wahl.
const nativeStorage = Platform.OS === "web" ? AsyncStorage : secureSessionStorage;

export type SupabaseClientHandle = {
  client: SupabaseClient;
  /** AppState-Listener abhaengen, wenn dieser Client durch einen Serverwechsel
   * ersetzt wird - sonst liefe der alte Auto-Refresh-Handler nach dem
   * Wechsel unnoetig (und potenziell verwirrend) weiter mit. */
  dispose: () => void;
};

// Fabrik statt Modul-Singleton: ServerConfigProvider (siehe
// src/config/ServerConfigProvider.tsx) kennt die aktive Server-URL/den
// Anon-Key erst zur Laufzeit (Cloud-Preset oder per Discovery ermittelter
// Eigener-Server-Wert) und ruft dies bei jedem Serverwechsel neu auf.
// supabase-js braucht fuer React Native/Expo einen eigenen Storage-Adapter
// fuer Session-Persistenz (Standardmuster laut Supabase-Dokumentation):
// autoRefreshToken/persistSession an, detectSessionInUrl aus (kein
// Browser-Redirect-Flow auf dem Geraet).
export function createSupabaseClient(url: string, anonKey: string): SupabaseClientHandle {
  const client = createClient(url, anonKey, {
    auth: {
      storage: isStaticRenderingPass ? noopStorage : nativeStorage,
      autoRefreshToken: !isStaticRenderingPass,
      persistSession: !isStaticRenderingPass,
      detectSessionInUrl: false,
    },
  });

  // Empfohlenes Supabase-/React-Native-Muster: den Auto-Refresh-Timer nur
  // laufen lassen, waehrend die App im Vordergrund ist. Ohne dies wuerde der
  // Refresh-Timer auch im Hintergrund unnoetig weiterlaufen bzw. nach langer
  // Inaktivitaet nicht zuverlaessig wieder anlaufen. Im Node-Static-Rendering-
  // Durchlauf (siehe oben) gibt es keine sinnvolle AppState-Registrierung.
  const subscription = isStaticRenderingPass
    ? null
    : AppState.addEventListener("change", (state) => {
        if (state === "active") {
          void client.auth.startAutoRefresh();
        } else {
          void client.auth.stopAutoRefresh();
        }
      });

  return {
    client,
    dispose: () => {
      subscription?.remove();
      void client.auth.stopAutoRefresh();
    },
  };
}
