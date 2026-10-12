import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { ApiError } from "../src/api/client";
import type { HouseholdInvitePreview } from "../src/api/types";
import { PrimaryButton } from "../src/components/PrimaryButton";
import { ScreenContainer } from "../src/components/ScreenContainer";
import { TextField } from "../src/components/TextField";
import { Badge, Card, CardLabel } from "../src/components/ui";
import { useHousehold } from "../src/household/HouseholdProvider";
import {
  usePreviewHouseholdInvite,
  useRedeemHouseholdInvite,
} from "../src/hooks/useHouseholdInvites";
import { leading, space, typeScale, useTheme } from "../src/theme";

const ROLE_LABELS: Record<string, string> = {
  owner: "Eigentümer",
  editor: "Bearbeiter",
  viewer: "Betrachter",
  child: "Eingeschränkt",
};

const ERROR_MESSAGES: Record<number, string> = {
  404: "Dieser Einladungscode ist unbekannt. Bitte prüfe die Eingabe.",
  410: "Dieser Einladungscode ist nicht mehr gültig (abgelaufen, bereits verwendet oder widerrufen).",
};

function messageForError(error: unknown): string {
  if (error instanceof ApiError) {
    const mapped = ERROR_MESSAGES[error.status];
    if (mapped) return mapped;
  }
  return error instanceof Error ? error.message : "Einladungscode konnte nicht geprüft werden.";
}

// Beitreten per Einladungscode (Stage 2e), Top-Level-Route wie onboarding.tsx
// - siehe app/_layout.tsx AuthGate fuer die Ausnahme, die diese Route auch
// fuer Nutzer ohne Haushalt erreichbar macht. Der optionale ?code=-Param
// deckt den Deep-Link-Fall ab (finanzplaner://beitreten?code=X), der nur
// funktioniert, wenn die App bereits installiert UND der Nutzer bereits
// angemeldet ist (siehe Plan-Kontext zu fehlender Universal-Links-Infra).
export default function BeitretenScreen() {
  const theme = useTheme();
  const router = useRouter();
  const household = useHousehold();
  const params = useLocalSearchParams<{ code?: string }>();

  const [code, setCode] = useState(params.code ?? "");
  const [preview, setPreview] = useState<HouseholdInvitePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const previewMutation = usePreviewHouseholdInvite();
  const redeemMutation = useRedeemHouseholdInvite();

  async function handlePreview() {
    setError(null);
    setPreview(null);
    const trimmed = code.trim();
    if (trimmed.length === 0) {
      setError("Bitte einen Einladungscode eingeben.");
      return;
    }
    try {
      const result = await previewMutation.mutateAsync(trimmed);
      setPreview(result);
    } catch (cause) {
      setError(messageForError(cause));
    }
  }

  async function handleJoin() {
    setError(null);
    try {
      const joined = await redeemMutation.mutateAsync(code.trim());
      await household.refresh();
      await household.selectHousehold(joined.id);
      router.replace("/");
    } catch (cause) {
      setError(messageForError(cause));
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "Haushalt beitreten" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.header}>
            <Text style={[styles.title, { color: theme.textPrimary }]}>Haushalt beitreten</Text>
            <Text style={[styles.subtitle, { color: theme.textSecondary }]}>
              Gib den Einladungscode ein, den du von einem Haushaltsmitglied erhalten hast.
            </Text>
          </View>

          <TextField
            label="Einladungscode"
            value={code}
            onChangeText={(value) => {
              setCode(value.toUpperCase());
              setPreview(null);
            }}
            placeholder="z. B. ABCD1234"
            autoCapitalize="characters"
          />

          {error ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.message, { color: theme.warning }]} accessibilityRole="alert">
                {error}
              </Text>
            </Card>
          ) : null}

          {!preview ? (
            <PrimaryButton
              label="Code prüfen"
              onPress={() => void handlePreview()}
              loading={previewMutation.isPending}
            />
          ) : (
            <View style={styles.previewBlock}>
              <Card accentEdge="positive">
                <CardLabel>Einladung</CardLabel>
                <Text style={[styles.previewName, { color: theme.textPrimary }]}>
                  {preview.household_name}
                </Text>
                <Badge label={`Rolle: ${ROLE_LABELS[preview.role] ?? preview.role}`} tone="info" />
              </Card>
              <PrimaryButton
                label="Beitreten"
                onPress={() => void handleJoin()}
                loading={redeemMutation.isPending}
              />
            </View>
          )}
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    flexGrow: 1,
    padding: space.xxl,
    gap: space.xl,
  },
  header: {
    gap: space.sm,
  },
  title: {
    fontSize: typeScale.display,
    fontWeight: "800",
    letterSpacing: -0.6,
  },
  subtitle: {
    fontSize: typeScale.base,
    lineHeight: leading(typeScale.base),
  },
  message: {
    fontSize: typeScale.body,
  },
  previewBlock: {
    gap: space.lg,
  },
  previewName: {
    fontSize: typeScale.large,
    fontWeight: "700",
  },
});
