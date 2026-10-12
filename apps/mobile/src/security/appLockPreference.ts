import AsyncStorage from "@react-native-async-storage/async-storage";

// Reine UX-Praeferenz (Boolean), kein Secret - deshalb AsyncStorage statt
// expo-secure-store (das bleibt fuer Tokens reserviert, siehe
// ../auth/tokenStore.ts).
const APP_LOCK_ENABLED_KEY = "finanzplaner.appLockEnabled";

export async function getAppLockEnabledPreference(): Promise<boolean> {
  const value = await AsyncStorage.getItem(APP_LOCK_ENABLED_KEY);
  return value === "true";
}

export async function setAppLockEnabledPreference(enabled: boolean): Promise<void> {
  await AsyncStorage.setItem(APP_LOCK_ENABLED_KEY, enabled ? "true" : "false");
}
