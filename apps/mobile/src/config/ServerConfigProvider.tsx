import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";
import { setApiBaseUrl } from "../api/config";
import type { ServerDiscoveryResult } from "../api/discovery";
import {
  CLOUD_PRESET,
  clearStoredServerConfig,
  getStoredServerConfig,
  setStoredServerConfig,
  type ServerConfig,
} from "./serverConfig";

// "loading": gespeicherte Wahl wird noch aus AsyncStorage gelesen.
// "unset": noch nie eine Wahl getroffen (oder zurueckgesetzt) - Server-Setup
// zeigen. "ready": eine gueltige Konfiguration ist aktiv.
export type ServerConfigStatus = "loading" | "unset" | "ready";

type ServerConfigContextValue = {
  status: ServerConfigStatus;
  config: ServerConfig | null;
  useCloudPreset: () => Promise<void>;
  useSelfHosted: (apiBaseUrl: string, discovery: ServerDiscoveryResult) => Promise<void>;
  reset: () => Promise<void>;
};

const ServerConfigContext = createContext<ServerConfigContextValue | undefined>(undefined);

export function ServerConfigProvider({ children }: PropsWithChildren) {
  const [status, setStatus] = useState<ServerConfigStatus>("loading");
  const [config, setConfig] = useState<ServerConfig | null>(null);

  useEffect(() => {
    let active = true;
    getStoredServerConfig().then((stored) => {
      if (!active) return;
      if (stored) {
        setApiBaseUrl(stored.apiBaseUrl);
        setConfig(stored);
        setStatus("ready");
      } else {
        setStatus("unset");
      }
    });
    return () => {
      active = false;
    };
  }, []);

  const value = useMemo<ServerConfigContextValue>(
    () => ({
      status,
      config,
      async useCloudPreset() {
        await setStoredServerConfig(CLOUD_PRESET);
        setApiBaseUrl(CLOUD_PRESET.apiBaseUrl);
        setConfig(CLOUD_PRESET);
        setStatus("ready");
      },
      async useSelfHosted(apiBaseUrl, discovery) {
        const next: ServerConfig = {
          mode: "self-hosted",
          apiBaseUrl,
          supabaseUrl: discovery.supabaseUrl,
          supabaseAnonKey: discovery.supabaseAnonKey,
          instanceDisplayName: discovery.instanceDisplayName,
        };
        await setStoredServerConfig(next);
        setApiBaseUrl(next.apiBaseUrl);
        setConfig(next);
        setStatus("ready");
      },
      async reset() {
        await clearStoredServerConfig();
        setConfig(null);
        setStatus("unset");
      },
    }),
    [status, config],
  );

  return <ServerConfigContext.Provider value={value}>{children}</ServerConfigContext.Provider>;
}

/** Aktive Serverkonfiguration (Cloud oder Eigener Server) samt Auswahl-/
 * Wechsel-Aktionen. Muss innerhalb von <ServerConfigProvider> verwendet
 * werden - siehe app/_layout.tsx, wo dieser Provider VOR <AuthProvider>
 * sitzt, weil Supabase-URL/Anon-Key erst hierueber bekannt sind. */
export function useServerConfig(): ServerConfigContextValue {
  const ctx = useContext(ServerConfigContext);
  if (!ctx) {
    throw new Error(
      "useServerConfig() muss innerhalb von <ServerConfigProvider> verwendet werden.",
    );
  }
  return ctx;
}
