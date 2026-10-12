import { useQueryClient } from "@tanstack/react-query";
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { Redirect, Stack, useSegments } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { clearPersistedQueryCache, persistOptions, queryClient } from "../src/api/queryClient";
import { AuthProvider, useAuth, type AuthStatus } from "../src/auth/AuthProvider";
import { ClassificationRunBanner } from "../src/components/ClassificationRunBanner";
import { ServerConfigProvider, useServerConfig } from "../src/config/ServerConfigProvider";
import { ServerSetupScreen } from "../src/config/ServerSetupScreen";
import {
  HouseholdProvider,
  useHousehold,
  type HouseholdState,
} from "../src/household/HouseholdProvider";
import { useTransactionQueue } from "../src/hooks/useTransactionQueue";
import { useNotificationResponseRouter } from "../src/notifications/useNotificationResponseRouter";
import { usePushNotifications } from "../src/notifications/usePushNotifications";
import { AppLockProvider, useAppLock } from "../src/security/AppLockProvider";
import { AppLockScreen } from "../src/security/AppLockScreen";
import {
  PrivacyProvider,
  ThemeProvider,
  radii,
  space,
  typeScale,
  useTheme,
  useThemeMode,
} from "../src/theme";

export default function RootLayout() {
  // Phase 9: PersistQueryClientProvider statt QueryClientProvider - er stellt
  // den minimierten Lesecache beim Start wieder her, bevor die Screens
  // rendern (siehe src/api/queryClient.ts zur Wahl des Persisters und zur
  // Datenminimierung).
  return (
    <PersistQueryClientProvider client={queryClient} persistOptions={persistOptions}>
      <ThemeProvider>
        <PrivacyProvider>
          <ServerConfigProvider>
            <ServerGate />
          </ServerConfigProvider>
        </PrivacyProvider>
      </ThemeProvider>
    </PersistQueryClientProvider>
  );
}

// Sitzt VOR AuthProvider: Supabase-URL/Anon-Key sind erst bekannt, sobald der
// Nutzer "Finanzplaner Cloud" oder "Eigener Server" gewaehlt hat (oder eine
// frühere Wahl aus AsyncStorage geladen wurde). Bereits getroffene Wahlen
// ueberspringen ServerSetupScreen beim naechsten Start automatisch (Status
// "ready" direkt nach der Hydration). Der `key` auf <AuthProvider> erzwingt
// bei einem Serverwechsel (Einstellungen -> Server wechseln) einen sauberen
// Neuaufbau von Auth-Client/-Session statt eines mutierten Alt-Zustands.
function ServerGate() {
  const theme = useTheme();
  const serverConfig = useServerConfig();

  if (serverConfig.status === "loading") {
    return (
      <View
        style={[StyleSheet.absoluteFill, styles.overlay, { backgroundColor: theme.background }]}
      >
        <ActivityIndicator color={theme.textSecondary} />
      </View>
    );
  }

  if (serverConfig.status === "unset" || !serverConfig.config) {
    return <ServerSetupScreen />;
  }

  return (
    <AuthProvider
      key={`${serverConfig.config.mode}|${serverConfig.config.supabaseUrl}`}
      config={serverConfig.config}
    >
      <HouseholdProvider>
        <AppLockProvider>
          <RootNavigator />
        </AppLockProvider>
      </HouseholdProvider>
    </AuthProvider>
  );
}

function RootNavigator() {
  const theme = useTheme();
  const { resolved: colorScheme } = useThemeMode();
  const { status: authStatus } = useAuth();
  const household = useHousehold();
  const appLock = useAppLock();
  const reactQueryClient = useQueryClient();
  usePushNotifications();
  useNotificationResponseRouter();
  // Phase 9 (Offline-Write-Queue): genau einmal nahe der Wurzel montieren,
  // damit Timer und Netzwerk-Listener des Queue-Processors nicht mehrfach
  // laufen (siehe src/hooks/useTransactionQueue.ts).
  useTransactionQueue();

  // Mandantentrennung ernst nehmen (Architekturplan Abschnitt 5): nach
  // Abmelden keine gecachten Fachdaten des vorherigen Nutzers/Haushalts
  // behalten - weder im Speicher noch im persistierten Lesecache (Phase 9).
  // Die Write Queue raeumt useTransactionQueue() selbst ab.
  useEffect(() => {
    if (authStatus === "signedOut") {
      reactQueryClient.clear();
      void clearPersistedQueryCache();
    }
  }, [authStatus, reactQueryClient]);

  const isBlockingLoading =
    authStatus === "loading" || (authStatus === "signedIn" && household.state === "loading");

  return (
    <>
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: theme.background },
          // Kopfzeilen der Detail-/Modalseiten auf die Chromfarbe des
          // Designsystems ziehen, damit sie nicht als hellere Kante ueber
          // dem dunklen Inhalt stehen.
          headerStyle: { backgroundColor: theme.chrome },
          headerTintColor: theme.textPrimary,
          headerTitleStyle: { fontWeight: "700", fontSize: typeScale.large },
          headerShadowVisible: false,
        }}
      >
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="mehr" />
        <Stack.Screen name="login" options={{ headerShown: false }} />
        <Stack.Screen
          name="onboarding"
          options={{ headerShown: true, title: "Haushalt erstellen" }}
        />
        <Stack.Screen
          name="beitreten"
          options={{ headerShown: true, title: "Haushalt beitreten" }}
        />
        <Stack.Screen
          name="buchung-neu"
          options={{ headerShown: true, title: "Neue Buchung", presentation: "modal" }}
        />
        <Stack.Screen
          name="ziele-neu"
          options={{ headerShown: true, title: "Neues Ziel", presentation: "modal" }}
        />
        <Stack.Screen
          name="vermoegen-neu"
          options={{ headerShown: true, title: "Vermögen/Schuld erfassen", presentation: "modal" }}
        />
        {/* Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md): Erstellen und
            Bearbeiten sind eigene Stack-Screens, kein Inline-Dialog in der
            Liste - registriert nach demselben Muster wie buchung-neu. */}
        <Stack.Screen
          name="kategorie-neu"
          options={{ headerShown: true, title: "Neue Kategorie", presentation: "modal" }}
        />
        <Stack.Screen name="kategorie/[id]" options={{ headerShown: true, title: "Kategorie" }} />
        {/* Vertragsverwaltung (docs/VERTRAEGE-FIXKOSTEN-PLAN.md): Detail und
            Neuanlage als eigene Stack-Screens, gleiches Muster wie Kategorien. */}
        <Stack.Screen
          name="vertrag/neu"
          options={{ headerShown: true, title: "Neuer Vertrag", presentation: "modal" }}
        />
        <Stack.Screen name="vertrag/[id]" options={{ headerShown: true, title: "Vertrag" }} />
        <Stack.Screen name="buchung/[id]" options={{ headerShown: true, title: "Buchung" }} />
        {/* Versicherungen (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 7): Detail und
            Neuanlage als eigene Stack-Screens, gleiches Muster wie Vertraege.
            Die Registrierung ist noetig, weil `presentation` nachtraeglich im
            Screen selbst wirkungslos bliebe - die Neuanlage soll wie
            "vertrag/neu" als Modal aufgehen. Die Liste liegt unter
            mehr/versicherungen.tsx und ist durch "mehr" bereits abgedeckt. */}
        <Stack.Screen
          name="versicherung/neu"
          options={{ headerShown: true, title: "Neue Versicherung", presentation: "modal" }}
        />
        <Stack.Screen
          name="versicherung/[id]"
          options={{ headerShown: true, title: "Versicherung" }}
        />
        {/* Vorhaben & Grossinvestitionen (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md,
            Abschnitt 9): Detail und Neuanlage als eigene Stack-Screens, gleiches
            Muster wie Vertraege. Die Liste liegt unter mehr/vorhaben.tsx und
            braucht keine eigene Registrierung (bereits durch "mehr" abgedeckt). */}
        <Stack.Screen
          name="vorhaben/neu"
          options={{ headerShown: true, title: "Neues Vorhaben", presentation: "modal" }}
        />
        <Stack.Screen name="vorhaben/[id]" options={{ headerShown: true, title: "Vorhaben" }} />
        <Stack.Screen name="+not-found" options={{ headerShown: true, title: "Nicht gefunden" }} />
      </Stack>

      <AuthGate authStatus={authStatus} householdState={household.state} />

      {/* Laufender KI-Finanz-Check bleibt auf jedem Screen sichtbar - der
          Worker arbeitet weiter, waehrend der Nutzer navigiert. */}
      {!isBlockingLoading &&
      authStatus === "signedIn" &&
      household.state === "ready" &&
      !appLock.isLocked ? (
        <ClassificationRunBanner />
      ) : null}

      {isBlockingLoading ? (
        <View
          style={[StyleSheet.absoluteFill, styles.overlay, { backgroundColor: theme.background }]}
        >
          <ActivityIndicator color={theme.textSecondary} />
        </View>
      ) : null}

      {!isBlockingLoading && authStatus === "signedIn" && household.state === "error" ? (
        <View
          style={[StyleSheet.absoluteFill, styles.overlay, { backgroundColor: theme.background }]}
        >
          <Text style={[styles.errorTitle, { color: theme.warning }]}>
            Haushaltsdaten nicht verfügbar
          </Text>
          <Text style={[styles.errorDetail, { color: theme.textSecondary }]}>
            {household.error?.message ?? "Unbekannter Fehler."}
          </Text>
          <Pressable
            onPress={() => void household.refresh()}
            style={({ pressed }) => [
              styles.retryButton,
              {
                backgroundColor: theme.surface,
                borderColor: theme.border,
                opacity: pressed ? 0.7 : 1,
              },
            ]}
          >
            <Text style={{ color: theme.textPrimary, fontWeight: "600" }}>Erneut versuchen</Text>
          </Pressable>
        </View>
      ) : null}

      {!isBlockingLoading && authStatus === "signedIn" && appLock.isLocked ? (
        <AppLockScreen />
      ) : null}

      <StatusBar style={colorScheme === "dark" ? "light" : "dark"} />
    </>
  );
}

/**
 * Routenschutz (Auftrag Punkt 4): nicht angemeldete Nutzer werden zum
 * Login-Screen umgeleitet; angemeldete Nutzer ohne Haushalt zum
 * Onboarding-Screen. Jeder Zweig verwendet einen eigenen, literalen
 * `<Redirect href="...">`, damit Expo Routers typisierte Routen (siehe
 * tsconfig "include": ".expo/types/**") den Pfad statisch pruefen koennen.
 */
function AuthGate({
  authStatus,
  householdState,
}: {
  authStatus: AuthStatus;
  householdState: HouseholdState;
}) {
  const segments = useSegments();
  const currentTop = segments[0] as string | undefined;

  if (authStatus === "loading") {
    return null;
  }

  if (authStatus === "signedOut") {
    return currentTop === "login" ? null : <Redirect href="/login" />;
  }

  // Ab hier: authStatus === "signedIn".
  if (currentTop === "login") {
    return <Redirect href="/" />;
  }

  if (householdState === "no-household") {
    return currentTop === "onboarding" || currentTop === "beitreten" ? null : (
      <Redirect href="/onboarding" />
    );
  }

  if (householdState === "ready" && currentTop === "onboarding") {
    return <Redirect href="/" />;
  }

  return null;
}

const styles = StyleSheet.create({
  overlay: {
    alignItems: "center",
    justifyContent: "center",
    gap: space.md,
    paddingHorizontal: space.xxxl,
  },
  errorTitle: {
    fontWeight: "700",
    fontSize: typeScale.large,
  },
  errorDetail: {
    fontSize: typeScale.body,
    textAlign: "center",
  },
  retryButton: {
    marginTop: space.sm,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.lg,
    paddingVertical: space.sm,
  },
});
