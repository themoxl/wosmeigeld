import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import type { CategoryGroup } from "../api/categoryTypes";
import { useCategoryPermissions, useCreateCategory } from "../hooks/useCategoryCatalog";
import { MIN_TOUCH, letterSpacingLabel, radii, space, typeScale, useTheme } from "../theme";
import { buildPickerSections, type PickerCategory } from "../utils/categorySearch";
import { SheetModal } from "./SheetModal";
import { Badge } from "./ui";

export type CategorySearchSheetProps = {
  visible: boolean;
  groups: CategoryGroup[];
  value: string | null;
  onChange: (categoryId: string | null) => void;
  onClose: () => void;
  title?: string;
  /** Betrag der Buchung in Cent - priorisiert passende Arten, versteckt nichts. */
  amountMinor?: number | null;
  /** Zuletzt verwendete Kategorien des Haushalts, neueste zuerst. */
  recentCategoryIds?: string[];
  /** `Ohne Kategorie` anbieten. Ausschalten nur, wo eine Kategorie Pflicht ist. */
  allowNone?: boolean;
  /**
   * Schnellanlage aus der erfolglosen Suche heraus. Ohne Angabe entscheidet
   * die Haushaltsrolle (Owner/Editor duerfen, Viewer/Child nicht).
   */
  allowCreate?: boolean;
};

type Row =
  | { kind: "none"; key: string }
  | { kind: "header"; key: string; title: string }
  | { kind: "category"; key: string; category: PickerCategory; selectable: boolean }
  | { kind: "createPrompt"; key: string }
  | { kind: "groupChoice"; key: string; groupId: string; groupName: string }
  | { kind: "noResult"; key: string };

/**
 * Die gemeinsame, durchsuchbare Kategorieauswahl (docs/KATEGORISIERUNGSPLAN.md,
 * "Eine gemeinsame, durchsuchbare Kategorieauswahl").
 *
 * Nahezu vollflaechiges Sheet mit FESTSTEHENDER Suche - die Vorgaengerversion
 * war eine lange, nicht durchsuchbare Gruppenliste und skalierte nicht auf
 * die 50 Kategorien des Standardpakets.
 *
 * Reihenfolge: `Ohne Kategorie`, aktuell gewaehlte, zuletzt verwendete, dann
 * alle aktiven Kategorien gruppiert. Die eigentliche Filter-/Sortierlogik
 * liegt in ../utils/categorySearch.ts und ist dort auch getestet - hier steht
 * nur die Darstellung.
 *
 * Barrierefreiheit: jede Zeile ist ein `radio` mit `accessibilityState`,
 * mindestens 44dp hoch, und die Auswahl wird zusaetzlich zur Farbe durch ein
 * Haekchen markiert (Farbe ist nie das einzige Unterscheidungsmerkmal).
 */
export function CategorySearchSheet({
  visible,
  groups,
  value,
  onChange,
  onClose,
  title = "Kategorie wählen",
  amountMinor = null,
  recentCategoryIds = [],
  allowNone = true,
  allowCreate,
}: CategorySearchSheetProps) {
  const theme = useTheme();
  const { canManage } = useCategoryPermissions();
  const createCategory = useCreateCategory();

  const [query, setQuery] = useState("");
  /** Zweiter Schritt der Schnellanlage: in welche Gruppe soll die neue Kategorie? */
  const [choosingGroupFor, setChoosingGroupFor] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [searchFocused, setSearchFocused] = useState(false);

  const mayCreate = allowCreate ?? canManage;

  const model = useMemo(
    () =>
      buildPickerSections({
        groups,
        value,
        recentIds: recentCategoryIds,
        query,
        amountMinor,
      }),
    [groups, value, recentCategoryIds, query, amountMinor],
  );

  const activeGroups = useMemo(
    () =>
      [...groups].filter((group) => !group.is_archived).sort((a, b) => a.sort_order - b.sort_order),
    [groups],
  );

  const trimmedQuery = query.trim();

  const rows = useMemo<Row[]>(() => {
    if (choosingGroupFor !== null) {
      return activeGroups.map((group) => ({
        kind: "groupChoice" as const,
        key: `choice:${group.id}`,
        groupId: group.id,
        groupName: group.name,
      }));
    }

    const out: Row[] = [];
    for (const section of model.sections) {
      if (section.kind === "none") {
        // `Ohne Kategorie` steht immer an erster Stelle und wird nicht
        // weggefiltert: es ist der ausdrueckliche Zustand "passt nichts",
        // nicht ein Suchtreffer.
        if (allowNone) out.push({ kind: "none", key: "none" });
        continue;
      }
      if (section.title) {
        out.push({ kind: "header", key: `h:${section.key}`, title: section.title });
      }
      for (const category of section.items) {
        out.push({
          kind: "category",
          key: `${section.key}:${category.id}`,
          category,
          // Archivierte Kategorien sind nicht neu waehlbar - sie erscheinen
          // nur als bestehende historische Zuordnung.
          selectable: !category.isArchived,
        });
      }
    }
    if (model.matchCount === 0 && trimmedQuery.length > 0) {
      out.push(
        mayCreate
          ? { kind: "createPrompt", key: "create" }
          : { kind: "noResult", key: "no-result" },
      );
    }
    return out;
  }, [model, allowNone, trimmedQuery, mayCreate, choosingGroupFor, activeGroups]);

  function reset() {
    setQuery("");
    setChoosingGroupFor(null);
    setCreateError(null);
  }

  function handleSelect(categoryId: string | null) {
    reset();
    onChange(categoryId);
  }

  function handleClose() {
    reset();
    onClose();
  }

  function handleCreatePressed() {
    setCreateError(null);
    const name = trimmedQuery;
    if (name.length === 0) return;
    const [onlyGroup] = activeGroups;
    if (activeGroups.length === 1 && onlyGroup) {
      void createIn(onlyGroup.id, name);
      return;
    }
    setChoosingGroupFor(name);
  }

  async function createIn(groupId: string, name: string) {
    try {
      const created = await createCategory.mutateAsync({
        group_id: groupId,
        name,
        // Die Richtung der Buchung ist der beste verfuegbare Startwert; sie
        // laesst sich in der Kategorienverwaltung jederzeit korrigieren.
        default_type: amountMinor !== null && amountMinor > 0 ? "income" : "expense",
      });
      handleSelect(created.id);
    } catch (cause) {
      setChoosingGroupFor(null);
      setCreateError(
        cause instanceof Error ? cause.message : "Kategorie konnte nicht angelegt werden.",
      );
    }
  }

  const sheetTitle = choosingGroupFor !== null ? "In welche Gruppe?" : title;

  return (
    <SheetModal visible={visible} title={sheetTitle} onClose={handleClose} size="full">
      {choosingGroupFor !== null ? (
        <View style={styles.searchBar}>
          <Text style={[styles.hintText, { color: theme.textSecondary }]}>
            {`Neue Kategorie "${choosingGroupFor}" - bitte eine Gruppe wählen.`}
          </Text>
        </View>
      ) : (
        <View style={styles.searchBar}>
          <TextInput
            value={query}
            onChangeText={setQuery}
            onFocus={() => setSearchFocused(true)}
            onBlur={() => setSearchFocused(false)}
            placeholder="Suchen: Gruppe, Kategorie oder Synonym"
            placeholderTextColor={theme.textSecondary}
            accessibilityLabel="Kategorien durchsuchen"
            autoCorrect={false}
            autoCapitalize="none"
            returnKeyType="search"
            style={[
              styles.searchInput,
              {
                borderColor: searchFocused ? theme.focusRing : theme.border,
                backgroundColor: theme.surfaceRaised,
                color: theme.textPrimary,
              },
            ]}
          />
          {query.length > 0 ? (
            <Pressable
              onPress={() => setQuery("")}
              accessibilityRole="button"
              accessibilityLabel="Suche zurücksetzen"
              style={styles.clearButton}
              hitSlop={8}
            >
              <Text style={[styles.clearLabel, { color: theme.textSecondary }]}>Zurücksetzen</Text>
            </Pressable>
          ) : null}
        </View>
      )}

      {createError ? (
        <Text accessibilityRole="alert" style={[styles.errorText, { color: theme.warning }]}>
          {createError}
        </Text>
      ) : null}

      {createCategory.isPending ? (
        <View style={styles.pendingRow}>
          <ActivityIndicator color={theme.textSecondary} />
          <Text style={[styles.hintText, { color: theme.textSecondary }]}>
            Kategorie wird angelegt...
          </Text>
        </View>
      ) : null}

      <FlatList
        data={rows}
        keyExtractor={(row) => row.key}
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={styles.listContent}
        renderItem={({ item }) => {
          switch (item.kind) {
            case "header":
              return (
                <Text
                  accessibilityRole="header"
                  style={[styles.groupLabel, { color: theme.textSecondary }]}
                >
                  {item.title}
                </Text>
              );
            case "none":
              return (
                <SelectableRow
                  label="Ohne Kategorie"
                  hint="Bleibt in der Prüfung, statt in ein Auffangbecken zu wandern."
                  selected={value === null}
                  onPress={() => handleSelect(null)}
                />
              );
            case "category":
              return (
                <SelectableRow
                  label={item.category.path}
                  badge={item.category.isArchived ? "Archiviert" : undefined}
                  hint={
                    item.category.isArchived
                      ? "Nicht mehr neu wählbar - bitte eine aktive Kategorie wählen."
                      : undefined
                  }
                  selected={item.category.id === value}
                  disabled={!item.selectable}
                  onPress={() => handleSelect(item.category.id)}
                />
              );
            case "createPrompt":
              return (
                <Pressable
                  onPress={handleCreatePressed}
                  accessibilityRole="button"
                  accessibilityLabel={`Neue Kategorie ${trimmedQuery} erstellen`}
                  disabled={createCategory.isPending}
                  style={({ pressed }) => [
                    styles.row,
                    {
                      borderColor: theme.primaryStrong,
                      backgroundColor: theme.surfaceRaised,
                      opacity: pressed ? 0.7 : 1,
                    },
                  ]}
                >
                  <Text style={[styles.rowLabel, { color: theme.positive }]}>
                    {`+ Neue Kategorie "${trimmedQuery}" erstellen`}
                  </Text>
                </Pressable>
              );
            case "groupChoice":
              return (
                <SelectableRow
                  label={item.groupName}
                  selected={false}
                  onPress={() => void createIn(item.groupId, choosingGroupFor ?? "")}
                />
              );
            case "noResult":
              return (
                <Text style={[styles.emptyText, { color: theme.textSecondary }]}>
                  Keine Kategorie gefunden. Neue Kategorien dürfen nur Eigentümer und Bearbeiter
                  anlegen.
                </Text>
              );
          }
        }}
        ListEmptyComponent={
          <Text style={[styles.emptyText, { color: theme.textSecondary }]}>
            Keine Kategorien vorhanden.
          </Text>
        }
      />
    </SheetModal>
  );
}

/**
 * Eine auswaehlbare Zeile. Mindestens 44dp hoch; die Auswahl wird durch ein
 * Haekchen UND die Farbe gekennzeichnet, nie nur durch die Farbe.
 */
function SelectableRow({
  label,
  hint,
  badge,
  selected,
  disabled = false,
  onPress,
}: {
  label: string;
  hint?: string;
  badge?: string;
  selected: boolean;
  disabled?: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();

  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ selected, disabled }}
      accessibilityLabel={badge ? `${label}, ${badge}` : label}
      accessibilityHint={hint}
      onPress={disabled ? undefined : onPress}
      style={({ pressed }) => [
        styles.row,
        {
          borderColor: selected ? theme.primaryStrong : theme.border,
          backgroundColor: selected ? theme.positiveMuted : theme.surfaceRaised,
          opacity: disabled ? 0.6 : pressed ? 0.7 : 1,
        },
      ]}
    >
      <View style={styles.rowTextColumn}>
        <View style={styles.rowTitleRow}>
          <Text
            style={[styles.checkMark, { color: selected ? theme.positive : "transparent" }]}
            importantForAccessibility="no"
          >
            {"✓"}
          </Text>
          <Text
            style={[styles.rowLabel, { color: selected ? theme.positive : theme.textPrimary }]}
            numberOfLines={2}
          >
            {label}
          </Text>
          {badge ? <Badge label={badge} size="small" /> : null}
        </View>
        {hint ? <Text style={[styles.rowHint, { color: theme.textMuted }]}>{hint}</Text> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  searchBar: {
    paddingHorizontal: space.xl,
    paddingBottom: space.md,
    gap: space.sm,
  },
  searchInput: {
    minHeight: 48,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
    fontSize: typeScale.base,
  },
  clearButton: {
    minHeight: MIN_TOUCH,
    justifyContent: "center",
    alignSelf: "flex-start",
  },
  clearLabel: {
    fontSize: typeScale.base,
    fontWeight: "600",
  },
  listContent: {
    paddingHorizontal: space.xl,
    paddingBottom: space.xxxl,
    gap: space.sm,
  },
  groupLabel: {
    marginTop: space.md,
    fontSize: typeScale.micro,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: letterSpacingLabel,
  },
  row: {
    minHeight: MIN_TOUCH + 4,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    justifyContent: "center",
  },
  rowTextColumn: {
    gap: space.hair,
  },
  rowTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  checkMark: {
    width: 16,
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  rowLabel: {
    flex: 1,
    fontSize: typeScale.base,
    fontWeight: "600",
  },
  rowHint: {
    fontSize: typeScale.caption,
    paddingLeft: space.xxl,
  },
  hintText: {
    fontSize: typeScale.body,
  },
  errorText: {
    paddingHorizontal: space.xl,
    paddingBottom: space.sm,
    fontSize: typeScale.body,
  },
  pendingRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    paddingHorizontal: space.xl,
    paddingBottom: space.sm,
  },
  emptyText: {
    padding: space.xl,
    fontSize: typeScale.base,
  },
});
