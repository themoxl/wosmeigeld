// Metro-Konfiguration fuer den pnpm-Workspace. Metro/Expo braucht explizite
// Hilfe, um Pakete aus dem Workspace-Root aufzuloesen - insbesondere
// @finanzplaner/design-tokens und @finanzplaner/contracts, beides rohe
// .ts-Quellen ohne Build-Schritt, deren "main"/"types" direkt auf src/index.ts
// zeigen.
const { getDefaultConfig } = require("expo/metro-config");
const path = require("path");

const projectRoot = __dirname;
const workspaceRoot = path.resolve(projectRoot, "../..");

// EXPO_PUBLIC_-Variablen (z. B. EXPO_PUBLIC_API_BASE_URL) pflegt dieses
// Monorepo zentral in einer einzigen .env-Datei im Repo-Root (siehe
// .env.example im Repo-Wurzelverzeichnis und apps/mobile/README.md), nicht in
// einer zusaetzlichen apps/mobile/.env. Expo selbst laedt .env-Dateien nur
// aus dem eigenen Projektordner, deshalb laden wir die Root-.env hier
// zusaetzlich manuell, bevor Metro/Babel process.env.EXPO_PUBLIC_* in den
// Client-Bundle inlinen. Fehlt die Datei (z. B. in CI), greift der Fallback
// in src/api/config.ts.
//
// Ausnahme: ein Build zum Weitergeben soll KEINE Adresse erben. Die Root-.env
// ist die Entwicklungsumgebung dieses Rechners; ohne diesen Schalter landete
// deren Adresse (192.168.x.y) in einer APK, die jemand anders installiert -
// und die App boete ihm eine "Cloud" an, die es fuer ihn nicht gibt. Der
// Schalter wird von scripts/build-apk.ps1 gesetzt, wenn beim Aufruf keine
// Adresse angegeben wurde.
if (process.env.FINANZPLANER_IGNORE_ROOT_ENV !== "1") {
  try {
    require("dotenv").config({ path: path.resolve(workspaceRoot, ".env") });
  } catch {
    // dotenv ist optional zur Laufzeit von Metro; ohne Root-.env gilt einfach
    // der Fallback in src/api/config.ts.
  }
}

const config = getDefaultConfig(projectRoot);

config.watchFolders = [workspaceRoot];
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, "node_modules"),
  path.resolve(workspaceRoot, "node_modules"),
];

// Hinweis zu "disableHierarchicalLookup": Das oft empfohlene Muster fuer
// Yarn/NPM-Workspaces setzt dies auf true, weil dort so gut wie alles in ein
// einziges Root-node_modules gehoistet wird. pnpm haelt Pakete dagegen
// standardmaessig NICHT gehoistet, sondern in einem eigenen .pnpm-Store, in
// dem jedes Paket seine eigenen Abhaengigkeiten in einem privaten
// node_modules-Ordner mitbringt (erreichbar nur ueber das hierarchische
// Hochlaufen von Metros/Node-Modulaufloesung ab dem realen (symlink-
// aufgeloesten) Pfad einer Datei). Mit disableHierarchicalLookup=true bricht
// deshalb unter pnpm die Aufloesung transitiver Pakete, die nicht auch direkte
// Dependencies von apps/mobile sind (getestet: "expo-modules-core",
// "standard-navigation" ueber expo-router). Deshalb bleibt es hier auf
// false (Metro-Default), waehrend watchFolders/nodeModulesPaths weiterhin
// dafuer sorgen, dass die rohen Workspace-Pakete aus packages/* gefunden
// werden. `npx expo export` (alle Plattformen: android/ios/web) wurde nach
// dieser Anpassung erfolgreich verifiziert.
config.resolver.disableHierarchicalLookup = false;

// Nur fuer den eingebetteten Release-Build (siehe scripts/build-apk.ps1, das
// diese Variable setzt): Metros Wurzelverzeichnis auf den App-Ordner
// festnageln.
//
// Sobald watchFolders ausserhalb des Projektordners liegen, waehlt Metro als
// "server root" deren gemeinsamen Vorfahren - hier also das Repo-Root.
// `expo export:embed` rechnet den Einstiegspunkt aber relativ zum
// PROJEKTordner aus und laesst ihn anschliessend vom SERVERverzeichnis
// aufloesen. Solange beide dasselbe sind, faellt das nicht auf; hier gehen die
// beiden Bezugspunkte um zwei Ebenen auseinander, und der Build bricht ab mit
//   Unable to resolve module ./index.js from <Projektverzeichnis>/.
// (gesucht wurde index.js im Repo-Root statt in apps/mobile).
//
// Warum nicht dauerhaft: im Entwicklungsbetrieb liefert Metro Dateien ueber
// HTTP aus, und zwar ausschliesslich unterhalb des Serververzeichnisses. Auf
// den App-Ordner eingeschraenkt kaeme der Entwicklungsserver nicht mehr an die
// Workspace-Pakete unter packages/. Beim Einbetten wird nichts ausgeliefert,
// sondern nur gebuendelt - dort ist die Einschraenkung folgenlos, waehrend
// watchFolders und nodeModulesPaths das Aufloesen weiterhin abdecken.
if (process.env.FINANZPLANER_EMBED_BUILD === "1") {
  config.server = { ...config.server, unstable_serverRoot: projectRoot };
}

module.exports = config;
