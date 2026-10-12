import { fileURLToPath } from "node:url";

import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Siehe apps/web/README.md und FINANZ_APP_ARCHITEKTURPLAN.md Abschnitt 10.
export default defineConfig({
  // Die einzige .env des Projekts liegt im Monorepo-Root (siehe .env.example).
  // Ohne envDir sucht Vite sie im Projektstammverzeichnis (apps/web/) - dort
  // gibt es keine, die VITE_*-Variablen bleiben also leer und die App bricht
  // zur Laufzeit mit "supabaseUrl is required" ab (weisser Screen, siehe
  // src/lib/supabase.ts). Im Docker-Weg faellt das nicht auf, weil Compose die
  // Variablen als Container-Umgebung reicht.
  // Absolut aus der Lage DIESER Datei abgeleitet, nicht relativ zum
  // Arbeitsverzeichnis: die Config wird auch aus dem Repo-Root heraus geladen
  // (pnpm --filter, vitest), ein relativer Pfad wuerde dann danebengreifen.
  envDir: fileURLToPath(new URL("../..", import.meta.url)),
  plugins: [react()],
  server: {
    watch: {
      // Native Dateisystem-Events kommen bei einem Windows-Host + Docker-
      // Bind-Mount (docker-compose.dev.yml) nicht zuverlaessig im Container
      // an - Polling ist hier die robuste Wahl, Kosten sind bei diesem
      // Projektumfang vernachlaessigbar.
      usePolling: true,
      interval: 300,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    css: true,
    setupFiles: ["./src/test/setup.ts"],
    // e2e/*.spec.ts sind Playwright-Tests (eigener Runner, siehe
    // playwright.config.ts) - Vitests Default-include-Glob wuerde sie sonst
    // faelschlich selbst einzusammeln versuchen (test.describe() kommt dort
    // von @playwright/test, nicht von vitest).
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
