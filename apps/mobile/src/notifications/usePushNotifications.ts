import Constants from "expo-constants";
import * as Notifications from "expo-notifications";
import { useEffect } from "react";
import { Platform } from "react-native";
import { registerPushToken } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import "./notificationHandler";

/** Holt einen frischen Expo-Push-Token und registriert ihn beim Backend.
 * Exportiert, damit NotificationsToggleCard direkt nach einer frisch
 * erteilten OS-Permission registrieren kann, ohne auf den naechsten
 * accessToken/status-Wechsel dieses Hooks zu warten. */
export async function registerForPushNotifications(accessToken: string): Promise<void> {
  const projectId = Constants.expoConfig?.extra?.eas?.projectId as string | undefined;
  if (!projectId) {
    // `eas build:configure` wurde noch nicht ausgefuehrt (siehe
    // apps/mobile/eas.json) - kein Absturz, nur keine Registrierung moeglich.
    console.warn("Kein EAS projectId gefunden - Push-Registrierung uebersprungen.");
    return;
  }

  try {
    const pushToken = await Notifications.getExpoPushTokenAsync({ projectId });
    const deviceName = Constants.deviceName ?? undefined;
    await registerPushToken(
      accessToken,
      pushToken.data,
      Platform.OS === "ios" ? "ios" : "android",
      deviceName,
    );
  } catch (cause) {
    // z. B. iOS-Simulator ohne APNs oder Geraet offline - kein Blocker fuer
    // den Rest der App.
    console.warn("Push-Token konnte nicht registriert werden.", cause);
  }
}

/**
 * Registriert das Geraet fuer Push-Benachrichtigungen (Phase 9), sobald der
 * Nutzer angemeldet ist UND die OS-Permission bereits erteilt ist. Das
 * Anfordern der Permission selbst passiert lazy in NotificationsToggleCard
 * (Einstellungen), nicht eager hier beim Login.
 */
export function usePushNotifications(): void {
  const { accessToken, status } = useAuth();

  useEffect(() => {
    if (status !== "signedIn" || !accessToken) return;

    let active = true;

    async function registerIfPermitted() {
      const permissions = await Notifications.getPermissionsAsync();
      if (!active || !permissions.granted) return;
      await registerForPushNotifications(accessToken as string);
    }

    void registerIfPermitted();

    // Seltener Fall: der native Geraetetoken hat sich geaendert. Den
    // Expo-Push-Token frisch abrufen statt den rohen DevicePushToken aus dem
    // Event zu verwenden (addPushTokenListener liefert einen
    // DevicePushToken, keinen ExpoPushToken).
    const subscription = Notifications.addPushTokenListener(() => {
      void registerForPushNotifications(accessToken as string);
    });

    return () => {
      active = false;
      subscription.remove();
    };
  }, [accessToken, status]);
}
