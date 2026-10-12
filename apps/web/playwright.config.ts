import { defineConfig, devices } from "@playwright/test";

// Oeffentlicher lokaler Supabase-CLI-Demo-Anon-Key (siehe .env.example) -
// kein echtes Secret, nur fuer `supabase start` gegen 127.0.0.1 gueltig.
const LOCAL_SUPABASE_ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0";

// Abschnitt 15 (Tests und Qualitaetsstrategie > Web) fordert Playwright fuer
// Import/Review/Budget/Buchungsregister sowie Screenshots bei 1440x900,
// 1920x1080 und einem schmalen Laptop - siehe responsive-smoke.spec.ts fuer
// die beiden zusaetzlichen Groessen.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI
    ? [["html", { open: "never" }], ["github"], ["list"]]
    : [["html", { open: "never" }], ["list"]],
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
    video: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "pnpm exec vite --port 5173 --strictPort",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      // Ausserhalb von Docker Compose (env_file: .env) hat ein blosses
      // `vite`/`vite dev` keine VITE_*-Vars - hier explizit mit den lokalen
      // Supabase-CLI-Standardwerten (.env.example) belegen, ueberschreibbar
      // durch echte process.env-Werte.
      VITE_API_BASE_URL: process.env.VITE_API_BASE_URL ?? "http://localhost:8000",
      VITE_SUPABASE_URL: process.env.VITE_SUPABASE_URL ?? "http://127.0.0.1:55321",
      VITE_SUPABASE_ANON_KEY: process.env.VITE_SUPABASE_ANON_KEY ?? LOCAL_SUPABASE_ANON_KEY,
    },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
      testIgnore: /responsive-smoke\.spec\.ts/,
    },
    {
      name: "responsive-1920x1080",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1920, height: 1080 } },
      testMatch: /responsive-smoke\.spec\.ts/,
    },
    {
      name: "responsive-1280x800-narrow-laptop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
      testMatch: /responsive-smoke\.spec\.ts/,
    },
  ],
});
