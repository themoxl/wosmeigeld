import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text } from "react-native";
import type { CatalogCategory, CatalogGroup, CategoryUpdate } from "../../src/api/categoryTypes";
import {
  CategoryForm,
  parseSynonyms,
  validateCategoryForm,
  type CategoryFormValues,
} from "../../src/components/CategoryForm";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { Card, CardLabel } from "../../src/components/ui";
import {
  useCategoryCatalog,
  useCategoryPermissions,
  useUpdateCategory,
} from "../../src/hooks/useCategoryCatalog";
import { leading, space, typeScale, useTheme } from "../../src/theme";

function findCategory(
  groups: CatalogGroup[],
  categoryId: string | undefined,
): { group: CatalogGroup; category: CatalogCategory } | null {
  if (!categoryId) return null;
  for (const group of groups) {
    const category = group.categories.find((candidate) => candidate.id === categoryId);
    if (category) return { group, category };
  }
  return null;
}

// Kategorie bearbeiten. Ein Gruppenwechsel ist hier bewusst NICHT moeglich:
// er wirkt rueckwirkend auf Berichte und laeuft deshalb ueber
// "In andere Gruppe verschieben" mit Servervorschau (app/mehr/kategorien.tsx).
export default function KategorieBearbeitenScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { canManage } = useCategoryPermissions();
  const catalogQuery = useCategoryCatalog({ includeArchived: true, includeUsage: true });
  const updateCategory = useUpdateCategory();

  const groups = catalogQuery.data?.groups ?? [];
  // Bewusst ohne useMemo: der React-Compiler memoisiert das selbst, und eine
  // eigene Memoisierung mit fruehem Return aus der Schleife kann er nicht
  // erhalten (react-hooks/preserve-manual-memoization).
  const found = findCategory(groups, id);

  // Formularzustand einmal pro geladener Kategorie initialisieren, nicht bei
  // jedem Refetch - sonst gingen unfertige Eingaben verloren. Dasselbe
  // Render-Muster wie in app/buchung/[id].tsx (kein setState im Effekt).
  const [initializedId, setInitializedId] = useState<string | null>(null);
  const [values, setValues] = useState<CategoryFormValues>({
    name: "",
    groupId: null,
    defaultType: "expense",
    budgetBucket: null,
    isBudgeted: true,
    icon: "",
    description: "",
    synonyms: "",
  });
  const [error, setError] = useState<string | null>(null);

  if (found && found.category.id !== initializedId) {
    setInitializedId(found.category.id);
    setValues({
      name: found.category.name,
      groupId: found.group.id,
      defaultType:
        found.category.default_type === "income" ||
        found.category.default_type === "transfer" ||
        found.category.default_type === "expense"
          ? found.category.default_type
          : null,
      budgetBucket:
        found.category.budget_bucket === "needs" ||
        found.category.budget_bucket === "wants" ||
        found.category.budget_bucket === "savings"
          ? found.category.budget_bucket
          : null,
      isBudgeted: found.category.is_budgeted ?? true,
      icon: found.category.icon ?? "",
      description: found.category.description ?? "",
      synonyms: (found.category.synonyms ?? []).join(", "),
    });
  }

  // Nach der ersten fachlichen Referenz darf die Art nicht mehr still
  // gewechselt werden (Plan, "Kategorieformular").
  const lockDefaultType = (found?.category.usage?.total ?? 0) > 0;

  async function handleSubmit() {
    if (!found) return;
    const validation = validateCategoryForm(values, groups, found.category.id);
    if (validation.error) {
      setError(validation.error);
      return;
    }
    setError(null);

    // Leere Textfelder bedeuten "loeschen", nicht "unveraendert" - dafuer
    // kennt der Vertrag `clear_fields`.
    const clearFields: NonNullable<CategoryUpdate["clear_fields"]> = [];
    const icon = values.icon.trim();
    const description = values.description.trim();
    if (!icon && found.category.icon) clearFields.push("icon");
    if (!description && found.category.description) clearFields.push("description");
    if (!values.budgetBucket && found.category.budget_bucket) clearFields.push("budget_bucket");

    try {
      await updateCategory.mutateAsync({
        categoryId: found.category.id,
        version: found.category.version ?? 1,
        patch: {
          name: validation.name,
          ...(lockDefaultType ? {} : { default_type: values.defaultType }),
          budget_bucket: values.budgetBucket,
          is_budgeted: values.isBudgeted,
          icon: icon || undefined,
          description: description || undefined,
          synonyms: parseSynonyms(values.synonyms),
          clear_fields: clearFields,
        },
      });
      router.back();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Kategorie konnte nicht gespeichert werden.",
      );
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: found?.category.name ?? "Kategorie" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {catalogQuery.isLoading ? (
            <InlineQueryState status="loading" skeletonRows={6} />
          ) : catalogQuery.isError ? (
            /* Vor dem "gibt es nicht"-Zweig: ohne Netz ist `found` ebenfalls
               leer, und eine geloeschte Kategorie ist etwas anderes als eine
               nicht geladene. */
            <InlineQueryState
              status="error"
              title="Kategorie konnte nicht geladen werden"
              message={catalogQuery.error instanceof Error ? catalogQuery.error.message : undefined}
              onRetry={() => void catalogQuery.refetch()}
            />
          ) : !found ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.notice, { color: theme.warning }]}>
                Diese Kategorie gibt es nicht (mehr).
              </Text>
            </Card>
          ) : !canManage ? (
            <Card>
              <CardLabel>Kategorie</CardLabel>
              <Text style={[styles.readOnlyTitle, { color: theme.textPrimary }]}>
                {`${found.group.name} · ${found.category.name}`}
              </Text>
              <Text style={[styles.notice, { color: theme.textSecondary }]}>
                Deine Rolle in diesem Haushalt darf Kategorien lesen, aber nicht ändern.
              </Text>
            </Card>
          ) : (
            <>
              {found.category.is_archived ? (
                <Card tone="highlight" density="compact">
                  <Text style={[styles.notice, { color: theme.highlight }]}>
                    Diese Kategorie ist archiviert. Sie bleibt in Buchungen, Budgets und Berichten
                    sichtbar, ist aber nicht neu wählbar.
                  </Text>
                </Card>
              ) : null}
              <CategoryForm
                values={values}
                onChange={(patch) => setValues((prev) => ({ ...prev, ...patch }))}
                groups={groups}
                lockDefaultType={lockDefaultType}
                error={error}
                submitLabel="Änderungen speichern"
                onSubmit={() => void handleSubmit()}
                submitting={updateCategory.isPending}
              />
              <Text style={[styles.notice, { color: theme.textSecondary }]}>
                Ein Gruppenwechsel wirkt rückwirkend auf Berichte und läuft deshalb über &quot;In
                andere Gruppe verschieben&quot; in der Kategorienverwaltung - dort wird vorher die
                Auswirkung angezeigt.
              </Text>
            </>
          )}
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
  notice: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  readOnlyTitle: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
});
