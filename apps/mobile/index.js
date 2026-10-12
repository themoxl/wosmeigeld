// Einstiegspunkt der App. Inhaltlich nichts weiter als das, was zuvor direkt
// als "main": "expo-router/entry" in package.json stand - aber als Datei
// INNERHALB dieses Projektordners.
//
// Warum das noetig ist: unter pnpm liegt expo-router nicht in
// apps/mobile/node_modules, sondern im Speicher des Workspace-Roots
// (node_modules/.pnpm/expo-router@.../node_modules/expo-router). Beim
// Release-Build reicht das Gradle-Plugin diesen Pfad als --entry-file an
// `expo export:embed` weiter, und Metro rechnet ihn dort ein zweites Mal
// relativ zu seinem eigenen Wurzelverzeichnis um. Aus "zwei Ebenen hoch"
// werden so vier, und der Bundler bricht mit "Unable to resolve module
// ./../../node_modules/.pnpm/expo-router@.../entry.js" ab. Im
// Entwicklungsbetrieb faellt das nicht auf, weil `expo start` den Einstieg
// ueber den Modulnamen aufloest statt ueber einen Pfad.
//
// Ein Einstiegspunkt im Projektordner macht den Umweg gegenstandslos: der
// Pfad ist dann kurz, eindeutig und liegt unterhalb der Metro-Wurzel.
import "expo-router/entry";
