import { Ionicons } from "@expo/vector-icons";
import * as LocalAuthentication from "expo-local-authentication";
import { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { PrimaryButton } from "../components/PrimaryButton";
import { radii, space, typeScale, useTheme } from "../theme";
import { useAppLock } from "./AppLockProvider";

const UNENFORCEABLE_ERRORS = new Set(["not_enrolled", "passcode_not_set", "not_available"]);

/** Fuehrt den eigentlichen Authentifizierungsversuch aus. Reine Funktion
 * ohne React-Hook-Bindung, damit sie sowohl aus einem Effekt (per
 * Promise-Chain, siehe unten) als auch aus dem "Erneut versuchen"-Button
 * (Event-Handler) aufrufbar ist, ohne setState synchron im Effektkoerper
 * auszuloesen. */
async function attemptUnlock(
  unlock: () => void,
  disable: () => Promise<void>,
  onFailed: () => void,
): Promise<void> {
  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: "Finanzplaner entsperren",
    cancelLabel: "Abbrechen",
    disableDeviceFallback: false,
  });

  if (result.success) {
    unlock();
    return;
  }

  if (UNENFORCEABLE_ERRORS.has(result.error)) {
    // Biometrie/Geraete-PIN wurden nachtraeglich entfernt (z. B. in den
    // OS-Einstellungen) - ein Lock, den niemand mehr durchqueren kann, ist
    // ein Aussperr-Risiko, keine Sicherheit. Selbstheilend deaktivieren
    // statt den Nutzer auszusperren.
    await disable();
    unlock();
    return;
  }

  onFailed();
}

/**
 * Vollbild-Overlay statt eigener Route (siehe app/_layout.tsx): der Lock
 * muss routen-unabhaengig ueber allem liegen, auch ueber Modals - eine
 * Expo-Router-Route wuerde mit AuthGates Redirect-Logik und der
 * Navigations-History interferieren.
 *
 * Weil er als Overlay und nicht als Screen laeuft, greift ScreenContainer
 * hier nicht; die Safe-Area kommt deshalb direkt aus den Insets.
 */
export function AppLockScreen() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();
  const { unlock, disable } = useAppLock();
  const [status, setStatus] = useState<"checking" | "failed">("checking");

  useEffect(() => {
    let active = true;
    attemptUnlock(unlock, disable, () => {
      if (active) setStatus("failed");
    });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- laeuft bewusst nur beim ersten Anzeigen; erneute Versuche laufen ueber den Retry-Button.
  }, []);

  function handleRetry() {
    setStatus("checking");
    void attemptUnlock(unlock, disable, () => setStatus("failed"));
  }

  return (
    <View
      style={[
        StyleSheet.absoluteFill,
        styles.overlay,
        {
          backgroundColor: theme.background,
          paddingTop: insets.top + space.xxxl,
          paddingBottom: insets.bottom + space.xxxl,
        },
      ]}
    >
      <View
        style={[styles.emblem, { backgroundColor: theme.surfaceRaised, borderColor: theme.border }]}
      >
        <Ionicons name="lock-closed" size={32} color={theme.primary} />
      </View>

      <Text style={[styles.title, { color: theme.textPrimary }]}>Finanzplaner ist gesperrt</Text>
      <Text style={[styles.detail, { color: theme.textSecondary }]}>
        Entsperre mit Fingerabdruck, Gesicht oder Geräte-PIN.
      </Text>

      {status === "failed" ? (
        <View style={styles.retryBlock}>
          <Text style={[styles.detail, { color: theme.warning }]} accessibilityRole="alert">
            Entsperren fehlgeschlagen oder abgebrochen.
          </Text>
          <PrimaryButton label="Erneut versuchen" onPress={handleRetry} />
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  overlay: {
    alignItems: "center",
    justifyContent: "center",
    gap: space.md,
    paddingHorizontal: space.xxl,
  },
  emblem: {
    width: 72,
    height: 72,
    borderRadius: radii.pill,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: space.sm,
  },
  title: {
    fontSize: typeScale.title,
    fontWeight: "800",
    letterSpacing: -0.4,
    textAlign: "center",
  },
  detail: {
    fontSize: typeScale.body,
    textAlign: "center",
  },
  retryBlock: {
    alignSelf: "stretch",
    gap: space.md,
    marginTop: space.lg,
  },
});
