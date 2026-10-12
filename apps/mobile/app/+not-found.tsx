import { Stack, useRouter } from "expo-router";
import { StyleSheet, Text, View } from "react-native";
import { PrimaryButton } from "../src/components/PrimaryButton";
import { ScreenContainer } from "../src/components/ScreenContainer";
import { leading, space, typeScale, useTheme } from "../src/theme";

export default function NotFoundScreen() {
  const theme = useTheme();
  const router = useRouter();

  return (
    <>
      <Stack.Screen options={{ title: "Nicht gefunden" }} />
      <ScreenContainer>
        <View style={styles.content}>
          <Text style={[styles.title, { color: theme.textPrimary }]}>
            Diese Seite gibt es nicht.
          </Text>
          <Text style={[styles.hint, { color: theme.textSecondary }]}>
            Der aufgerufene Link führt ins Leere - vermutlich stammt er aus einer älteren Version
            der App.
          </Text>
          <PrimaryButton label="Zurück zu Heute" onPress={() => router.replace("/")} />
        </View>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    flex: 1,
    justifyContent: "center",
    paddingHorizontal: space.xxl,
    gap: space.md,
  },
  title: {
    fontSize: typeScale.title,
    fontWeight: "800",
    textAlign: "center",
    letterSpacing: -0.4,
  },
  hint: {
    fontSize: typeScale.body,
    textAlign: "center",
    lineHeight: leading(typeScale.body),
    marginBottom: space.sm,
  },
});
