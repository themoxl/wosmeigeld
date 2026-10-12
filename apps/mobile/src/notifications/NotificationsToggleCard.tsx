import * as Notifications from "expo-notifications";
import { useEffect, useState } from "react";
import { AppState, Linking, StyleSheet, Text } from "react-native";
import { useAuth } from "../auth/AuthProvider";
import { Badge, Card, CardLabel, DataRow } from "../components/ui";
import { typeScale, useTheme } from "../theme";
import { registerForPushNotifications } from "./usePushNotifications";

/**
 * "Benachrichtigungen"-Karte in den Einstellungen (Phase 9). Spiegelt den
 * OS-Permission-Status statt eine eigene App-Praeferenz zu fuehren - einmal
 * erteilte OS-Permissions lassen sich innerhalb der App ohnehin nicht
 * zurueckziehen, nur ueber die Systemeinstellungen (siehe Linking.openSettings()
 * unten fuer den "denied"-Fall).
 *
 * Deshalb hier bewusst KEIN Schalter, sondern eine Zeile mit Status: ein
 * Schalter, der sich nicht ausschalten laesst, waere ein Versprechen, das die
 * Karte nicht halten kann.
 */
export function NotificationsToggleCard() {
  const theme = useTheme();
  const { accessToken } = useAuth();
  const [status, setStatus] = useState<Notifications.PermissionStatus | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    Notifications.getPermissionsAsync().then((permissions) => {
      if (active) setStatus(permissions.status);
    });
    // Nach Rueckkehr aus den OS-Einstellungen (z. B. Permission dort erteilt)
    // den Status erneut lesen - gleiches AppState-Idiom wie
    // ../auth/supabase.ts und ../security/AppLockProvider.tsx.
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        Notifications.getPermissionsAsync().then((permissions) => {
          if (active) setStatus(permissions.status);
        });
      }
    });
    return () => {
      active = false;
      subscription.remove();
    };
  }, []);

  async function handlePress() {
    if (pending || !status) return;
    setPending(true);
    try {
      if (status === Notifications.PermissionStatus.UNDETERMINED) {
        const result = await Notifications.requestPermissionsAsync();
        setStatus(result.status);
        if (result.granted && accessToken) {
          await registerForPushNotifications(accessToken);
        }
      } else {
        // "granted" oder "denied" (canAskAgain=false, OS erlaubt keinen
        // erneuten In-App-Dialog): nur ueber die Systemeinstellungen aenderbar.
        await Linking.openSettings();
      }
    } finally {
      setPending(false);
    }
  }

  const granted = status === Notifications.PermissionStatus.GRANTED;
  const denied = status === Notifications.PermissionStatus.DENIED;
  const label = granted ? "Aktiviert" : "Deaktiviert";
  // Der Weg zum Umstellen gehoert in die Zeile, nicht in die Pille: eine
  // Statuspille mit Klammerzusatz laeuft auf schmalen Displays aus dem Bild.
  const hint = granted
    ? "Zum Ausschalten in die Systemeinstellungen"
    : denied
      ? "Nur in den Systemeinstellungen aktivierbar"
      : "Tippen, um sie zu erlauben";

  return (
    <Card>
      <CardLabel>Benachrichtigungen</CardLabel>
      <DataRow
        title="Fixkosten in Kürze fällig"
        subtitle={hint}
        avatarIcon="notifications-outline"
        accessibilityLabel={`Benachrichtigungen für fällige Fixkosten: ${label}. ${hint}`}
        onPress={() => void handlePress()}
        divider={false}
        trailing={<Badge tone={granted ? "positive" : "neutral"} label={label} />}
      />
      <Text style={[styles.hint, { color: theme.textMuted }]}>
        Erinnert dich einen Tag vor der Abbuchung, damit das Konto gedeckt ist.
      </Text>
    </Card>
  );
}

const styles = StyleSheet.create({
  hint: {
    fontSize: typeScale.micro,
  },
});
