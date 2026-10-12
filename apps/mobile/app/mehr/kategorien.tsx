import { Ionicons } from "@expo/vector-icons";
import { Stack, useRouter } from "expo-router";
import { useMemo, useState } from "react";
import { Alert, FlatList, Pressable, StyleSheet, Text, View, type AlertButton } from "react-native";
import type { CatalogCategory, CatalogGroup } from "../../src/api/categoryTypes";
import { EmptyState } from "../../src/components/EmptyState";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { SearchField } from "../../src/components/SearchField";
import { SegmentedControl } from "../../src/components/SegmentedControl";
import { SheetModal } from "../../src/components/SheetModal";
import { TextField } from "../../src/components/TextField";
import { Badge, Card, DataRow, IconButton } from "../../src/components/ui";
import {
  useApplyCategoryArchive,
  useApplyCategoryGroupArchive,
  useApplyCategoryMerge,
  useApplyCategoryMove,
  useApplyStandardPackage,
  useCategoryCatalog,
  useCategoryPermissions,
  useCreateCategoryGroup,
  usePreviewCategoryArchive,
  usePreviewCategoryGroupArchive,
  usePreviewCategoryMerge,
  usePreviewCategoryMove,
  usePreviewStandardPackage,
  useReorderCategories,
  useReorderCategoryGroups,
  useRestoreCategory,
  useRestoreCategoryGroup,
  useUpdateCategoryGroup,
} from "../../src/hooks/useCategoryCatalog";
import { generateId } from "../../src/offline/queueStore";
import { MIN_TOUCH, radii, space, typeScale, useTheme } from "../../src/theme";
import { normalizeSearchText } from "../../src/utils/categorySearch";

// Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md, "Zieloberflaeche:
// Kategorien verwalten / Mobile"): aufklappbare Gruppenkarten mit denselben
// Informationen und Zustaenden wie im Web. Kritische Aktionen liegen
// ausschliesslich im BESCHRIFTETEN Menue - bewusst nicht hinter einer
// Wischgeste, weil Archivieren und Zusammenfuehren nicht versehentlich
// ausgeloest werden duerfen.
//
// Es gibt hier keinen Loeschen-Pfad. Kategorien werden archiviert; ein
// hartes Loeschen wuerde historische Summen zerstoeren und ist weder in der
// API noch in der Oberflaeche vorgesehen.

const TYPE_LABELS: Record<string, string> = {
  expense: "Ausgabe",
  income: "Einnahme",
  transfer: "Transfer",
};

const BUCKET_LABELS: Record<string, string> = {
  needs: "Bedarf",
  wants: "Wunsch",
  savings: "Sparen",
};

const SCOPE_OPTIONS = [
  { value: "active" as const, label: "Aktiv" },
  { value: "archived" as const, label: "Mit archivierten" },
];

type MenuAction = {
  key: string;
  label: string;
  hint?: string;
  destructive?: boolean;
  onPress: () => void;
};

type MenuState = { title: string; actions: MenuAction[] } | null;

/** Auswahl einer Zielgruppe bzw. Zielkategorie fuer Verschieben/Zusammenfuehren. */
type PickerState =
  | { kind: "moveTarget"; category: CatalogCategory }
  | { kind: "mergeTarget"; category: CatalogCategory }
  | { kind: "archiveReplacement"; category: CatalogCategory; operationId: string }
  | null;

/** Gruppe anlegen oder umbenennen - beides braucht nur einen Namen. */
type GroupEditorState = { mode: "create" } | { mode: "rename"; group: CatalogGroup } | null;

function describeCategory(category: CatalogCategory): string {
  const parts: string[] = [];
  parts.push(TYPE_LABELS[category.default_type ?? ""] ?? "Ohne Art");
  parts.push(
    category.budget_bucket
      ? (BUCKET_LABELS[category.budget_bucket] ?? category.budget_bucket)
      : "kein Budgetbereich",
  );
  parts.push((category.is_budgeted ?? true) ? "im Budget" : "nicht im Budget");
  const used = category.usage?.total;
  if (typeof used === "number") {
    parts.push(used === 0 ? "noch nicht verwendet" : `${used}x verwendet`);
  }
  return parts.join(" · ");
}

export default function KategorienScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { canManage, isOwner, role } = useCategoryPermissions();
  const catalogQuery = useCategoryCatalog({ includeArchived: true, includeUsage: true });

  const createGroup = useCreateCategoryGroup();
  const updateGroup = useUpdateCategoryGroup();
  const reorderCategories = useReorderCategories();
  const reorderGroups = useReorderCategoryGroups();
  const previewMove = usePreviewCategoryMove();
  const applyMove = useApplyCategoryMove();
  const previewArchive = usePreviewCategoryArchive();
  const applyArchive = useApplyCategoryArchive();
  const restoreCategory = useRestoreCategory();
  const previewGroupArchive = usePreviewCategoryGroupArchive();
  const applyGroupArchive = useApplyCategoryGroupArchive();
  const restoreGroup = useRestoreCategoryGroup();
  const previewMerge = usePreviewCategoryMerge();
  const applyMerge = useApplyCategoryMerge();
  const previewPackage = usePreviewStandardPackage();
  const applyPackage = useApplyStandardPackage();

  const [showArchived, setShowArchived] = useState(false);
  const [search, setSearch] = useState("");
  const [expandedGroupIds, setExpandedGroupIds] = useState<string[]>([]);
  const [menu, setMenu] = useState<MenuState>(null);
  const [picker, setPicker] = useState<PickerState>(null);
  const [groupEditor, setGroupEditor] = useState<GroupEditorState>(null);
  const [groupDraft, setGroupDraft] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const catalog = catalogQuery.data;
  const revision = catalog?.taxonomy_revision ?? 0;

  const visibleGroups = useMemo<CatalogGroup[]>(() => {
    const needle = normalizeSearchText(search);
    const groups = [...(catalog?.groups ?? [])].sort((a, b) => a.sort_order - b.sort_order);
    return groups
      .filter((group) => (showArchived ? true : !group.is_archived))
      .map((group) => ({
        ...group,
        categories: [...group.categories]
          .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
          .filter((category) => (showArchived ? true : !category.is_archived))
          .filter((category) => {
            if (needle.length === 0) return true;
            const haystack = normalizeSearchText(
              [group.name, category.name, ...(category.synonyms ?? [])].join(" "),
            );
            return needle.split(" ").every((token) => haystack.includes(token));
          }),
      }))
      .filter((group) => {
        if (normalizeSearchText(search).length === 0) return true;
        // Bei aktiver Suche nur Gruppen mit Treffern zeigen; ohne Suche
        // bleiben leere Gruppen sichtbar - in der VERWALTUNG (anders als im
        // Picker) muessen sie bearbeitbar bleiben.
        return group.categories.length > 0;
      });
  }, [catalog, showArchived, search]);

  function toggleGroup(groupId: string) {
    setExpandedGroupIds((prev) =>
      prev.includes(groupId) ? prev.filter((id) => id !== groupId) : [...prev, groupId],
    );
  }

  function reportError(cause: unknown, fallback: string) {
    const message = cause instanceof Error ? cause.message : fallback;
    setActionError(message);
    Alert.alert(fallback, message);
  }

  // --- Gruppenaktionen -----------------------------------------------------

  // Gruppenname erfassen: bewusst ein eigenes Sheet statt Alert.prompt -
  // Alert.prompt ist in React Native iOS-only und waere auf Android ein
  // stiller Klick ins Leere.
  function handleAddGroup() {
    setMenu(null);
    setGroupDraft("");
    setGroupEditor({ mode: "create" });
  }

  function handleRenameGroup(group: CatalogGroup) {
    setMenu(null);
    setGroupDraft(group.name);
    setGroupEditor({ mode: "rename", group });
  }

  async function submitGroupEditor() {
    if (!groupEditor) return;
    const name = groupDraft.split(/\s+/).filter(Boolean).join(" ");
    if (name.length === 0) {
      setActionError("Bitte einen Gruppennamen eingeben.");
      return;
    }
    const editor = groupEditor;
    setGroupEditor(null);
    setActionError(null);
    try {
      if (editor.mode === "create") {
        await createGroup.mutateAsync({ name });
      } else {
        await updateGroup.mutateAsync({
          groupId: editor.group.id,
          version: editor.group.version,
          patch: { name },
        });
      }
    } catch (cause) {
      reportError(
        cause,
        editor.mode === "create"
          ? "Gruppe konnte nicht angelegt werden"
          : "Gruppe konnte nicht umbenannt werden",
      );
    }
  }

  function handleArchiveGroup(group: CatalogGroup) {
    setMenu(null);
    previewGroupArchive
      .mutateAsync(group.id)
      .then((preview) => {
        const names = preview.active_category_names.join(", ");
        Alert.alert(
          "Gruppe archivieren?",
          preview.requires_resolution
            ? `In "${preview.group_name}" sind noch ${preview.active_category_ids.length} aktive Kategorien: ${names}. Sie werden in derselben Operation mit archiviert. Historische Buchungen und Budgets bleiben unverändert.`
            : `"${preview.group_name}" enthält keine aktiven Kategorien mehr und kann archiviert werden. Die Historie bleibt erhalten.`,
          [
            { text: "Abbrechen", style: "cancel" },
            {
              text: "Archivieren",
              style: "destructive",
              onPress: () => {
                applyGroupArchive
                  .mutateAsync({
                    groupId: group.id,
                    version: group.version,
                    apply: {
                      operation_id: preview.operation_id,
                      archive_categories: preview.requires_resolution,
                    },
                  })
                  .catch((cause: unknown) =>
                    reportError(cause, "Gruppe konnte nicht archiviert werden"),
                  );
              },
            },
          ],
        );
      })
      .catch((cause: unknown) => reportError(cause, "Auswirkung konnte nicht ermittelt werden"));
  }

  function handleRestoreGroup(group: CatalogGroup) {
    setMenu(null);
    restoreGroup
      .mutateAsync(group.id)
      .catch((cause: unknown) =>
        reportError(cause, "Gruppe konnte nicht wiederhergestellt werden"),
      );
  }

  function moveGroup(group: CatalogGroup, direction: -1 | 1) {
    setMenu(null);
    const ordered = [...(catalog?.groups ?? [])].sort((a, b) => a.sort_order - b.sort_order);
    const ids = swap(
      ordered.map((g) => g.id),
      group.id,
      direction,
    );
    if (!ids) return;
    reorderGroups
      .mutateAsync({ taxonomyRevision: revision, orderedIds: ids })
      .catch((cause: unknown) => reportError(cause, "Reihenfolge konnte nicht gespeichert werden"));
  }

  function groupMenu(group: CatalogGroup): MenuAction[] {
    if (!canManage) return [];
    if (group.is_archived) {
      return [
        {
          key: "restore",
          label: "Gruppe wiederherstellen",
          hint: "Die Gruppe wird wieder auswählbar.",
          onPress: () => handleRestoreGroup(group),
        },
      ];
    }
    return [
      {
        key: "rename",
        label: "Gruppe umbenennen",
        hint: "Buchungen und Budgets bleiben unverändert zugeordnet.",
        onPress: () => handleRenameGroup(group),
      },
      {
        key: "add",
        label: "Kategorie hinzufügen",
        onPress: () => {
          setMenu(null);
          router.push(`/kategorie-neu?groupId=${group.id}`);
        },
      },
      { key: "up", label: "Nach oben", onPress: () => moveGroup(group, -1) },
      { key: "down", label: "Nach unten", onPress: () => moveGroup(group, 1) },
      {
        key: "archive",
        label: "Gruppe archivieren",
        hint: "Zeigt vorher, welche Kategorien betroffen sind.",
        destructive: true,
        onPress: () => handleArchiveGroup(group),
      },
    ];
  }

  // --- Kategorieaktionen ---------------------------------------------------

  function moveCategoryOrder(group: CatalogGroup, category: CatalogCategory, direction: -1 | 1) {
    setMenu(null);
    const ordered = [...group.categories].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
    const ids = swap(
      ordered.map((c) => c.id),
      category.id,
      direction,
    );
    if (!ids) return;
    reorderCategories
      .mutateAsync({ taxonomyRevision: revision, orderedIds: ids, groupId: group.id })
      .catch((cause: unknown) => reportError(cause, "Reihenfolge konnte nicht gespeichert werden"));
  }

  function confirmMove(category: CatalogCategory, targetGroupId: string) {
    setPicker(null);
    previewMove
      .mutateAsync({ categoryId: category.id, targetGroupId })
      .then((preview) => {
        const range =
          preview.first_booked_date && preview.last_booked_date
            ? ` (${preview.first_booked_date} bis ${preview.last_booked_date})`
            : "";
        Alert.alert(
          "Kategorie verschieben?",
          `"${preview.category_name}" wechselt von "${preview.source_group_name}" nach "${preview.target_group_name}".\n\n` +
            `Rückwirkend betroffen: ${preview.affected_splits} Buchungssplit(s)${range} und ` +
            `${preview.affected_budget_allocations} Budgetzeile(n). Berichte verwenden ab sofort die neue Gruppe.\n\n${preview.hint}`,
          [
            { text: "Abbrechen", style: "cancel" },
            {
              text: "Verschieben",
              onPress: () => {
                applyMove
                  .mutateAsync({
                    categoryId: category.id,
                    apply: { operation_id: preview.operation_id, target_group_id: targetGroupId },
                  })
                  .catch((cause: unknown) =>
                    reportError(cause, "Kategorie konnte nicht verschoben werden"),
                  );
              },
            },
          ],
        );
      })
      .catch((cause: unknown) => reportError(cause, "Auswirkung konnte nicht ermittelt werden"));
  }

  function handleArchiveCategory(category: CatalogCategory) {
    setMenu(null);
    previewArchive
      .mutateAsync(category.id)
      .then((preview) => {
        if (preview.blocking_reason) {
          Alert.alert("Archivieren nicht möglich", preview.blocking_reason);
          return;
        }
        const body =
          `"${preview.category_name}" bleibt in ${preview.historical.transaction_splits} Buchungssplit(s), ` +
          `${preview.historical.budget_allocations} Budgetzeile(n) und allen Berichten sichtbar - mit Badge "Archiviert".\n\n` +
          (preview.requires_resolution
            ? `Zukunftswirksam sind noch ${preview.forward.total} Referenz(en) (Regeln, Fixkosten, Abos, Ziele). ` +
              `Ohne Ersatzkategorie werden Regeln deaktiviert und die übrigen Referenzen gelöst.`
            : "Es gibt keine zukunftswirksamen Referenzen.");
        const buttons: AlertButton[] = [
          { text: "Abbrechen", style: "cancel" },
          {
            text: "Ohne Ersatz archivieren",
            style: "destructive",
            onPress: () => void applyArchiveNow(category, preview.operation_id, null),
          },
        ];
        if (preview.requires_resolution) {
          buttons.push({
            text: "Ersatz wählen",
            onPress: () => {
              setPicker({
                kind: "archiveReplacement",
                category,
                operationId: preview.operation_id,
              });
            },
          });
        }
        Alert.alert("Kategorie archivieren?", body, buttons);
      })
      .catch((cause: unknown) => reportError(cause, "Auswirkung konnte nicht ermittelt werden"));
  }

  async function applyArchiveNow(
    category: CatalogCategory,
    operationId: string,
    replacementCategoryId: string | null,
  ) {
    setPicker(null);
    try {
      await applyArchive.mutateAsync({
        categoryId: category.id,
        version: category.version ?? 1,
        apply: { operation_id: operationId, replacement_category_id: replacementCategoryId },
      });
    } catch (cause) {
      reportError(cause, "Kategorie konnte nicht archiviert werden");
    }
  }

  function confirmMerge(source: CatalogCategory, targetId: string) {
    setPicker(null);
    previewMerge
      .mutateAsync({ source_category_id: source.id, target_category_id: targetId })
      .then((preview) => {
        if (preview.blocking_reason) {
          Alert.alert("Zusammenführen nicht möglich", preview.blocking_reason);
          return;
        }
        const collisions =
          preview.collisions.length > 0
            ? `\n\nZu klären: ${preview.collisions.map((c) => `${c.count}x ${c.detail}`).join(", ")}`
            : "";
        Alert.alert(
          "Endgültig zusammenführen?",
          `Die gesamte Historie von "${preview.source_category_name}" wird auf "${preview.target_category_name}" vereinheitlicht: ` +
            `${preview.references.total} Referenz(en), ${preview.transactions_to_bump} Buchung(en) bekommen eine neue Version.\n\n` +
            `Das lässt sich nicht rückgängig machen. Wer nur künftige Zuordnungen ändern will, archiviert stattdessen mit Ersatz.${collisions}`,
          [
            { text: "Abbrechen", style: "cancel" },
            {
              text: "Zusammenführen",
              style: "destructive",
              onPress: () => {
                applyMerge
                  .mutateAsync({
                    operation_id: preview.operation_id,
                    // Idempotenzschluessel: eine Wiederholung desselben
                    // Requests liefert dasselbe Ergebnis statt eines zweiten
                    // Merges.
                    request_id: generateId(),
                  })
                  .catch((cause: unknown) => reportError(cause, "Zusammenführen fehlgeschlagen"));
              },
            },
          ],
        );
      })
      .catch((cause: unknown) => reportError(cause, "Auswirkung konnte nicht ermittelt werden"));
  }

  function categoryMenu(group: CatalogGroup, category: CatalogCategory): MenuAction[] {
    if (!canManage) return [];
    if (category.is_archived) {
      return [
        {
          key: "restore",
          label: "Wiederherstellen",
          hint: "Die Kategorie ist danach wieder wählbar.",
          onPress: () => {
            setMenu(null);
            restoreCategory
              .mutateAsync({ categoryId: category.id })
              .catch((cause: unknown) =>
                reportError(cause, "Kategorie konnte nicht wiederhergestellt werden"),
              );
          },
        },
      ];
    }
    const actions: MenuAction[] = [
      {
        key: "edit",
        label: "Bearbeiten",
        onPress: () => {
          setMenu(null);
          router.push(`/kategorie/${category.id}`);
        },
      },
      {
        key: "move",
        label: "In andere Gruppe verschieben",
        hint: "Zeigt vorher die rückwirkende Auswirkung auf Berichte.",
        onPress: () => {
          setMenu(null);
          setPicker({ kind: "moveTarget", category });
        },
      },
      { key: "up", label: "Nach oben", onPress: () => moveCategoryOrder(group, category, -1) },
      { key: "down", label: "Nach unten", onPress: () => moveCategoryOrder(group, category, 1) },
      {
        key: "archive",
        label: "Archivieren",
        hint: "Historie bleibt erhalten; die Kategorie ist danach nicht mehr wählbar.",
        destructive: true,
        onPress: () => handleArchiveCategory(category),
      },
    ];
    if (isOwner) {
      actions.push({
        key: "merge",
        label: "Mit anderer Kategorie zusammenführen",
        hint: "Endgültig - vereinheitlicht die gesamte Historie.",
        destructive: true,
        onPress: () => {
          setMenu(null);
          setPicker({ kind: "mergeTarget", category });
        },
      });
    }
    return actions;
  }

  // --- Standardpaket -------------------------------------------------------

  function handleStandardPackage() {
    setMenu(null);
    previewPackage
      .mutateAsync()
      .then((preview) => {
        const body =
          `Version ${preview.taxonomy_version}: ${preview.new_count} neu, ` +
          `${preview.already_present_count} bereits vorhanden, ${preview.possible_match_count} mögliche Entsprechung, ` +
          `${preview.name_conflict_count} Namenskonflikt.\n\n` +
          "Das Anwenden ist additiv: es löscht, archiviert oder benennt nichts um. " +
          "Mögliche Entsprechungen werden nur nach ausdrücklicher Bestätigung zugeordnet - " +
          "hier bleibt es deshalb bei den neuen Kategorien.";
        Alert.alert("Standardkategorien ergänzen", body, [
          { text: "Abbrechen", style: "cancel" },
          {
            text: `${preview.new_count} ergänzen`,
            onPress: () => {
              applyPackage
                .mutateAsync({ operation_id: preview.operation_id, request_id: generateId() })
                .then((result) => {
                  Alert.alert(
                    "Standardkategorien ergänzt",
                    `${result.created_groups} Gruppe(n) und ${result.created_categories} Kategorie(n) angelegt. ` +
                      `${result.skipped_name_conflicts} wegen Namenskonflikt übersprungen.`,
                  );
                })
                .catch((cause: unknown) =>
                  reportError(cause, "Standardkategorien konnten nicht ergänzt werden"),
                );
            },
          },
        ]);
      })
      .catch((cause: unknown) => reportError(cause, "Vorschau konnte nicht berechnet werden"));
  }

  const headerMenu: MenuAction[] = canManage
    ? [
        { key: "group", label: "Gruppe hinzufügen", onPress: handleAddGroup },
        ...(isOwner
          ? [
              {
                key: "package",
                label: "Standardkategorien ergänzen",
                hint: "Additiv und wiederholbar - überschreibt keine eigene Änderung.",
                onPress: handleStandardPackage,
              },
            ]
          : []),
      ]
    : [];

  // --- Zielauswahl fuer Verschieben/Merge/Ersatz ---------------------------

  const pickerOptions = useMemo(() => {
    if (!picker) return [];
    const groups = catalog?.groups ?? [];
    if (picker.kind === "moveTarget") {
      return groups
        .filter((group) => !group.is_archived && group.id !== picker.category.group_id)
        .map((group) => ({ id: group.id, label: group.name }));
    }
    return groups
      .filter((group) => !group.is_archived)
      .flatMap((group) =>
        group.categories
          .filter((category) => !category.is_archived && category.id !== picker.category.id)
          .map((category) => ({ id: category.id, label: `${group.name} · ${category.name}` })),
      );
  }, [picker, catalog]);

  const pickerTitle =
    picker?.kind === "moveTarget"
      ? "In welche Gruppe verschieben?"
      : picker?.kind === "mergeTarget"
        ? "Womit zusammenführen?"
        : "Aktive Ersatzkategorie";

  function handlePickerSelect(targetId: string) {
    if (!picker) return;
    if (picker.kind === "moveTarget") confirmMove(picker.category, targetId);
    else if (picker.kind === "mergeTarget") confirmMerge(picker.category, targetId);
    else void applyArchiveNow(picker.category, picker.operationId, targetId);
  }

  return (
    <>
      <Stack.Screen options={{ title: "Kategorien" }} />
      <ScreenContainer>
        <View style={styles.toolbar}>
          <SearchField
            value={search}
            onChangeText={setSearch}
            placeholder="Kategorie oder Gruppe suchen"
            accessibilityLabel="Kategorien durchsuchen"
          />
          <SegmentedControl
            options={SCOPE_OPTIONS}
            value={showArchived ? "archived" : "active"}
            onChange={(value) => setShowArchived(value === "archived")}
            accessibilityLabel="Aktive oder archivierte Kategorien"
          />

          {canManage ? (
            <View style={styles.toolbarRow}>
              <View style={styles.toolbarAction}>
                <PrimaryButton
                  label="+ Kategorie hinzufügen"
                  size="compact"
                  onPress={() => router.push("/kategorie-neu")}
                />
              </View>
              {headerMenu.length > 0 ? (
                <View style={styles.toolbarAction}>
                  <PrimaryButton
                    label="Mehr Aktionen ..."
                    variant="secondary"
                    size="compact"
                    onPress={() => setMenu({ title: "Kategorien verwalten", actions: headerMenu })}
                  />
                </View>
              ) : null}
            </View>
          ) : (
            <Card tone="info" density="compact">
              <Text style={[styles.hint, { color: theme.info }]}>
                {`Deine Rolle in diesem Haushalt (${role ?? "unbekannt"}) darf Kategorien lesen, aber nicht ändern.`}
              </Text>
            </Card>
          )}

          {actionError ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.hint, { color: theme.warning }]} accessibilityRole="alert">
                {actionError}
              </Text>
            </Card>
          ) : null}
        </View>

        {catalogQuery.isLoading ? <InlineQueryState status="loading" skeletonRows={5} /> : null}

        {catalogQuery.isError ? (
          <InlineQueryState
            status="error"
            title="Kategorien konnten nicht geladen werden"
            message={catalogQuery.error instanceof Error ? catalogQuery.error.message : undefined}
            onRetry={() => void catalogQuery.refetch()}
          />
        ) : null}

        <FlatList
          data={visibleGroups}
          keyExtractor={(group) => group.id}
          contentContainerStyle={styles.listContent}
          refreshing={catalogQuery.isFetching && !catalogQuery.isLoading}
          onRefresh={() => void catalogQuery.refetch()}
          ListEmptyComponent={
            !catalogQuery.isLoading ? (
              <EmptyState
                title="Keine Kategorien"
                description="Lege eine eigene Kategorie an oder ergänze das Standardpaket."
              />
            ) : null
          }
          renderItem={({ item: group }) => {
            const expanded = expandedGroupIds.includes(group.id);
            const actions = groupMenu(group);
            return (
              <Card density="compact">
                <View style={styles.cardHeader}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityState={{ expanded }}
                    accessibilityLabel={`Gruppe ${group.name}`}
                    onPress={() => toggleGroup(group.id)}
                    style={styles.cardHeaderMain}
                  >
                    <View style={styles.groupTitleRow}>
                      <Ionicons
                        name={expanded ? "chevron-down" : "chevron-forward"}
                        size={18}
                        color={theme.textSecondary}
                      />
                      <Text style={[styles.groupName, { color: theme.textPrimary }]}>
                        {group.name}
                      </Text>
                      {group.is_archived ? <Badge label="Archiviert" size="small" /> : null}
                    </View>
                    <Text style={[styles.hint, { color: theme.textSecondary }]}>
                      {group.active_category_count} aktiv · {group.archived_category_count}{" "}
                      archiviert
                      {group.is_archived ? " · Gruppe archiviert" : ""}
                    </Text>
                  </Pressable>
                  {actions.length > 0 ? (
                    <IconButton
                      icon="ellipsis-horizontal"
                      accessibilityLabel={`Aktionen für Gruppe ${group.name}`}
                      onPress={() => setMenu({ title: group.name, actions })}
                    />
                  ) : null}
                </View>

                {expanded
                  ? group.categories.map((category, index) => {
                      const categoryActions = categoryMenu(group, category);
                      return (
                        <DataRow
                          key={category.id}
                          title={`${category.icon ? `${category.icon} ` : ""}${group.name} · ${category.name}`}
                          subtitle={
                            category.merged_into_name
                              ? `${describeCategory(category)} · Zusammengeführt mit ${category.merged_into_name}`
                              : describeCategory(category)
                          }
                          avatarLabel={category.name}
                          titleBadge={
                            category.is_archived ? (
                              <Badge label="Archiviert" size="small" />
                            ) : undefined
                          }
                          trailing={
                            categoryActions.length > 0 ? (
                              <IconButton
                                icon="ellipsis-horizontal"
                                size={36}
                                accessibilityLabel={`Aktionen für Kategorie ${category.name}`}
                                onPress={() =>
                                  setMenu({ title: category.name, actions: categoryActions })
                                }
                              />
                            ) : undefined
                          }
                          divider={index < group.categories.length - 1}
                        />
                      );
                    })
                  : null}

                {expanded && group.categories.length === 0 ? (
                  <Text style={[styles.hint, { color: theme.textSecondary }]}>
                    Diese Gruppe enthält keine Kategorien.
                  </Text>
                ) : null}
              </Card>
            );
          }}
        />

        {/* Beschriftetes Aktionsmenue statt Wischgeste - kritische Aktionen
            duerfen nicht versehentlich ausloesbar sein. */}
        <SheetModal visible={menu !== null} title={menu?.title ?? ""} onClose={() => setMenu(null)}>
          <View style={styles.menuContent}>
            {(menu?.actions ?? []).map((action) => (
              <Pressable
                key={action.key}
                accessibilityRole="button"
                accessibilityHint={action.hint}
                onPress={action.onPress}
                style={({ pressed }) => [
                  styles.menuRow,
                  {
                    borderColor: theme.border,
                    backgroundColor: pressed ? theme.surfaceHover : theme.surface,
                  },
                ]}
              >
                <Text
                  style={[
                    styles.menuLabel,
                    { color: action.destructive ? theme.warning : theme.textPrimary },
                  ]}
                >
                  {action.label}
                </Text>
                {action.hint ? (
                  <Text style={[styles.hint, { color: theme.textSecondary }]}>{action.hint}</Text>
                ) : null}
              </Pressable>
            ))}
          </View>
        </SheetModal>

        <SheetModal
          visible={groupEditor !== null}
          title={groupEditor?.mode === "rename" ? "Gruppe umbenennen" : "Neue Gruppe"}
          onClose={() => setGroupEditor(null)}
        >
          <View style={styles.menuContent}>
            <TextField
              label="Name der Kategoriegruppe"
              value={groupDraft}
              onChangeText={setGroupDraft}
              placeholder="Name der Kategoriegruppe"
              accessibilityLabel="Name der Kategoriegruppe"
              autoFocus
              maxLength={120}
            />
            {groupEditor?.mode === "rename" ? (
              <Text style={[styles.hint, { color: theme.textSecondary }]}>
                Buchungen und Budgets bleiben unverändert zugeordnet - nur der Anzeigename ändert
                sich.
              </Text>
            ) : null}
            <PrimaryButton label="Speichern" onPress={() => void submitGroupEditor()} />
          </View>
        </SheetModal>

        <SheetModal
          visible={picker !== null}
          title={pickerTitle}
          onClose={() => setPicker(null)}
          size="full"
        >
          <FlatList
            data={pickerOptions}
            keyExtractor={(option) => option.id}
            contentContainerStyle={styles.menuContent}
            ListEmptyComponent={
              <Text style={[styles.emptyPicker, { color: theme.textSecondary }]}>
                Keine passende Zielkategorie vorhanden.
              </Text>
            }
            renderItem={({ item: option }) => (
              <Pressable
                accessibilityRole="button"
                onPress={() => handlePickerSelect(option.id)}
                style={({ pressed }) => [
                  styles.menuRow,
                  {
                    borderColor: theme.border,
                    backgroundColor: pressed ? theme.surfaceHover : theme.surface,
                  },
                ]}
              >
                <Text style={[styles.menuLabel, { color: theme.textPrimary }]}>{option.label}</Text>
              </Pressable>
            )}
          />
        </SheetModal>
      </ScreenContainer>
    </>
  );
}

/** Tauscht `id` mit dem Nachbarn in Richtung `direction`; null, wenn am Rand. */
function swap(ids: string[], id: string, direction: -1 | 1): string[] | null {
  const index = ids.indexOf(id);
  const target = index + direction;
  if (index === -1 || target < 0 || target >= ids.length) return null;
  const next = [...ids];
  const a = next[index];
  const b = next[target];
  if (a === undefined || b === undefined) return null;
  next[index] = b;
  next[target] = a;
  return next;
}

const styles = StyleSheet.create({
  toolbar: {
    paddingHorizontal: space.lg,
    paddingBottom: space.md,
    gap: space.sm,
  },
  toolbarRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  toolbarAction: {
    flex: 1,
  },
  hint: {
    fontSize: typeScale.caption,
  },
  listContent: {
    paddingHorizontal: space.lg,
    paddingBottom: space.xxl,
    gap: space.md,
    flexGrow: 1,
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  cardHeaderMain: {
    flex: 1,
    minHeight: MIN_TOUCH,
    justifyContent: "center",
    gap: space.hair,
  },
  groupTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.xs,
  },
  groupName: {
    flexShrink: 1,
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  menuContent: {
    padding: space.lg,
    paddingTop: 0,
    gap: space.sm,
  },
  menuRow: {
    minHeight: MIN_TOUCH + 4,
    justifyContent: "center",
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    gap: space.hair,
  },
  menuLabel: {
    fontSize: typeScale.base,
    fontWeight: "600",
  },
  emptyPicker: {
    fontSize: typeScale.body,
    padding: space.lg,
  },
});
