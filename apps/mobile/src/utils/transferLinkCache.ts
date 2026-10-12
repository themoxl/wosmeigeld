import AsyncStorage from "@react-native-async-storage/async-storage";

// Bekannte Einschraenkung (siehe Abschlussbericht des Phase-2-Auftrags):
// GET /v1/transactions/{id} liefert laut Backend-Kontrakt kein
// transfer_link_id-Feld, nur is_transfer: boolean. Um "Verknuepfung
// aufheben" trotzdem ueber DELETE /v1/transfers/link/{transfer_link_id}
// anbieten zu koennen, wird die Link-ID beim Erstellen (Response von
// POST /v1/transfers/link) hier fuer BEIDE beteiligten Buchungen
// zwischengespeichert. Das deckt den Hauptfall ab (Nutzer verknuepft und
// hebt die Verknuepfung spaeter in derselben App wieder auf), aber NICHT
// Transfers, die z. B. ueber die Web-App oder vor Installation dieses
// Caches verknuepft wurden - dafuer zeigt die Detailseite dann einen
// Hinweis statt eines aktiven "Verknuepfung aufheben"-Buttons.
const STORAGE_KEY = "finanzplaner:mobile:transfer-link-cache";

type CacheEntry = {
  linkId: string;
  otherTransactionId: string;
};

type CacheShape = Record<string, CacheEntry>;

async function readCache(): Promise<CacheShape> {
  const raw = await AsyncStorage.getItem(STORAGE_KEY);
  if (!raw) return {};
  try {
    return JSON.parse(raw) as CacheShape;
  } catch {
    return {};
  }
}

async function writeCache(cache: CacheShape): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(cache));
}

/** Nach erfolgreichem POST /v1/transfers/link fuer beide Seiten des Paares aufrufen. */
export async function rememberTransferLink(
  outgoingTransactionId: string,
  incomingTransactionId: string,
  linkId: string,
): Promise<void> {
  const cache = await readCache();
  cache[outgoingTransactionId] = { linkId, otherTransactionId: incomingTransactionId };
  cache[incomingTransactionId] = { linkId, otherTransactionId: outgoingTransactionId };
  await writeCache(cache);
}

/** Liefert die bekannte transfer_link.id fuer eine Buchung, sofern hier zwischengespeichert. */
export async function getCachedTransferLinkId(transactionId: string): Promise<string | null> {
  const cache = await readCache();
  return cache[transactionId]?.linkId ?? null;
}

/** Nach erfolgreichem Aufheben der Verknuepfung fuer beide Seiten aufrufen. */
export async function forgetTransferLink(transactionId: string): Promise<void> {
  const cache = await readCache();
  const entry = cache[transactionId];
  if (entry) {
    delete cache[entry.otherTransactionId];
  }
  delete cache[transactionId];
  await writeCache(cache);
}
