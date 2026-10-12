import { useQueryClient } from "@tanstack/react-query";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { createShoppingItem } from "../src/api/client";
import { useAuth } from "../src/auth/AuthProvider";
import { PrimaryButton } from "../src/components/PrimaryButton";
import { ScreenContainer } from "../src/components/ScreenContainer";
import { TextField } from "../src/components/TextField";
import { Card, CardLabel } from "../src/components/ui";
import { useHousehold } from "../src/household/HouseholdProvider";
import { space, typeScale, useTheme } from "../src/theme";
import { parseAmountToMinorUnits } from "../src/utils/money";

// Formular zum Anlegen eines Einkaufsliste-Artikels (Phase 8,
// Architekturplan Abschnitt 6.8). Von app/mehr/einkaufsliste.tsx aus
// erreichbar, listId als Query-Param.
export default function NeuerEinkaufsartikelScreen() {
  const theme = useTheme();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const { listId } = useLocalSearchParams<{ listId: string }>();

  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [quantityText, setQuantityText] = useState("1");
  const [unit, setUnit] = useState("");
  const [plannedText, setPlannedText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Eingabefelder stehen hier in einer Karte; ohne eigenen Grund wuerde das
  // Feld mit der Karte verschmelzen.
  const fieldStyle = { backgroundColor: theme.surfaceRaised };

  async function handleCreate() {
    setError(null);

    const trimmedName = name.trim();
    if (trimmedName.length === 0) {
      setError("Bitte einen Artikelnamen eingeben.");
      return;
    }
    if (!accessToken || !selectedHouseholdId || !listId) {
      setError("Keine Einkaufsliste ausgewählt.");
      return;
    }

    let plannedPriceMinor: number | undefined;
    if (plannedText.trim().length > 0) {
      try {
        plannedPriceMinor = parseAmountToMinorUnits(plannedText);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Ungültiger Planpreis.");
        return;
      }
    }

    setSubmitting(true);
    try {
      await createShoppingItem(accessToken, selectedHouseholdId, {
        shopping_list_id: listId,
        name: trimmedName,
        category: category.trim() || undefined,
        quantity: quantityText.trim() || undefined,
        unit: unit.trim() || undefined,
        planned_price_minor: plannedPriceMinor,
      });
      await queryClient.invalidateQueries({ queryKey: ["shopping-items", listId] });
      router.back();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Artikel konnte nicht angelegt werden.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "Neuer Artikel" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Card>
            <CardLabel>Artikel</CardLabel>
            <TextField
              label="Artikel"
              value={name}
              onChangeText={setName}
              placeholder="z. B. Nudeln"
              style={fieldStyle}
            />
            <TextField
              label="Kategorie (optional)"
              value={category}
              onChangeText={setCategory}
              placeholder="z. B. Obst & Gemüse"
              style={fieldStyle}
            />
          </Card>

          <Card>
            <CardLabel>Menge & Preis</CardLabel>
            <View style={styles.fieldRow}>
              <View style={styles.fieldCell}>
                <TextField
                  label="Menge"
                  value={quantityText}
                  onChangeText={setQuantityText}
                  style={fieldStyle}
                />
              </View>
              <View style={styles.fieldCell}>
                <TextField
                  label="Einheit (optional)"
                  value={unit}
                  onChangeText={setUnit}
                  placeholder="Stk"
                  style={fieldStyle}
                />
              </View>
            </View>
            <TextField
              label="Planpreis in EUR (optional)"
              value={plannedText}
              onChangeText={setPlannedText}
              placeholder="2,50"
              keyboardType="numbers-and-punctuation"
              style={fieldStyle}
            />
          </Card>

          {error ? (
            <Card tone="warning" density="compact">
              <Text accessibilityRole="alert" style={[styles.error, { color: theme.warning }]}>
                {error}
              </Text>
            </Card>
          ) : null}

          <PrimaryButton
            label="Artikel anlegen"
            onPress={() => void handleCreate()}
            loading={submitting}
          />
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    gap: space.lg,
  },
  fieldRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  fieldCell: {
    flex: 1,
    minWidth: 0,
  },
  error: {
    fontSize: typeScale.body,
  },
});
