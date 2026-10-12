import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  downloadBackup,
  getBackupInfo,
  importBackup,
  previewBackupImport,
  type BackupImportPreview,
  type BackupImportResult,
} from "../lib/api";

// Einstellungen → Sicherung. Der ganze Haushalt als eine Datei: herunterladen,
// aufbewahren, wieder einspielen.
//
// Bewusst zweistufig beim Einspielen (Datei wählen → Vorschau → Namen abtippen
// → einspielen), dasselbe Muster wie beim Leeren des Haushalts im
// Datenbank-Explorer. Ein einzelner Knopf "Importieren" wäre hier die
// gefährlichste Schaltfläche der ganzen Anwendung: sie ersetzt die Daten aller
// Mitglieder, und wer sie versehentlich trifft, merkt es erst hinterher.

const BEHANDLUNG_LABEL: Record<BackupImportPreview["tables"][number]["handling"], string> = {
  ersetzen: "wird ersetzt",
  ueberschreiben: "wird überschrieben",
  ergaenzen: "wird ergänzt",
  uebersprungen: "bleibt unverändert",
};

function formatZahl(wert: number): string {
  return wert.toLocaleString("de-DE");
}

function formatZeitpunkt(iso: string): string {
  const datum = new Date(iso);
  return Number.isNaN(datum.getTime()) ? iso : datum.toLocaleString("de-DE");
}

/** Löst den Browser-Download aus. Gleiches Muster wie in CostImportExportPanel. */
function speichere(blob: Blob, dateiname: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = dateiname;
  link.click();
  URL.revokeObjectURL(url);
}

export function BackupPanel() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const queryClient = useQueryClient();

  const [datei, setDatei] = useState<File | null>(null);
  const [vorschau, setVorschau] = useState<BackupImportPreview | null>(null);
  const [ergebnis, setErgebnis] = useState<BackupImportResult | null>(null);
  const [bestaetigung, setBestaetigung] = useState("");
  // Ein <input type="file"> lässt sich nicht über einen Wert zurücksetzen -
  // ein neuer key erzwingt ein frisches Element. Gleiches Vorgehen wie in
  // ImportPage.
  const [dateiFeldKey, setDateiFeldKey] = useState(0);

  const istEigentuemer = selectedHousehold?.role === "owner";

  const info = useQuery({
    queryKey: ["backup", "info", selectedHouseholdId],
    queryFn: () => getBackupInfo(accessToken!, selectedHouseholdId!),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const exportMutation = useMutation({
    mutationFn: async () => downloadBackup(accessToken!, selectedHouseholdId!),
    onSuccess: ({ blob, filename }) => speichere(blob, filename),
  });

  const vorschauMutation = useMutation({
    mutationFn: async (gewaehlt: File) =>
      previewBackupImport(accessToken!, selectedHouseholdId!, gewaehlt),
    onSuccess: (daten) => {
      setVorschau(daten);
      setErgebnis(null);
      setBestaetigung("");
    },
  });

  const importMutation = useMutation({
    mutationFn: async () =>
      importBackup(accessToken!, selectedHouseholdId!, datei!, bestaetigung.trim()),
    onSuccess: (daten) => {
      setErgebnis(daten);
      setVorschau(null);
      setDatei(null);
      setBestaetigung("");
      setDateiFeldKey((wert) => wert + 1);
      // Nach dem Einspielen stimmt kein zwischengespeicherter Stand mehr -
      // Buchungen, Konten, Kategorien, Berichte, alles.
      void queryClient.invalidateQueries();
    },
  });

  const darfEinspielen =
    Boolean(vorschau) &&
    bestaetigung.trim() === vorschau?.household_name &&
    !importMutation.isPending;

  const dateiGewaehlt = (gewaehlt: File | null) => {
    setDatei(gewaehlt);
    setVorschau(null);
    setErgebnis(null);
    setBestaetigung("");
    vorschauMutation.reset();
    importMutation.reset();
    if (gewaehlt) {
      vorschauMutation.mutate(gewaehlt);
    }
  };

  const abbrechen = () => {
    setDatei(null);
    setVorschau(null);
    setBestaetigung("");
    setDateiFeldKey((wert) => wert + 1);
    vorschauMutation.reset();
  };

  if (!selectedHouseholdId) {
    return <p className="card-body">Kein Haushalt ausgewählt.</p>;
  }

  return (
    <>
      {/* ------------------------------------------------------------------ */}
      {/* Sichern                                                            */}
      {/* ------------------------------------------------------------------ */}
      <div className="card" style={{ marginTop: "var(--space-4)" }}>
        <h2 className="card-heading">Sicherung herunterladen</h2>
        <p className="card-body">
          Lädt den gesamten Haushalt als ZIP-Datei herunter — Konten, Buchungen, Kategorien,
          Verträge, Budgets, Vorhaben und alles Übrige. Die Datei liegt danach bei Ihnen; die
          Anwendung bewahrt keine Kopie auf.
        </p>

        {info.isLoading && <p className="card-body">Wird ermittelt …</p>}
        {info.isError && (
          <p className="form-error">Der Umfang der Sicherung konnte nicht ermittelt werden.</p>
        )}
        {info.data && (
          <>
            <p className="card-body" style={{ marginTop: "var(--space-3)" }}>
              <strong>{formatZahl(info.data.total_rows)} Datensätze</strong> aus{" "}
              {info.data.table_count} Tabellen in „{info.data.household_name}“.
            </p>
            <details style={{ marginTop: "var(--space-2)" }}>
              <summary className="card-body" style={{ cursor: "pointer" }}>
                Was enthalten ist — und was nicht
              </summary>
              <ul className="card-body" style={{ marginTop: "var(--space-2)" }}>
                {info.data.tables.slice(0, 12).map((eintrag) => (
                  <li key={eintrag.table}>
                    {eintrag.table}: {formatZahl(eintrag.rows)}
                  </li>
                ))}
                {info.data.tables.length > 12 && (
                  <li>… und {info.data.tables.length - 12} weitere Tabellen</li>
                )}
              </ul>
              <p className="card-body" style={{ marginTop: "var(--space-2)" }}>
                <strong>Nicht enthalten:</strong>
              </p>
              <ul className="card-body">
                {info.data.excluded.map((satz) => (
                  <li key={satz}>{satz}</li>
                ))}
              </ul>
            </details>
          </>
        )}

        <button
          type="button"
          className="btn-primary"
          style={{ marginTop: "var(--space-4)" }}
          onClick={() => exportMutation.mutate()}
          disabled={exportMutation.isPending || !info.data}
        >
          {exportMutation.isPending ? "Wird erstellt …" : "Sicherung herunterladen"}
        </button>
        {exportMutation.isError && (
          <p className="form-error">
            {(exportMutation.error as Error).message ||
              "Die Sicherung konnte nicht erstellt werden."}
          </p>
        )}
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Einspielen                                                         */}
      {/* ------------------------------------------------------------------ */}
      <div className="card explorer-danger" style={{ marginTop: "var(--space-4)" }}>
        <h2 className="card-heading">Sicherung einspielen</h2>
        <p className="card-body">
          Setzt den Haushalt auf den Stand der gewählten Datei zurück. Alles, was seit dieser
          Sicherung entstanden ist, geht dabei verloren.
        </p>

        {!istEigentuemer ? (
          <p className="card-body" style={{ marginTop: "var(--space-3)" }}>
            Nur die Eigentümerin oder der Eigentümer des Haushalts kann eine Sicherung einspielen —
            der Vorgang ersetzt die Daten aller Mitglieder.
          </p>
        ) : (
          <>
            <label className="field" style={{ marginTop: "var(--space-3)" }}>
              <span>Sicherungsdatei (.zip)</span>
              <input
                key={dateiFeldKey}
                type="file"
                accept=".zip,application/zip"
                className="form-input file-input"
                onChange={(event) => dateiGewaehlt(event.target.files?.[0] ?? null)}
                disabled={vorschauMutation.isPending || importMutation.isPending}
              />
            </label>

            {vorschauMutation.isPending && <p className="card-body">Datei wird gelesen …</p>}
            {vorschauMutation.isError && (
              <p className="form-error">
                {(vorschauMutation.error as Error).message ||
                  "Die Datei konnte nicht gelesen werden."}
              </p>
            )}

            {vorschau && (
              <div style={{ marginTop: "var(--space-4)" }}>
                <p className="card-body">
                  Sicherung von <strong>„{vorschau.source_household_name}“</strong>, erstellt am{" "}
                  {formatZeitpunkt(vorschau.created_at)}.
                </p>
                <p className="card-body">
                  Sie enthält {formatZahl(vorschau.rows_in_file)} Datensätze. Im Haushalt stehen
                  derzeit {formatZahl(vorschau.rows_in_household)}.
                </p>

                {vorschau.source_household_id !== selectedHouseholdId && (
                  <p className="food-fill-note">
                    Die Sicherung stammt aus einem anderen Haushalt. Sie lässt sich einspielen — die
                    Daten werden dabei diesem Haushalt zugeordnet.
                  </p>
                )}
                {!vorschau.schema_matches && (
                  <p className="food-fill-note">
                    Die Sicherung stammt aus einem anderen Programmstand. Sie lässt sich einspielen;
                    seither entfernte Felder werden ausgelassen, neu hinzugekommene bleiben leer.
                  </p>
                )}
                {vorschau.unknown_tables.length > 0 && (
                  <p className="food-fill-note">
                    Nicht mehr bekannte Tabellen werden übergangen:{" "}
                    {vorschau.unknown_tables.join(", ")}.
                  </p>
                )}

                <details style={{ marginTop: "var(--space-3)" }}>
                  <summary className="card-body" style={{ cursor: "pointer" }}>
                    Auswirkung je Tabelle ({vorschau.tables.length})
                  </summary>
                  <table className="table" style={{ marginTop: "var(--space-2)" }}>
                    <thead>
                      <tr>
                        <th>Tabelle</th>
                        <th style={{ textAlign: "right" }}>heute</th>
                        <th style={{ textAlign: "right" }}>in der Datei</th>
                        <th>Wirkung</th>
                      </tr>
                    </thead>
                    <tbody>
                      {vorschau.tables.map((eintrag) => (
                        <tr key={eintrag.table}>
                          <td>{eintrag.table}</td>
                          <td style={{ textAlign: "right" }}>
                            {formatZahl(eintrag.rows_in_household)}
                          </td>
                          <td style={{ textAlign: "right" }}>{formatZahl(eintrag.rows_in_file)}</td>
                          <td title={eintrag.note ?? undefined}>
                            {BEHANDLUNG_LABEL[eintrag.handling]}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>

                <p
                  className="food-fill-note food-fill-note--error"
                  style={{ marginTop: "var(--space-4)" }}
                >
                  Danach sind {formatZahl(vorschau.rows_in_household)} Datensätze dieses Haushalts
                  ersetzt. Das lässt sich nicht rückgängig machen.
                </p>

                <label className="field" style={{ marginTop: "var(--space-3)" }}>
                  <span>
                    Zum Bestätigen den Haushaltsnamen eintippen:{" "}
                    <code>{vorschau.household_name}</code>
                  </span>
                  <input
                    type="text"
                    className="form-input"
                    value={bestaetigung}
                    onChange={(event) => setBestaetigung(event.target.value)}
                    placeholder={vorschau.household_name}
                    autoComplete="off"
                    disabled={importMutation.isPending}
                  />
                </label>

                <div
                  style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-3)" }}
                >
                  <button
                    type="button"
                    className="btn-danger"
                    onClick={() => importMutation.mutate()}
                    disabled={!darfEinspielen}
                  >
                    {importMutation.isPending
                      ? "Wird eingespielt …"
                      : `${formatZahl(vorschau.rows_in_file)} Datensätze einspielen`}
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={abbrechen}
                    disabled={importMutation.isPending}
                  >
                    Abbrechen
                  </button>
                </div>
              </div>
            )}

            {importMutation.isError && (
              <p className="form-error">
                {(importMutation.error as Error).message ||
                  "Die Sicherung konnte nicht eingespielt werden."}
              </p>
            )}

            {ergebnis && (
              <div style={{ marginTop: "var(--space-4)" }}>
                <p className="card-body">
                  <strong>Eingespielt.</strong> {formatZahl(ergebnis.deleted_rows)} Datensätze
                  entfernt, {formatZahl(ergebnis.imported_rows)} eingespielt.
                </p>
                {ergebnis.cleared_user_references > 0 && (
                  <p className="food-fill-note">
                    Verweise auf {ergebnis.cleared_user_references} Nutzerkonten aus der
                    Ursprungsinstanz wurden geleert — die betroffenen Einträge haben jetzt keinen
                    Bearbeiter mehr.
                  </p>
                )}
                {ergebnis.skipped_tables.length > 0 && (
                  <p className="card-body">
                    Unverändert geblieben: {ergebnis.skipped_tables.join(", ")} — Zugriffsrechte
                    werden nie aus einer Sicherung gesetzt.
                  </p>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
