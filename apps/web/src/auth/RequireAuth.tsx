import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";

// Leitet nicht angemeldete Nutzer zu /login um. Die /status-Seite bleibt
// bewusst ausserhalb dieses Wrappers (siehe App.tsx) - sie braucht keine
// Anmeldung.
export function RequireAuth() {
  const { session, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <section className="page">
        <p role="status">Lade Sitzung…</p>
      </section>
    );
  }

  if (!session) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
