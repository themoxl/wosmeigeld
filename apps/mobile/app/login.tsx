import { useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native";
import { useAuth } from "../src/auth/AuthProvider";
import { PrimaryButton } from "../src/components/PrimaryButton";
import { ScreenContainer, SCREEN_EDGES_WITH_TOP } from "../src/components/ScreenContainer";
import { SegmentedControl } from "../src/components/SegmentedControl";
import { TextField } from "../src/components/TextField";
import { Card } from "../src/components/ui";
import { space, typeScale, useTheme } from "../src/theme";

type Mode = "signIn" | "signUp";

const MODE_OPTIONS = [
  { value: "signIn" as const, label: "Anmelden" },
  { value: "signUp" as const, label: "Registrieren" },
];

// Login/Signup-Screen ausserhalb der (tabs)-Gruppe (Auftrag Punkt 3). Auth
// laeuft direkt gegen Supabase (siehe src/auth/AuthProvider.tsx); bei Erfolg
// uebernimmt app/_layout.tsx den Redirect zurueck in die App.
export default function LoginScreen() {
  const theme = useTheme();
  const { signIn, signUp } = useAuth();

  const [mode, setMode] = useState<Mode>("signIn");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  function validate(): string | null {
    if (!email.includes("@") || email.trim().length < 3) {
      return "Bitte eine gültige E-Mail-Adresse eingeben.";
    }
    if (password.length < 6) {
      return "Das Passwort muss mindestens 6 Zeichen haben.";
    }
    return null;
  }

  async function handleSubmit() {
    setError(null);
    setInfo(null);

    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }

    setSubmitting(true);
    try {
      if (mode === "signIn") {
        await signIn(email.trim(), password);
      } else {
        const { sessionCreated } = await signUp(email.trim(), password);
        if (!sessionCreated) {
          setInfo(
            "Konto erstellt. Bitte bestätige deine E-Mail-Adresse und melde dich anschließend an.",
          );
        }
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unbekannter Fehler bei der Anmeldung.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    // Erster Bildschirm der App und ohne native Kopfzeile registriert -
    // deshalb haelt er den oberen Rand selbst frei.
    <ScreenContainer edges={SCREEN_EDGES_WITH_TOP}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.header}>
            <Text style={[styles.eyebrow, { color: theme.positive }]}>FINANZPLANER</Text>
            <Text style={[styles.title, { color: theme.textPrimary }]}>
              {mode === "signIn" ? "Willkommen zurück" : "Konto erstellen"}
            </Text>
            <Text style={[styles.subtitle, { color: theme.textSecondary }]}>
              {mode === "signIn"
                ? "Melde dich an, um fortzufahren."
                : "Erstelle ein neues Konto für deinen Haushalt."}
            </Text>
          </View>

          <SegmentedControl
            options={MODE_OPTIONS}
            value={mode}
            onChange={setMode}
            accessibilityLabel="Anmelden oder registrieren"
          />

          <View style={styles.form}>
            <TextField
              label="E-Mail"
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              autoComplete="email"
              keyboardType="email-address"
              placeholder="name@beispiel.de"
              testID="login-email"
            />
            <TextField
              label="Passwort"
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              autoCapitalize="none"
              autoComplete="password"
              placeholder="Mindestens 6 Zeichen"
              testID="login-password"
            />

            {error ? (
              <Card tone="warning" density="compact">
                <Text style={[styles.message, { color: theme.warning }]} accessibilityRole="alert">
                  {error}
                </Text>
              </Card>
            ) : null}
            {info ? (
              <Card tone="highlight" density="compact">
                <Text style={[styles.message, { color: theme.highlight }]}>{info}</Text>
              </Card>
            ) : null}

            <PrimaryButton
              label={mode === "signIn" ? "Anmelden" : "Registrieren"}
              onPress={() => void handleSubmit()}
              loading={submitting}
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
  form: {
    gap: space.lg,
  },
  message: {
    fontSize: typeScale.body,
  },
});
