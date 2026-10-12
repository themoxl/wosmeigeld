interface PlaceholderPageProps {
  title: string;
  phase?: number;
}

// Platzhalter fuer alle Sidebar-Bereiche, die erst in einer spaeteren Phase
// (Abschnitt 17 des Architekturplans) mit echter Fachlogik gefuellt werden.
export function PlaceholderPage({ title, phase }: PlaceholderPageProps) {
  return (
    <section className="page">
      <h1 className="page-heading">{title}</h1>
      <p className="placeholder-note">
        {phase
          ? `Kommt in Phase ${phase} des Architekturplans.`
          : "Diese Ansicht ist fuer eine spätere Phase geplant."}
      </p>
    </section>
  );
}
