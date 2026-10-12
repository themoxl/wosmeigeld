import { SystemStatusPanel } from "../components/SystemStatusPanel";

// Oeffentlich erreichbar (ausserhalb RequireAuth in App.tsx) - der dokumentierte
// Ops-Einstiegspunkt (docs/BETRIEB.md), der auch funktionieren muss, wenn die
// Anmeldung selbst gestoert ist. Derselbe Inhalt steckt zusaetzlich im
// System-Register der Einstellungen (SettingsPage.tsx).
export function StatusPage() {
  return (
    <section className="page">
      <div className="page-header">
        <h1 className="page-heading">Systemstatus</h1>
      </div>
      <SystemStatusPanel />
    </section>
  );
}
