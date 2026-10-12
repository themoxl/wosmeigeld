import { NavLink } from "react-router-dom";
import {
  isClassificationRunActive,
  useLatestClassificationRun,
} from "../hooks/useClassificationRun";

/**
 * Globaler Status des laufenden KI-Checks in der Seitenleiste (siehe
 * docs/KI-FINANZASSISTENT-PLAN.md, Ausbaustufe 0). Er ist auf jeder Seite
 * sichtbar, weil der Lauf im Hintergrund weiterlaeuft, waehrend der Nutzer
 * navigiert - vorher war das ausserhalb der Review-Seite nicht erkennbar.
 *
 * Bewusst nur waehrend des Laufs: das Ergebnis eines beendeten Laufs steht
 * dauerhaft auf der Review-Seite, wo es auch hingehoert. Ein Ergebnisbanner in
 * der Navigation muesste sonst nach Zeit oder Klick verfallen - Logik, die
 * ohne echten Nutzen nur schiefgehen kann.
 */
export function ClassificationRunChip() {
  const { data: run } = useLatestClassificationRun();

  if (!run || !isClassificationRunActive(run)) return null;

  return (
    <NavLink to="/review" className="sidebar-footer-block sidebar-footer-block--link">
      <span className="sidebar-footer-title">KI-Finanz-Check</span>
      <span className="sidebar-footer-value">
        {run.is_stale
          ? "Keine Rückmeldung – prüfen →"
          : `Kategorien ${run.processed_count}/${run.total_count} →`}
      </span>
    </NavLink>
  );
}
