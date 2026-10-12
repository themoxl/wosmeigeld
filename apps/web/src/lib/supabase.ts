import { createClient } from "@supabase/supabase-js";

// Architekturplan Abschnitt 2: Auth laeuft client-seitig direkt gegen
// Supabase Auth (JWT wird nur durch die BFF-API validiert, nicht dort
// ausgestellt). Konfiguration kommt ausschliesslich aus Env-Variablen, die
// Docker Compose bereits durchreicht (siehe .env.example) - hier wird an der
// Konfiguration selbst nichts veraendert, nur konsumiert.
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnonKey) {
  console.warn(
    "VITE_SUPABASE_URL/VITE_SUPABASE_ANON_KEY sind nicht gesetzt - Anmeldung wird nicht funktionieren.",
  );
}

export const supabase = createClient(supabaseUrl ?? "", supabaseAnonKey ?? "");
