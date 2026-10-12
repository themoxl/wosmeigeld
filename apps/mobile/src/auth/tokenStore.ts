import * as SecureStore from "expo-secure-store";

const ACCESS_TOKEN_KEY = "finanzplaner.accessToken";

export async function getStoredAccessToken(): Promise<string | null> {
  return SecureStore.getItemAsync(ACCESS_TOKEN_KEY);
}

export async function setStoredAccessToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(ACCESS_TOKEN_KEY, token);
}

export async function clearStoredAccessToken(): Promise<void> {
  await SecureStore.deleteItemAsync(ACCESS_TOKEN_KEY);
}

// Generischer Storage-Adapter fuer Supabase's `auth.storage`-Option
// (Architekturplan Abschnitt 2 "Mobile Cache: Expo SQLite plus sichere
// Tokenablage in SecureStore" und Abschnitt 5 "Mobile Sicherheit"). Anders als
// obiger Einzel-Key-Wrapper braucht Supabase ein generisches
// {getItem,setItem,removeItem}-Interface, da es darunter die komplette
// Session (Access- + Refresh-Token, Ablaufzeit, Nutzerdaten) unter einem
// selbst gewaehlten Schluessel (z. B. "sb-<project-ref>-auth-token") ablegt,
// nicht nur den Access-Token allein.
export const secureSessionStorage = {
  getItem: (key: string) => SecureStore.getItemAsync(key),
  setItem: (key: string, value: string) => SecureStore.setItemAsync(key, value),
  removeItem: (key: string) => SecureStore.deleteItemAsync(key),
};
