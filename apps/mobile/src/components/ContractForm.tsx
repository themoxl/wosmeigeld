import { StyleSheet, Switch, Text, View } from "react-native";
import type {
  ContractDirection,
  ContractFrequency,
  ContractStatus,
  ContractType,
} from "../api/types";
import { OptionList } from "./OptionList";
import { IsoDatePickerField } from "./IsoDatePickerField";
import { TextField } from "./TextField";
import { Card, CardLabel } from "./ui";
import { leading, space, typeScale, useTheme } from "../theme";
import { parseAmountToMinorUnits } from "../utils/money";

/**
 * Gemeinsames Formular fuer "Vertrag anlegen" und "Vertrag bearbeiten"
 * (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Phase 1).
 *
 * Der Betrag wird immer als positiver Wert erfasst; die Richtung ist ein
 * eigenes Feld. Damit ist die frueher widerspruechliche Vorzeichenmischung aus
 * `recurring_rules` nicht mehr moeglich.
 */

export interface ContractFormValues {
  name: string;
  providerLabel: string;
  contractType: ContractType;
  direction: ContractDirection;
  amount: string;
  frequency: ContractFrequency;
  nextDueDate: string;
  status: ContractStatus;
  cancelledOn: string;
  endsOn: string;
  note: string;
  reserveEnabled: boolean;
  reserveStartedOn: string;
  reserveStartingAmount: string;
}

export const EMPTY_CONTRACT_FORM: ContractFormValues = {
  name: "",
  providerLabel: "",
  contractType: "other",
  direction: "expense",
  amount: "",
  frequency: "monthly",
  nextDueDate: new Date().toISOString().slice(0, 10),
  status: "active",
  cancelledOn: "",
  endsOn: "",
  note: "",
  reserveEnabled: false,
  reserveStartedOn: new Date().toISOString().slice(0, 10),
  reserveStartingAmount: "0,00",
};

const TYPE_OPTIONS: { value: ContractType; label: string }[] = [
  { value: "subscription", label: "Abonnement" },
  { value: "insurance", label: "Versicherung" },
  { value: "rent", label: "Miete & Wohnen" },
  { value: "loan", label: "Kredit" },
  { value: "installment", label: "Ratenkauf" },
  { value: "utility", label: "Energie" },
  { value: "telecom", label: "Telekommunikation" },
  { value: "membership", label: "Mitgliedschaft" },
  { value: "donation", label: "Spende" },
  { value: "savings", label: "Sparplan" },
  { value: "income", label: "Einnahme" },
  { value: "other", label: "Sonstiges" },
];

const FREQUENCY_OPTIONS: { value: ContractFrequency; label: string }[] = [
  { value: "weekly", label: "Wöchentlich" },
  { value: "monthly", label: "Monatlich" },
  { value: "quarterly", label: "Quartalsweise" },
  { value: "semiannual", label: "Halbjährlich" },
  { value: "yearly", label: "Jährlich" },
];

const STATUS_OPTIONS: { value: ContractStatus; label: string }[] = [
  { value: "active", label: "Aktiv" },
  { value: "paused", label: "Pausiert" },
  { value: "cancelled", label: "Gekündigt (läuft bis Enddatum)" },
  { value: "ended", label: "Beendet" },
  { value: "not_a_contract", label: "Kein Vertrag" },
];

const DIRECTION_OPTIONS: { value: ContractDirection; label: string }[] = [
  { value: "expense", label: "Ausgabe" },
  { value: "income", label: "Einnahme" },
];

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function validateContractForm(values: ContractFormValues): string | null {
  if (!values.name.trim()) return "Bitte einen Namen eintragen.";
  // parseAmountToMinorUnits wirft bei ungueltiger Eingabe (anders als im Web,
  // wo es null liefert) - die Meldung ist bereits nutzertauglich formuliert.
  let amount: number;
  try {
    amount = parseAmountToMinorUnits(values.amount);
  } catch (error) {
    return error instanceof Error ? error.message : "Betrag ist ungültig.";
  }
  if (amount === 0) return "Bitte einen Betrag ungleich null eintragen.";
  if (!ISO_DATE.test(values.nextDueDate)) return "Fälligkeit im Format JJJJ-MM-TT eintragen.";
  if (values.cancelledOn && !ISO_DATE.test(values.cancelledOn)) {
    return "Kündigungsdatum im Format JJJJ-MM-TT eintragen.";
  }
  if (values.endsOn && !ISO_DATE.test(values.endsOn)) {
    return "Enddatum im Format JJJJ-MM-TT eintragen.";
  }
  if (values.reserveEnabled && isReserveEligible(values)) {
    if (!ISO_DATE.test(values.reserveStartedOn)) {
      return "Rücklagen-Startdatum im Format JJJJ-MM-TT eintragen.";
    }
    try {
      const startingAmount = parseAmountToMinorUnits(values.reserveStartingAmount || "0");
      if (startingAmount < 0) return "Der bestätigte Anfangsbetrag darf nicht negativ sein.";
    } catch (error) {
      return error instanceof Error ? error.message : "Anfangsbetrag ist ungültig.";
    }
  }
  return null;
}

export function isReserveEligible(
  values: Pick<ContractFormValues, "direction" | "frequency">,
): boolean {
  return (
    values.direction === "expense" &&
    (values.frequency === "quarterly" ||
      values.frequency === "semiannual" ||
      values.frequency === "yearly")
  );
}

type Props = {
  values: ContractFormValues;
  onChange: (values: ContractFormValues) => void;
  disabled?: boolean;
};

export function ContractForm({ values, onChange, disabled = false }: Props) {
  const theme = useTheme();
  const set = <K extends keyof ContractFormValues>(key: K, value: ContractFormValues[K]) =>
    onChange({ ...values, [key]: value });
  const setEligibilityField = <K extends "direction" | "frequency">(
    key: K,
    value: ContractFormValues[K],
  ) => {
    const next = { ...values, [key]: value };
    if (!isReserveEligible(next)) next.reserveEnabled = false;
    onChange(next);
  };
  const reserveEligible = isReserveEligible(values);

  return (
    <View style={styles.container}>
      <TextField
        label="Name"
        value={values.name}
        onChangeText={(text) => set("name", text)}
        placeholder="z. B. Musterfunk Festnetz"
        editable={!disabled}
      />
      <TextField
        label="Anbieter (optional)"
        value={values.providerLabel}
        onChangeText={(text) => set("providerLabel", text)}
        placeholder="z. B. Musterfunk Deutschland GmbH"
        editable={!disabled}
      />
      <TextField
        label="Betrag (immer positiv)"
        value={values.amount}
        onChangeText={(text) => set("amount", text)}
        placeholder="39,95"
        keyboardType="decimal-pad"
        editable={!disabled}
      />

      <FieldGroup label="Richtung">
        <OptionList
          options={DIRECTION_OPTIONS}
          value={values.direction}
          onChange={(value) => setEligibilityField("direction", value)}
          accessibilityLabel="Richtung"
        />
      </FieldGroup>
      <FieldGroup label="Rhythmus">
        <OptionList
          options={FREQUENCY_OPTIONS}
          value={values.frequency}
          onChange={(value) => setEligibilityField("frequency", value)}
          accessibilityLabel="Rhythmus"
        />
      </FieldGroup>
      <FieldGroup label="Vertragsart">
        <OptionList
          options={TYPE_OPTIONS}
          value={values.contractType}
          onChange={(value) => set("contractType", value)}
          accessibilityLabel="Vertragsart"
        />
      </FieldGroup>

      <TextField
        label="Nächste Fälligkeit (JJJJ-MM-TT)"
        value={values.nextDueDate}
        onChangeText={(text) => set("nextDueDate", text)}
        placeholder="2026-09-01"
        editable={!disabled}
      />

      {reserveEligible ? (
        <Card>
          <View style={styles.reserveToggle}>
            <View style={styles.reserveCopy}>
              <Text style={[styles.sectionTitle, { color: theme.textPrimary }]}>
                Für diese Rechnung monatlich zurücklegen
              </Text>
              <Text style={[styles.hint, { color: theme.textSecondary }]}>
                Der Startwert ist ein bestätigter Rücklagenstand, keine Kontobuchung.
              </Text>
            </View>
            <Switch
              accessibilityLabel="Für diese Rechnung monatlich zurücklegen"
              value={values.reserveEnabled}
              onValueChange={(value) => set("reserveEnabled", value)}
              disabled={disabled}
              trackColor={{ false: theme.border, true: theme.positiveStrong }}
            />
          </View>
          {values.reserveEnabled ? (
            <>
              <IsoDatePickerField
                label="Rücklagen-Startdatum"
                value={values.reserveStartedOn}
                onChange={(date) => set("reserveStartedOn", date)}
                disabled={disabled}
              />
              <TextField
                label="Bereits vorhandener Anfangsbetrag"
                value={values.reserveStartingAmount}
                onChangeText={(text) => set("reserveStartingAmount", text)}
                keyboardType="decimal-pad"
                editable={!disabled}
              />
              <Text style={[styles.hint, { color: theme.textSecondary }]}>
                Nach dem Speichern siehst du Soll, bestätigten Stand und Folgemonat unter Budget →
                Rücklagen.
              </Text>
            </>
          ) : null}
        </Card>
      ) : null}

      <View style={styles.section}>
        <Text style={[styles.sectionTitle, { color: theme.textPrimary }]}>
          Laufzeit &amp; Kündigung
        </Text>
        <Text style={[styles.hint, { color: theme.textSecondary }]}>
          Gekündigt heißt nicht beendet: bis zum Enddatum zählt der Vertrag weiter mit.
        </Text>
      </View>

      <FieldGroup label="Status">
        <OptionList
          options={STATUS_OPTIONS}
          value={values.status}
          onChange={(value) => set("status", value)}
          accessibilityLabel="Status"
        />
      </FieldGroup>
      <TextField
        label="Gekündigt am (optional)"
        value={values.cancelledOn}
        onChangeText={(text) => set("cancelledOn", text)}
        placeholder="2026-09-30"
        editable={!disabled}
      />
      <TextField
        label="Läuft bis (optional)"
        value={values.endsOn}
        onChangeText={(text) => set("endsOn", text)}
        placeholder="2026-12-31"
        editable={!disabled}
      />
      <TextField
        label="Notiz (optional)"
        value={values.note}
        onChangeText={(text) => set("note", text)}
        editable={!disabled}
      />
    </View>
  );
}

/**
 * Beschriftung einer Auswahlgruppe. Grossbuchstaben-Label statt Fliesstext:
 * so unterscheidet sich die Ueberschrift einer Gruppe auf einen Blick von der
 * Beschriftung eines einzelnen Feldes darueber.
 */
function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <CardLabel>{label}</CardLabel>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: space.lg,
  },
  section: {
    gap: space.sm,
  },
  sectionTitle: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  hint: {
    fontSize: typeScale.caption,
    lineHeight: leading(typeScale.caption),
  },
  reserveToggle: {
    minHeight: 48,
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
  },
  reserveCopy: {
    flex: 1,
    gap: space.xs,
  },
});
