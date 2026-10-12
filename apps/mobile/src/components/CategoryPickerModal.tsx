import type { CategoryGroup } from "../api/types";
import { useRecentCategoryIds } from "../hooks/useCategoryCatalog";
import { CategorySearchSheet } from "./CategorySearchSheet";

type Props = {
  visible: boolean;
  groups: CategoryGroup[];
  value: string | null;
  onChange: (categoryId: string | null) => void;
  onClose: () => void;
  title?: string;
  /** Betrag der Buchung in Cent - priorisiert passende Arten, versteckt nichts. */
  amountMinor?: number | null;
};

/**
 * Kategorieauswahl fuer die Buchungsliste, die Detailseite, Review und
 * Import.
 *
 * Die Props sind absichtlich unveraendert geblieben (`visible`, `groups`,
 * `value`, `onChange`, `onClose`, `title`): der Inhalt ist mit der
 * Kategorienverwaltung von der langen, nicht durchsuchbaren Gruppenliste auf
 * das durchsuchbare Sheet (CategorySearchSheet) umgestellt worden, aber alle
 * bestehenden Aufrufstellen konnten so unveraendert bleiben.
 *
 * "Zuletzt verwendet" wird hier und nicht in der Sheet-Komponente ermittelt,
 * damit CategorySearchSheet reine Darstellung bleibt und auch mit einer
 * anderen Quelle fuer die letzten Kategorien verwendbar ist.
 */
export function CategoryPickerModal({
  visible,
  groups,
  value,
  onChange,
  onClose,
  title = "Kategorie wählen",
  amountMinor = null,
}: Props) {
  const recentCategoryIds = useRecentCategoryIds();

  return (
    <CategorySearchSheet
      visible={visible}
      groups={groups}
      value={value}
      onChange={onChange}
      onClose={onClose}
      title={title}
      amountMinor={amountMinor}
      recentCategoryIds={recentCategoryIds}
    />
  );
}
