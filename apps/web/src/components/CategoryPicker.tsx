import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import { useCategoryContext, useCreateCategory } from "../hooks/useCategoryCatalog";
import {
  normalizeCategorySearchText,
  type Category,
  type CategoryGroup,
} from "../lib/categoryTypes";

// Gemeinsame, durchsuchbare Kategorieauswahl (docs/KATEGORISIERUNGSPLAN.md,
// "Eine gemeinsame, durchsuchbare Kategorieauswahl"). Ersetzt das frühere
// native <select> (CategorySelect) an allen Stellen: Buchungstabelle, neue
// Buchung, Auswahl-Leiste, Split-Editor, Review, Regeln und Fixkosten.
//
// Fachlicher Vertrag der Reihenfolge:
//   1. Ohne Kategorie
//   2. aktuell gewählte Kategorie
//   3. zuletzt verwendete Kategorien des Haushalts
//   4. alle aktiven Kategorien, gruppiert und sortiert
//
// Zugänglichkeit: role="combobox" mit aria-expanded/aria-controls, ein
// role="listbox"-Popup mit role="option" + aria-selected, Bedienung über
// ArrowUp/ArrowDown/Home/End/Enter/Escape, aria-activedescendant für den
// Screenreader-Fokus und ein sichtbarer Fokusring. Farbe ist nie die einzige
// Kennzeichnung - jede Zeile trägt immer den vollständigen Textpfad
// "Gruppe · Kategorie", Archiviertes zusätzlich ein Text-Badge.
//
// Das Popup wird per Portal nach document.body gerendert, mit
// position: fixed statt des naheliegenden position: absolute im relativ
// positionierten Feld (analog zu GridPopover.tsx). Grund: der Picker steht
// in Kontexten mit eigenem Scrollbereich (.txn-table-wrapper in der
// Buchungstabelle und der Review-Übersicht, .modal-body im
// Korrekturdialog) - ein absolut positioniertes Popup würde dort am Rand
// des Scrollbereichs abgeschnitten, nicht am tatsächlichen Fensterrand.
// Genau das führte dazu, dass die Liste in der Review-Tabelle wie
// eingeklappt wirkte, obwohl auf dem Bildschirm noch Platz war.

const CREATE_OPTION_VALUE = "__create__";

// Zuletzt verwendete Kategorien je Haushalt. Dafür gibt es keinen Endpunkt;
// die Liste wird deshalb lokal geführt und überlebt Reloads über
// localStorage. Ein fehlender Eintrag ist unkritisch - dann entfällt der
// Abschnitt schlicht.
const RECENT_STORAGE_PREFIX = "finanzplaner:web:recent-categories:";
const RECENT_LIMIT = 5;

function readRecentCategoryIds(householdId: string): string[] {
  if (!householdId) {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(`${RECENT_STORAGE_PREFIX}${householdId}`);
    if (!raw) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function writeRecentCategoryIds(householdId: string, ids: string[]): void {
  if (!householdId) {
    return;
  }
  try {
    window.localStorage.setItem(`${RECENT_STORAGE_PREFIX}${householdId}`, JSON.stringify(ids));
  } catch {
    // localStorage evtl. nicht verfügbar - die Liste bleibt dann leer.
  }
}

interface FlatCategory {
  category: Category;
  groupId: string;
  groupName: string;
  path: string;
  haystack: string;
}

function buildFlatCategories(groups: CategoryGroup[] | undefined): FlatCategory[] {
  if (!groups) {
    return [];
  }
  return (
    [...groups]
      // Leere Gruppen erscheinen in der Verwaltung, nicht im Picker.
      .filter((group) => !group.is_archived && group.categories.some((c) => !c.is_archived))
      .sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, "de"))
      .flatMap((group) =>
        [...group.categories]
          .filter((category) => !category.is_archived)
          .sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, "de"))
          .map((category) => ({
            category,
            groupId: group.id,
            groupName: group.name,
            path: `${group.name} · ${category.name}`,
            haystack: normalizeCategorySearchText(
              [
                group.name,
                category.name,
                ...(category.synonyms ?? []),
                category.description ?? "",
              ].join(" "),
            ),
          })),
      )
  );
}

interface PickerOption {
  /** "" = Ohne Kategorie, CREATE_OPTION_VALUE = Schnellanlage, sonst Kategorie-ID. */
  value: string;
  /** Immer der vollständige Pfad "Gruppe · Kategorie" (außer bei Sonderzeilen). */
  label: string;
  hint: string | null;
  icon: string | null;
  color: string | null;
}

type PickerRow =
  | { kind: "header"; key: string; title: string }
  | { kind: "option"; key: string; index: number; option: PickerOption }
  | { kind: "archived"; key: string; label: string };

function toOption(entry: FlatCategory): PickerOption {
  return {
    value: entry.category.id,
    label: entry.path,
    hint: null,
    icon: entry.category.icon,
    color: entry.category.color,
  };
}

export interface CategoryPickerProps {
  /** Aktiver Picker-Baum aus GET /v1/categories. */
  categoryGroups: CategoryGroup[] | undefined;
  /** Kategorie-ID; "" bedeutet "keine Kategorie" (category_id: null). */
  value: string;
  onChange: (value: string) => void;
  /** Beschriftung der Nullauswahl - je nach Kontext z. B. "Kategorie entfernen". */
  noneLabel?: string;
  className?: string;
  id?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  "aria-label"?: string;
  /**
   * Betrag der Buchung in Minor Units. Priorisiert passende `default_type`
   * (negativ -> expense, positiv -> income), versteckt aber niemals andere
   * Kategorien: Erstattungen und Korrekturbuchungen bleiben möglich.
   */
  amountMinor?: number | null;
  /**
   * Anzeigename einer bereits zugeordneten, inzwischen archivierten
   * Kategorie. Sie wird schreibgeschützt mit Badge "Archiviert" gezeigt und
   * kann auf eine aktive Kategorie geändert werden.
   */
  archivedSelectionLabel?: string | null;
  /** Gruppe für die Schnellanlage; sonst die erste aktive Gruppe. */
  createGroupId?: string;
  /** Schnellanlage ausblenden (Standard: erlaubt, wenn die Rolle schreiben darf). */
  allowCreate?: boolean;
  /** Wird nach Escape bzw. beim Verlassen des Pickers aufgerufen (Inline-Zellen). */
  onDismiss?: () => void;
}

export function CategoryPicker({
  categoryGroups,
  value,
  onChange,
  noneLabel = "Ohne Kategorie",
  className,
  id,
  disabled = false,
  autoFocus = false,
  "aria-label": ariaLabel,
  amountMinor = null,
  archivedSelectionLabel = null,
  createGroupId,
  allowCreate,
  onDismiss,
}: CategoryPickerProps) {
  const generatedId = useId();
  const listboxId = `${generatedId}-listbox`;
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const listboxRef = useRef<HTMLUListElement>(null);

  const { householdId, canEdit } = useCategoryContext();
  const createMutation = useCreateCategory();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [createError, setCreateError] = useState<string | null>(null);
  const [recentIds, setRecentIds] = useState<string[]>(() => readRecentCategoryIds(householdId));
  const [popoverPosition, setPopoverPosition] = useState<{
    top: number;
    left: number;
    width: number;
  } | null>(null);

  const flatCategories = useMemo(() => buildFlatCategories(categoryGroups), [categoryGroups]);
  const byId = useMemo(() => {
    const map = new Map<string, FlatCategory>();
    flatCategories.forEach((entry) => map.set(entry.category.id, entry));
    return map;
  }, [flatCategories]);

  const selected = value ? (byId.get(value) ?? null) : null;
  // Zugeordnet, aber nicht (mehr) im aktiven Baum: archiviert oder gemergt.
  const selectionIsArchived = Boolean(value) && !selected;

  const preferredType = amountMinor == null ? null : amountMinor > 0 ? "income" : "expense";
  const normalizedQuery = normalizeCategorySearchText(query.trim());
  const isSearching = normalizedQuery.length > 0;

  const effectiveAllowCreate = allowCreate ?? canEdit;
  const createTargetGroupId =
    createGroupId ??
    categoryGroups?.find((group) => !group.is_archived)?.id ??
    categoryGroups?.[0]?.id ??
    null;
  const createTargetGroupName =
    categoryGroups?.find((group) => group.id === createTargetGroupId)?.name ?? null;

  /**
   * Zeilen des Popups in genau der Reihenfolge, in der sie gerendert werden.
   * Die laufende `index`-Nummer der auswählbaren Zeilen ist zugleich der
   * Tastaturindex und die aria-activedescendant-ID - deshalb wird sie hier
   * einmal zentral vergeben statt beim Rendern hochgezählt.
   */
  const { rows, optionCount, createRowIndex } = useMemo(() => {
    const built: PickerRow[] = [];
    let index = 0;
    let createIndex = -1;

    const pushOption = (option: PickerOption) => {
      built.push({ kind: "option", key: `${option.value}-${index}`, index, option });
      index += 1;
    };

    const noneOption: PickerOption = {
      value: "",
      label: noneLabel,
      hint: null,
      icon: null,
      color: null,
    };

    if (selectionIsArchived) {
      built.push({
        kind: "archived",
        key: "archived-current",
        label: archivedSelectionLabel ?? "Archivierte Kategorie",
      });
    }

    if (isSearching) {
      const hits = flatCategories.filter((entry) => entry.haystack.includes(normalizedQuery));
      // Priorisieren, nicht filtern (Plan: "verstecken andere Kategorien aber nicht").
      const ordered = preferredType
        ? [
            ...hits.filter((entry) => entry.category.default_type === preferredType),
            ...hits.filter((entry) => entry.category.default_type !== preferredType),
          ]
        : hits;

      if (normalizeCategorySearchText(noneLabel).includes(normalizedQuery)) {
        pushOption(noneOption);
      }
      built.push({ kind: "header", key: "h-results", title: `Treffer (${ordered.length})` });
      ordered.forEach((entry) => pushOption(toOption(entry)));

      if (effectiveAllowCreate && ordered.length === 0 && createTargetGroupId) {
        createIndex = index;
        pushOption({
          value: CREATE_OPTION_VALUE,
          label: `Neue Kategorie „${query.trim()}“ erstellen`,
          hint: createTargetGroupName
            ? `wird in der Gruppe „${createTargetGroupName}“ angelegt`
            : null,
          icon: null,
          color: null,
        });
      }
      return { rows: built, optionCount: index, createRowIndex: createIndex };
    }

    pushOption(noneOption);

    if (selected) {
      built.push({ kind: "header", key: "h-current", title: "Aktuell gewählt" });
      pushOption(toOption(selected));
    }

    const recentEntries = recentIds
      .filter((recentId) => recentId !== value)
      .map((recentId) => byId.get(recentId))
      .filter((entry): entry is FlatCategory => Boolean(entry))
      .slice(0, RECENT_LIMIT);
    if (recentEntries.length > 0) {
      built.push({ kind: "header", key: "h-recent", title: "Zuletzt verwendet" });
      recentEntries.forEach((entry) => pushOption(toOption(entry)));
    }

    let lastGroupId: string | null = null;
    flatCategories.forEach((entry) => {
      if (entry.groupId !== lastGroupId) {
        lastGroupId = entry.groupId;
        built.push({ kind: "header", key: `h-${entry.groupId}`, title: entry.groupName });
      }
      pushOption(toOption(entry));
    });

    return { rows: built, optionCount: index, createRowIndex: createIndex };
  }, [
    archivedSelectionLabel,
    byId,
    createTargetGroupId,
    createTargetGroupName,
    effectiveAllowCreate,
    flatCategories,
    isSearching,
    noneLabel,
    normalizedQuery,
    preferredType,
    query,
    recentIds,
    selected,
    selectionIsArchived,
    value,
  ]);

  const clampedActiveIndex = optionCount === 0 ? -1 : Math.min(activeIndex, optionCount - 1);
  const activeOptionId =
    clampedActiveIndex >= 0 ? `${listboxId}-option-${clampedActiveIndex}` : undefined;

  const reposition = useCallback(() => {
    const anchor = wrapperRef.current;
    const panel = listboxRef.current;
    if (!anchor || !panel) {
      return;
    }
    const margin = 8;
    const gap = 2;
    const anchorRect = anchor.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();

    let left = anchorRect.left;
    left = Math.min(left, window.innerWidth - panelRect.width - margin);
    left = Math.max(margin, left);

    // Nach unten aufklappen, solange Platz ist - sonst nach oben, damit die
    // Liste nicht am unteren Fensterrand abgeschnitten wird.
    let top = anchorRect.bottom + gap;
    if (top + panelRect.height > window.innerHeight - margin) {
      const above = anchorRect.top - panelRect.height - gap;
      top = above >= margin ? above : margin;
    }

    const width = anchorRect.width;
    setPopoverPosition((current) =>
      current && current.top === top && current.left === left && current.width === width
        ? current
        : { top, left, width },
    );
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      return;
    }
    reposition();
    // `rows` als Abhaengigkeit: die Trefferliste aendert bei jeder Eingabe
    // ihre Hoehe, wodurch sich auch die Aufklapprichtung (oben/unten) neu
    // entscheiden kann.
  }, [open, reposition, rows]);

  useEffect(() => {
    if (!open) {
      return;
    }
    // Capture-Phase: Scrollen findet oft in einem inneren Container statt
    // (Tabellenwrapper, Modal-Body), nicht am Fenster.
    const handle = () => reposition();
    window.addEventListener("scroll", handle, true);
    window.addEventListener("resize", handle);
    return () => {
      window.removeEventListener("scroll", handle, true);
      window.removeEventListener("resize", handle);
    };
  }, [open, reposition]);

  const optionAt = (index: number): PickerOption | null => {
    const row = rows.find((entry) => entry.kind === "option" && entry.index === index);
    return row && row.kind === "option" ? row.option : null;
  };

  const rememberRecent = (categoryId: string) => {
    if (!categoryId) {
      return;
    }
    const next = [categoryId, ...recentIds.filter((entry) => entry !== categoryId)].slice(
      0,
      RECENT_LIMIT,
    );
    setRecentIds(next);
    writeRecentCategoryIds(householdId, next);
  };

  const closePicker = (notifyDismiss = false) => {
    setOpen(false);
    setQuery("");
    setActiveIndex(0);
    if (notifyDismiss) {
      onDismiss?.();
    }
  };

  const openPicker = () => {
    if (disabled) {
      return;
    }
    setOpen(true);
    setCreateError(null);
    setActiveIndex(0);
  };

  const handleCreate = () => {
    const name = query.trim();
    if (!name || !createTargetGroupId) {
      return;
    }
    setCreateError(null);
    createMutation.mutate(
      {
        group_id: createTargetGroupId,
        name,
        default_type: preferredType === "income" ? "income" : "expense",
        is_budgeted: true,
        synonyms: [],
      },
      {
        onSuccess: (category) => {
          rememberRecent(category.id);
          onChange(category.id);
          closePicker();
          inputRef.current?.focus();
        },
        onError: (error: unknown) => {
          setCreateError(
            error instanceof Error ? error.message : "Kategorie konnte nicht angelegt werden.",
          );
        },
      },
    );
  };

  const selectOption = (option: PickerOption) => {
    if (option.value === CREATE_OPTION_VALUE) {
      handleCreate();
      return;
    }
    rememberRecent(option.value);
    onChange(option.value);
    closePicker();
    inputRef.current?.focus();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) {
      return;
    }
    if (!open) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter") {
        event.preventDefault();
        openPicker();
      } else if (event.key === "Escape") {
        onDismiss?.();
      }
      return;
    }

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setActiveIndex((current) => (optionCount === 0 ? 0 : (current + 1) % optionCount));
        break;
      case "ArrowUp":
        event.preventDefault();
        setActiveIndex((current) =>
          optionCount === 0 ? 0 : (current - 1 + optionCount) % optionCount,
        );
        break;
      case "Home":
        event.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        event.preventDefault();
        setActiveIndex(Math.max(optionCount - 1, 0));
        break;
      case "Enter": {
        event.preventDefault();
        const option = optionAt(clampedActiveIndex);
        if (option) {
          selectOption(option);
        }
        break;
      }
      case "Escape":
        event.preventDefault();
        closePicker(true);
        break;
      case "Tab":
        closePicker();
        break;
      default:
        break;
    }
  };

  const displayLabel = selected
    ? selected.path
    : selectionIsArchived
      ? (archivedSelectionLabel ?? "Archivierte Kategorie")
      : noneLabel;

  return (
    <div
      ref={wrapperRef}
      className={`category-picker ${className ?? ""}`.trim()}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          closePicker(true);
        }
      }}
    >
      <input
        ref={inputRef}
        id={id}
        type="text"
        role="combobox"
        className="form-input category-picker-input"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-haspopup="listbox"
        aria-autocomplete="list"
        aria-activedescendant={open ? activeOptionId : undefined}
        aria-label={ariaLabel}
        autoComplete="off"
        autoFocus={autoFocus}
        disabled={disabled}
        placeholder={open ? displayLabel : noneLabel}
        value={open ? query : displayLabel}
        onChange={(event) => {
          if (!open) {
            openPicker();
          }
          setQuery(event.target.value);
          setActiveIndex(0);
        }}
        onKeyDown={handleKeyDown}
        // Ein Klick öffnet nur - er schließt nie. Sonst würde ein Klick, der
        // im geöffneten Feld bloß den Cursor setzt, das Popup zuklappen.
        // Geschlossen wird über Escape, Auswahl oder Verlassen des Feldes.
        onMouseDown={() => {
          if (!open) {
            openPicker();
          }
        }}
      />

      {selectionIsArchived && !open && (
        <span className="status-badge status-badge--warning category-picker-archived-badge">
          Archiviert
        </span>
      )}

      {open &&
        createPortal(
          <ul
            ref={listboxRef}
            className="category-picker-listbox"
            id={listboxId}
            role="listbox"
            aria-label="Kategorien"
            style={{
              position: "fixed",
              top: popoverPosition?.top ?? 0,
              left: popoverPosition?.left ?? 0,
              width: popoverPosition?.width,
              // Vor der ersten Messung wuerde die Liste sonst kurz oben
              // links im Fenster aufblitzen.
              visibility: popoverPosition ? "visible" : "hidden",
            }}
          >
            {rows.map((row) => {
              if (row.kind === "header") {
                return (
                  <li key={row.key} className="category-picker-section-title" role="presentation">
                    {row.title}
                  </li>
                );
              }
              if (row.kind === "archived") {
                return (
                  <li
                    key={row.key}
                    className="category-picker-option category-picker-option--readonly"
                    aria-disabled="true"
                  >
                    <span className="category-picker-option-text">
                      <span className="category-picker-option-label">{row.label}</span>
                      <span className="category-picker-option-hint">
                        Archiviert – bleibt in der Historie sichtbar, ist aber nicht neu wählbar.
                      </span>
                    </span>
                  </li>
                );
              }

              const { option, index } = row;
              const isActive = index === clampedActiveIndex;
              const isSelected = option.value === value && option.value !== CREATE_OPTION_VALUE;
              return (
                <li
                  key={row.key}
                  id={`${listboxId}-option-${index}`}
                  role="option"
                  aria-selected={isSelected}
                  className={[
                    "category-picker-option",
                    isActive ? "category-picker-option--active" : "",
                    option.value === CREATE_OPTION_VALUE ? "category-picker-option--create" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  ref={(el) => {
                    if (isActive && el && typeof el.scrollIntoView === "function") {
                      el.scrollIntoView({ block: "nearest" });
                    }
                  }}
                  // onMouseDown statt onClick: der Blur des Eingabefelds würde
                  // das Popup sonst schließen, bevor der Klick ankommt.
                  onMouseDown={(event) => {
                    event.preventDefault();
                    selectOption(option);
                  }}
                >
                  {option.color && (
                    <span
                      className="category-picker-dot"
                      style={{ backgroundColor: option.color }}
                      aria-hidden="true"
                    />
                  )}
                  {option.icon && (
                    <span className="category-picker-icon" aria-hidden="true">
                      {option.icon}
                    </span>
                  )}
                  <span className="category-picker-option-text">
                    <span className="category-picker-option-label">{option.label}</span>
                    {option.hint && (
                      <span className="category-picker-option-hint">{option.hint}</span>
                    )}
                  </span>
                  {isSelected && (
                    <span className="category-picker-check" aria-hidden="true">
                      ✓
                    </span>
                  )}
                </li>
              );
            })}

            {isSearching && optionCount === 0 && createRowIndex < 0 && (
              <li className="category-picker-empty" role="presentation">
                Keine Kategorie gefunden.
              </li>
            )}

            {createMutation.isPending && (
              <li className="category-picker-empty" role="presentation">
                Kategorie wird angelegt…
              </li>
            )}

            {createError && (
              <li className="category-picker-empty status-error" role="alert">
                {createError}
              </li>
            )}
          </ul>,
          document.body,
        )}
    </div>
  );
}
