import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { createHousehold } from "../src/api/client";
import { useAuth } from "../src/auth/AuthProvider";
import { PrimaryButton } from "../src/components/PrimaryButton";
import { ScreenContainer } from "../src/components/ScreenContainer";
import { TextField } from "../src/components/TextField";
import { Card } from "../src/components/ui";
import { useHousehold } from "../src/household/HouseholdProvider";
import { leading, space, typeScale, useTheme } from "../src/theme";

// Onboarding-Screen (Auftrag Punkt 4): wird von app/_layout.tsx angezeigt,
// sobald GET /v1/me keinen Haushalt liefert. Erstellt per POST /v1/households
// einen neuen Haushalt (das Backend legt dabei automatisch 5
// Standard-Kategoriegruppen an) und waehlt ihn danach aus.
export default function OnboardingScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { accessToken } = useAuth();
  const household = useHousehold();

  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    setError(null);
    const trimmed = name.trim();
    if (trimmed.length === 0) {
      setError("Bitte einen Namen für den Haushalt eingeben.");
      return;
    }
    if (!accessToken) {
      setError("Nicht angemeldet.");
      return;
    }

    setSubmitting(true);
    try {
      const created = await createHousehold(accessToken, trimmed);
      await household.refresh();
      await household.selectHousehold(created.id);
      router.replace("/");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Haushalt konnte nicht erstellt werden.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "Haushalt erstellen" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.header}>
            <Text style={[styles.title, { color: theme.textPrimary }]}>Willkommen!</Text>
            <Text style={[styles.subtitle, { color: theme.textSecondary }]}>
              Du bist noch in keinem Haushalt. Erstelle einen neuen Haushalt, um Konten und
              Buchungen anzulegen. Dabei werden automatisch die Standard-Kategoriegruppen
              (Einnahmen, Bedürfnisse, Wünsche, Sparen, Transfers) angelegt.
            </Text>
          </View>

          <TextField
            label="Name des Haushalts"
            value={name}
            onChangeText={setName}
            placeholder="z. B. Unser Haushalt"
            autoCapitalize="words"
          />

          {error ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.message, { color: theme.warning }]} accessibilityRole="alert">
                {error}
              </Text>
            </Card>
          ) : null}

          <View style={styles.actions}>
            <PrimaryButton
              label="Haushalt erstellen"
              onPress={() => void handleCreate()}
              loading={submitting}
            />
            <PrimaryButton
              label="Ich habe einen Einladungscode"
              variant="secondary"
              onPress={() => router.push("/beitreten")}
            />
          </View>
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
  actions: {
    gap: space.md,
  },
});
