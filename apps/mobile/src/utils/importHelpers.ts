import type { ThemeTokens } from "../theme";
import type { ImportFormat, RowStatus } from "../api/types";

// ImportRun.detected_format ist backendseitig plain string (kein
// Literal-Union wie ImportFormat, das von MappingProfile.bank_format
// abgeleitet ist) - der switch unten deckt trotzdem exakt dieselben Werte
// ab, der default-Fall faengt alles andere sicher ab.
export function getFormatLabel(format: string | null): string {
  switch (format as ImportFormat | null) {
    case "csv_generic":
      return "CSV Bankauszug";
    case "xlsx_generic":
      return "Excel (XLSX)";
    case "xls_generic":
      return "Excel (XLS, alt)";
    case "mt940":
      return "MT940 Swift";
    case "camt":
      return "CAMT.053 XML";
    case "paypal_csv":
      return "PayPal CSV";
    default:
      return format ?? "Automatisch";
  }
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Farbe und Hinterlegung kommen ausschliesslich aus dem Theme (src/theme/palette.ts).
// "Mögliches Duplikat" nutzt bewusst highlight (Bernstein) statt warning (Koralle):
// es ist ein Hinweis zum Nachsehen, kein Fehler.
export function getRowStatusInfo(status: RowStatus, theme: ThemeTokens) {
  switch (status) {
    case "new":
      return { label: "Neu", color: theme.positive, bg: theme.positiveMuted };
    case "duplicate_exact":
      return {
        label: "Exaktes Duplikat",
        color: theme.warning,
        bg: theme.warningMuted,
      };
    case "duplicate_possible":
      return {
        label: "Mögliches Duplikat",
        color: theme.highlight,
        bg: theme.highlightMuted,
      };
    case "transfer_candidate":
      return { label: "Umbuchung", color: theme.info, bg: theme.infoMuted };
    case "committed":
      return { label: "Übernommen", color: theme.positive, bg: theme.positiveMuted };
    case "skipped":
      return { label: "Übersprungen", color: theme.textSecondary, bg: theme.border };
    case "error":
      return { label: "Fehler", color: theme.warning, bg: theme.warningMuted };
    default:
      return { label: status, color: theme.textSecondary, bg: theme.border };
  }
}
