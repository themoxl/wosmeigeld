# @finanzplaner/contracts

Geteilte TypeScript-Typen für Web und Mobile.

## Generierte API-Typen

`src/generated/api-types.ts` wird aus dem OpenAPI-Schema von `services/api`
generiert (kein handgepflegter Typ-Vertrag mehr - das war die Ursache
mehrerer Contract-Bugs in früheren Phasen, siehe Backlog-Historie). Diese
Datei ist **auto-generiert und wird committet** wie eine Lockfile - nicht
von Hand bearbeiten, Änderungen gehen bei der nächsten Generierung verloren.

Regenerieren, wann immer sich ein Pydantic-Schema in `services/api/app`
ändert:

```bash
pnpm run generate:api-types
```

Voraussetzung: `services/api`s Python-Dependencies sind installiert (siehe
`services/api/README.md`) - das Skript braucht **keine** laufende
DB/Supabase, nur FastAPIs Schema-Introspektion
(`services/api/scripts/export_openapi.py` importiert `app.main.app` und
druckt `app.openapi()` als JSON).

Den Python-Interpreter sucht `scripts/generate-api-types.mjs` in dieser
Reihenfolge:

1. die Umgebungsvariable `PYTHON`, falls gesetzt,
2. das projektinterne Venv `services/api/.venv` - plattformabhaengig
   (`Scripts/python.exe` unter Windows, `bin/python` sonst), also genau der
   in `services/api/README.md` beschriebene Standardweg,
3. ein Interpreter aus dem `PATH` (`python`, auf POSIX zuvor `python3`).

Findet sich keiner, bricht das Skript mit einer Fehlermeldung ab, die den
erwarteten Venv-Pfad und die Befehle zum Anlegen nennt - vorher wurde
schlicht `python` aufgerufen, was ohne aktiviertes Venv entweder gar nicht
existierte oder die API-Dependencies nicht hatte.

Warum das nicht automatisch in CI läuft: `.github/workflows/ci.yml` hat
getrennte Jobs für Python (`api`/`worker`) und Node (`web-and-mobile`) auf
getrennten Runnern, keiner hat beide Toolchains installiert. Statt CI dafür
aufzublähen, wird die generierte Datei wie jeder andere Quellcode committet
und im Node-Job normal mitgetypecheckt.

`apps/web/src/lib/types.ts` und `apps/mobile/src/api/types.ts` importieren
`components` aus diesem Paket und bilden daraus die im jeweiligen App-Code
verwendeten Typnamen ab (`export type Household = components["schemas"]["Household"];`
usw.) - so bleiben bestehende Importe in Seiten/Hooks unverändert, während
die tatsächliche Feldform immer exakt dem Backend entspricht.

Einzelne Felder, die das Backend response-seitig bewusst als `str` statt
eines `Literal[...]` typisiert (z. B. `Transaction.status`,
`Category.budget_bucket`), erscheinen entsprechend als `string`, nicht als
Literal-Union - auch wenn ein DB-Enum/Check-Constraint sie faktisch
einschränkt. Betroffene Anzeige-Stellen im App-Code casten dafür gezielt
(mit Kommentar, der die tatsächliche DB-Garantie nennt) statt den Typ hier
künstlich zu verschärfen.

## Warum keine generierten Request-Funktionen (nur Typen)?

`apiFetch` (`apps/web/src/lib/api.ts`) und `authorizedFetch`
(`apps/mobile/src/api/client.ts`) bleiben handgeschrieben. Ein vollständiger
generierter Client (z. B. `openapi-fetch`) würde zusätzlich das bewährte
`ApiError`-Fehlermodell (Detail-Extraktion, 204-Handling, React-Query-
`onError`-Konventionen in dutzenden Mutations) gegen ein anderes Modell
tauschen - eine Verhaltensänderung an jeder einzelnen Aufrufstelle in beiden
Apps, ohne laufendes Supabase/Docker nicht Ende-zu-Ende testbar. Die
generierten Typen allein beheben die eigentliche Bugklasse (Feld-Drift
zwischen Backend und Frontend) bereits vollständig an der Wurzel, bei einem
über `tsc` vollständig verifizierbaren Diff. Ein Wechsel des Request-Layers
selbst bleibt ein separates, unabhängiges DX-Vorhaben.
