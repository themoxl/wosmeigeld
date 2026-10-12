import { Outlet } from "react-router-dom";
import { useHousehold } from "./HouseholdProvider";
import { HouseholdOnboarding } from "./HouseholdOnboarding";

// Nur innerhalb von RequireAuth verwendet: laedt GET /v1/me (via
// HouseholdProvider) und zeigt das Onboarding-Formular, solange kein
// Haushalt existiert. Erst mit gewaehltem Haushalt werden die eigentlichen
// Seiten (Konten, Buchungen, ...) gerendert.
export function RequireHousehold() {
  const { households, isLoading, isError, selectedHouseholdId } = useHousehold();

  if (isLoading) {
    return (
      <section className="page">
        <p role="status">Lade Haushalt…</p>
      </section>
    );
  }

  if (isError) {
    return (
      <section className="page">
        <p role="alert" className="status-error">
          Haushaltsdaten konnten nicht geladen werden. Bitte Seite neu laden.
        </p>
      </section>
    );
  }

  if (households.length === 0 || !selectedHouseholdId) {
    return <HouseholdOnboarding />;
  }

  return <Outlet />;
}
