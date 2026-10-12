import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import type { CatalogGroup, CategoryBudgetBucket, CategoryDefaultType } from "../api/categoryTypes";
import { MIN_TOUCH, radii, space, typeScale, useTheme } from "../theme";
import { normalizeSearchText } from "../utils/categorySearch";
import { OptionList } from "./OptionList";
import { PrimaryButton } from "./PrimaryButton";
import { TextField } from "./TextField";
import { Card } from "./ui";

// Kategorieformular (docs/KATEGORISIERUNGSPLAN.md, "Kategorieformular").
// Wird von app/kategorie-neu.tsx und app/kategorie/[id].tsx verwendet - die
// Validierung darf sich zwischen Anlegen und Bearbeiten nicht unterscheiden.

export interface CategoryFormValues {
  name: string;
  groupId: string | null;
  defaultType: CategoryDefaultType | null;
  budgetBucket: CategoryBudgetBucket | null;
  isBudgeted: boolean;
  icon: string;
  description: string;
  /** Kommagetrennt eingegeben, beim Speichern in eine Liste zerlegt. */
  synonyms: string;
}

const TYPE_OPTIONS: { value: CategoryDefaultType; label: string }[] = [
  { value: "expense", label: "Ausgabe" },
  { value: "income", label: "Einnahme" },
  { value: "transfer", label: "Transfer" },
];

const BUCKET_OPTIONS: { value: CategoryBudgetBucket | "none"; label: string }[] = [
  { value: "needs", label: "Bedarf" },
  { value: "wants", label: "Wunsch" },
  { value: "savings", label: "Sparen" },
  { value: "none", label: "Keiner" },
];

/** Trimmen und Mehrfachleerzeichen normalisieren - wie `_clean_name()` in der API. */
export function cleanCategoryName(value: string): string {
  return value.split(/\s+/).filter(Boolean).join(" ");
}

export function parseSynonyms(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

export interface CategoryFormValidation {
  error: string | null;
  name: string;
}

/**
 * Validiert Name und Gruppe. Duplikate werden unabhaengig von
 * Gross-/Kleinschreibung und Umlautumschrift erkannt - dieselbe
 * Normalisierung wie in der Suche, damit "Oepnv" und "ÖPNV" in derselben
 * Gruppe nicht nebeneinander entstehen. Die verbindliche Pruefung macht
 * trotzdem der Server (409 `category_duplicate_name`); hier geht es nur
 * darum, den Fehlversuch zu ersparen.
 */
export function validateCategoryForm(
  values: CategoryFormValues,
  groups: CatalogGroup[],
  /** Beim Bearbeiten: die eigene ID, damit sie nicht mit sich selbst kollidiert. */
  ownCategoryId?: string,
): CategoryFormValidation {
  const name = cleanCategoryName(values.name);
  if (name.length === 0) return { error: "Bitte einen Namen eingeben.", name };
  if (name.length > 120) return { error: "Der Name darf höchstens 120 Zeichen haben.", name };
  if (!values.groupId) return { error: "Bitte eine Gruppe wählen.", name };

  const group = groups.find((candidate) => candidate.id === values.groupId);
  const normalized = normalizeSearchText(name);
  const clash = group?.categories.find(
    (category) =>
      category.id !== ownCategoryId &&
      !category.is_archived &&
      normalizeSearchText(category.name) === normalized,
  );
  if (clash) {
    return {
      error: `In "${group?.name}" gibt es bereits "${clash.name}". Bitte einen anderen Namen wählen oder die vorhandene Kategorie bearbeiten.`,
      name,
    };
  }
  return { error: null, name };
}

type Props = {
  values: CategoryFormValues;
  onChange: (patch: Partial<CategoryFormValues>) => void;
  groups: CatalogGroup[];
  /**
   * Nach der ersten fachlichen Referenz ist `default_type` nicht mehr still
   * aenderbar - eine Bedeutungsaenderung erfordert eine neue Kategorie.
   */
  lockDefaultType?: boolean;
  error?: string | null;
  submitLabel: string;
  onSubmit: () => void;
  submitting?: boolean;
};

export function CategoryForm({
  values,
  onChange,
  groups,
  lockDefaultType = false,
  error,
  submitLabel,
  onSubmit,
  submitting = false,
}: Props) {
  const theme = useTheme();
  const [groupSheetOpen, setGroupSheetOpen] = useState(false);
  const activeGroups = groups.filter((group) => !group.is_archived);
  const selectedGroup = activeGroups.find((group) => group.id === values.groupId);

  return (
    <View style={styles.form}>
      <TextField
        label="Name"
        value={values.name}
        onChangeText={(name) => onChange({ name })}
        placeholder="z. B. OEPNV & Bahn"
        maxLength={120}
      />

      <View style={styles.field}>
        <Text style={[styles.label, { color: theme.textSecondary }]}>Gruppe</Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Gruppe: ${selectedGroup?.name ?? "keine gewählt"}`}
          accessibilityState={{ expanded: groupSheetOpen }}
          onPress={() => setGroupSheetOpen((open) => !open)}
          style={({ pressed }) => [
            styles.selectField,
            {
              borderColor: selectedGroup ? theme.positive : theme.border,
              backgroundColor: theme.surface,
              opacity: pressed ? 0.7 : 1,
            },
          ]}
        >
          <Text
            style={[
              styles.selectValue,
              { color: selectedGroup ? theme.textPrimary : theme.textSecondary },
            ]}
          >
            {selectedGroup?.name ?? "Gruppe wählen"}
          </Text>
          <Ionicons
            name={groupSheetOpen ? "chevron-up" : "chevron-down"}
            size={18}
            color={theme.textSecondary}
          />
        </Pressable>
        {groupSheetOpen ? (
          <OptionList
            options={activeGroups.map((group) => ({ value: group.id, label: group.name }))}
            value={values.groupId}
            onChange={(groupId) => {
              onChange({ groupId });
              setGroupSheetOpen(false);
            }}
          />
        ) : null}
      </View>

      <View style={styles.field}>
        <Text style={[styles.label, { color: theme.textSecondary }]}>Art</Text>
        {lockDefaultType ? (
          <Text style={[styles.hint, { color: theme.textSecondary }]}>
            {`${TYPE_OPTIONS.find((option) => option.value === values.defaultType)?.label ?? "Ohne Art"} - nach der ersten Verwendung nicht mehr änderbar. Für eine andere Bedeutung bitte eine neue Kategorie anlegen.`}
          </Text>
        ) : (
          <OptionList
            options={TYPE_OPTIONS}
            value={values.defaultType}
            onChange={(defaultType) => onChange({ defaultType })}
          />
        )}
      </View>

      <View style={styles.field}>
        <Text style={[styles.label, { color: theme.textSecondary }]}>Budgetbereich</Text>
        <OptionList
          options={BUCKET_OPTIONS}
          value={values.budgetBucket ?? "none"}
          onChange={(bucket) =>
            onChange({ budgetBucket: bucket === "none" ? null : (bucket as CategoryBudgetBucket) })
          }
        />
        <Text style={[styles.hint, { color: theme.textSecondary }]}>
          Der Budgetbereich ist eine eigene Dimension und keine Kategoriegruppe.
        </Text>
      </View>

      <Pressable
        accessibilityRole="switch"
        accessibilityState={{ checked: values.isBudgeted }}
        accessibilityLabel="Im Budget anzeigen"
        onPress={() => onChange({ isBudgeted: !values.isBudgeted })}
        style={({ pressed }) => [
          styles.selectField,
          {
            borderColor: values.isBudgeted ? theme.positive : theme.border,
            backgroundColor: values.isBudgeted ? theme.positiveMuted : theme.surface,
            opacity: pressed ? 0.7 : 1,
          },
        ]}
      >
        <Text
          style={[
            styles.selectValue,
            { color: values.isBudgeted ? theme.positive : theme.textPrimary },
          ]}
        >
          Im Budget anzeigen
        </Text>
        <Text
          style={[
            styles.switchState,
            { color: values.isBudgeted ? theme.positive : theme.textSecondary },
          ]}
        >
          {values.isBudgeted ? "Ja" : "Nein"}
        </Text>
      </Pressable>

      <TextField
        label="Symbol (optional)"
        value={values.icon}
        onChangeText={(icon) => onChange({ icon })}
        placeholder="z. B. ein Emoji"
        maxLength={8}
      />

      <TextField
        label="Beschreibung (optional)"
        value={values.description}
        onChangeText={(description) => onChange({ description })}
        multiline
      />

      <TextField
        label="Synonyme (optional, mit Komma getrennt)"
        value={values.synonyms}
        onChangeText={(synonyms) => onChange({ synonyms })}
        placeholder="Deutschlandticket, BVG, Nahverkehr"
      />
      <Text style={[styles.hint, { color: theme.textSecondary }]}>
        Synonyme werden in der Kategoriesuche mitgesucht.
      </Text>

      {error ? (
        <Card tone="warning" density="compact">
          <Text style={[styles.hint, { color: theme.warning }]} accessibilityRole="alert">
            {error}
          </Text>
        </Card>
      ) : null}

      <PrimaryButton label={submitLabel} onPress={onSubmit} loading={submitting} />
    </View>
  );
}

const styles = StyleSheet.create({
  form: {
    gap: space.lg,
  },
  field: {
    gap: space.sm,
  },
  label: {
    fontSize: typeScale.body,
    fontWeight: "600",
  },
  hint: {
    fontSize: typeScale.body,
  },
  selectField: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    minHeight: MIN_TOUCH + 4,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
  },
  selectValue: {
    flex: 1,
    fontSize: typeScale.base,
  },
  switchState: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
});
