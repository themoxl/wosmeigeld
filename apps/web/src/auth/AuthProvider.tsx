import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "../lib/supabase";

// Abschnitt 2 / 5 des Architekturplans: Auth laeuft client-seitig direkt
// gegen Supabase Auth. Dieser Provider haelt die aktuelle Session, kapselt
// signIn/signUp/signOut und stellt den Access Token fuer authentifizierte
// API-Aufrufe (siehe lib/api.ts) bereit.

export interface AuthActionResult {
  error: string | null;
}

interface AuthContextValue {
  session: Session | null;
  accessToken: string | null;
  isLoading: boolean;
  signIn: (email: string, password: string) => Promise<AuthActionResult>;
  signUp: (email: string, password: string) => Promise<AuthActionResult>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

// GoTrue-Fehler tragen seit v2 einen stabilen `code`; die englische Meldung
// ist nur Fallback fuer aeltere Server. Deshalb wird zuerst der Code
// ausgewertet und erst danach der Text.
interface AuthErrorLike {
  message: string;
  name?: string;
  status?: number;
  code?: string;
}

// Der Request hat den Server gar nicht erreicht: Supabase laeuft nicht, die
// VITE_SUPABASE_URL zeigt ins Leere, oder der Browser blockt. GoTrue liefert
// dann keinen Code, sondern nur die rohe Browser-Meldung ("NetworkError when
// attempting to fetch resource." in Firefox, "Failed to fetch" in Chrome).
function isNetworkFailure(error: AuthErrorLike): boolean {
  if (error.name === "AuthRetryableFetchError" || error.status === 0) {
    return true;
  }
  const normalized = error.message.toLowerCase();
  return (
    normalized.includes("failed to fetch") ||
    normalized.includes("networkerror") ||
    normalized.includes("network request failed") ||
    normalized.includes("load failed")
  );
}

// Uebersetzt die haeufigsten GoTrue-Fehler ins Deutsche. Unbekannte Meldungen
// werden unveraendert durchgereicht, damit kein Fehler verschluckt wird.
function translateAuthError(error: AuthErrorLike): string {
  if (isNetworkFailure(error)) {
    const target = import.meta.env.VITE_SUPABASE_URL;
    return target
      ? `Der Anmeldedienst unter ${target} ist nicht erreichbar. Laeuft die lokale Supabase-Instanz?`
      : "Der Anmeldedienst ist nicht erreichbar: VITE_SUPABASE_URL ist nicht gesetzt.";
  }

  switch (error.code) {
    case "invalid_credentials":
      return "E-Mail oder Passwort ist falsch.";
    case "email_not_confirmed":
      return "Diese E-Mail-Adresse ist noch nicht bestaetigt.";
    case "user_already_exists":
    case "email_exists":
      return "Fuer diese E-Mail-Adresse existiert bereits ein Konto.";
    case "weak_password":
      return "Das Passwort muss mindestens 6 Zeichen lang sein.";
    case "email_address_invalid":
    case "validation_failed":
      return "Diese E-Mail-Adresse ist ungueltig.";
    case "over_request_rate_limit":
    case "over_email_send_rate_limit":
      return "Zu viele Versuche. Bitte kurz warten und erneut versuchen.";
    case "signup_disabled":
      return "Registrierung ist auf diesem Server deaktiviert.";
    case "user_banned":
      return "Dieses Konto ist gesperrt.";
    default:
      break;
  }

  const normalized = error.message.toLowerCase();
  if (normalized.includes("invalid login credentials")) {
    return "E-Mail oder Passwort ist falsch.";
  }
  if (normalized.includes("already registered")) {
    return "Fuer diese E-Mail-Adresse existiert bereits ein Konto.";
  }
  if (normalized.includes("password should be at least")) {
    return "Das Passwort muss mindestens 6 Zeichen lang sein.";
  }
  if (normalized.includes("unable to validate email") || normalized.includes("invalid email")) {
    return "Diese E-Mail-Adresse ist ungueltig.";
  }
  if (normalized.includes("email rate limit") || normalized.includes("rate limit")) {
    return "Zu viele Versuche. Bitte kurz warten und erneut versuchen.";
  }
  return error.message;
}

// signInWithPassword/signUp geben Auth-Fehler zurueck, reichen andere Fehler
// (Storage-Lock, Netzwerk-Ausnahmen ausserhalb des Retry-Pfads) aber als
// Exception durch. Ohne dieses catch bliebe das Formular ohne Meldung im
// Zustand "Bitte warten…" haengen.
async function runAuthAction(
  action: () => Promise<{ error: AuthErrorLike | null }>,
): Promise<AuthActionResult> {
  try {
    const { error } = await action();
    return { error: error ? translateAuthError(error) : null };
  } catch (cause) {
    const fallback: AuthErrorLike =
      cause instanceof Error
        ? { message: cause.message, name: cause.name }
        : { message: String(cause) };
    return { error: translateAuthError(fallback) };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let active = true;

    supabase.auth.getSession().then(({ data }) => {
      if (!active) {
        return;
      }
      setSession(data.session);
      setIsLoading(false);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setIsLoading(false);
    });

    return () => {
      active = false;
      listener.subscription.unsubscribe();
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      accessToken: session?.access_token ?? null,
      isLoading,
      signIn: (email, password) =>
        runAuthAction(() => supabase.auth.signInWithPassword({ email, password })),
      signUp: (email, password) => runAuthAction(() => supabase.auth.signUp({ email, password })),
      signOut: async () => {
        await supabase.auth.signOut();
      },
    }),
    [session, isLoading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// Kontext-Hook neben dem Provider in derselben Datei (siehe
// Aufgabenstellung) - Fast Refresh bleibt fuer den Provider selbst
// funktionsfaehig, nur der Hook-Export loest die Regel aus.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth() muss innerhalb von <AuthProvider> verwendet werden.");
  }
  return ctx;
}
