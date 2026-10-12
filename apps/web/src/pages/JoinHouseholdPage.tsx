import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { ApiError, previewHouseholdInvite, redeemHouseholdInvite } from "../lib/api";
import type { HouseholdInvitePreview } from "../lib/types";

const ROLE_LABELS: Record<string, string> = {
  owner: "Eigentümer",
  editor: "Bearbeiter",
  viewer: "Betrachter",
  child: "Eingeschränkt",
};

const ERROR_MESSAGES: Record<number, string> = {
  404: "Dieser Einladungscode ist unbekannt. Bitte prüfen Sie die Eingabe.",
  410: "Dieser Einladungscode ist nicht mehr gültig (abgelaufen, bereits verwendet oder widerrufen).",
};

function messageForError(error: unknown): string {
  if (error instanceof ApiError && error.status) {
    const mapped = ERROR_MESSAGES[error.status];
    if (mapped) return mapped;
  }
  return error instanceof Error ? error.message : "Einladungscode konnte nicht geprüft werden.";
}

// Beitreten per Einladungscode (Stage 2e). Bewusst AUSSERHALB von
// RequireHousehold registriert (siehe App.tsx): frisch registrierte Nutzer
// ohne Haushalt muessen diese Seite erreichen koennen, nicht nur bereits
// eingerichtete.
export function JoinHouseholdPage() {
  const { accessToken } = useAuth();
  const { selectHousehold, refetch } = useHousehold();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [code, setCode] = useState(searchParams.get("code") ?? "");
  const [preview, setPreview] = useState<HouseholdInvitePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [isJoining, setIsJoining] = useState(false);

  const handlePreview = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!accessToken || !code.trim()) return;

    setError(null);
    setPreview(null);
    setIsLoadingPreview(true);
    try {
      const result = await previewHouseholdInvite(accessToken, code.trim());
      setPreview(result);
    } catch (err) {
      setError(messageForError(err));
    } finally {
      setIsLoadingPreview(false);
    }
  };

  const handleJoin = async () => {
    if (!accessToken || !code.trim()) return;

    setError(null);
    setIsJoining(true);
    try {
      const household = await redeemHouseholdInvite(accessToken, code.trim());
      selectHousehold(household.id);
      refetch();
      navigate("/", { replace: true });
    } catch (err) {
      setError(messageForError(err));
      setIsJoining(false);
    }
  };

  return (
    <section className="page">
      <h1 className="page-heading">Haushalt beitreten</h1>
      <div className="card onboarding-card">
        <p className="card-body">
          Geben Sie den Einladungscode ein, den Sie von einem Haushaltsmitglied erhalten haben.
        </p>

        <form className="auth-form" onSubmit={(event) => void handlePreview(event)}>
          <label className="form-field">
            <span className="form-label">Einladungscode</span>
            <input
              type="text"
              required
              value={code}
              onChange={(event) => {
                setCode(event.target.value.toUpperCase());
                setPreview(null);
              }}
              className="form-input"
              autoFocus
            />
          </label>

          {error && (
            <p role="alert" className="status-error">
              {error}
            </p>
          )}

          {!preview && (
            <button
              type="submit"
              className="btn-primary"
              disabled={isLoadingPreview || !code.trim()}
            >
              {isLoadingPreview ? "Wird geprüft…" : "Code prüfen"}
            </button>
          )}
        </form>

        {preview && (
          <div style={{ marginTop: "var(--space-4)" }}>
            <p className="card-body">
              Sie werden eingeladen, dem Haushalt <strong>{preview.household_name}</strong> als{" "}
              <strong>{ROLE_LABELS[preview.role] ?? preview.role}</strong> beizutreten.
            </p>
            <button
              type="button"
              className="btn-primary"
              disabled={isJoining}
              onClick={() => void handleJoin()}
            >
              {isJoining ? "Wird beigetreten…" : "Beitreten"}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
