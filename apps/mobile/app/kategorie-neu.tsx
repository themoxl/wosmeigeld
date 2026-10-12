import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text } from "react-native";
import {
  CategoryForm,
  parseSynonyms,
  validateCategoryForm,
  type CategoryFormValues,
} from "../src/components/CategoryForm";
import { InlineQueryState } from "../src/components/InlineQueryState";
import { ScreenContainer } from "../src/components/ScreenContainer";
import { Card } from "../src/components/ui";
import {
  useCategoryCatalog,
  useCategoryPermissions,
  useCreateCategory,
} from "../src/hooks/useCategoryCatalog";
import { space, typeScale, useTheme } from "../src/theme";

// Eigener Stack-Screen fuer das Anlegen einer Kategorie (siehe Plan:
// "Erstellen/Bearbeiten oeffnet einen eigenen Stack-Screen"). Als Modal
// praesentiert, registriert in app/_layout.tsx - dasselbe Muster wie
// buchung-neu.
export default function KategorieNeuScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { groupId } = useLocalSearchParams<{ groupId?: string }>();
  const { canManage } = useCategoryPermissions();
  const catalogQuery = useCategoryCatalog({ includeArchived: false, includeUsage: false });
  const createCategory = useCreateCategory();

  const groups = catalogQuery.data?.groups ?? [];
  const [values, setValues] = useState<CategoryFormValues>({
    name: "",
    groupId: groupId ?? null,
    defaultType: "expense",
    budgetBucket: null,
    isBudgeted: true,
    icon: "",
    description: "",
    synonyms: "",
  });
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    const validation = validateCategoryForm(values, groups);
    if (validation.error || !values.groupId) {
      setError(validation.error);
      return;
    }
    setError(null);
    try {
      await createCategory.mutateAsync({
        group_id: values.groupId,
        name: validation.name,
        default_type: values.defaultType,
        budget_bucket: values.budgetBucket,
        is_budgeted: values.isBudgeted,
        icon: values.icon.trim() || null,
        description: values.description.trim() || null,
        synonyms: parseSynonyms(values.synonyms),
      });
      router.back();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Kategorie konnte nicht angelegt werden.");
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "Neue Kategorie" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {!canManage ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.notice, { color: theme.warning }]}>
                Deine Rolle in diesem Haushalt darf keine Kategorien anlegen.
              </Text>
            </Card>
          ) : catalogQuery.isLoading ? (
            <InlineQueryState status="loading" skeletonRows={6} />
          ) : catalogQuery.isError ? (
            <InlineQueryState
              status="error"
              title="Gruppen konnten nicht geladen werden"
              onRetry={() => void catalogQuery.refetch()}
            />
          ) : (
            <CategoryForm
              values={values}
              onChange={(patch) => setValues((prev) => ({ ...prev, ...patch }))}
              groups={groups}
              error={error}
              submitLabel="Kategorie anlegen"
              onSubmit={() => void handleSubmit()}
              submitting={createCategory.isPending}
            />
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
  },
});
