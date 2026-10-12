import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { createHousehold, ApiError } from "../lib/api";
import { useHousehold } from "./HouseholdProvider";

// Onboarding-Formular fuer Nutzer ohne Haushalt. Ruft POST /v1/households
// auf, das Backend legt dabei automatisch die fuenf Standard-
// Kategoriegruppen an (siehe Aufgabenstellung).
export function HouseholdOnboarding() {
  const { accessToken } = useAuth();
  const { refetch, selectHousehold } = useHousehold();

  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!accessToken || !name.trim()) {
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const household = await createHousehold(accessToken, name.trim());
      selectHousehold(household.id);
      refetch();
    } catch (err) {
      setError(
        err instanceof ApiError || err instanceof Error
          ? err.message
          : "Haushalt konnte nicht erstellt werden.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="page">
      <h1 className="page-heading">Haushalt erstellen</h1>
      <div className="card onboarding-card">
        <p className="card-body">
          Es ist noch kein Haushalt vorhanden. Lege einen Haushalt an, um Konten, Kategorien und
          Buchungen zu verwalten.
        </p>

        <form className="auth-form" onSubmit={handleSubmit}>
          <label className="form-field">
            <span className="form-label">Name des Haushalts</span>
            <input
              type="text"
              required
              minLength={1}
              maxLength={160}
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="form-input"
              autoFocus
            />
          </label>

          {error && (
            <p role="alert" className="status-error">
              {error}
            </p>
          )}

          <button type="submit" className="btn-primary" disabled={isSubmitting || !name.trim()}>
            {isSubmitting ? "Wird erstellt…" : "Haushalt erstellen"}
          </button>
        </form>

        <p className="card-body" style={{ marginTop: "var(--space-3)" }}>
          <Link to="/beitreten">Ich habe einen Einladungscode</Link>
        </p>
      </div>
    </section>
  );
}
