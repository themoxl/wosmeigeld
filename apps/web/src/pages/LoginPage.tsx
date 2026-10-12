import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";

type Mode = "signin" | "signup";

interface LocationState {
  from?: { pathname?: string; search?: string };
}

// Eigenes, zentriertes Formular ausserhalb des Layouts mit Sidebar (siehe
// Aufgabenstellung Punkt 3). E-Mail + Passwort, Umschalter zwischen
// "Anmelden" und "Registrieren".
export function LoginPage() {
  const { session, isLoading, signIn, signUp } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isPasswordVisible, setIsPasswordVisible] = useState(false);

  if (!isLoading && session) {
    const state = location.state as LocationState | null;
    const redirectTo = state?.from
      ? `${state.from.pathname ?? "/"}${state.from.search ?? ""}`
      : "/";
    return <Navigate to={redirectTo} replace />;
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    const result =
      mode === "signin" ? await signIn(email, password) : await signUp(email, password);

    setIsSubmitting(false);

    if (result.error) {
      setError(result.error);
      return;
    }

    navigate("/", { replace: true });
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <span className="auth-brand">Finanzplaner</span>
        <h1 className="auth-heading">{mode === "signin" ? "Anmelden" : "Konto erstellen"}</h1>

        <form className="auth-form" onSubmit={handleSubmit}>
          <label className="form-field" htmlFor="login-email">
            <span className="form-label">E-Mail</span>
            <input
              id="login-email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="form-input"
            />
          </label>

          {/* Label und Eingabefeld ueber htmlFor/id verbunden, damit der
              Sichtbarkeits-Umschalter neben dem Feld stehen kann, ohne im
              Label zu liegen (Klick auf ein Label fokussiert sonst nur das
              Eingabefeld statt den Knopf auszuloesen). */}
          <div className="form-field">
            <label className="form-label" htmlFor="login-password">
              Passwort
            </label>
            <div className="input-with-action">
              <input
                id="login-password"
                type={isPasswordVisible ? "text" : "password"}
                required
                minLength={6}
                autoComplete={mode === "signin" ? "current-password" : "new-password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="form-input"
              />
              <button
                type="button"
                className="input-action"
                aria-controls="login-password"
                aria-pressed={isPasswordVisible}
                title={isPasswordVisible ? "Passwort verbergen" : "Passwort anzeigen"}
                onClick={() => setIsPasswordVisible((current) => !current)}
              >
                {isPasswordVisible ? "Verbergen" : "Anzeigen"}
              </button>
            </div>
          </div>

          {error && (
            <p role="alert" className="status-error">
              {error}
            </p>
          )}

          <button type="submit" className="btn-primary" disabled={isSubmitting}>
            {isSubmitting ? "Bitte warten…" : mode === "signin" ? "Anmelden" : "Registrieren"}
          </button>
        </form>

        <button
          type="button"
          className="auth-switch"
          onClick={() => {
            setMode((current) => (current === "signin" ? "signup" : "signin"));
            setError(null);
          }}
        >
          {mode === "signin" ? "Noch kein Konto? Registrieren" : "Bereits ein Konto? Anmelden"}
        </button>
      </div>
    </div>
  );
}
