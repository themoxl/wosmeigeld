// Generiert src/generated/api-types.ts aus dem OpenAPI-Schema von
// services/api (siehe ../README.md fuer den Workflow). Ruft dafuer
// services/api/scripts/export_openapi.py auf - das Skript braucht keine
// laufende DB/kein Supabase, nur die services/api-Python-Dependencies
// (FastAPI/Pydantic) muessen installiert sein.

import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import openapiTS, { astToString } from "openapi-typescript";
import * as prettier from "prettier";

const __dirname = dirname(fileURLToPath(import.meta.url));
const contractsRoot = join(__dirname, "..");
const apiRoot = join(contractsRoot, "..", "..", "services", "api");
const outputPath = join(contractsRoot, "src", "generated", "api-types.ts");

// Interpreterwahl in dieser Reihenfolge:
//   1. Env-Var PYTHON (explizite Vorgabe schlaegt alles),
//   2. das projektinterne Venv unter services/api/.venv (der dokumentierte
//      Standardweg aus services/api/README.md) - plattformabhaengiger Pfad,
//   3. ein Interpreter aus dem PATH ("python", auf POSIX zusaetzlich
//      "python3").
// Ein blosses "python" als Default reicht nicht: unter Windows liegt oft nur
// der App-Execution-Alias im PATH, und selbst ein echter globaler
// Interpreter hat die API-Dependencies (FastAPI/Pydantic) nicht installiert.
const venvPython =
  process.platform === "win32"
    ? join(apiRoot, ".venv", "Scripts", "python.exe")
    : join(apiRoot, ".venv", "bin", "python");

const candidates = process.env.PYTHON
  ? [process.env.PYTHON]
  : existsSync(venvPython)
    ? [venvPython]
    : process.platform === "win32"
      ? ["python"]
      : ["python3", "python"];

/**
 * Ruft export_openapi.py mit dem ersten funktionierenden Interpreter auf.
 * Gibt das OpenAPI-JSON auf stdout zurueck.
 */
function exportOpenApiSchema() {
  const failures = [];
  for (const bin of candidates) {
    try {
      return execFileSync(bin, ["scripts/export_openapi.py"], {
        cwd: apiRoot,
        encoding: "utf-8",
        maxBuffer: 1024 * 1024 * 32,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) {
      failures.push(`  ${bin}: ${error.stderr?.toString().trim() || error.message}`);
    }
  }

  const venvHint =
    process.platform === "win32"
      ? "  cd services/api\n  python -m venv .venv\n  .venv\\Scripts\\python.exe -m pip install -r requirements-dev.txt"
      : "  cd services/api\n  python3 -m venv .venv\n  .venv/bin/python -m pip install -r requirements-dev.txt";

  throw new Error(
    "Konnte das OpenAPI-Schema nicht exportieren - kein nutzbarer " +
      "Python-Interpreter gefunden.\n\n" +
      `Versuchte Interpreter:\n${failures.join("\n")}\n\n` +
      "Erwartet wird das projektinterne Venv unter\n" +
      `  ${venvPython}\n` +
      "Anlegen und Dependencies installieren:\n" +
      `${venvHint}\n\n` +
      "Alternativ einen eigenen Interpreter erzwingen (er braucht die " +
      "Dependencies aus\nservices/api/requirements-dev.txt):\n" +
      "  PYTHON=/pfad/zu/python pnpm run generate:api-types",
  );
}

const schemaJson = exportOpenApiSchema();

const schema = JSON.parse(schemaJson);
// defaultNonNullable:false ist noetig, damit Felder mit einem Pydantic-
// Default (z. B. AccountCreate.currency) im generierten Typ optional
// bleiben (`field?: T`) statt faelschlich required zu werden - openapi-
// typescripts Default behandelt "hat einen Default" faelschlich wie
// "ist immer gesetzt", was fuer Request-Bodies falsch ist (der Client DARF
// das Feld weglassen, der Server setzt dann den Default).
const ast = await openapiTS(schema, { defaultNonNullable: false });

const header = `// AUTO-GENERIERT von \`pnpm run generate:api-types\` (packages/contracts/scripts/generate-api-types.mjs)
// aus dem OpenAPI-Schema von services/api. NICHT VON HAND BEARBEITEN - Aenderungen
// gehen bei der naechsten Generierung verloren. Bei Backend-Schemaaenderungen
// neu generieren, siehe packages/contracts/README.md.

`;

const raw = header + astToString(ast);

// openapi-typescripts eigene Formatierung weicht vom Projekt-Prettier ab -
// direkt danach angleichen, damit `pnpm format:check` die generierte Datei
// nicht staendig als Diff meldet.
const prettierConfig = (await prettier.resolveConfig(outputPath)) ?? {};
const formatted = await prettier.format(raw, { ...prettierConfig, filepath: outputPath });

mkdirSync(dirname(outputPath), { recursive: true });
writeFileSync(outputPath, formatted);

console.log(`Geschrieben: ${outputPath}`);
