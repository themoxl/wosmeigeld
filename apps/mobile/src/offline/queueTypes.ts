import type {
  CreateTransactionInput,
  PatchTransactionInput,
  PutTransactionSplitsInput,
  TransactionDetail,
} from "../api/types";

// Phase 9 (Offline-Write-Queue): Typen fuer noch nicht synchronisierte
// Buchungs-Schreibvorgaenge. Die Payloads sind bewusst genau die
// Request-Koerper des Backend-Vertrags (siehe ../api/types), damit ein
// persistierter Eintrag nach einem App-Update nicht plotzlich zu einem
// anderen Request wird als beim Einreihen gemeint war.

export type QueueEntryStatus =
  /** Wartet auf den naechsten Sendeversuch (ggf. hinter einem Backoff). */
  | "pending"
  /** Request ist raus, Antwort steht aus. */
  | "sending"
  /** Braucht eine Entscheidung der Nutzerin - siehe app/mehr/sync-konflikte.tsx. */
  | "conflict";

/**
 * Warum ein Eintrag auf eine Entscheidung wartet. Bewusst als
 * unterscheidbare Union statt eines Freitextes: die Konfliktansicht bietet je
 * nach Grund andere Aktionen an.
 */
export type QueueConflict =
  /** 409 version_conflict - die Buchung wurde anderswo geaendert. */
  | { kind: "version_conflict"; currentVersion: number; current: TransactionDetail }
  /** 409 transaction_archived - die Buchung wurde anderswo geloescht. */
  | { kind: "transaction_archived"; currentVersion: number; current: TransactionDetail }
  /**
   * Die App wurde beendet, waehrend ein POST /v1/transactions unterwegs war.
   * Anders als PATCH/PUT/DELETE ist die Neuanlage NICHT idempotent - der
   * Vertrag kennt weder eine client-seitig vorgegebene ID noch einen
   * Idempotency-Key. Ein blinder Wiederholversuch koennte die Buchung also
   * doppelt anlegen, ein Verwerfen koennte sie verlieren; beides darf die App
   * nicht selbst entscheiden.
   */
  | { kind: "uncertain_create" }
  /**
   * Dauerhaft abgelehnt (fachlicher 4xx oder zu viele Fehlversuche). Verhindert
   * die Endlosschleife, in der ein nie erfolgreicher Eintrag alle folgenden
   * Eintraege derselben Buchung blockiert.
   */
  | { kind: "rejected"; status: number; message: string };

/** Neuanlage: exakt der Body von POST /v1/transactions. */
export type QueueCreatePayload = CreateTransactionInput;
/** Detailaenderung: Body von PATCH /v1/transactions/{id} ohne das Versionsfeld. */
export type QueueUpdatePayload = Omit<PatchTransactionInput, "expected_version">;
/** Betrag/Splits: Body von PUT /v1/transactions/{id}/splits ohne das Versionsfeld. */
export type QueueSplitsPayload = Omit<PutTransactionSplitsInput, "expected_version">;
/**
 * Nur die Kategorie: Body von PUT /v1/transactions/{id}/category ohne das
 * Versionsfeld (docs/KATEGORISIERUNGSPLAN.md, "Manuelle Mehrfachzuordnung":
 * "Mobile kann Offline-Aenderungen pro Buchung in die bestehende Queue
 * legen").
 *
 * Bewusst eine EIGENE Eintragsart statt "splits" mit einem einzigen Split:
 * der Kategorieendpunkt lehnt eine Mehrfachsplit-Buchung mit
 * `409 transaction_has_multiple_splits` ab, statt deren Aufteilung still zu
 * ueberschreiben. Genau dieser Schutz ginge verloren, wenn eine
 * Bulk-Zuordnung als vollstaendiges Splits-PUT in die Queue liefe - offline
 * ist gerade nicht bekannt, ob die Buchung inzwischen aufgeteilt wurde.
 */
export type QueueCategoryPayload = { category_id: string | null };

interface QueueEntryBase {
  /**
   * Eindeutig pro Eintrag - nicht mit localId verwechseln: mehrere Eintraege
   * koennen dieselbe Buchung betreffen.
   */
  entryId: string;
  /**
   * Bei "create" eine rein lokale Platzhalter-ID (der Server vergibt beim
   * Anlegen seine eigene), sonst die echte Buchungs-ID.
   */
  localId: string;
  householdId: string;
  status: QueueEntryStatus;
  /** Gezaehlte Sendeversuche - Grundlage des exponentiellen Backoffs. */
  attempts: number;
  createdAt: string;
  /** Epoch-Millisekunden, ab wann der naechste Versuch erlaubt ist. */
  nextAttemptAt: number;
  /** Letzte Fehlermeldung, nur zur Anzeige. */
  lastError?: string;
  /** Nur bei status "conflict" gesetzt. */
  conflict?: QueueConflict;
}

export type QueueEntry =
  | (QueueEntryBase & { kind: "create"; payload: QueueCreatePayload })
  /** expectedVersion = zuletzt gesehene Serverversion (Transaction.version). */
  | (QueueEntryBase & { kind: "update"; payload: QueueUpdatePayload; expectedVersion: number })
  | (QueueEntryBase & { kind: "splits"; payload: QueueSplitsPayload; expectedVersion: number })
  | (QueueEntryBase & {
      kind: "category";
      payload: QueueCategoryPayload;
      expectedVersion: number;
    })
  | (QueueEntryBase & { kind: "delete"; payload: null });

export type QueueOpKind = QueueEntry["kind"];
export type Queue = QueueEntry[];

/** Eintraege mit einem Optimistic-Concurrency-Token (alles ausser create/delete). */
export type VersionedQueueEntry = Extract<QueueEntry, { expectedVersion: number }>;

export function isVersionedEntry(entry: QueueEntry): entry is VersionedQueueEntry {
  return entry.kind === "update" || entry.kind === "splits" || entry.kind === "category";
}
