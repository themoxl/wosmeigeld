/// <reference types="jest" />
import { lightTheme } from "../theme/palette";
import { formatFileSize, getFormatLabel, getRowStatusInfo } from "./importHelpers";

describe("importHelpers", () => {
  it("formats import format labels correctly", () => {
    expect(getFormatLabel("csv_generic")).toBe("CSV Bankauszug");
    expect(getFormatLabel("xlsx_generic")).toBe("Excel (XLSX)");
    expect(getFormatLabel("mt940")).toBe("MT940 Swift");
    expect(getFormatLabel("camt")).toBe("CAMT.053 XML");
    expect(getFormatLabel("paypal_csv")).toBe("PayPal CSV");
    expect(getFormatLabel(null)).toBe("Automatisch");
  });

  it("formats file sizes correctly", () => {
    expect(formatFileSize(500)).toBe("500 B");
    expect(formatFileSize(2048)).toBe("2.0 KB");
    expect(formatFileSize(1572864)).toBe("1.5 MB");
  });

  it("returns appropriate badge status info", () => {
    const theme = lightTheme;
    const newBadge = getRowStatusInfo("new", theme);
    expect(newBadge.label).toBe("Neu");
    expect(newBadge.color).toBe(theme.positive);

    const dupBadge = getRowStatusInfo("duplicate_exact", theme);
    expect(dupBadge.label).toBe("Exaktes Duplikat");
    expect(dupBadge.color).toBe(theme.warning);
  });
});
