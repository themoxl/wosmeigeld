import { useState, useEffect, useRef, type ChangeEvent, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useFormatDate } from "../hooks/useProfile";
import { useHousehold } from "../household/HouseholdProvider";
import {
  cancelImportRun,
  commitImportRun,
  createMappingProfile,
  deleteImportRun,
  deleteMappingProfile,
  getImportRows,
  getImportRun,
  listAccounts,
  listImportRuns,
  listMappingProfiles,
  fetchSystemHealth,
  previewImportRevert,
  reparseImportRun,
  revertImportRun,
  suggestImportMapping,
  updateImportRun,
  updateMappingProfile,
  uploadImportFile,
} from "../lib/api";
import {
  inferMapping,
  previewRow,
  MAPPING_FIELDS,
  type MappingFieldKey,
} from "../lib/importPreview";
import "./import.css";
import { AiReasonNotice } from "../components/AiReasonNotice";
import { Modal } from "../components/Modal";
import { formatMinorUnitsAsEuro } from "../lib/money";
import type {
  ImportFormat,
  ImportMappingSuggestion,
  ImportRow,
  ImportRun,
  ImportStatus,
  RowStatus,
} from "../lib/types";

const FORMAT_LABELS: Record<ImportFormat, string> = {
  csv_generic: "Generisches CSV",
  xlsx_generic: "Generisches XLSX",
  xls_generic: "Generisches XLS (alt)",
  mt940: "MT940 (SWIFT)",
  camt: "CAMT (ISO 20022)",
  paypal_csv: "PayPal CSV",
};

// detected_format ist backendseitig ein plain string (kein Literal-Union -
// siehe services/api/app/schemas/import_platform.py), enthaelt zur Laufzeit
// aber immer einen der ImportFormat-Werte. Fallback auf den Rohwert deckt
// unbekannte/zukuenftige Formate ab, ohne den Typ hier aufzuweichen.
function formatLabel<T extends string>(labels: Record<T, string>, key: string): string {
  return (labels as Record<string, string>)[key] ?? key;
}

const ROW_STATUS_LABELS: Record<RowStatus, string> = {
  new: "Neu",
  duplicate_exact: "Exakte Dublette",
  duplicate_possible: "Mögliche Dublette",
  transfer_candidate: "Transfer-Kandidat",
  error: "Fehler",
  committed: "Übernommen",
  skipped: "Übersprungen",
};

const RUN_STATUS_LABELS: Record<ImportStatus, string> = {
  pending: "Wartet auf Verarbeitung",
  analysing: "Wird analysiert",
  ready: "Bereit zur Übernahme",
  needs_mapping: "Spaltenzuordnung nötig",
  committing: "Wird übernommen",
  completed: "Vollständig übernommen",
  failed: "Verarbeitung fehlgeschlagen",
  cancelled: "Abgebrochen",
};

// Begruendung, warum "Übernehmen" gerade nicht geht. Sie wird IMMER sichtbar
// angezeigt, wenn der Knopf deaktiviert ist - ein kommentarlos toter Knopf war
// genau der Grund, warum der fehlende Kontobezug erst beim Klick auffiel.
function statusBlockedReason(status: ImportStatus): string | null {
  switch (status) {
    case "pending":
      return "Die Verarbeitung wurde noch nicht gestartet - bitte kurz warten.";
    case "analysing":
      return "Die Datei wird gerade analysiert - bitte kurz warten.";
    case "committing":
      return "Der Lauf wird bereits übernommen.";
    case "completed":
      return "Der Lauf wurde bereits vollständig übernommen.";
    case "needs_mapping":
      return "Bitte zuerst Datum und Betrag zuordnen.";
    case "failed":
      return "Die Verarbeitung ist fehlgeschlagen - bitte die Datei erneut hochladen.";
    case "cancelled":
      return "Der Lauf wurde abgebrochen.";
    default:
      return null;
  }
}

// Reihenfolge: erst der Status, dann das Zielkonto. Ein fehlendes Zielkonto ist
// seit PATCH /v1/imports/{id} nachtraeglich heilbar - bei einem Lauf, der gar
// nicht mehr im Status 'ready' ist, waere die Aufforderung dazu aber falsch.
function commitBlockedReason(run: ImportRun): string | null {
  const statusReason = statusBlockedReason(run.status);
  if (statusReason) {
    return statusReason;
  }
  if (!run.account_id) {
    return "Bitte zuerst ein Zielkonto zuweisen.";
  }
  return null;
}

/**
 * Client-Heuristik fuer den Entfernen-Knopf (Faustregel des Backends):
 * uebernommene Laeufe bleiben als Herkunftsnachweis bestehen, laufende
 * Verarbeitungen werden nicht angetastet.
 *
 * committed_count statt allein des Status: ein Teil-Commit laesst den Lauf auf
 * 'ready' stehen, solange offene Dubletten uebrig sind. Vorher stand dort
 * "Bereit zur Übernahme" samt Entfernen-Knopf, und der Server lehnte den Klick
 * anschliessend ab - der Lauf sah unberuehrt aus, obwohl bereits Buchungen aus
 * ihm entstanden waren.
 *
 * Die 409-Meldung des Servers muss trotzdem sichtbar werden koennen: diese
 * Vorabfilterung soll den Fehlerfall seltener machen, nicht ersetzen.
 */
function deleteBlockedReason(run: ImportRun): string | null {
  if (run.status === "completed" || (run.committed_count ?? 0) > 0) {
    return (
      "Bereits übernommen - der Lauf bleibt als Nachweis bestehen, sonst würden " +
      "die daraus entstandenen Buchungen still ihre Herkunft verlieren."
    );
  }
  if (run.status === "pending" || run.status === "analysing" || run.status === "committing") {
    return "Wird gerade verarbeitet - er lässt sich abbrechen und danach entfernen.";
  }
  return null;
}

/**
 * Stehen aus diesem Lauf noch Buchungen im Bestand? Nur dann bewirkt ein
 * Zuruecknehmen ueberhaupt etwas - bei einem bereits zurueckgenommenen Lauf
 * waere der Knopf eine Sackgasse, die im Dialog mit "es gibt nichts
 * zurueckzunehmen" endet.
 *
 * 'committing' zaehlt nicht mit: dort entstehen die Buchungen gerade erst, und
 * ein Herausnehmen mittendrin traefe eine Menge, die sich unter der Hand noch
 * aendert. Der Lauf bekommt in diesem Zustand die Begruendung "wird gerade
 * verarbeitet", wie beim Entfernen auch.
 */
function hasActiveTransactions(run: ImportRun): boolean {
  return run.status !== "committing" && (run.active_transaction_count ?? 0) > 0;
}

/**
 * Laesst sich dieser Lauf abbrechen? Genau die Status, die das Entfernen
 * sperren, sind hier die erlaubten - die beiden Aktionen ergaenzen sich, statt
 * eine Luecke zu lassen. 'committing' ist bewusst NICHT dabei: dort entstehen
 * bereits Buchungen, ein Abbruch mittendrin liesse den Lauf halb uebernommen.
 */
function isCancellable(run: ImportRun): boolean {
  return run.status === "pending" || run.status === "analysing";
}

const MAPPING_FIELD_ORDER = MAPPING_FIELDS;

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

/** Alter eines Laufs in Millisekunden, gemessen an seinem created_at. */
function runAgeMs(run: { created_at: string } | undefined | null, now: number): number {
  if (!run) return 0;
  const started = new Date(run.created_at).getTime();
  return Number.isNaN(started) ? 0 : Math.max(0, now - started);
}

/** Verstrichene Zeit als "6 s" / "3 min" - fuer die Verarbeitungszeile. */
function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 90) return `${seconds} s`;
  return `${Math.round(seconds / 60)} min`;
}

type RowFilterMode = "all" | "new" | "duplicates" | "transfers" | "errors";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ImportPage() {
  const { selectedHouseholdId } = useHousehold();
  return <ImportWorkspace key={selectedHouseholdId ?? "none"} />;
}

function ImportWorkspace() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDateTime: formatDateTimeValue } = useFormatDate();
  const formatDate = (iso: string | null) => (iso ? formatDateTimeValue(iso) : "-");

  // Upload State
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState<string>("");
  const [selectedMappingProfileId, setSelectedMappingProfileId] = useState<string>("");
  const [dispatchStarted, setDispatchStarted] = useState<{ runId: string; time: number } | null>(
    null,
  );
  const [uploadError, setUploadError] = useState<string | null>(null);
  // Zaehler am key des Datei-Feldes. Ohne ihn behielt das unkontrollierte
  // <input type="file"> nach einem erfolgreichen Upload seinen Dateinamen in
  // der Anzeige, waehrend setSelectedFile(null) den State leerte: das Feld
  // zeigte "Umsaetze.xlsx", der Knopf war deaktiviert und darunter stand "Zum
  // Hochladen fehlt noch die Datei" - fuer den Nutzer nicht aufloesbar, weil
  // die Anzeige das Gegenteil behauptete. Ein neuer key remountet das Feld und
  // haelt DOM und State zusammen.
  const [fileInputKey, setFileInputKey] = useState(0);
  // Merkt den Namen der zuletzt erfolgreich hochgeladenen Datei, damit der
  // Hinweistext den geleerten Zustand erklaeren kann, statt ihn nur zu melden.
  const [lastUploadedName, setLastUploadedName] = useState<string | null>(null);

  // Active Import Selection State
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [rowFilter, setRowFilter] = useState<RowFilterMode>("all");
  const [selectionExplicit, setSelectionExplicit] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const draftScope = useRef<string | null>(null);
  const previousRun = useRef<{ id: string | null; status: string | undefined }>({
    id: null,
    status: undefined,
  });
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileToDelete, setProfileToDelete] = useState<string | null>(null);
  const [profileMessage, setProfileMessage] = useState<string | null>(null);
  const [selectedRowIds, setSelectedRowIds] = useState<Set<string>>(new Set());
  const [commitMessage, setCommitMessage] = useState<string | null>(null);
  // Erfolg und Fehler getrennt halten: der Fehlerfall bekommt eine eigene,
  // als Fehler erkennbare Darstellung mit der Servermeldung im Wortlaut.
  const [commitError, setCommitError] = useState<string | null>(null);

  // Nachtraegliche Zuweisung des Zielkontos (PATCH /v1/imports/{id}).
  const [assignAccountId, setAssignAccountId] = useState<string>("");
  const [assignError, setAssignError] = useState<string | null>(null);
  const [assignMessage, setAssignMessage] = useState<string | null>(null);

  // Entfernen eines Importlaufs (DELETE /v1/imports/{id}).
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteMessage, setDeleteMessage] = useState<string | null>(null);

  // Abbrechen eines noch laufenden Imports (POST /v1/imports/{id}/cancel).
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [cancelMessage, setCancelMessage] = useState<string | null>(null);

  // Zuruecknehmen eines bereits uebernommenen Laufs. Gesetzt heisst: der
  // Bestaetigungsdialog ist offen und laedt seine Vorschau.
  const [revertRun, setRevertRun] = useState<ImportRun | null>(null);
  const [revertError, setRevertError] = useState<string | null>(null);
  const [revertMessage, setRevertMessage] = useState<string | null>(null);
  // Auch die Buchungen mitnehmen, an denen inzwischen etwas haengt. Bewusst bei
  // jedem Oeffnen des Dialogs wieder aus: die Entscheidung gilt fuer diesen
  // einen Lauf und soll nicht aus einem frueheren Dialog nachwirken.
  const [revertIncludeLinked, setRevertIncludeLinked] = useState(false);

  // Umschalter der Vorschau: kanonische Sicht (was importiert wird) oder
  // Originalspalten (was in der Datei steht). Beides wird gebraucht - die
  // kanonische Sicht zum Pruefen der Buchungen, die Originalsicht zum Pruefen
  // der Zuordnung. Bisher gab es nur die erste, weshalb nicht erkennbar war,
  // welche Spalte der Datei ueberhaupt wo gelandet ist.
  const [showRawColumns, setShowRawColumns] = useState(false);

  // KI-Spaltenvorschlag (nie automatisch angewendet - siehe Anzeigeblock unten)
  const [suggestion, setSuggestion] = useState<ImportMappingSuggestion | null>(null);
  const [suggestionError, setSuggestionError] = useState<string | null>(null);
  const [suggestionApplied, setSuggestionApplied] = useState(false);

  // Zuordnungskorrektur. Der Entwurf startet LEER - jede Vorbelegung waere
  // hier geraten, weil die Spaltennamen der Datei erst nach dem Einlesen
  // feststehen (frueher standen "Datum"/"Betrag"/"Empfänger"/
  // "Verwendungszweck" darin, die in echten Bankexporten kaum vorkommen).
  const [showMappingForm, setShowMappingForm] = useState(false);
  const [mappingDraft, setMappingDraft] = useState<Partial<Record<MappingFieldKey, string>>>({});
  const [mappingDirty, setMappingDirty] = useState(false);
  const appliedMapping = useRef<Partial<Record<MappingFieldKey, string>>>({});
  const [profileName, setProfileName] = useState("");
  const [replaceProfileId, setReplaceProfileId] = useState("");
  const [mappingFormError, setMappingFormError] = useState<string | null>(null);
  const [mappingSuccessMsg, setMappingSuccessMsg] = useState<string | null>(null);
  const [reparseError, setReparseError] = useState<string | null>(null);
  const [reparseMessage, setReparseMessage] = useState<string | null>(null);

  // Queries
  const accountsQuery = useQuery({
    queryKey: ["accounts", selectedHouseholdId],
    queryFn: () => listAccounts(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  /**
   * Zustand des Hintergrunddienstes. Ohne ihn ist "Wartet auf Verarbeitung"
   * mehrdeutig: es bedeutet entweder "der Worker ist gleich soweit" oder "es
   * laeuft ueberhaupt kein Worker, das hier wartet fuer immer". Genau diese
   * beiden Faelle sahen bisher identisch aus.
   */
  const systemHealthQuery = useQuery({
    queryKey: ["system-health"],
    queryFn: fetchSystemHealth,
    refetchInterval: 15000,
    // Ein nicht erreichbarer Systemstatus darf die Importseite nicht mit einem
    // Fehler ueberziehen - die Zeile blendet sich dann einfach aus.
    retry: false,
  });

  // `services` wird bewusst mitgeprueft, obwohl der Vertrag es als Pflichtfeld
  // fuehrt: die optionale Kette endete bisher an `data`, sodass eine Antwort
  // ohne diese Liste (aelterer Server, Proxy-Fehlerseite mit Status 200) beim
  // .find eine TypeError warf - und zwar im Rendern, was die GESAMTE
  // Importseite weiss liess. Genau das schliesst der retry:false-Kommentar
  // oben aus.
  const workerHealth =
    systemHealthQuery.data?.services?.find((s) => s.service === "worker") ?? null;
  const workerIsDown = workerHealth?.status === "down" || workerHealth?.status === "degraded";

  const importRunsQuery = useQuery({
    queryKey: ["imports", selectedHouseholdId],
    queryFn: () => listImportRuns(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
    // Die Historie zeigte ihren Stand vom Seitenaufbau und riet zugleich, sie
    // "danach neu zu laden" - eine Aufforderung an den Nutzer, das Polling von
    // Hand zu ersetzen. Solange ein Lauf in Arbeit ist, aktualisiert sie sich
    // jetzt selbst; danach hoert sie auf.
    refetchInterval: (query) =>
      (query.state.data ?? []).some(
        (r) =>
          !r.is_stale &&
          (r.status === "pending" || r.status === "analysing" || r.status === "committing"),
      )
        ? 3000
        : false,
  });

  const runs = importRunsQuery.data ?? [];
  const currentRunId = activeRunId ?? runs[0]?.id ?? null;
  // Keep the initial run selected when a background history refresh changes ordering.
  if (!activeRunId && runs[0]) setActiveRunId(runs[0].id);

  const activeRunQuery = useQuery({
    queryKey: ["import-run", selectedHouseholdId, currentRunId],
    queryFn: () =>
      getImportRun(accessToken as string, selectedHouseholdId as string, currentRunId!),
    enabled: Boolean(accessToken && selectedHouseholdId && currentRunId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status !== "pending" && status !== "analysing" && status !== "committing") {
        return false;
      }
      return query.state.data?.is_stale ? false : 2000;
    },
  });

  const isRunProcessing =
    activeRunQuery.data?.status === "pending" ||
    activeRunQuery.data?.status === "analysing" ||
    activeRunQuery.data?.status === "committing";

  const currentRunAge =
    dispatchStarted?.runId === currentRunId
      ? Math.max(0, activeRunQuery.dataUpdatedAt - dispatchStarted.time)
      : activeRunQuery.data?.started_at
        ? runAgeMs({ created_at: activeRunQuery.data.started_at }, activeRunQuery.dataUpdatedAt)
        : null;
  const runIsStale = isRunProcessing && activeRunQuery.data?.is_stale === true;

  const activeRowsQuery = useQuery({
    queryKey: ["import-rows", selectedHouseholdId, currentRunId],
    queryFn: () =>
      getImportRows(accessToken as string, selectedHouseholdId as string, currentRunId!),
    enabled:
      Boolean(accessToken && selectedHouseholdId && currentRunId && activeRunQuery.data) &&
      !isRunProcessing,
  });

  const mappingProfilesQuery = useQuery({
    queryKey: ["mapping-profiles", selectedHouseholdId],
    queryFn: () => listMappingProfiles(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const currentRun =
    activeRunQuery.data ?? runs.find((r) => r.id === currentRunId) ?? runs[0] ?? null;

  // listAccounts liefert nur offene Konten (services/api: "where ... not
  // is_closed") - eine leere Liste heisst also wirklich "kein Konto vorhanden".
  const accounts = accountsQuery.data ?? [];

  // Mutations
  const uploadMutation = useMutation({
    mutationFn: (vars: { file: File; accountId?: string; mappingProfileId?: string }) =>
      uploadImportFile(
        accessToken as string,
        selectedHouseholdId as string,
        vars.file,
        vars.accountId,
        vars.mappingProfileId,
      ),
    onSuccess: (newRun: ImportRun) => {
      setSelectedFile(null);
      // Muss zusammen mit setSelectedFile(null) passieren, sonst behaelt das
      // DOM-Feld seinen Dateinamen und widerspricht dem State (siehe
      // fileInputKey). Der Name wandert in den Hinweistext.
      setFileInputKey((k) => k + 1);
      setLastUploadedName(newRun.filename);
      setDispatchStarted({ runId: newRun.id, time: Date.now() });
      setSelectedMappingProfileId("");
      queryClient.setQueryData(["import-run", selectedHouseholdId, newRun.id], newRun);
      setUploadError(null);
      setCommitMessage(null);
      setCommitError(null);
      setSuggestion(null);
      setSuggestionError(null);
      setSuggestionApplied(false);
      setActiveRunId(newRun.id);
      setSelectedRowIds(new Set());
      setAssignAccountId("");
      setAssignError(null);
      setAssignMessage(null);
      void queryClient.invalidateQueries({ queryKey: ["imports", selectedHouseholdId] });
      void queryClient.invalidateQueries({
        queryKey: ["import-run", selectedHouseholdId, newRun.id],
      });
      void queryClient.invalidateQueries({
        queryKey: ["import-rows", selectedHouseholdId, newRun.id],
      });
    },
    onError: (err: unknown) => {
      setUploadError(errorText(err, "Upload fehlgeschlagen."));
      void queryClient.invalidateQueries({ queryKey: ["imports", selectedHouseholdId] });
    },
  });

  const commitMutation = useMutation({
    mutationFn: (vars: { runId: string; rowIds?: string[] }) =>
      commitImportRun(
        accessToken as string,
        selectedHouseholdId as string,
        vars.runId,
        vars.rowIds,
      ),
    onSuccess: (res: { committed: number }, vars: { runId: string; rowIds?: string[] }) => {
      setCommitMessage(`${res.committed} Buchungen erfolgreich ins Konto übernommen.`);
      setCommitError(null);
      setSelectedRowIds(new Set());
      void queryClient.invalidateQueries({ queryKey: ["imports", selectedHouseholdId] });
      void queryClient.invalidateQueries({
        queryKey: ["import-run", selectedHouseholdId, vars.runId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["import-rows", selectedHouseholdId, vars.runId],
      });
      void queryClient.invalidateQueries({ queryKey: ["transactions", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["accounts", selectedHouseholdId] });
    },
    onError: (err: unknown) => {
      // Wortlaut des Servers durchreichen: bei HTTP-Fehlern steht dort der
      // eigentliche Grund (z. B. fehlendes Zielkonto oder falscher Status).
      setCommitMessage(null);
      setCommitError(
        `Fehler beim Übernehmen: ${errorText(err, "Die Übernahme wurde vom Server abgelehnt.")}`,
      );
    },
  });

  // Zielkonto nachtraeglich zuweisen. Bewusst nur das Zielkonto: ein Wechsel
  // des Mapping-Profils wuerde serverseitig NICHT neu geparst werden, die
  // Oberflaeche wuerde also etwas versprechen, das nicht passiert.
  const assignAccountMutation = useMutation({
    mutationFn: (vars: { runId: string; accountId: string }) =>
      updateImportRun(accessToken as string, selectedHouseholdId as string, vars.runId, {
        account_id: vars.accountId,
      }),
    onSuccess: (updated: ImportRun, vars: { runId: string; accountId: string }) => {
      const accountName =
        accounts.find((a) => a.id === updated.account_id)?.name ?? "dem gewählten Konto";
      setAssignError(null);
      setAssignMessage(`Zielkonto zugewiesen: ${accountName}.`);
      setAssignAccountId("");
      setCommitError(null);
      void queryClient.invalidateQueries({ queryKey: ["imports", selectedHouseholdId] });
      void queryClient.invalidateQueries({
        queryKey: ["import-run", selectedHouseholdId, vars.runId],
      });
    },
    onError: (err: unknown) => {
      setAssignMessage(null);
      setAssignError(
        "Das Zielkonto konnte nicht zugewiesen werden: " +
          errorText(err, "Die Änderung wurde vom Server abgelehnt."),
      );
    },
  });

  // Importlauf entfernen. Der Server entscheidet, ob das erlaubt ist - die
  // Client-Heuristik blendet den Knopf nur vorab aus (siehe
  // deleteBlockedReason), deshalb wird die Serverantwort hier immer angezeigt.
  const deleteRunMutation = useMutation({
    mutationFn: (vars: { runId: string; filename: string }) =>
      deleteImportRun(accessToken as string, selectedHouseholdId as string, vars.runId),
    onSuccess: (_result: void, vars: { runId: string; filename: string }) => {
      setDeleteError(null);
      setDeleteMessage(
        `Der Importlauf „${vars.filename}“ wurde samt seiner eingelesenen Zeilen entfernt.`,
      );
      if (activeRunId === vars.runId) {
        setActiveRunId(null);
        setSelectedRowIds(new Set());
        setCommitMessage(null);
        setCommitError(null);
        setAssignAccountId("");
        setAssignError(null);
        setAssignMessage(null);
      }
      queryClient.setQueryData<ImportRun[]>(["imports", selectedHouseholdId], (old) =>
        old?.filter((run) => run.id !== vars.runId),
      );
      queryClient.removeQueries({ queryKey: ["import-run", selectedHouseholdId, vars.runId] });
      queryClient.removeQueries({ queryKey: ["import-rows", selectedHouseholdId, vars.runId] });
      void queryClient.invalidateQueries({ queryKey: ["imports", selectedHouseholdId] });
    },
    onError: (err: unknown) => {
      setDeleteMessage(null);
      setDeleteError(
        "Der Importlauf wurde nicht entfernt: " +
          errorText(err, "Das Entfernen wurde vom Server abgelehnt."),
      );
    },
  });

  /**
   * Laufenden Import abbrechen. Der Gegenpol zu deleteRunMutation: Loeschen
   * verlangt einen Lauf, der NICHT in Arbeit ist, Abbrechen greift genau dann,
   * wenn er es ist. Vorher fehlte diese Haelfte vollstaendig - ein Lauf, den
   * kein Worker abholte, blieb dauerhaft "wird gerade verarbeitet" und war
   * weder zu beenden noch zu entfernen.
   */
  const cancelRunMutation = useMutation({
    mutationFn: (vars: { runId: string; filename: string }) =>
      cancelImportRun(accessToken as string, selectedHouseholdId as string, vars.runId),
    onSuccess: (updated: ImportRun, vars: { runId: string; filename: string }) => {
      setCancelError(null);
      setCancelMessage(
        updated.status === "cancelled"
          ? `Der Importlauf „${vars.filename}“ wurde abgebrochen. Er lässt sich jetzt entfernen.`
          : `Der Abbruch von „${vars.filename}“ ist vorgemerkt - der Hintergrunddienst beendet ` +
              "den laufenden Schritt und stoppt dann.",
      );
      void queryClient.invalidateQueries({ queryKey: ["imports", selectedHouseholdId] });
      void queryClient.invalidateQueries({
        queryKey: ["import-run", selectedHouseholdId, vars.runId],
      });
    },
    onError: (err: unknown) => {
      setCancelMessage(null);
      setCancelError(
        "Der Importlauf wurde nicht abgebrochen: " +
          errorText(err, "Der Abbruch wurde vom Server abgelehnt."),
      );
    },
  });

  /**
   * Erneutes Einlesen mit korrigierter Zuordnung. Das ist der Knopf, der dem
   * Assistenten bisher fehlte: er konnte nur ein Profil SPEICHERN, aber nichts
   * anwenden - und PATCH mit einem Profil parst serverseitig nicht neu.
   */
  const reparseMutation = useMutation({
    mutationFn: (vars: { runId: string; mapping: Record<string, string> }) =>
      reparseImportRun(
        accessToken as string,
        selectedHouseholdId as string,
        vars.runId,
        vars.mapping,
      ),
    onSuccess: async (
      updated: ImportRun,
      vars: { runId: string; mapping: Record<string, string> },
    ) => {
      await queryClient.cancelQueries({
        queryKey: ["import-rows", selectedHouseholdId, vars.runId],
      });
      queryClient.setQueryData(["import-run", selectedHouseholdId, vars.runId], updated);
      queryClient.setQueryData(["import-rows", selectedHouseholdId, vars.runId], []);
      if (vars.runId === currentRunId) {
        setReparseError(null);
        setDispatchStarted({ runId: vars.runId, time: Date.now() });
        setReparseMessage("Zuordnung angewendet. Vorschau wird aktualisiert.");
        appliedMapping.current = vars.mapping;
        setMappingDirty(false);
        setShowMappingForm(false);
        setSelectionExplicit(false);
        setSelectedRowIds(new Set());
      }
      void queryClient.invalidateQueries({ queryKey: ["imports", selectedHouseholdId] });
      void queryClient.invalidateQueries({
        queryKey: ["import-run", selectedHouseholdId, vars.runId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["import-rows", selectedHouseholdId, vars.runId],
      });
    },
    onError: (err: unknown) => {
      setReparseMessage(null);
      void queryClient.invalidateQueries({ queryKey: ["imports", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["import-run", selectedHouseholdId] });
      setReparseError(
        "Die Zuordnung wurde nicht angewendet: " +
          errorText(err, "Der Server hat das erneute Einlesen abgelehnt."),
      );
    },
  });

  // Der KI-Vorschlag laeuft bewusst als Mutation und nicht als Query: er wird
  // nur auf ausdrueckliche Anforderung ausgeloest (er startet serverseitig ein
  // Modell) und darf nie im Hintergrund automatisch nachgeladen werden.
  const suggestionMutation = useMutation({
    mutationFn: (runId: string) =>
      suggestImportMapping(accessToken as string, selectedHouseholdId as string, runId),
    onSuccess: (result: ImportMappingSuggestion, runId) => {
      if (runId !== currentRunId) return;
      setSuggestion(result);
      setSuggestionError(null);
      setSuggestionApplied(false);
    },
    onError: (err: unknown, runId) => {
      if (runId !== currentRunId) return;
      setSuggestion(null);
      setSuggestionError(errorText(err, "Der KI-Spaltenvorschlag konnte nicht abgerufen werden."));
    },
  });

  const createMappingProfileMutation = useMutation({
    mutationFn: (input: {
      name: string;
      bank_format: ImportFormat;
      column_mapping: Record<string, string>;
      profileId?: string;
    }) => {
      const { profileId, ...profile } = input;
      return profileId
        ? updateMappingProfile(
            accessToken as string,
            selectedHouseholdId as string,
            profileId,
            profile,
          )
        : createMappingProfile(accessToken as string, selectedHouseholdId as string, profile);
    },
    onSuccess: () => {
      setProfileName("");
      setReplaceProfileId("");
      setMappingFormError(null);
      setMappingSuccessMsg("Zuordnung gespeichert.");
      void queryClient.invalidateQueries({ queryKey: ["mapping-profiles", selectedHouseholdId] });
    },
    onError: (err: unknown) => {
      setMappingFormError(errorText(err, "Profil konnte nicht gespeichert werden."));
    },
  });

  const deleteProfileMutation = useMutation({
    mutationFn: (id: string) =>
      deleteMappingProfile(accessToken as string, selectedHouseholdId as string, id),
    onSuccess: (_result, id) => {
      if (selectedMappingProfileId === id) setSelectedMappingProfileId("");
      if (replaceProfileId === id) {
        setReplaceProfileId("");
        setProfileName("");
      }
      setProfileError(null);
      setProfileToDelete(null);
      setProfileMessage("Zuordnung gelöscht. Bereits importierte Buchungen bleiben erhalten.");
      void queryClient.invalidateQueries({ queryKey: ["mapping-profiles", selectedHouseholdId] });
    },
    onError: (error: unknown) =>
      setProfileError(errorText(error, "Zuordnung konnte nicht gelöscht werden.")),
  });

  // Handlers
  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    // Auch das Abwaehlen der Datei uebernehmen, damit die Anzeige nicht eine
    // Datei behauptet, die der Dateidialog gerade verworfen hat.
    setSelectedFile(e.target.files?.[0] ?? null);
    setUploadError(null);
    setLastUploadedName(null);
    setSelectedMappingProfileId("");
  };

  const handleUploadSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (uploadMutation.isPending) return;
    if (!selectedFile) {
      setUploadError("Bitte eine Datei auswählen.");
      return;
    }
    if (!selectedAccountId) {
      // Pflicht statt "optional": ein Lauf ohne Zielkonto kann serverseitig nie
      // uebernommen werden und laesst sich auch nicht nachtraeglich zuordnen.
      setUploadError(
        "Bitte ein Zielkonto wählen. Ohne Zielkonto kann der Importlauf später nicht übernommen werden.",
      );
      return;
    }
    uploadMutation.mutate({
      file: selectedFile,
      accountId: selectedAccountId,
      mappingProfileId: selectedMappingProfileId || undefined,
    });
  };

  /**
   * Uebernimmt den KI-Vorschlag in die Auswahlfelder - ausschliesslich auf
   * Klick. Nur Felder, die der Vorschlag wirklich benennt, werden ersetzt;
   * eine bereits getroffene Auswahl bleibt sonst stehen.
   */
  const handleApplySuggestion = () => {
    if (!suggestion?.available) {
      setSuggestionError(
        "Dieser Vorschlag ist unvollständig und wird deshalb nicht übernommen - " +
          "bitte die Zuordnung von Hand setzen.",
      );
      return;
    }
    const mapping = suggestion.column_mapping ?? {};
    setMappingDirty(true);
    setMappingDraft((prev) => ({
      ...prev,
      ...Object.fromEntries(
        MAPPING_FIELD_ORDER.map(({ key }) => [key, mapping[key] ?? prev[key] ?? ""]).filter(
          ([, value]) => value,
        ),
      ),
    }));
    setSuggestionApplied(true);
    setMappingSuccessMsg(null);
  };

  /**
   * Wendet die Zuordnung auf DIESEN Lauf an. Getrennt vom Profilspeichern:
   * das eine wirkt jetzt, das andere kuenftig.
   */
  const handleReparse = () => {
    if (!currentRunId || reparseMutation.isPending || mappingInvalid || reparseBlockedReason)
      return;
    const missing = MAPPING_FIELD_ORDER.filter(
      ({ key, required }) => required && !mappingDraft[key],
    );
    if (missing.length > 0) {
      setReparseError(
        `Ohne ${missing.map((f) => f.label).join(" und ")} lässt sich keine Buchung bilden - ` +
          "bitte die passende Spalte auswählen.",
      );
      return;
    }
    setReparseError(null);
    setReparseMessage(null);
    reparseMutation.mutate({
      runId: currentRunId,
      mapping: Object.fromEntries(
        Object.entries(mappingDraft).filter(([, value]) => value),
      ) as Record<string, string>,
    });
  };

  const handleSaveMappingProfile = (e: FormEvent) => {
    e.preventDefault();
    if (mappingInvalid || createMappingProfileMutation.isPending || reparseMutation.isPending)
      return;
    if (!profileName.trim()) {
      setMappingFormError("Bitte einen Namen für die gespeicherte Zuordnung eingeben.");
      return;
    }
    // Das Format kommt aus dem ERKANNTEN Format des Laufs, nicht aus einer
    // Auswahlliste mit Standardwert. Der bisherige Standard 'csv_generic'
    // wanderte ungeprueft ins Profil und schaltete dort als erzwungenes Format
    // die Autoerkennung ab - ein so gespeichertes Profil machte jeden
    // XLSX-Upload unbrauchbar.
    const detected = currentRun?.detected_format;
    if (!detected || !(detected in FORMAT_LABELS)) {
      setMappingFormError(
        "Das Format dieses Laufs steht noch nicht fest - die Zuordnung lässt sich erst " +
          "merken, nachdem die Datei erfolgreich gelesen wurde.",
      );
      return;
    }
    setMappingFormError(null);
    setMappingSuccessMsg(null);
    createMappingProfileMutation.mutate({
      profileId: replaceProfileId || undefined,
      name: profileName.trim(),
      bank_format: detected as ImportFormat,
      column_mapping: Object.fromEntries(
        Object.entries(mappingDraft).filter(([, value]) => value),
      ) as Record<string, string>,
    });
  };

  // Filter rows
  const allRows = activeRowsQuery.data ?? [];
  const sourceRows =
    allRows.length > 0
      ? allRows
      : (currentRun?.sample_raw ?? []).map((raw_data, index) => ({
          id: `sample-${index}`,
          raw_data,
        }));

  /**
   * Die Spaltennamen der Datei, in der Reihenfolge ihres ersten Auftretens.
   * Quelle ist bevorzugt der Lauf selbst (detected_columns, existiert auch
   * ohne Staging-Zeilen), ersatzweise raw_data der eingelesenen Zeilen.
   *
   * raw_data wurde vom Server immer geliefert und im Client nie verwendet -
   * der generierte Vertrag typisierte es als Record<string, never> und machte
   * es damit unlesbar. Die Vorschau konnte deshalb nur die vier kanonischen
   * Felder zeigen, und ob "Beschreibung" oder "Details" zum Empfaenger wurde,
   * war nirgends zu sehen.
   */
  const rawColumns: string[] = (() => {
    const fromRun = currentRun?.detected_columns;
    if (fromRun && fromRun.length > 0) return fromRun;
    const seen: string[] = [];
    for (const row of sourceRows) {
      for (const key of Object.keys(row.raw_data ?? {})) {
        if (!seen.includes(key)) seen.push(key);
      }
    }
    return seen;
  })();

  const filteredRows = allRows.filter((row: ImportRow) => {
    if (rowFilter === "new") return row.status === "new";
    if (rowFilter === "duplicates")
      return row.status === "duplicate_exact" || row.status === "duplicate_possible";
    if (rowFilter === "transfers") return row.status === "transfer_candidate";
    if (rowFilter === "errors") return row.status === "error";
    return true;
  });

  // Auswahlspalte + Zeilennummer + Status plus die je nach Ansicht wechselnde
  // Mitte (vier kanonische Felder oder alle Originalspalten).
  const tableColSpan = 3 + (showRawColumns ? rawColumns.length : 4);

  /**
   * Die Spalten, aus denen die Zuordnung waehlen kann. Bevorzugt die des
   * Laufs; ersatzweise die des KI-Vorschlags, der bei einem Lauf ohne
   * Staging-Zeilen die einzige Quelle sein kann. source_columns wurde vom
   * Server immer geliefert und im Client nie angezeigt.
   */
  const mappingColumnChoices: string[] =
    rawColumns.length > 0 ? rawColumns : (suggestion?.source_columns ?? []);

  /**
   * Warum das erneute Einlesen gerade nicht geht. Serverseitig erlaubt sind
   * 'ready', 'needs_mapping' und 'failed' - laufende und uebernommene Laeufe
   * lehnt der Server mit 409 ab, das soll der Nutzer vorher wissen.
   */
  const reparseBlockedReason: string | null = !currentRun
    ? "Es ist kein Importlauf ausgewählt."
    : currentRun.status === "completed" || (currentRun.committed_count ?? 0) > 0
      ? "Aus diesem Import wurden bereits Buchungen übernommen. Zum Korrigieren zuerst die Buchungen über die Historie entfernen."
      : isCancellable(currentRun) || currentRun.status === "committing"
        ? "Der Lauf wird gerade verarbeitet."
        : currentRun.status === "cancelled"
          ? "Der Import wurde abgebrochen. Bitte die Datei erneut hochladen."
          : activeRowsQuery.isSuccess && allRows.length === 0
            ? "Für diese Zuordnung die Datei erneut hochladen."
            : null;

  // Selection handlers
  const toggleRowSelection = (rowId: string) => {
    setSelectionExplicit(true);
    setSelectedRowIds((prev) => {
      const next = new Set(prev);
      if (next.has(rowId)) {
        next.delete(rowId);
      } else {
        next.add(rowId);
      }
      return next;
    });
  };

  const handleSelectAllFiltered = () => {
    const uncommittedFiltered = filteredRows.filter(
      (r) => r.status !== "committed" && r.status !== "skipped" && r.status !== "error",
    );
    setSelectionExplicit(true);
    const next = new Set(selectedRowIds);
    uncommittedFiltered.forEach((r) => next.add(r.id));
    setSelectedRowIds(next);
  };

  const handleSelectOnlyNew = () => {
    const newRowIds = allRows.filter((r) => r.status === "new").map((r) => r.id);
    setSelectionExplicit(true);
    setSelectedRowIds(new Set(newRowIds));
  };

  const handleDeselectAll = () => {
    setSelectionExplicit(true);
    setSelectedRowIds(new Set());
  };

  const handleAssignAccount = (e: FormEvent) => {
    e.preventDefault();
    if (!currentRunId || !assignAccountId) return;
    setAssignError(null);
    setAssignMessage(null);
    assignAccountMutation.mutate({ runId: currentRunId, accountId: assignAccountId });
  };

  /**
   * Entfernen mit Rueckfrage: der Vorgang ist unwiderruflich und wirft die
   * eingelesenen Zeilen weg.
   */
  const handleDeleteRun = (run: ImportRun) => {
    const confirmed = window.confirm(
      `Importlauf „${run.filename}“ wirklich entfernen? Die ${run.total_rows} eingelesenen ` +
        "Zeilen werden dabei verworfen. Das lässt sich nicht rückgängig machen.",
    );
    if (!confirmed) return;
    setDeleteError(null);
    setDeleteMessage(null);
    deleteRunMutation.mutate({ runId: run.id, filename: run.filename });
  };

  /**
   * Vorschau auf das Zuruecknehmen. Laeuft erst, wenn der Dialog offen ist -
   * und ohne Zwischenspeicher, weil sich der Bestand zwischen zwei Aufrufen
   * geaendert haben kann (eine Buchung bearbeitet, einem Vertrag zugeordnet)
   * und der Dialog dann eine Zahl zeigen wuerde, die nicht mehr gilt.
   */
  const revertPreviewQuery = useQuery({
    queryKey: ["import-revert-preview", selectedHouseholdId, revertRun?.id],
    queryFn: () =>
      previewImportRevert(
        accessToken as string,
        selectedHouseholdId as string,
        revertRun?.id as string,
      ),
    enabled: !!accessToken && !!selectedHouseholdId && !!revertRun,
    staleTime: 0,
    gcTime: 0,
  });

  const revertRunMutation = useMutation({
    mutationFn: (vars: { runId: string; filename: string; includeLinked: boolean }) =>
      revertImportRun(
        accessToken as string,
        selectedHouseholdId as string,
        vars.runId,
        vars.includeLinked,
      ),
    onSuccess: (result, vars) => {
      setRevertError(null);
      setRevertRun(null);
      const behalten =
        result.kept > 0 ? ` ${result.kept} blieben stehen, weil daran inzwischen etwas hängt.` : "";
      // Was beim Mitnehmen geloest wurde, gehoert in die Meldung: eine wieder
      // offene Vertragsfaelligkeit entdeckt der Nutzer sonst erst Wochen
      // spaeter und kann sie dann nicht mehr zuordnen.
      const geloest = (result.untangled ?? []).length
        ? " Dabei gelöst: " +
          (result.untangled ?? []).map((item) => `${item.count} ${item.label}`).join(", ") +
          "."
        : "";
      setRevertMessage(
        `Aus „${vars.filename}“ wurden ${result.reverted} Buchungen entfernt.${behalten}${geloest} ` +
          "Die Datei lässt sich jetzt erneut einlesen.",
      );
      // Der Buchungsbestand aendert sich - alles, was darauf aufbaut, muss neu
      // geladen werden.
      void queryClient.invalidateQueries({ queryKey: ["transactions", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["budget", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["overviewReport", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["review", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["imports", selectedHouseholdId] });
      // Beim Mitnehmen verknuepfter Buchungen aendern sich auch Vertraege,
      // Erstattungen und Einkaeufe - sie stuenden sonst mit Zahlen da, die es
      // seit diesem Klick nicht mehr gibt.
      void queryClient.invalidateQueries({ queryKey: ["contracts", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["reimbursements", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["accounts", selectedHouseholdId] });
      void queryClient.invalidateQueries({
        queryKey: ["import-run", selectedHouseholdId, vars.runId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["import-rows", selectedHouseholdId, vars.runId],
      });
    },
    onError: (err: unknown) => {
      setRevertMessage(null);
      setRevertError(
        "Die Buchungen wurden nicht entfernt: " +
          errorText(err, "Der Vorgang wurde vom Server abgelehnt."),
      );
    },
  });

  /**
   * Was der Dialog anzeigt und der Knopf bestaetigt, haengt am Haken: ohne ihn
   * nur die unverknuepften Buchungen, mit ihm alle. Beide Zahlenwerke stecken in
   * derselben Vorschauantwort - das Umschalten laedt also nichts nach und kann
   * dabei auch keinen anderen Bestand erwischen.
   */
  const revertPreview = revertPreviewQuery.data;
  const revertAnzahl = revertIncludeLinked
    ? (revertPreview?.transactions_total ?? 0)
    : (revertPreview?.revertable ?? 0);
  const revertSumme = revertIncludeLinked
    ? (revertPreview?.sum_minor_with_linked ?? 0)
    : (revertPreview?.sum_minor ?? 0);
  const revertVon = revertIncludeLinked
    ? revertPreview?.from_date_with_linked
    : revertPreview?.from_date;
  const revertBis = revertIncludeLinked
    ? revertPreview?.to_date_with_linked
    : revertPreview?.to_date;

  /**
   * Abbrechen ohne Rueckfrage - anders als das Entfernen wirft es nichts weg,
   * was der Nutzer schon hat: der Lauf wurde ja gerade nicht verarbeitet.
   */
  const handleCancelRun = (run: ImportRun) => {
    setCancelError(null);
    setCancelMessage(null);
    cancelRunMutation.mutate({ runId: run.id, filename: run.filename });
  };

  const handleCommitSelected = () => {
    if (!currentRunId || commitMutation.isPending || commitBlockReason || eligibleCount === 0)
      return;
    const idsToCommit = selectionExplicit ? selectedEligible.map((row) => row.id) : [];
    setCommitError(null);
    commitMutation.mutate({
      runId: currentRunId,
      rowIds: idsToCommit.length > 0 ? idsToCommit : undefined,
    });
  };

  const targetAccount = currentRun?.account_id
    ? (accounts.find((a) => a.id === currentRun.account_id) ?? null)
    : null;
  const selectedEligible = allRows.filter(
    (row) => selectedRowIds.has(row.id) && !["error", "committed", "skipped"].includes(row.status),
  );
  const defaultEligible = allRows.filter(
    (row) => row.status === "new" || row.status === "transfer_candidate",
  );
  const eligibleCount = selectionExplicit ? selectedEligible.length : defaultEligible.length;
  const commitBlockReason = currentRun
    ? (commitBlockedReason(currentRun) ??
      (mappingDirty ? "Zuordnung zuerst anwenden oder Änderungen verwerfen." : null))
    : null;
  const mappingPreview = sourceRows
    .slice(0, 5)
    .map((row) => ({ ...previewRow(row, mappingDraft), id: row.id }));
  const mappingInvalid =
    !mappingDraft.booked_date ||
    !mappingDraft.amount ||
    !mappingColumnChoices.includes(mappingDraft.booked_date) ||
    !mappingColumnChoices.includes(mappingDraft.amount) ||
    mappingPreview.length === 0 ||
    mappingPreview.some((row) => row.date === null || row.amount === null);
  const counts = {
    all: allRows.length,
    new: allRows.filter((row) => row.status === "new").length,
    duplicates: allRows.filter(
      (row) => row.status === "duplicate_exact" || row.status === "duplicate_possible",
    ).length,
    transfers: allRows.filter((row) => row.status === "transfer_candidate").length,
    errors: allRows.filter((row) => row.status === "error").length,
    committed: allRows.filter((row) => row.status === "committed").length,
  };

  // A processing -> terminal transition must fetch rows even if this run was viewed earlier.
  useEffect(() => {
    const previous = previousRun.current;
    const status = activeRunQuery.data?.status;
    if (previous.id !== currentRunId) {
      setSelectedRowIds(new Set());
      setSelectionExplicit(false);
      setRowFilter("all");
      setShowMappingForm(false);
      setMappingDraft({});
      setMappingDirty(false);
      appliedMapping.current = {};
      setProfileName("");
      setReplaceProfileId("");
      setMappingSuccessMsg(null);
      setMappingFormError(null);
      setReparseError(null);
      setReparseMessage(null);
      setSuggestion(null);
      setSuggestionError(null);
      setSuggestionApplied(false);
      draftScope.current = null;
    }
    if (
      status &&
      status !== previous.status &&
      !["pending", "analysing", "committing"].includes(status)
    ) {
      void queryClient.invalidateQueries({
        queryKey: ["import-rows", selectedHouseholdId, currentRunId],
      });
      void queryClient.invalidateQueries({ queryKey: ["imports", selectedHouseholdId] });
    }
    previousRun.current = { id: currentRunId, status };
  }, [currentRunId, activeRunQuery.data?.status, queryClient, selectedHouseholdId]);

  const columnsKey = JSON.stringify(rawColumns);
  useEffect(() => {
    if (currentRunId && draftScope.current !== currentRunId && columnsKey !== "[]") {
      const initial = inferMapping(JSON.parse(columnsKey) as string[]);
      setMappingDraft(initial);
      appliedMapping.current = initial;
      draftScope.current = currentRunId;
    }
  }, [currentRunId, columnsKey]);

  const resetFile = () => {
    setSelectedFile(null);
    setFileInputKey((key) => key + 1);
    setSelectedMappingProfileId("");
    setUploadError(null);
  };
  const mappingOpen = showMappingForm || currentRun?.status === "needs_mapping";
  const completed = currentRun?.status === "completed";
  const closeMapping = () => {
    setMappingDraft(appliedMapping.current);
    setMappingDirty(false);
    setShowMappingForm(false);
    setMappingSuccessMsg(null);
  };

  return (
    <div className="import-page import-workspace">
      <header className="page-header import-page-header">
        <div>
          <h1 className="page-heading">Importzentrum</h1>
          <p className="import-muted">Kontoumsätze einlesen und vor der Übernahme prüfen.</p>
        </div>
        <ol className="import-steps" aria-label="Importschritte">
          <li aria-current={!currentRun || selectedFile ? "step" : undefined}>
            <span>1</span>Datei
          </li>
          <li aria-current={currentRun && !completed && !selectedFile ? "step" : undefined}>
            <span>2</span>Prüfen
          </li>
          <li aria-current={completed && !selectedFile ? "step" : undefined}>
            <span>{completed ? "✓" : "3"}</span>Übernehmen
          </li>
        </ol>
      </header>

      <section className="card import-upload" aria-labelledby="import-upload-title">
        <div className="import-section-heading">
          <h2 id="import-upload-title">Datei importieren</h2>
          <span className="import-muted">CSV · Excel · MT940 · CAMT · PayPal</span>
        </div>
        <form onSubmit={handleUploadSubmit}>
          <div className="import-upload-grid">
            <div className="form-field">
              <label className="form-label" htmlFor="import-account-select">
                Zielkonto
              </label>
              <select
                id="import-account-select"
                className="form-input"
                value={selectedAccountId}
                disabled={uploadMutation.isPending}
                onChange={(event) => {
                  setSelectedAccountId(event.target.value);
                  setSelectedMappingProfileId("");
                  setUploadError(null);
                }}
              >
                <option value="">Konto wählen</option>
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name}
                    {account.institution ? ` · ${account.institution}` : ""}
                  </option>
                ))}
              </select>
              {accountsQuery.isError && (
                <p className="status-error" role="alert">
                  Konten konnten nicht geladen werden.
                </p>
              )}
              {!accountsQuery.isPending && !accountsQuery.isError && accounts.length === 0 && (
                <p className="import-muted">
                  Zuerst unter <Link to="/konten">Konten</Link> ein Konto anlegen.
                </p>
              )}
            </div>
            <div
              className={`import-dropzone ${dragging ? "is-dragging" : ""} ${selectedFile ? "has-file" : ""}`}
              onDragOver={(event) => {
                event.preventDefault();
                if (!uploadMutation.isPending) setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                if (!uploadMutation.isPending) {
                  setSelectedFile(event.dataTransfer.files[0] ?? null);
                  setFileInputKey((key) => key + 1);
                  setSelectedMappingProfileId("");
                  setUploadError(null);
                  setLastUploadedName(null);
                }
              }}
            >
              <input
                key={fileInputKey}
                ref={fileInputRef}
                type="file"
                id="import-file-input"
                className="import-file-input"
                aria-label="Datei auswählen"
                accept=".csv,.xlsx,.xls,.txt,.sta,.mt940,.xml"
                disabled={uploadMutation.isPending}
                onChange={handleFileChange}
              />
              <span className="import-file-icon" aria-hidden="true">
                ↑
              </span>
              <div className="import-file-copy">
                <strong title={selectedFile?.name}>
                  {selectedFile?.name ?? "Datei hier ablegen"}
                </strong>
                <span>
                  {selectedFile
                    ? formatFileSize(selectedFile.size)
                    : "oder über den Dateidialog auswählen"}
                </span>
              </div>
              <button
                type="button"
                className="btn-secondary"
                disabled={uploadMutation.isPending}
                onClick={() => fileInputRef.current?.click()}
              >
                {selectedFile ? "Ändern" : "Datei wählen"}
              </button>
              {selectedFile && (
                <button
                  type="button"
                  className="import-icon-button"
                  aria-label="Datei entfernen"
                  disabled={uploadMutation.isPending}
                  onClick={resetFile}
                >
                  ×
                </button>
              )}
            </div>
            <button
              type="submit"
              className="btn-primary import-analyse"
              disabled={uploadMutation.isPending}
            >
              {uploadMutation.isPending ? "Wird hochgeladen …" : "Datei analysieren"}
            </button>
          </div>
          <div aria-live="polite">
            {uploadMutation.isPending && (
              <p className="import-progress">
                <span className="import-spinner" />
                Datei wird übertragen. Die Analyse startet anschließend automatisch.
              </p>
            )}
            {uploadError && (
              <p className="status-error" role="alert">
                {uploadError}
              </p>
            )}
            {lastUploadedName && !selectedFile && !uploadMutation.isPending && (
              <p className="import-muted import-upload-success">
                ✓ <span title={lastUploadedName}>{lastUploadedName}</span> hochgeladen
              </p>
            )}
          </div>
          <details className="import-details import-profile-manager">
            <summary>
              Gespeicherte Zuordnungen{" "}
              <span className="import-muted">
                {(mappingProfilesQuery.data ?? []).length > 0
                  ? `(${mappingProfilesQuery.data?.length})`
                  : "(keine)"}
              </span>
              {selectedMappingProfileId && (
                <span className="status-badge status-badge--highlight">
                  Für diesen Upload ausgewählt
                </span>
              )}
            </summary>
            <div className="import-profile-content">
              <label className="form-label" htmlFor="import-mapping-profile-select">
                Zuordnung für diese Datei
              </label>
              <select
                id="import-mapping-profile-select"
                className="form-input"
                value={selectedMappingProfileId}
                disabled={uploadMutation.isPending}
                onChange={(event) => setSelectedMappingProfileId(event.target.value)}
              >
                <option value="">Automatisch erkennen</option>
                {(mappingProfilesQuery.data ?? []).map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name} · {formatLabel(FORMAT_LABELS, profile.bank_format)}
                  </option>
                ))}
              </select>
              {selectedMappingProfileId && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={uploadMutation.isPending}
                  onClick={() => setSelectedMappingProfileId("")}
                >
                  Automatische Erkennung verwenden
                </button>
              )}
              {(mappingProfilesQuery.data ?? []).map((profile) => (
                <div className="import-profile-row" key={profile.id}>
                  <div>
                    <strong>{profile.name}</strong>
                    <span className="import-muted">
                      {formatLabel(FORMAT_LABELS, profile.bank_format)}
                    </span>
                  </div>
                  {profileToDelete === profile.id ? (
                    <div className="import-actions">
                      <span className="import-muted">Nur die Zuordnung wird gelöscht.</span>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={deleteProfileMutation.isPending}
                        onClick={() => setProfileToDelete(null)}
                      >
                        Abbrechen
                      </button>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={deleteProfileMutation.isPending}
                        onClick={() => deleteProfileMutation.mutate(profile.id)}
                      >
                        {deleteProfileMutation.isPending ? "Wird gelöscht …" : "Löschen bestätigen"}
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="btn-secondary"
                      aria-label={`Zuordnung ${profile.name} löschen`}
                      disabled={deleteProfileMutation.isPending || uploadMutation.isPending}
                      onClick={() => {
                        setProfileError(null);
                        setProfileMessage(null);
                        setProfileToDelete(profile.id);
                      }}
                    >
                      {deleteProfileMutation.isPending &&
                      deleteProfileMutation.variables === profile.id
                        ? "Wird gelöscht …"
                        : "Löschen"}
                    </button>
                  )}
                </div>
              ))}
              {mappingProfilesQuery.isError && (
                <p className="status-error" role="alert">
                  Zuordnungen konnten nicht geladen werden.
                </p>
              )}
              {profileError && (
                <p className="status-error" role="alert">
                  {profileError}
                </p>
              )}
              {profileMessage && (
                <p className="status-success" role="status">
                  {profileMessage}
                </p>
              )}
            </div>
          </details>
        </form>
      </section>

      {currentRun ? (
        <section
          className="card import-review"
          aria-labelledby="import-review-title"
          aria-busy={isRunProcessing || reparseMutation.isPending}
        >
          <div className="import-section-heading import-review-heading">
            <div className="import-review-name">
              <span className="import-eyebrow">
                {completed ? "Import abgeschlossen" : "Buchungen prüfen"}
              </span>
              <h2 id="import-review-title" title={currentRun.filename}>
                {currentRun.filename}
              </h2>
              <p className="import-muted">
                {targetAccount?.name ??
                  (currentRun.account_id ? "Zugewiesenes Konto" : "Kein Zielkonto")}{" "}
                <span aria-hidden="true">·</span>{" "}
                {currentRun.detected_format
                  ? formatLabel(FORMAT_LABELS, currentRun.detected_format)
                  : "Format wird erkannt"}{" "}
                <span aria-hidden="true">·</span> {formatFileSize(currentRun.file_size_bytes)}
              </p>
            </div>
            <div className="import-actions">
              <span
                className={`status-badge status-badge--${completed ? "positive" : currentRun.status === "failed" ? "error" : "highlight"}`}
              >
                {RUN_STATUS_LABELS[currentRun.status]}
              </span>
              {!completed && !isRunProcessing && (
                <button
                  type="button"
                  className="btn-secondary"
                  aria-expanded={mappingOpen}
                  onClick={() => (mappingOpen ? closeMapping() : setShowMappingForm(true))}
                >
                  Spalten zuordnen
                </button>
              )}
            </div>
          </div>
          {!currentRun.account_id && (
            <div className="import-notice">
              <p>Diesem Importlauf ist kein Zielkonto zugewiesen.</p>
              {currentRun.status === "ready" ? (
                accounts.length === 0 ? (
                  <p>
                    In diesem Haushalt existiert noch kein Konto. Unter{" "}
                    <Link to="/konten">Konten</Link> anlegen.
                  </p>
                ) : (
                  <form onSubmit={handleAssignAccount} className="import-inline-form">
                    <label className="form-label" htmlFor="assign-account-select">
                      Zielkonto nachträglich zuweisen
                    </label>
                    <select
                      id="assign-account-select"
                      className="form-input"
                      value={assignAccountId}
                      onChange={(event) => setAssignAccountId(event.target.value)}
                    >
                      <option value="">Konto wählen</option>
                      {accounts.map((account) => (
                        <option key={account.id} value={account.id}>
                          {account.name}
                        </option>
                      ))}
                    </select>
                    <button
                      className="btn-primary"
                      disabled={!assignAccountId || assignAccountMutation.isPending}
                    >
                      Zielkonto zuweisen
                    </button>
                  </form>
                )
              ) : (
                <p className="import-muted">
                  Nachtragen lässt sich das Zielkonto nur vor der Übernahme im Status „Bereit zur
                  Übernahme“.
                </p>
              )}
            </div>
          )}
          {assignError && (
            <p className="status-error" role="alert">
              {assignError}
            </p>
          )}
          {assignMessage && (
            <p className="status-success" role="status">
              {assignMessage}
            </p>
          )}
          {currentRun.error_message && (
            <p className="status-error" role="alert">
              {currentRun.error_message}
            </p>
          )}
          {activeRunQuery.isError && (
            <p className="status-error" role="alert">
              Aktualisierung fehlgeschlagen:{" "}
              {errorText(activeRunQuery.error, "Bitte erneut versuchen.")}{" "}
              <button className="btn-secondary" onClick={() => void activeRunQuery.refetch()}>
                Erneut versuchen
              </button>
            </p>
          )}
          {isRunProcessing ? (
            <div className="import-processing" role="status">
              <span
                className={runIsStale || workerIsDown ? "import-processing-mark" : "import-spinner"}
                aria-hidden="true"
              >
                {runIsStale || workerIsDown ? "!" : ""}
              </span>
              <div>
                <strong>
                  {runIsStale
                    ? "Verarbeitung unterbrochen"
                    : workerIsDown
                      ? "Die Verarbeitung steht still"
                      : currentRun.status === "committing"
                        ? "Buchungen werden übernommen"
                        : "Datei wird analysiert"}
                </strong>
                <p className="import-muted">
                  {runIsStale || workerIsDown
                    ? "Die Datei bleibt gespeichert. Bitte später erneut versuchen."
                    : `${currentRunAge === null ? "" : `Seit ${formatElapsed(currentRunAge)} · `}Diese Ansicht aktualisiert sich automatisch.`}
                </p>
                {workerIsDown && workerHealth?.detail && (
                  <p className="import-muted">{workerHealth.detail}</p>
                )}
              </div>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  void activeRunQuery.refetch();
                  void systemHealthQuery.refetch();
                }}
              >
                Aktualisieren
              </button>
            </div>
          ) : (
            <>
              {mappingOpen && (
                <div className="import-mapping-panel">
                  <div className="import-section-heading">
                    <div>
                      <h3>Spalten zuordnen</h3>
                      <p className="import-muted">
                        Spalte wählen. Das Beispiel und die Vorschau ändern sich sofort.
                      </p>
                    </div>
                    {currentRun.status !== "needs_mapping" && (
                      <button
                        className="import-icon-button"
                        type="button"
                        aria-label="Spaltenzuordnung schließen"
                        onClick={closeMapping}
                      >
                        ×
                      </button>
                    )}
                  </div>
                  <div className="import-mapping-layout">
                    <div className="import-mapping-fields">
                      {MAPPING_FIELD_ORDER.map(({ key, label, required }) => {
                        const example = sourceRows
                          .map((row) => String(row.raw_data?.[mappingDraft[key] ?? ""] ?? ""))
                          .find((value) => value.trim());
                        const invalid =
                          required &&
                          mappingPreview.length > 0 &&
                          mappingPreview.some((row) =>
                            key === "booked_date" ? row.date === null : row.amount === null,
                          );
                        return (
                          <div className="import-mapping-field" key={key}>
                            <label className="form-label" htmlFor={`mapping-col-${key}`}>
                              {label}
                              {required && <span className="import-required"> (erforderlich)</span>}
                            </label>
                            <select
                              id={`mapping-col-${key}`}
                              className="form-input"
                              value={mappingDraft[key] ?? ""}
                              disabled={reparseMutation.isPending}
                              aria-invalid={invalid}
                              aria-describedby={`mapping-example-${key}`}
                              onChange={(event) => {
                                setMappingDirty(true);
                                setMappingDraft((previous) => ({
                                  ...previous,
                                  [key]: event.target.value,
                                }));
                                setMappingSuccessMsg(null);
                              }}
                            >
                              <option value="">
                                {required ? "Spalte wählen" : "Nicht zuordnen"}
                              </option>
                              {mappingColumnChoices.map((column) => (
                                <option key={column} value={column}>
                                  {column}
                                </option>
                              ))}
                            </select>
                            <div
                              id={`mapping-example-${key}`}
                              className={`import-source-example ${invalid ? "status-error" : ""}`}
                              title={example}
                            >
                              {example || (mappingDraft[key] ? "Kein Beispielwert vorhanden" : "—")}
                              {invalid && (
                                <span>
                                  {" "}
                                  · {key === "booked_date" ? "Datum prüfen" : "Betrag prüfen"}
                                </span>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    <div
                      className="import-live-preview"
                      aria-label="Live-Vorschau"
                      aria-live="polite"
                    >
                      <div className="import-preview-label">
                        <span className="import-live-dot" />
                        Live-Vorschau{" "}
                        <span className="import-muted">Erste {mappingPreview.length} Zeilen</span>
                      </div>
                      <div className="import-table-scroll">
                        <table className="txn-table">
                          <thead>
                            <tr>
                              <th className="txn-th">Datum</th>
                              <th className="txn-th">Empfänger</th>
                              <th className="txn-th">Verwendungszweck</th>
                              <th className="txn-th txn-th--amount">Betrag</th>
                            </tr>
                          </thead>
                          <tbody>
                            {mappingPreview.map((row) => (
                              <tr className="txn-tr" key={row.id}>
                                <td className="txn-td">
                                  {row.date ?? <span className="status-error">Datum prüfen</span>}
                                </td>
                                <td className="txn-td">{row.payee || "—"}</td>
                                <td className="txn-td">{row.purpose || "—"}</td>
                                <td className="txn-td txn-td--amount">
                                  {row.amount === null ? (
                                    <span className="status-error">Betrag prüfen</span>
                                  ) : (
                                    formatMinorUnitsAsEuro(row.amount)
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {mappingPreview.length === 0 && (
                        <p className="import-muted">
                          Noch keine Rohzeilen verfügbar. Bitte die Datei erneut hochladen.
                        </p>
                      )}
                      <p className="import-muted import-preview-note">
                        Entwurf · Wird erst mit „Zuordnung anwenden“ eingelesen.
                      </p>
                    </div>
                  </div>
                  <div className="import-mapping-footer">
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => {
                        setMappingDirty(true);
                        setMappingDraft(inferMapping(mappingColumnChoices));
                        setMappingSuccessMsg(null);
                      }}
                    >
                      Automatisch zuordnen
                    </button>
                    <div className="import-actions">
                      {mappingInvalid && (
                        <span className="import-muted">
                          Datum und Betrag in der Vorschau prüfen.
                        </span>
                      )}
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={
                          mappingInvalid || reparseMutation.isPending || !!reparseBlockedReason
                        }
                        onClick={handleReparse}
                      >
                        {reparseMutation.isPending ? "Wird angewendet …" : "Zuordnung anwenden"}
                      </button>
                    </div>
                  </div>
                  {reparseBlockedReason && <p className="import-muted">{reparseBlockedReason}</p>}
                  <details className="import-details">
                    <summary>Zuordnung speichern oder laden</summary>
                    <div className="import-profile-content">
                      {(mappingProfilesQuery.data ?? []).length > 0 && (
                        <div className="import-actions">
                          <label className="form-label" htmlFor="draft-profile-select">
                            Gespeicherte Zuordnung laden
                          </label>
                          <select
                            id="draft-profile-select"
                            className="form-input"
                            defaultValue=""
                            onChange={(event) => {
                              const profile = mappingProfilesQuery.data?.find(
                                (item) => item.id === event.target.value,
                              );
                              if (profile) {
                                setMappingDirty(true);
                                setMappingDraft(
                                  Object.fromEntries(
                                    MAPPING_FIELD_ORDER.map(({ key }) => [
                                      key,
                                      profile.column_mapping?.[key] ?? "",
                                    ]),
                                  ),
                                );
                                setMappingSuccessMsg(null);
                              }
                              event.target.value = "";
                            }}
                          >
                            <option value="">Zuordnung wählen</option>
                            {mappingProfilesQuery.data?.map((profile) => (
                              <option key={profile.id} value={profile.id}>
                                {profile.name}
                              </option>
                            ))}
                          </select>
                        </div>
                      )}
                      {(mappingProfilesQuery.data ?? []).length > 0 && (
                        <div>
                          <label className="form-label" htmlFor="save-profile-select">
                            Speichern als
                          </label>
                          <select
                            id="save-profile-select"
                            className="form-input"
                            value={replaceProfileId}
                            onChange={(event) => {
                              setReplaceProfileId(event.target.value);
                              setProfileName(
                                mappingProfilesQuery.data?.find(
                                  (profile) => profile.id === event.target.value,
                                )?.name ?? "",
                              );
                              setMappingSuccessMsg(null);
                            }}
                          >
                            <option value="">Neue Zuordnung</option>
                            {mappingProfilesQuery.data?.map((profile) => (
                              <option key={profile.id} value={profile.id}>
                                {profile.name} ersetzen
                              </option>
                            ))}
                          </select>
                        </div>
                      )}
                      <form onSubmit={handleSaveMappingProfile} className="import-inline-form">
                        <label className="form-label" htmlFor="mapping-profile-name">
                          Name
                        </label>
                        <input
                          id="mapping-profile-name"
                          className="form-input"
                          placeholder="z. B. MLP Girokonto"
                          value={profileName}
                          onChange={(event) => setProfileName(event.target.value)}
                        />
                        <button
                          type="submit"
                          className="btn-secondary"
                          disabled={
                            !profileName.trim() ||
                            mappingInvalid ||
                            createMappingProfileMutation.isPending ||
                            reparseMutation.isPending
                          }
                        >
                          {createMappingProfileMutation.isPending
                            ? "Wird gespeichert …"
                            : replaceProfileId
                              ? "Zuordnung ersetzen"
                              : "Zuordnung speichern"}
                        </button>
                      </form>
                      <p className="import-muted">Nur auf Wunsch beim nächsten Upload verwenden.</p>
                      {mappingFormError && (
                        <p className="status-error" role="alert">
                          {mappingFormError}
                        </p>
                      )}
                      {mappingSuccessMsg && (
                        <p className="status-success" role="status">
                          {mappingSuccessMsg}
                        </p>
                      )}
                    </div>
                  </details>
                  <details className="import-details">
                    <summary>Hilfe bei der Zuordnung</summary>
                    <div className="import-profile-content">
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={suggestionMutation.isPending}
                        onClick={() => currentRunId && suggestionMutation.mutate(currentRunId)}
                      >
                        {suggestionMutation.isPending
                          ? "Vorschlag wird erstellt …"
                          : "Spaltenvorschlag von der lokalen KI"}
                      </button>
                      {suggestionError && (
                        <p className="status-error" role="alert">
                          {suggestionError}
                        </p>
                      )}
                      {suggestion &&
                        (suggestion.available && suggestion.reason === "ok" ? (
                          <div className="import-notice">
                            <strong>KI-Vorschlag</strong>
                            <p className="import-muted">
                              {Object.entries(suggestion.column_mapping ?? {})
                                .map(
                                  ([key, value]) =>
                                    `${MAPPING_FIELD_ORDER.find((field) => field.key === key)?.label ?? key}: ${value}`,
                                )
                                .join(" · ")}
                            </p>
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={handleApplySuggestion}
                            >
                              {suggestionApplied
                                ? "Erneut ins Formular übernehmen"
                                : "Vorschlag ins Formular übernehmen"}
                            </button>
                            <details className="import-details">
                              <summary>Vorschlagsdetails</summary>
                              <p className="import-muted">
                                {suggestion.model_name} · Datum:{" "}
                                {suggestion.date_format ?? "automatisch"} · Dezimalzeichen:{" "}
                                {suggestion.decimal_separator ?? "automatisch"}
                              </p>
                            </details>
                          </div>
                        ) : (
                          <AiReasonNotice
                            className="import-muted"
                            prefix="Kein KI-Vorschlag verfügbar:"
                            code={suggestion.reason}
                            message={suggestion.message}
                          />
                        ))}
                    </div>
                  </details>
                </div>
              )}
              {reparseError && (
                <p className="status-error" role="alert">
                  {reparseError}
                </p>
              )}
              {reparseMessage && (
                <p className="status-success" role="status">
                  {reparseMessage}
                </p>
              )}
              {completed && (
                <div className="import-completed" role="status">
                  <span aria-hidden="true">✓</span>
                  <div>
                    <strong>
                      {currentRun.committed_count ?? counts.committed} Buchungen übernommen
                    </strong>
                    <p className="import-muted">Der Import ist abgeschlossen.</p>
                  </div>
                  <Link className="btn-secondary" to="/buchungen">
                    Buchungen ansehen
                  </Link>
                </div>
              )}
              {commitMessage && !completed && (
                <p className="status-success" role="status">
                  {commitMessage}
                </p>
              )}
              {commitError && (
                <p className="status-error" role="alert">
                  {commitError}
                </p>
              )}
              <div className="import-review-toolbar">
                <div className="import-filter-tabs" aria-label="Buchungen filtern">
                  {(
                    [
                      ["all", "Alle"],
                      ["new", "Neu"],
                      ["duplicates", "Dubletten"],
                      ["transfers", "Transfers"],
                      ["errors", "Fehler"],
                    ] as [RowFilterMode, string][]
                  ).map(([filter, label]) => (
                    <button
                      type="button"
                      key={filter}
                      className={`import-filter ${rowFilter === filter ? "is-active" : ""}`}
                      aria-pressed={rowFilter === filter}
                      onClick={() => setRowFilter(filter)}
                    >
                      {label}
                      <span>{counts[filter]}</span>
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  className="btn-secondary"
                  aria-pressed={showRawColumns}
                  onClick={() => setShowRawColumns(!showRawColumns)}
                >
                  {showRawColumns ? "Buchungsansicht" : "Originalspalten"}
                </button>
              </div>
              <div className="import-table-scroll import-rows-scroll">
                <table className="txn-table" aria-label="Importierte Zeilen">
                  <thead>
                    <tr>
                      <th className="txn-th">
                        <input
                          type="checkbox"
                          aria-label="Alle sichtbaren auswählen"
                          disabled={
                            completed ||
                            !filteredRows.some(
                              (row) => !["error", "committed", "skipped"].includes(row.status),
                            )
                          }
                          checked={
                            filteredRows.some(
                              (row) => !["error", "committed", "skipped"].includes(row.status),
                            ) &&
                            filteredRows
                              .filter(
                                (row) => !["error", "committed", "skipped"].includes(row.status),
                              )
                              .every((row) => selectedRowIds.has(row.id))
                          }
                          onChange={(event) =>
                            event.target.checked ? handleSelectAllFiltered() : handleDeselectAll()
                          }
                        />
                      </th>
                      <th className="txn-th">#</th>
                      {showRawColumns ? (
                        rawColumns.map((column) => (
                          <th className="txn-th" key={column}>
                            {column}
                          </th>
                        ))
                      ) : (
                        <>
                          <th className="txn-th">Datum</th>
                          <th className="txn-th">Empfänger</th>
                          <th className="txn-th">Verwendungszweck</th>
                          <th className="txn-th txn-th--amount">Betrag</th>
                        </>
                      )}
                      <th className="txn-th">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeRowsQuery.isPending ||
                    (activeRowsQuery.isFetching && !allRows.length) ? (
                      <tr>
                        <td className="txn-td import-table-empty" colSpan={tableColSpan}>
                          Buchungen werden geladen …
                        </td>
                      </tr>
                    ) : activeRowsQuery.isError ? (
                      <tr>
                        <td className="txn-td import-table-empty" colSpan={tableColSpan}>
                          <span role="alert">Buchungen konnten nicht geladen werden.</span>{" "}
                          <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => void activeRowsQuery.refetch()}
                          >
                            Erneut versuchen
                          </button>
                        </td>
                      </tr>
                    ) : filteredRows.length === 0 ? (
                      <tr>
                        <td className="txn-td import-table-empty" colSpan={tableColSpan}>
                          {allRows.length
                            ? "Keine Buchungen in diesem Filter."
                            : "Noch keine Buchungen verfügbar."}
                        </td>
                      </tr>
                    ) : (
                      filteredRows.map((row) => (
                        <tr
                          key={row.id}
                          className={`txn-tr ${selectedRowIds.has(row.id) ? "txn-tr--selected" : ""}`}
                        >
                          <td className="txn-td">
                            <input
                              type="checkbox"
                              aria-label={`Zeile ${row.row_number} auswählen`}
                              checked={selectedRowIds.has(row.id)}
                              disabled={
                                completed || ["error", "committed", "skipped"].includes(row.status)
                              }
                              onChange={() => toggleRowSelection(row.id)}
                            />
                          </td>
                          <td className="txn-td import-muted">{row.row_number}</td>
                          {showRawColumns ? (
                            rawColumns.map((column) => (
                              <td className="txn-td" key={column}>
                                {String(row.raw_data?.[column] ?? "") || "—"}
                              </td>
                            ))
                          ) : (
                            <>
                              <td className="txn-td import-date">
                                {row.booked_date || row.value_date || "—"}
                              </td>
                              <td className="txn-td txn-payee">{row.payee_raw || "—"}</td>
                              <td className="txn-td txn-note">{row.purpose || "—"}</td>
                              <td
                                className={`txn-td txn-td--amount transaction-amount ${(row.amount_minor ?? 0) < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}`}
                              >
                                {row.amount_minor !== null
                                  ? formatMinorUnitsAsEuro(row.amount_minor)
                                  : "—"}
                              </td>
                            </>
                          )}
                          <td className="txn-td">
                            <span
                              className={`status-badge status-badge--${row.status === "error" ? "error" : row.status.startsWith("duplicate") ? "warning" : row.status === "new" || row.status === "committed" ? "positive" : "neutral"}`}
                            >
                              {ROW_STATUS_LABELS[row.status]}
                            </span>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
              {!completed && (
                <div className="import-commit-bar">
                  <div>
                    <strong>
                      {selectionExplicit
                        ? `${selectedEligible.length} ausgewählt`
                        : `${defaultEligible.length} neue Buchungen bereit`}
                    </strong>
                    {commitBlockReason ? (
                      <p className="status-error">{commitBlockReason}</p>
                    ) : selectionExplicit &&
                      selectedEligible.some((row) => row.status.startsWith("duplicate")) ? (
                      <p className="status-error">
                        Ausgewählte Dubletten werden zusätzlich übernommen.
                      </p>
                    ) : (
                      <p className="import-muted">
                        {selectionExplicit
                          ? "Nur die markierten Zeilen werden übernommen."
                          : "Dubletten bleiben zunächst unberücksichtigt."}
                      </p>
                    )}
                    <div className="import-selection-actions">
                      <button type="button" onClick={handleSelectOnlyNew}>
                        Nur neue auswählen
                      </button>
                      {selectionExplicit && (
                        <button
                          type="button"
                          onClick={() => {
                            setSelectionExplicit(false);
                            setSelectedRowIds(new Set());
                          }}
                        >
                          Standardauswahl
                        </button>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={
                      commitMutation.isPending ||
                      !!commitBlockReason ||
                      eligibleCount === 0 ||
                      activeRowsQuery.isPending ||
                      reparseMutation.isPending
                    }
                    onClick={handleCommitSelected}
                  >
                    {commitMutation.isPending
                      ? "Wird übernommen …"
                      : `${eligibleCount} Buchungen übernehmen`}
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      ) : (
        <section className="card import-empty">
          <span className="import-eyebrow">Bereit für den ersten Import</span>
          <h2>Ihre Buchungen, vor der Übernahme im Blick.</h2>
          <p className="import-muted">
            Zielkonto und Datei auswählen. Anschließend prüfen Sie die Vorschau.
          </p>
        </section>
      )}

      <section className="card import-history" aria-labelledby="import-history-title">
        <div className="import-section-heading">
          <h2 id="import-history-title">
            Import-Historie <span className="import-muted">{runs.length}</span>
          </h2>
          <button
            type="button"
            className="btn-secondary"
            disabled={importRunsQuery.isFetching}
            onClick={() => void importRunsQuery.refetch()}
          >
            Historie neu laden
          </button>
        </div>
        {deleteMessage && (
          <p className="status-success" role="status">
            {deleteMessage}
          </p>
        )}
        {cancelMessage && (
          <p className="status-success" role="status">
            {cancelMessage}
          </p>
        )}
        {revertMessage && (
          <p className="status-success" role="status">
            {revertMessage}
          </p>
        )}
        {[deleteError, cancelError, revertError].filter(Boolean).map((error) => (
          <p key={error} className="status-error" role="alert">
            {error}
          </p>
        ))}
        {importRunsQuery.isError ? (
          <p className="status-error" role="alert">
            Historie konnte nicht geladen werden.
          </p>
        ) : importRunsQuery.isPending ? (
          <p className="import-muted">Historie wird geladen …</p>
        ) : runs.length === 0 ? (
          <p className="import-muted">Noch keine bisherigen Importläufe vorhanden.</p>
        ) : (
          <div className="import-table-scroll">
            <table className="txn-table">
              <thead>
                <tr>
                  <th className="txn-th">Datei</th>
                  <th className="txn-th">Datum</th>
                  <th className="txn-th">Zeilen</th>
                  <th className="txn-th">Status</th>
                  <th className="txn-th">Aktionen</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr
                    className={`txn-tr ${run.id === currentRunId ? "txn-tr--selected" : ""}`}
                    key={run.id}
                  >
                    <td className="txn-td">
                      <span className="import-history-filename" title={run.filename}>
                        {run.filename}
                      </span>
                    </td>
                    <td className="txn-td import-muted">{formatDate(run.created_at)}</td>
                    <td className="txn-td">{run.total_rows}</td>
                    <td className="txn-td">
                      <span
                        className={`status-badge status-badge--${run.status === "completed" ? "positive" : run.status === "failed" ? "error" : "neutral"}`}
                      >
                        {RUN_STATUS_LABELS[run.status]}
                      </span>
                    </td>
                    <td className="txn-td">
                      <div className="import-history-actions">
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => {
                            setActiveRunId(run.id);
                            setCommitMessage(null);
                            setCommitError(null);
                            setAssignAccountId("");
                            setAssignError(null);
                            setAssignMessage(null);
                          }}
                        >
                          Anzeigen
                        </button>
                        {isCancellable(run) ? (
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={cancelRunMutation.isPending}
                            onClick={() => handleCancelRun(run)}
                          >
                            Abbrechen
                          </button>
                        ) : hasActiveTransactions(run) ? (
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={revertRunMutation.isPending}
                            onClick={() => {
                              setRevertError(null);
                              setRevertMessage(null);
                              setRevertIncludeLinked(false);
                              setRevertRun(run);
                            }}
                          >
                            Buchungen entfernen
                          </button>
                        ) : deleteBlockedReason(run) === null ? (
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={deleteRunMutation.isPending}
                            onClick={() => handleDeleteRun(run)}
                          >
                            {deleteRunMutation.isPending &&
                            deleteRunMutation.variables?.runId === run.id
                              ? "Wird entfernt …"
                              : "Entfernen"}
                          </button>
                        ) : (
                          <span
                            className="import-muted"
                            title={deleteBlockedReason(run) ?? undefined}
                          >
                            {run.status === "committing"
                              ? "Wird verarbeitet"
                              : "Als Nachweis gespeichert"}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {revertRun && (
        <Modal title="Buchungen aus dem Import entfernen" onClose={() => setRevertRun(null)}>
          <p className="card-body">
            Die Buchungen aus <strong>{revertRun.filename}</strong> werden aus dem Bestand genommen:
            sie verschwinden aus allen Buchungslisten, aus dem Budget, aus den Auswertungen und aus
            dem Kontosaldo. Dieselbe Datei lässt sich danach erneut einlesen und wird wieder
            vollständig als neu erkannt - der Weg für „der Import war unsauber, nochmal richtig".
          </p>
          <p className="placeholder-note">
            Der Importlauf selbst bleibt in der Historie stehen. Er hält fest, welche Datei wann
            eingelesen wurde, und ist der Herkunftsnachweis der herausgenommenen Buchungen.
          </p>

          {revertPreviewQuery.isPending && <p className="placeholder-note">Wird geprüft ...</p>}

          {revertPreviewQuery.isError && (
            <p className="status-error" role="alert">
              Die Vorschau konnte nicht geladen werden:{" "}
              {errorText(revertPreviewQuery.error, "Der Server hat nicht geantwortet.")}
            </p>
          )}

          {revertPreviewQuery.data && (
            <>
              <div className="status-list">
                <div className="status-row">
                  <span className="status-row-label">Wird entfernt</span>
                  <span className="status-row-detail">
                    {revertAnzahl} von {revertPreviewQuery.data.transactions_total} Buchungen
                  </span>
                </div>
                {revertVon && revertBis && (
                  <div className="status-row">
                    <span className="status-row-label">Zeitraum</span>
                    <span className="status-row-detail">
                      {revertVon} bis {revertBis}
                    </span>
                  </div>
                )}
                <div className="status-row">
                  <span className="status-row-label">Summe</span>
                  <span className="status-row-detail">{formatMinorUnitsAsEuro(revertSumme)}</span>
                </div>
              </div>

              {revertPreviewQuery.data.blocked.length > 0 && (
                <div className="status-list mt-3">
                  <p className="card-body">
                    {revertIncludeLinked
                      ? "An diesen Buchungen hängt Arbeit, die nach dem Import entstanden ist - sie werden mit entfernt:"
                      : "Diese Buchungen bleiben stehen - an ihnen hängt Arbeit, die nach dem Import entstanden ist:"}
                  </p>
                  {revertPreviewQuery.data.blocked.map((blocker) => (
                    <div className="status-row" key={blocker.reason}>
                      <span className="status-row-label">{blocker.label}</span>
                      <span className="status-badge status-badge--warning">{blocker.count}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Der Ausweg aus der Sackgasse "0 von 1 Buchungen": ohne diesen
                  Haken blieb genau die verknuepfte Buchung stehen, und der
                  erneute Import wies die zugehoerige Zeile als Dublette ab -
                  der Lauf liess sich also nie vollstaendig wiederholen. */}
              {revertPreviewQuery.data.blocked.length > 0 && (
                <label className="mt-3 flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={revertIncludeLinked}
                    onChange={(event) => setRevertIncludeLinked(event.target.checked)}
                  />
                  <span>
                    Auch diese{" "}
                    {revertPreviewQuery.data.transactions_total -
                      revertPreviewQuery.data.revertable}{" "}
                    Buchungen entfernen. Die Verknüpfungen werden dabei gelöst: Vertragsfälligkeiten
                    stehen wieder offen, Erstattungs- und Einkaufszuordnungen sowie Umbuchungspaare
                    werden aufgehoben. Verträge, Vorgänge und Einkäufe selbst bleiben bestehen.
                  </span>
                </label>
              )}

              {revertAnzahl === 0 && (
                <p className="placeholder-note">
                  Es gibt nichts zu entfernen - aus diesem Lauf steht keine Buchung mehr im Bestand.
                </p>
              )}
            </>
          )}

          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={() => setRevertRun(null)}>
              Abbrechen
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={revertRunMutation.isPending || revertAnzahl === 0}
              onClick={() =>
                revertRunMutation.mutate({
                  runId: revertRun.id,
                  filename: revertRun.filename,
                  includeLinked: revertIncludeLinked,
                })
              }
            >
              {revertRunMutation.isPending
                ? "Wird entfernt ..."
                : `${revertAnzahl} Buchungen entfernen`}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
