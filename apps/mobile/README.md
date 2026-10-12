# Finanzplaner Mobile

Expo/React-Native-App (iOS und Android) fuer den Finanzplaner. Siehe
`FINANZ_APP_ARCHITEKTURPLAN.md` Abschnitt 11 fuer die verbindliche
Spezifikation von Navigation und Kernablaeufen. Aktueller Stand: Phase 1
(Auth, Haushalt und Datenbank) - Login/Registrierung, Haushalts-Onboarding
und -Auswahl, Konten- und Buchungsverwaltung.

Die Bottom Tabs sind seit der Umsetzung von `docs/mobile_redesign_showcase.html`
in dieser Reihenfolge fest: **Heute**, **Berichte**, **Budget**, **Vertraege**
und **Mehr**. Buchungen sind kein Tab mehr - die Liste ist im Alltag passiv,
Berichte beantworten dagegen die Frage, wegen der man die App oeffnet. Die
Route `/buchungen` bleibt unveraendert und ist ueber den Konten-Strip auf
"Heute", "Alle Buchungen", jede Kategorie-Aufschluesselung und das Mehr-Menue
erreichbar (siehe `app/(tabs)/_layout.tsx`). Der frueher eigene "Ziele"-Tab ist
entfallen; Sparziele und Wunschliste sind unter Mehr -> Sparen & Wünsche
erreichbar.

## Setup

Aus dem Repo-Root (installiert den gesamten pnpm-Workspace):

```bash
pnpm install
```

Danach die Mobile-App starten:

```bash
cd apps/mobile
pnpm start
```

Das oeffnet Expo Dev Tools. Von dort aus per QR-Code in Expo Go, im iOS-
Simulator (`pnpm ios`), im Android-Emulator (`pnpm android`) oder im Browser
(`pnpm web`) oeffnen.

### Stolperfalle: `expo start` bricht mit `EACCES ... .ignored_<paket>` ab

Unter Windows kann pnpm in `packages/*/node_modules` defekte Junctions der
Form `.ignored_<paket>` hinterlassen (Reparse-Ziel unlesbar). Weil
`metro.config.js` mit `watchFolders = [workspaceRoot]` das gesamte Monorepo
crawlt, laeuft Metros Walker hinein und stirbt:

```text
Error: EACCES: permission denied, lstat '...\packages\contracts\node_modules\.ignored_typescript'
```

Eine `resolver.blockList` hilft nicht: der Walker ruft `fs.lstat` **vor**
`filterDir` auf (`node_modules/walker/lib/walker.js`), und `EACCES` steht
nicht auf der Ignorierliste von `@expo/metro-file-map` (nur `ENOENT` und
`EPERM`). Die Junctions werden von nichts aufgeloest und koennen entfernt
werden:

```bash
cmd /c rmdir "<Repo-Wurzel>\packages\contracts\node_modules\.ignored_typescript"
cmd /c rmdir "<Repo-Wurzel>\packages\design-tokens\node_modules\.ignored_typescript"
```

`rmdir` loest nur die Junction, nicht das Ziel. Taucht die Meldung nach einem
`pnpm install` erneut auf, ist es dieselbe Ursache - vorhandene Reste findet
`Get-ChildItem packages -Filter ".ignored_*" -Recurse -Force`.

## Verbindung zur lokalen API

Die App liest die API-Basis-URL aus `EXPO_PUBLIC_API_BASE_URL` (Expo-
Konvention: Variablen mit dem Praefix `EXPO_PUBLIC_` sind zur Laufzeit im
Client verfuegbar). Der Wert wird **zentral in der Repo-Root-`.env`** gepflegt
(`.env` im Repo-Wurzelverzeichnis, Vorlage in `.env.example`), zusammen mit den
Variablen fuer Web/API/Worker - nicht in einer separaten `.env` innerhalb von
`apps/mobile`. `metro.config.js` laedt diese Root-`.env` zusaetzlich zum
Standardverhalten von Expo, damit `EXPO_PUBLIC_API_BASE_URL` ohne Duplikat
ankommt. Ohne gesetzte Variable greift der Fallback `http://localhost:8882`
(siehe `src/api/config.ts`).

**Wichtig bei Tests auf einem echten Geraet:** `localhost` zeigt dort auf das
Geraet selbst, nicht auf den Entwicklungsrechner. In dem Fall
`EXPO_PUBLIC_API_BASE_URL` auf die IP des Entwicklungsrechners im lokalen
Netz setzen, z. B. `http://192.168.1.23:8882`, und die lokale API mit
`docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile local-db up` aus dem Repo-Root starten.

Der "Heute"-Tab zeigt den Systemstatus (`GET /v1/system/health`) fuer `api`,
`database`, `redis`, `worker`, `storage` und `lm_studio` an - das ist die
sichtbare Demonstration, dass sich die App mit der lokalen API verbindet
(Abnahmekriterium Phase 0).

## Auth und Haushalt (Phase 1)

Anmeldung/Registrierung laufen **client-seitig direkt gegen Supabase Auth**,
nicht ueber die eigene API (Architekturplan Abschnitt 2: "JWT wird durch die
BFF-API validiert", nicht dort ausgestellt). Dafuer wird zusaetzlich
`EXPO_PUBLIC_SUPABASE_URL` / `EXPO_PUBLIC_SUPABASE_ANON_KEY` aus der
Repo-Root-`.env` gelesen (siehe `src/auth/supabase.ts`, Fallback auf die
lokalen `supabase start`-Demo-Werte aus `.env.example`, falls die Variablen
fehlen).

- `src/auth/supabase.ts` - `@supabase/supabase-js`-Client mit
  `@react-native-async-storage/async-storage` als Session-Storage-Adapter
  (`persistSession`/`autoRefreshToken` an, `detectSessionInUrl` aus).
- `src/auth/AuthProvider.tsx` (`useAuth()`) - haelt die Supabase-Session und
  bietet `signIn`/`signUp`/`signOut`.
- `src/household/HouseholdProvider.tsx` (`useHousehold()`) - laedt nach
  Login `GET /v1/me`, waehlt bei einem/mehreren Haushalten automatisch den
  ersten aus und merkt die Auswahl in AsyncStorage unter dem Key
  `finanzplaner:mobile:household-id`. Ohne Haushalt zeigt
  `app/_layout.tsx` den Onboarding-Screen (`app/onboarding.tsx`, "Haushalt
  erstellen"); bei mehreren Haushalten kann unter Mehr -> Einstellungen
  gewechselt werden.
- `app/_layout.tsx` schuetzt alle Routen: nicht angemeldete Nutzer werden zu
  `app/login.tsx` umgeleitet, angemeldete Nutzer ohne Haushalt zu
  `app/onboarding.tsx`.
- `src/auth/tokenStore.ts` (der duenne `expo-secure-store`-Wrapper aus Phase 0) bleibt bewusst ungenutzt: `supabase-js` uebernimmt die
  Session-Persistenz vollstaendig selbst ueber den AsyncStorage-Adapter
  oben. Die Datei bleibt als vorbereiteter, sicherer Speicherplatz fuer
  Phase 9 (biometrischer App-Lock) erhalten, in der ein kurzlebiger
  Entsperr-Status/PIN-Hash abgelegt werden koennte - nicht die
  Supabase-Session selbst.
- `src/api/client.ts` - authentifizierter Fetch-Helfer (`Authorization:
Bearer <token>` und optional `X-Household-Id`) plus typisierte Funktionen
  fuer `/v1/me`, `/v1/households`, `/v1/accounts`, `/v1/categories`,
  `/v1/transactions` (siehe `src/api/types.ts` fuer die Formen).

Konten (Mehr -> Konten) und Buchungen (Tab "Buchungen") sind vollstaendig an
diese Endpunkte angebunden, inklusive Anlegeformular fuer Konten
(`app/mehr/konten-neu.tsx`). Geldbetraege werden aus Texteingaben ueber
`src/utils/money.ts#parseAmountToMinorUnits` in Cent-Ganzzahlen umgerechnet
(kein `parseFloat(...) * 100`, siehe Architekturplan Abschnitt 2 "niemals
Float").

## Buchungen: Import statt manuelle Neuanlage

Seit dem Redesign (`docs/MOBILE-APP-REDESIGN-PLAN.md` Abschnitt 5) gibt es in
der Mobile-App keinen Weg mehr, eine Buchung manuell anzulegen. Der Button
"+ Neue Buchung" und alle Empty-State-Hinweise auf manuelle Erfassung sind aus
UI und Accessibility-Tree entfernt; der kanonische Zufluss neuer Buchungen ist
der Kontoauszug-Import (Mehr -> Import, `/mehr/import`).

`app/buchung-neu.tsx` bleibt fuer eine Uebergangsrelease als reiner Redirect
auf `/mehr/import` bestehen, damit alte Deep Links (z. B. Homescreen-
Verknuepfungen oder Push-Ziele) nicht auf einer Fehlerseite landen. API-POST
(`createTransaction`) sowie die Verarbeitung bereits persistierter
Offline-Create-Eintraege (`src/offline/pendingTransactions.ts`,
`src/offline/queueProcessor.ts`) bleiben ebenfalls fuer diese
Uebergangsrelease funktionsfaehig, damit kein lokaler Altbestand auf
Geraeten verloren geht. Redirect und Offline-Create-Pfad werden erst in einer
spaeteren Release entfernt, sobald sicher ist, dass kein Altbestand mehr
davon abhaengt.

## Budget: unkategorisierte Ausgaben zaehlen jetzt mit

Unkategorisierte Importausgaben (negative Splits ohne Kategorie auf
On-Budget-Konten) reduzieren seit der Redesign-Korrektur in
`services/api/app/services/budget_service.py` korrekt den Budgetrest: Sie
zaehlen zu `total_spent_minor` und erscheinen zusaetzlich separat als
`uncategorized_spent_minor`. Die Budget-Ansicht zeigt sie als kompakte
Warnzeile mit Betrag und Link zum vorgefilterten Buchungsregister. Vorher
blieben sie ausserhalb der Summe und liessen den Budgetrest kuenstlich hoch
erscheinen. Sie werden weiterhin keinem 50/30/20-Bucket zugeordnet.

## Scripts

- `pnpm start` - Expo Dev Server
- `pnpm android` / `pnpm ios` / `pnpm web` - direkt auf einer Plattform starten
- `pnpm lint` - ESLint (`eslint-config-expo`)
- `pnpm typecheck` - `tsc --noEmit`
- `pnpm test` - Jest (`jest-expo`-Preset)

## Struktur

```text
app/              Expo-Router-Routen (Dateisystem-Routing)
  (tabs)/         Bottom Tabs: Heute, Berichte, Budget, Vertraege, Mehr
                  (buchungen.tsx liegt hier ebenfalls, aber ohne Tab-Eintrag)
  mehr/           Detailseiten aus dem "Mehr"-Menu (Konten, Import, Einstellungen, ...)
  login.tsx       Login/Registrierung (ausserhalb der Tabs)
  onboarding.tsx  "Haushalt erstellen", wenn GET /v1/me keinen Haushalt liefert
  buchung-neu.tsx Redirect auf /mehr/import (Uebergangsrelease, siehe oben)
src/
  api/            API-Basis-URL, authentifizierter fetch-Client, React-Query-Client
  auth/           Supabase-Client (supabase-js) und AuthProvider/useAuth()
  household/      HouseholdProvider/useHousehold() (GET /v1/me, Haushaltsauswahl)
  components/     Geteilte, themenfaehige UI-Bausteine
    ui/           Bausteine des Handy-Designsystems (Card, Money, Badge, ...)
  hooks/          React-Query-Hooks (Konten, Kategorien, Buchungen, Systemstatus)
  theme/          Handy-Designsystem: Farben, Masse, Schriften, Privat-Modus
  utils/          Kleine Helfer (u. a. Betrag<->Cent-Umrechnung)
```

## Designsystem

Verbindlich beschrieben in **`docs/MOBILE-DESIGNSYSTEM.md`**; die
gestalterische Absicht steht in `docs/mobile_redesign_showcase.html`.

Kurz: die App hat seit der Umsetzung des Showcase ein **eigenes**, mobil
gedachtes Designsystem in `src/theme/` (dunkel als Gestaltungsabsicht, hell
gleichwertig unterstuetzt, grosse Radien, leuchtende Statusfarben, Inter und
JetBrains Mono). `@finanzplaner/design-tokens` bleibt der Web-App vorbehalten -
die dortigen Werte sind auf eine helle Desktop-Arbeitsflaeche gebaut und
wuerden auf einem Handy flach und fremd wirken.

- `src/theme/palette.ts` - Farben (`useTheme()`), `toneColors()`, `toneForLabel()`
- `src/theme/layout.ts` - `space`, `radii`, `typeScale`, `elevation`, `MIN_TOUCH`
- `src/theme/fonts.ts` + `globalFont.tsx` - Inter/JetBrains Mono, App-weit
  angewandt, ohne dass Aufrufstellen etwas tun muessen
- `src/theme/PrivacyProvider.tsx` - Privat-Modus (blendet alle Betraege aus)
- `src/components/ui/` - Card, Badge, Money, PacingBar, SplitMeter, DonutChart,
  TrendChart, Avatar, DataRow, StatTile, IconButton, Fab, Chip, Numpad,
  ScreenHeader

Das Erscheinungsbild (System/Hell/Dunkel) und der Privat-Modus lassen sich
unter Mehr -> Einstellungen -> Darstellung umstellen.

### APK bauen nach dem Redesign

Das Redesign hat mit `react-native-svg`, `expo-linear-gradient` und
`expo-haptics` neue Abhaengigkeiten mit nativem Code hinzugefuegt. Der naechste
Bau muss deshalb einmal das Android-Projekt neu erzeugen:

```powershell
apps/mobile/scripts/build-apk.ps1 -Clean
```

Danach genuegt wieder der Lauf ohne `-Clean`.

## Offene Punkte / spaetere Phasen

- `expo-secure-store` (`src/auth/tokenStore.ts`) bleibt bewusst ungenutzt,
  siehe Abschnitt "Auth und Haushalt" oben.
- Kein automatisierter Emulator-/Simulator-Durchlauf in dieser Umgebung
  durchgefuehrt (kein laufendes Android-/iOS-Geraet verfuegbar) - stattdessen
  `npx expo export` als statischer Bundling-/Importfehler-Check.
