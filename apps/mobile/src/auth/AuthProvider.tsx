import type { Session } from "@supabase/supabase-js";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";
import type { ServerConfig } from "../config/serverConfig";
import { createSupabaseClient } from "./supabase";

// "loading": erste Session-Pruefung (getSession()) noch nicht abgeschlossen.
// "signedOut" / "signedIn": Ergebnis von getSession() bzw. onAuthStateChange.
export type AuthStatus = "loading" | "signedOut" | "signedIn";

type SignUpResult = {
  // Lokal ist die E-Mail-Bestaetigung deaktiviert (siehe Auftragskontext):
  // supabase.auth.signUp() liefert dann sofort eine gueltige Session zurueck.
  // In einer Umgebung mit aktiver Bestaetigung ist data.session null, bis die
  // E-Mail bestaetigt wurde - das zeigt der Login-Screen dann als Hinweis an.
  sessionCreated: boolean;
};

type AuthContextValue = {
  status: AuthStatus;
  session: Session | null;
  accessToken: string | null;
  userEmail: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<SignUpResult>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

type AuthProviderProps = PropsWithChildren<{
  /** Kommt als Prop statt ueber useServerConfig() intern: so bleibt der
   * Hooks-Aufruf in dieser Komponente unbedingt (rules-of-hooks) - ein
   * interner Fruehausstieg fuer "config fehlt noch" wuerde Hooks bedingt
   * machen. app/_layout.tsx (ServerGate) montiert <AuthProvider> ohnehin erst,
   * sobald ServerConfigProvider status "ready" mit gesetztem config meldet,
   * und erzwingt bei einem Serverwechsel per `key={...}` einen kompletten
   * Neuaufbau. */
  config: ServerConfig;
}>;

export function AuthProvider({ children, config }: AuthProviderProps) {
  // useMemo statt Modul-Singleton (siehe ./supabase.ts): der Client haengt
  // von der aktiven Serverkonfiguration ab.
  const { client: supabase, dispose } = useMemo(
    () => createSupabaseClient(config.supabaseUrl, config.supabaseAnonKey),
    [config.supabaseUrl, config.supabaseAnonKey],
  );
  useEffect(() => dispose, [dispose]);

  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  useEffect(() => {
    let active = true;

    supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data.session);
      setStatus(data.session ? "signedIn" : "signedOut");
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      if (!active) return;
      setSession(nextSession);
      setStatus(nextSession ? "signedIn" : "signedOut");
    });

    return () => {
      active = false;
      listener.subscription.unsubscribe();
    };
  }, [supabase]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      session,
      accessToken: session?.access_token ?? null,
      userEmail: session?.user?.email ?? null,
      async signIn(email, password) {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      },
      async signUp(email, password) {
        const { data, error } = await supabase.auth.signUp({ email, password });
        if (error) throw error;
        return { sessionCreated: data.session !== null };
      },
      async signOut() {
        const { error } = await supabase.auth.signOut();
        if (error) throw error;
      },
    }),
    [status, session, supabase],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Haelt die Supabase-Session (getSession() + onAuthStateChange) und liefert Access Token/Auth-Aktionen. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth() muss innerhalb von <AuthProvider> verwendet werden.");
  }
  return ctx;
}
