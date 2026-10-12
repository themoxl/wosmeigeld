// Fuer "Eigener Server" im Server-Setup (src/config/ServerSetupScreen.tsx):
// der Nutzer gibt nur die API-Adresse seiner Installation ein, diese Funktion
// fragt dort GET /v1/system/instance-config ab (siehe
// services/api/app/api/v1/routes/system.py) und liefert die zugehoerige
// Supabase-Konfiguration zurueck - der Nutzer muss Supabase-URL/Anon-Key
// nicht selbst kennen oder eintippen.
export type ServerDiscoveryResult = {
  supabaseUrl: string;
  supabaseAnonKey: string;
  instanceDisplayName: string | null;
};

/** Drei unterscheidbare Fehlerarten statt eines generischen "geht nicht":
 * unerreichbar/Timeout, HTTP-Fehler (inkl. 503 "noch nicht konfiguriert"),
 * unerwartete Antwortform - sonst weiss der Nutzer beim Einrichten seines
 * eigenen Servers nicht, wo er ansetzen soll. */
export async function discoverServerConfig(
  baseUrl: string,
  signal?: AbortSignal,
): Promise<ServerDiscoveryResult> {
  const normalized = baseUrl.replace(/\/+$/, "");

  let response: Response;
  try {
    response = await fetch(`${normalized}/v1/system/instance-config`, { signal });
  } catch (cause) {
    throw new Error("Server nicht erreichbar. Bitte Adresse und Netzwerk prüfen.", { cause });
  }

  if (response.status === 503) {
    throw new Error("Dieser Server hat noch keine Supabase-Verbindung eingerichtet.");
  }
  if (!response.ok) {
    throw new Error(
      `Server hat mit HTTP ${response.status} geantwortet. Ist das die richtige Adresse?`,
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch (cause) {
    throw new Error("Antwort war kein gültiges JSON. Ist das wirklich eine Finanzplaner-API?", {
      cause,
    });
  }

  if (!payload || typeof payload !== "object") {
    throw new Error(
      "Antwort hat nicht die erwartete Form. Ist das wirklich eine Finanzplaner-API?",
    );
  }
  const body = payload as {
    supabase_url?: unknown;
    supabase_anon_key?: unknown;
    instance_display_name?: unknown;
  };
  if (
    typeof body.supabase_url !== "string" ||
    !body.supabase_url ||
    typeof body.supabase_anon_key !== "string" ||
    !body.supabase_anon_key
  ) {
    throw new Error(
      "Antwort hat nicht die erwartete Form. Ist das wirklich eine Finanzplaner-API?",
    );
  }

  return {
    supabaseUrl: body.supabase_url,
    supabaseAnonKey: body.supabase_anon_key,
    instanceDisplayName:
      typeof body.instance_display_name === "string" ? body.instance_display_name : null,
  };
}
