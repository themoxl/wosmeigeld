import AsyncStorage from "@react-native-async-storage/async-storage";
import { createAsyncStoragePersister } from "@tanstack/query-async-storage-persister";
import { QueryClient, type Query } from "@tanstack/react-query";
import type {
  PersistedClient,
  PersistQueryClientOptions,
} from "@tanstack/react-query-persist-client";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      // Phase 9: Ein persistierter Cache nuetzt nichts, wenn React Query die
      // wiederhergestellten Daten sofort als "zu alt" verwirft. 24h deckt den
      // typischen Offline-Fall ab; frischer wird trotzdem sofort nachgeladen,
      // sobald der onlineManager wieder "online" meldet (siehe
      // ../hooks/useConnectivity.ts).
      gcTime: 24 * 60 * 60 * 1000,
    },
  },
});

// --- Persistenter Lesecache (Phase 9) ---------------------------------------
//
// Warum AsyncStorage-Persister und nicht expo-sqlite:
//   * Gebraucht wird genau ein dehydrierter Blob, keine abfragbaren Zeilen.
//     Mit expo-sqlite muesste ein eigener Persister plus ein Schema je
//     Query-Familie von Hand gepflegt werden - mehr Code fuer dasselbe
//     Ergebnis, und jede neue Query-Familie waere eine Migration.
//   * expo-sqlite bringt KEINE Verschluesselung mit (kein SQLCipher). In
//     puncto Vertraulichkeit waere damit nichts gewonnen; beides sind
//     Klartextdateien im App-Sandbox-Verzeichnis.
//   * Der AsyncStorage-Persister ist der von TanStack Query mitgelieferte
//     Weg und wird mit dem Query-Client zusammen versioniert.
//
// Wie docs/SICHERHEIT.md ("lokale Cache-Datenbank verschluesseln ODER auf
// minimierte Daten beschraenken") eingehalten wird: ueber den ZWEITEN Weg,
// Datenminimierung. Verschluesselung waere hier keine ehrliche Option -
// React Native/Hermes hat keine WebCrypto-Symmetrieprimitive, expo-crypto
// kann nur Hash und Zufall, und expo-secure-store ist fuer Blobs dieser
// Groesse nicht ausgelegt. Eine selbstgebaute Chiffre waere schlechter als
// gar keine. Stattdessen gilt:
//   1. Familien-Allowlist: nur Konten, Kategorien und die Buchungsliste
//      werden ueberhaupt geschrieben - keine Detailansichten, Budgets,
//      Berichte, Review-Inbox oder KI-Ergebnisse.
//   2. Feldminimierung: die identifizierenden Freitextfelder werden vor dem
//      Schreiben entfernt (Buchungen: payee_label/note; Konten: institution).
//      Auf der Platte liegen damit Betraege, Datum und Kategorie-Zuordnung,
//      aber weder Haendler- noch Bankname.
//   3. Kurze Lebensdauer: maxAge 24h, und beim Abmelden wird der persistierte
//      Cache geloescht (siehe clearPersistedQueryCache()).
// Sichtbare Folge: offline gelesene Buchungen zeigen keinen Empfaengertext,
// bis das naechste Mal frisch geladen wurde. Das ist der bewusst in Kauf
// genommene Preis fuer Punkt 2.

const STORAGE_KEY = "finanzplaner:mobile:query-cache:v1";

/**
 * Query-Familien (queryKey[0]), die den App-Neustart ueberleben duerfen.
 *
 * "forecast" gehoert hier ausdruecklich NICHT hinein
 * (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Abschnitt 9): eine offline
 * zwischengespeicherte Prognose ist per Definition veraltet - sie wurde gegen
 * einen Kontostand von gestern gerechnet und wuerde einen laengst
 * eingetretenen Engpass als Zukunft anzeigen. Reiner Arbeitsspeicher-Cache
 * ist hier die richtige Antwort, nicht eine kuerzere maxAge. Der Ausschluss
 * ist in ./queryClient.test.ts festgehalten, damit ein spaeterer Eintrag
 * nicht unbemerkt durchrutscht.
 *
 * "insurance" fehlt hier ebenfalls ABSICHTLICH, und der Preis dafuer ist
 * hoeher als bei den uebrigen Ausschluessen: die mobile Versicherungsansicht
 * ist fuer den Ernstfall gebaut ("ich stehe am Unfallort und brauche Nummer
 * und Hotline") - also fuer genau den Moment, in dem das Netz fehlen kann.
 * Ohne Verbindung bleibt sie leer.
 *
 * Am 14.08.2026 bewusst so entschieden. Die Alternative waere die Aufnahme mit
 * Feldminimierung gewesen (Versicherer, Policennummer, Hotline und Frist auf
 * die Platte; Sparte, Zwecktext, Tarif und Beitrag vorher entfernen) - sie
 * haette SICHERHEIT.md:84 ueber denselben zweiten Weg eingehalten wie die
 * Buchungen. Verworfen zugunsten der einfacheren Regel "Gesundheitsnahe Daten
 * verlassen den Arbeitsspeicher nicht"; eine Sparte wie Zahnzusatz oder
 * Berufsunfaehigkeit ist eine Gesundheitsangabe, und der Papierordner bleibt
 * die Rueckfallebene. Wer das drehen will, dreht damit eine Abwaegung um,
 * nicht ein Versehen.
 */
const PERSISTED_QUERY_KEYS = new Set<string>(["accounts", "categories", "transactions"]);

/** Freitext-/Identifikationsfelder, die je Familie vor dem Schreiben geleert werden. */
const MINIMIZED_FIELDS: Record<string, string[]> = {
  transactions: ["payee_label", "note"],
  accounts: ["institution"],
};

function minimizeRow(row: unknown, fields: string[]): unknown {
  if (!row || typeof row !== "object") return row;
  const copy: Record<string, unknown> = { ...(row as Record<string, unknown>) };
  for (const field of fields) {
    // Auf null setzen statt zu loeschen: die Vertragstypen fordern das Feld,
    // und die UI faellt bei null bereits auf einen Ersatztext zurueck.
    if (field in copy) copy[field] = null;
  }
  return copy;
}

/** Entfernt die minimierten Felder aus dem dehydrierten Zustand. */
function minimizePersistedClient(client: PersistedClient): PersistedClient {
  return {
    ...client,
    clientState: {
      ...client.clientState,
      queries: client.clientState.queries.map((query) => {
        const family = query.queryKey[0];
        const fields = typeof family === "string" ? MINIMIZED_FIELDS[family] : undefined;
        if (!fields || !Array.isArray(query.state.data)) return query;
        return {
          ...query,
          state: { ...query.state, data: query.state.data.map((row) => minimizeRow(row, fields)) },
        };
      }),
    },
  };
}

export const queryPersister = createAsyncStoragePersister({
  storage: AsyncStorage,
  key: STORAGE_KEY,
  serialize: (client) => JSON.stringify(minimizePersistedClient(client)),
});

export const persistOptions: Omit<PersistQueryClientOptions, "queryClient"> = {
  persister: queryPersister,
  // Aelter als 24h wird verworfen, statt potenziell laengst ueberholte
  // Finanzdaten weiter anzuzeigen.
  maxAge: 24 * 60 * 60 * 1000,
  // Bei inkompatiblen Aenderungen an den persistierten Query-Formen
  // hochzaehlen - ein alter Cache wird dann verworfen statt falsch gelesen.
  //
  // "categories" ist eine der drei persistierten Familien und hat mit der
  // Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md) neue Felder bekommen
  // (icon, color, is_budgeted, sort_order, synonyms, description,
  // semantic_key, version). Ein Cache aus phase9-v1 enthaelt sie nicht -
  // deshalb wird er hier verworfen. Der Lesecode bleibt trotzdem defensiv
  // (`category.synonyms ?? []`): der Buster schuetzt nur vor dem eigenen
  // alten Cache, nicht vor einem aelteren Server.
  //
  // phase11: die neue paginierte Buchungsliste (useTransactionsPage,
  // docs/MOBILE-APP-REDESIGN-PLAN.md Abschnitt 5) haengt an derselben
  // Query-Key-Familie "transactions" wie useTransactions() - siehe
  // shouldDehydrateQuery unten fuer den Grund und die Abgrenzung. Ein
  // Array- gegen ein Infinite-Query-Objekt auszutauschen ist eine
  // inkompatible Formaenderung derselben Familie, deshalb wird auch dafuer
  // hochgezaehlt.
  buster: "phase11-transactions-page-v1",
  dehydrateOptions: {
    shouldDehydrateQuery: (query: Query) => {
      if (query.state.status !== "success") return false;
      const family = query.queryKey[0];
      if (typeof family !== "string" || !PERSISTED_QUERY_KEYS.has(family)) return false;
      // useTransactionsPage() (../hooks/useTransactions.ts) teilt sich absichtlich
      // denselben Familiennamen "transactions", damit bestehende
      // invalidateQueries({queryKey: ["transactions", ...]})-Aufrufe (Bulk-
      // Kategorisierung, Offline-Queue-Flush, Review, Import,
      // KI-Klassifikation) sie automatisch mittreffen. Ihre Daten liegen aber
      // als InfiniteData ({pages, pageParams}) vor, keine Array-Form -
      // minimizeRow() unten wuerde eine solche Form unveraendert (also inkl.
      // payee_label/note) auf die Platte schreiben. Deshalb hier zusaetzlich
      // auf Array-Form gefiltert: nur useTransactions() bleibt persistent,
      // die paginierte Liste bleibt (gefiltert wie ungefiltert) reiner
      // Arbeitsspeicher-Cache.
      return Array.isArray(query.state.data);
    },
  },
};

/**
 * Beim Abmelden aufzurufen: sonst blieben die Fachdaten des vorherigen
 * Nutzers auf dem Geraet liegen (Mandantentrennung, Architekturplan
 * Abschnitt 5).
 */
export async function clearPersistedQueryCache(): Promise<void> {
  await queryPersister.removeClient();
}
