import { useRef, useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native";
import { HAS_CLOUD_PRESET } from "../api/config";
import { discoverServerConfig } from "../api/discovery";
import { PrimaryButton } from "../components/PrimaryButton";
import { ScreenContainer, SCREEN_EDGES_WITH_TOP } from "../components/ScreenContainer";
import { TextField } from "../components/TextField";
import { Card } from "../components/ui";
import { space, typeScale, useTheme } from "../theme";
import { useServerConfig } from "./ServerConfigProvider";

// Discovery-Timeout: eine NAS im eigenen Netz antwortet ueblicherweise sehr
// schnell; 8s deckt einen kurzzeitig aufwachenden Server (Standby) ab, ohne
// bei einer wirklich falschen Adresse ewig zu haengen.
const DISCOVERY_TIMEOUT_MS = 8000;

/** Gate vor der eigentlichen App (siehe app/_layout.tsx, ServerGate): kein
 * eigener Router-Screen, weil er noch vor Auth/Haushalt entscheidet, WELCHEN
 * Server die App ueberhaupt anspricht. */
export function ServerSetupScreen() {
  const theme = useTheme();
  const serverConfig = useServerConfig();

  const [cloudLoading, setCloudLoading] = useState(false);
  const [serverUrl, setServerUrl] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function handleCloud() {
    setCloudLoading(true);
    try {
      await serverConfig.useCloudPreset();
    } finally {
      setCloudLoading(false);
    }
  }

  async function handleConnect() {
    const trimmed = serverUrl.trim();
    if (!/^https?:\/\/.+/i.test(trimmed)) {
      setError("Bitte eine vollständige Adresse eingeben, z. B. http://192.168.1.23:8882");
      return;
    }

    setError(null);
    setConnecting(true);
    const controller = new AbortController();
    abortRef.current = controller;
    const timeout = setTimeout(() => controller.abort(), DISCOVERY_TIMEOUT_MS);

    try {
      const discovery = await discoverServerConfig(trimmed, controller.signal);
      await serverConfig.useSelfHosted(trimmed, discovery);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Verbindung fehlgeschlagen.");
    } finally {
      clearTimeout(timeout);
      abortRef.current = null;
      setConnecting(false);
    }
  }

  const showInsecureWarning = /^http:\/\//i.test(serverUrl.trim());

  return (
    // Laeuft vor dem Router und damit ohne native Kopfzeile - der obere Rand
    // muss hier selbst freigehalten werden.
    <ScreenContainer edges={SCREEN_EDGES_WITH_TOP}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.header}>
            <Text style={[styles.eyebrow, { color: theme.positive }]}>FINANZPLANER</Text>
            <Text style={[styles.title, { color: theme.textPrimary }]}>
              {HAS_CLOUD_PRESET ? "Womit verbinden?" : "Server wählen"}
            </Text>
            <Text style={[styles.subtitle, { color: theme.textSecondary }]}>
              {HAS_CLOUD_PRESET
                ? "Womit soll sich die App verbinden?"
                : "Mit welchem Server soll sich die App verbinden?"}
            </Text>
          </View>

          {HAS_CLOUD_PRESET ? (
            <>
              <View style={styles.section}>
                <PrimaryButton
                  label="Finanzplaner Cloud"
                  onPress={() => void handleCloud()}
                  loading={cloudLoading}
                  disabled={connecting}
                />
              </View>

              <View style={[styles.divider, { backgroundColor: theme.border }]} />
            </>
          ) : null}

          <View style={styles.section}>
            {HAS_CLOUD_PRESET ? (
              <Text style={[styles.sectionTitle, { color: theme.textPrimary }]}>
                Eigener Server
              </Text>
            ) : null}
            <Text style={[styles.sectionHint, { color: theme.textSecondary }]}>
              Fuer den eigenen Home-Server/NAS: Adresse der dortigen Finanzplaner-API eingeben.
            </Text>
            <TextField
              label="Server-Adresse"
              value={serverUrl}
              onChangeText={setServerUrl}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              placeholder="http://192.168.1.23:8882"
              testID="server-setup-url"
              editable={!connecting}
            />
            {showInsecureWarning ? (
              <Text style={[styles.warning, { color: theme.warning }]}>
                Unverschluesselt (http) - nur im eigenen Netzwerk verwenden.
              </Text>
            ) : null}
            {error ? (
              <Card tone="warning" density="compact">
                <Text style={[styles.warning, { color: theme.warning }]} accessibilityRole="alert">
                  {error}
                </Text>
              </Card>
            ) : null}
            <PrimaryButton
              label="Verbinden"
              onPress={() => void handleConnect()}
              loading={connecting}
              disabled={cloudLoading}
              variant="secondary"
            />
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  flex: {
    flex: 1,
  },
  content: {
    flexGrow: 1,
    justifyContent: "center",
    paddingHorizontal: space.xxl,
    paddingVertical: space.xxxl,
    gap: space.xxl,
  },
  header: {
    gap: space.xs,
  },
  eyebrow: {
    fontSize: typeScale.micro,
    fontWeight: "700",
    letterSpacing: 1.2,
  },
  title: {
    fontSize: typeScale.hero,
    fontWeight: "800",
    letterSpacing: -0.8,
  },
  subtitle: {
    fontSize: typeScale.base,
  },
  section: {
    gap: space.md,
  },
  sectionTitle: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  sectionHint: {
    fontSize: typeScale.body,
  },
  warning: {
    fontSize: typeScale.body,
  },
  divider: {
    height: 1,
  },
});
