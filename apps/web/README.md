# Finanzplaner Web

Eigenständige Desktop-Web-App des Finanzplaners (Vite + React + TypeScript).
Siehe `FINANZ_APP_ARCHITEKTURPLAN.md` Abschnitt 10 für die vollständige
Spezifikation (Designrichtung, Navigation, Ansichten).

## Schnellstart

```bash
pnpm install
pnpm dev
```

Die App läuft anschließend unter http://localhost:5173, die Statusseite unter
http://localhost:5173/status. Alternativ über Docker Compose vom Repo-Root aus
(startet Web zusammen mit API, Worker und DB) – siehe das Haupt-README.

## Umgebungsvariablen

- `VITE_API_BASE_URL` – Basis-URL der API (z. B. `http://localhost:8882`).
  Fehlt die Variable, wird ein leerer String verwendet (relative Pfade), was
  hinter dem Caddy-Reverse-Proxy im selben Origin funktioniert.

## Skripte

- `pnpm dev` – Vite-Devserver
- `pnpm build` – Typprüfung (`tsc -b`) und Produktionsbuild
- `pnpm preview` – Vorschau des Produktionsbuilds
- `pnpm lint` – ESLint (Flat Config)
- `pnpm typecheck` – reine Typprüfung ohne Emit
- `pnpm test` – Vitest (Testing Library, jsdom)

## Struktur

```text
src/
  main.tsx            Einstiegspunkt, QueryClient- und Router-Setup
  App.tsx             Routing, Theme-State (hell/dunkel)
  nav.ts              Sidebar-Navigationseinträge (Abschnitt 10.2)
  lib/api.ts           typisierter Fetch fuer GET /v1/system/health
  styles/theme.ts      Design-Tokens -> CSS-Custom-Properties
  styles/global.css    globale Styles, referenziert nur CSS-Variablen
  components/          Layout, Sidebar, StatusBadge
  pages/               Übersicht, Systemstatus, Platzhalterseiten
  test/                Vitest-Setup und Tests
```

Design-Tokens (`@finanzplaner/design-tokens`) und geteilte Typen
(`@finanzplaner/contracts`) werden als Workspace-Pakete referenziert, nicht
dupliziert.
