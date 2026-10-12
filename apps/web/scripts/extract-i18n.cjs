const ts = require("typescript");
const fs = require("fs");
const path = require("path");

const webSrcDir = path.join(__dirname, "..", "src");
const outputDir = path.join(webSrcDir, "i18n");
const outputFile = path.join(outputDir, "keys.json");

const filePaths = [];

function collectFiles(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "test" && entry.name !== "__tests__" && entry.name !== "node_modules") {
        collectFiles(full);
      }
    } else if (
      (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) &&
      !entry.name.endsWith(".test.ts") &&
      !entry.name.endsWith(".test.tsx") &&
      !entry.name.endsWith(".spec.ts") &&
      !entry.name.endsWith(".spec.tsx") &&
      !entry.name.endsWith(".d.ts")
    ) {
      filePaths.push(full);
    }
  }
}

collectFiles(webSrcDir);

const nonUiAttributes = new Set([
  "className",
  "id",
  "data-testid",
  "type",
  "href",
  "path",
  "to",
  "key",
  "rel",
  "src",
  "method",
  "role",
  "style",
  "color",
  "variant",
  "size",
  "name",
  "field",
  "accessorKey",
  "align",
  "target",
  "htmlFor",
  "tabIndex",
  "autoComplete",
  "encoding",
  "contentType",
  "viewBox",
  "d",
  "strokeWidth",
  "strokeLinecap",
  "strokeLinejoin",
]);

const technicalProps = new Set([
  "queryKey",
  "mutationKey",
  "endpoint",
  "url",
  "method",
  "storageKey",
  "id",
  "path",
  "icon",
  "code",
  "type",
  "field",
]);

const nonUiSingleWords = new Set([
  "light",
  "dark",
  "asc",
  "desc",
  "get",
  "post",
  "put",
  "delete",
  "patch",
  "id",
  "uuid",
  "iso",
  "de-de",
  "en-us",
  "utf-8",
  "json",
  "csv",
  "svg",
  "dashboard",
  "reports",
  "sparkles",
  "inbox",
  "list",
  "search",
  "credit-card",
  "shield",
  "pie-chart",
  "file-text",
  "shopping-cart",
  "home",
  "award",
  "target",
  "trending-up",
  "trending-down",
  "upload",
  "sliders",
  "folder",
  "database",
  "settings",
  "authretryablefetcherror",
  "failed to fetch",
  "networkerror",
  "network request failed",
  "load failed",
  "checkbox",
  "radio",
  "text",
  "number",
  "submit",
  "button",
  "none",
  "off",
  "all",
  "true",
  "false",
  "null",
  "undefined",
  "v1",
  "v2",
  "bearer",
  "authorization",
  "content-type",
  "application/json",
  "localstorage",
  "sessionstorage",
  "pro",
]);

function isGermanUiString(str) {
  if (!str) return false;
  const s = str.trim();
  if (!s) return false;

  if (/^http(s)?:\/\//.test(s)) return false;
  if (/^\/[a-zA-Z0-9_\-\/:]*$/.test(s)) return false;
  if (/^[a-z0-9_\-\.\:]+$/i.test(s) && !/[äöüßÄÖÜ]/.test(s)) {
    if (s.includes(":") || s.includes("/") || s.includes(".") || s.includes("_")) return false;
  }
  if (nonUiSingleWords.has(s.toLowerCase())) return false;

  // German umlauts or ß
  if (/[äöüßÄÖÜ]/.test(s)) return true;

  // Common German UI words / regex pattern
  const germanPattern =
    /\b(Speichern|Abbrechen|Bearbeiten|Löschen|Neu|Neue|Neuer|Neues|Ja|Nein|An|Aus|Hoch|Mittel|Niedrig|Minimal|Sehr|Suche|Suchen|Filtern|Filter|Name|Datum|Betrag|Kategorie|Konto|Konten|Haushalt|Vertrag|Verträge|Regel|Regeln|Budget|Vorhaben|Förderung|Förderungen|Rücklage|Rücklagen|Erstattung|Erstattungen|Beleg|Belege|Wunschliste|Sparziel|Sparziele|Vermögen|Schulden|Lebensmittel|Einstellungen|Mitglied|Mitglieder|Einladung|Einladungen|Datenbank|Eigentümer|Bearbeiter|Betrachter|Eingeschränkt|Sicherung|Systemstatus|Übersicht|Berichte|Empfehlungen|Review-Inbox|Importe|Kategorien|Überblick|Geld|Planung|Schließen|Übernehmen|Bestätigen|Zurück|Weiter|Hinzufügen|Entfernen|Exportieren|Importieren|Aktualisieren|Details|Status|Aktion|Aktionen|Summe|Gesamt|Monat|Monate|Jahr|Jahre|Heute|Gestern|Morgen|Keine|Kein|Alle|Auswählen|Ausgewählt|Fehler|Erfolg|Warnung|Hinweis|Info|Laden|Wird|Gespeichert|Wiederholen|Wiederholung|Optional|Notiz|Notizen|Tage|Wochen|Darlehen|Bauleistung|Kaufpreis|Eigenkapital|Puffer|Stand|Gesamtbetrag|Monatlich|Jährlich|Quartal|Einmalig|Aktiv|Inaktiv|Offen|Erledigt|Entwurf|Gültig|Ungültig|Keine Buchungen gefunden|Abbrechen…|Speichern…|Suchen…|Filtern…)\b/i;

  if (germanPattern.test(s)) return true;

  return false;
}

function getTemplateString(node) {
  let result = node.head.text;
  let idx = 1;
  for (const span of node.templateSpans) {
    const exprText = span.expression.getText();
    let placeholder = "param" + idx;
    if (/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(exprText)) {
      placeholder = exprText;
    } else if (exprText.includes("count") || exprText.includes("length") || exprText.includes("total") || exprText.includes("Anzahl")) {
      placeholder = "count";
    } else if (exprText.includes("date") || exprText.includes("Datum") || exprText.includes("formatDate")) {
      placeholder = "date";
    } else if (exprText.includes("amount") || exprText.includes("Betrag") || exprText.includes("Currency") || exprText.includes("euro")) {
      placeholder = "amount";
    } else if (exprText.includes("name") || exprText.includes("Name")) {
      placeholder = "name";
    }
    result += "{{" + placeholder + "}}" + span.literal.text;
    idx++;
  }
  return result;
}

const keysSet = new Set();

for (const filePath of filePaths) {
  const code = fs.readFileSync(filePath, "utf-8");
  const sourceFile = ts.createSourceFile(filePath, code, ts.ScriptTarget.Latest, true);

  function visit(node) {
    if (ts.isJsxText(node)) {
      const rawText = node.text;
      const lines = rawText
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
      if (lines.length > 0) {
        const text = lines.join(" ");
        if (isGermanUiString(text)) {
          keysSet.add(text);
        }
      }
    } else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      const text = node.text;
      if (isGermanUiString(text)) {
        const parent = node.parent;
        if (ts.isJsxAttribute(parent)) {
          const attrName = parent.name.getText();
          if (!nonUiAttributes.has(attrName)) {
            keysSet.add(text);
          }
        } else if (ts.isPropertyAssignment(parent)) {
          const propName = parent.name.getText();
          if (!technicalProps.has(propName)) {
            keysSet.add(text);
          }
        } else if (
          ts.isCallExpression(parent) ||
          ts.isArrayLiteralExpression(parent) ||
          ts.isReturnStatement(parent) ||
          ts.isConditionalExpression(parent) ||
          ts.isBinaryExpression(parent) ||
          ts.isVariableDeclaration(parent)
        ) {
          keysSet.add(text);
        }
      }
    } else if (ts.isTemplateExpression(node)) {
      const templateStr = getTemplateString(node);
      if (isGermanUiString(templateStr)) {
        keysSet.add(templateStr);
      }
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
}

const keysArray = Array.from(keysSet).sort((a, b) => a.localeCompare(b, "de"));

if (!fs.existsSync(outputDir)) {
  fs.mkdirSync(outputDir, { recursive: true });
}

fs.writeFileSync(outputFile, JSON.stringify(keysArray, null, 2), "utf-8");
console.log(`Extracted ${keysArray.length} i18n keys into ${outputFile}`);
