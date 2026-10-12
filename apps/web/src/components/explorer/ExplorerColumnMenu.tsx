import { useState } from "react";
import { useFacets } from "../../hooks/useAnalytics";
import { findCondition } from "../../lib/analytics";
import { ColumnFilterMenu } from "../datagrid/ColumnFilterMenu";
import type { GridColumn, SortDirection } from "../datagrid/types";
import type { AnalyticsCondition, AnalyticsQuery } from "../../lib/types";

// Das Spaltenmenue des Explorers: dieselbe Maske wie im Buchungsregister,
// aber mit den Werten vom Server.
//
// Die Werteliste beruecksichtigt alle UEBRIGEN Filter, aber nicht sich selbst
// (`POST /v1/analytics/facets` mit `skip_field`). Genau dieses Verhalten
// kennen Nutzer aus Excel, und sein Fehlen faellt sofort auf: sonst koennte
// man einen einmal gesetzten Filter nie mehr erweitern.

interface ExplorerColumnMenuProps {
  column: GridColumn;
  query: AnalyticsQuery;
  onConditionChange: (condition: AnalyticsCondition | null) => void;
  sortDirection?: SortDirection | null;
  onSortChange?: (direction: SortDirection | null) => void;
  grouped?: boolean;
  onToggleGroup?: () => void;
  onHide?: () => void;
  onClose: () => void;
}

export function ExplorerColumnMenu({
  column,
  query,
  onConditionChange,
  sortDirection = null,
  onSortChange,
  grouped,
  onToggleGroup,
  onHide,
  onClose,
}: ExplorerColumnMenuProps) {
  const [valueSearch, setValueSearch] = useState("");
  const wantsValues = column.hasValues === true;
  const { data, isLoading } = useFacets(column.key, query, wantsValues, valueSearch);

  return (
    <ColumnFilterMenu
      column={column}
      condition={findCondition(query, column.key) ?? null}
      onConditionChange={onConditionChange}
      sortDirection={sortDirection}
      onSortChange={onSortChange}
      grouped={grouped}
      onToggleGroup={onToggleGroup}
      onHide={onHide}
      values={wantsValues ? (data?.values ?? []) : undefined}
      valuesLoading={wantsValues && isLoading}
      valuesCapped={data?.capped}
      onValueSearchChange={setValueSearch}
      onClose={onClose}
    />
  );
}
