import AsyncStorage from "@react-native-async-storage/async-storage";
import { CLOUD_API_BASE_URL } from "../api/config";
import { CLOUD_SUPABASE_ANON_KEY, CLOUD_SUPABASE_URL } from "../auth/supabase";

export type ServerMode = "cloud" | "self-hosted";

export type ServerConfig = {
  mode: ServerMode;
  apiBaseUrl: string;
  supabaseUrl: string;
  supabaseAnonKey: string;
  instanceDisplayName: string | null;
};

// Reine App-Einstellung (welchen Server nutze ich), kein Secret - deshalb
// AsyncStorage statt expo-secure-store, gleiches Muster wie
// ../security/appLockPreference.ts und ../household/HouseholdProvider.tsx.
const SERVER_CONFIG_STORAGE_KEY = "finanzplaner.serverConfig";

export const CLOUD_PRESET: ServerConfig = {
  mode: "cloud",
  apiBaseUrl: CLOUD_API_BASE_URL,
  supabaseUrl: CLOUD_SUPABASE_URL,
  supabaseAnonKey: CLOUD_SUPABASE_ANON_KEY,
  instanceDisplayName: null,
};

function isServerConfig(value: unknown): value is ServerConfig {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ServerConfig>;
  return (
    (candidate.mode === "cloud" || candidate.mode === "self-hosted") &&
    typeof candidate.apiBaseUrl === "string" &&
    typeof candidate.supabaseUrl === "string" &&
    typeof candidate.supabaseAnonKey === "string" &&
    (candidate.instanceDisplayName === null || typeof candidate.instanceDisplayName === "string")
  );
}

/** Gespeicherte Serverwahl aus einer frueheren Sitzung - null, wenn noch nie
 * eine getroffen wurde ODER der gespeicherte Wert nicht mehr die erwartete
 * Form hat (z. B. nach einem App-Update). Ein defekter/unerwarteter Eintrag
 * darf den Start nicht zum Absturz bringen, sondern fuehrt zurueck auf die
 * Serverauswahl. */
export async function getStoredServerConfig(): Promise<ServerConfig | null> {
  const raw = await AsyncStorage.getItem(SERVER_CONFIG_STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return isServerConfig(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export async function setStoredServerConfig(config: ServerConfig): Promise<void> {
  await AsyncStorage.setItem(SERVER_CONFIG_STORAGE_KEY, JSON.stringify(config));
}

export async function clearStoredServerConfig(): Promise<void> {
  await AsyncStorage.removeItem(SERVER_CONFIG_STORAGE_KEY);
}
