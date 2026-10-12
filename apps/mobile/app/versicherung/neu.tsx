import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import {
  PREMIUM_FREQUENCY_LABEL,
  PREMIUM_PAYER_LABEL,
  type PolicyPremiumDraft,
  type SimplePolicyDraft,
} from "../../src/api/insurance";
import type { PremiumFrequency, PremiumPayer } from "../../src/api/types";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { OptionList, type Option } from "../../src/components/OptionList";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { SectionHeader } from "../../src/components/SectionHeader";
import { TextField } from "../../src/components/TextField";
import { Card } from "../../src/components/ui";
import { useContracts } from "../../src/hooks/useContracts";
import {
  groupLinesByRiskField,
  useCreateInsurancePolicy,
  useInsuranceContext,
  useInsuranceLines,
} from "../../src/hooks/useInsurance";
import { leading, space, typeScale, useMaskedAmount, useTheme } from "../../src/theme";
import { formatMinorUnits, parseAmountToMinorUnits } from "../../src/utils/money";

/**
 * Eine einfache Police anlegen - die einzige schreibende Funktion der App
 * (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 7, P2b).
 *
 * GENAU VIER FELDER: Sparte, Versicherer, Policennummer, Geldfall. Das ist
 * keine abgespeckte Fassung des Webformulars, sondern eine andere Gattung: eine
 * Police entsteht mobil in dem Moment, in dem man den Vertrag gerade in der
 * Hand hat. Vier Felder sind eine Notiz, dreissig waeren Pflege - und Pflege
 * gehoert an den Schreibtisch, wo der Versicherungsschein aufgeschlagen liegt.
 *
 * Was dabei entsteht, ist ausdruecklich eine Police mit ANGENOMMENER Frist
 * (`main_due_source = 'catalog_suggested'`). Das ist der haeufige Fall und kein
 * Fehler; die Liste zeigt sie deshalb ohne Countdown, und das Bestaetigen
 * geschieht im Web.
 *
 * Der Geldfall ist EIN Feld, obwohl er je nach Wahl eine Anschlussangabe
 * braucht: "wer zahlt" und "wie viel" sind eine einzige Entscheidung. Getrennte
 * Felder "Betrag" und "Zahler" liessen sich widerspruechlich ausfuellen -
 * genau das lehnt der Server mit `insurance_premium_source_conflict` ab.
 */

type PremiumKind = PolicyPremiumDraft["kind"];

/**
 * Ein Schluessel, den der Server selbst als Voreinstellung verwendet
 * (`InsurancePolicyCreate.line_key`). Er steht hier als ausdrueckliche Wahl und
 * nicht als stiller Vorgabewert: eine Police ohne Sparte ist eine bewusste
 * Entscheidung ("weiss ich gerade nicht"), die im Web als offene Aufgabe
 * auftaucht - kein Versehen, das man dem Nutzer unterschiebt.
 */
const UNKNOWN_LINE_KEY = "unknown";

const PREMIUM_KIND_OPTIONS: Option<PremiumKind>[] = [
  { value: "contract", label: "Der Haushalt zahlt – über einen Vertrag" },
  { value: "external", label: "Jemand anderes zahlt (Arbeitgeber, Partner …)" },
  { value: "none", label: "Kein Beitrag hinterlegt" },
];

const FREQUENCY_OPTIONS: Option<PremiumFrequency>[] = (
  ["monthly", "quarterly", "semiannual", "yearly", "weekly", "single"] as const
).map((value) => ({ value, label: PREMIUM_FREQUENCY_LABEL[value] }));

const PAYER_OPTIONS: Option<PremiumPayer>[] = (
  ["employer", "self_payroll", "partner", "other"] as const
).map((value) => ({ value, label: PREMIUM_PAYER_LABEL[value] }));

export default function VersicherungNeuScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { canEdit } = useInsuranceContext();

  const [lineKey, setLineKey] = useState<string | null>(null);
  const [insurerName, setInsurerName] = useState("");
  const [policyNumber, setPolicyNumber] = useState("");
  const [premiumKind, setPremiumKind] = useState<PremiumKind | null>(null);
  const [contractId, setContractId] = useState<string | null>(null);
  const [externalAmount, setExternalAmount] = useState("");
  const [frequency, setFrequency] = useState<PremiumFrequency | null>(null);
  const [payer, setPayer] = useState<PremiumPayer | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Der Katalog wird nur von diesem Formular gebraucht und deshalb auch nur
  // hier geladen (die Liste kommt ohne ihn aus).
  const linesQuery = useInsuranceLines({ enabled: true });
  const contractsQuery = useContracts();
  const createPolicy = useCreateInsurancePolicy();

  const lineSections = groupLinesByRiskField(
    // Der Platzhalter aus dem Katalog wuerde sonst doppelt erscheinen - einmal
    // in seiner Risikogruppe und einmal als ausdrueckliche Wahl oben.
    (linesQuery.data ?? []).filter((line) => line.line_key !== UNKNOWN_LINE_KEY),
  );

  // OptionList nimmt eine reine Zeichenkette als Beschriftung, kein Element -
  // <Money> kann hier also nicht stehen. Der Privat-Modus greift trotzdem:
  // useMaskedAmount() ersetzt den fertig formatierten Betrag durch "•••• €",
  // bevor er in den Beschriftungstext wandert. Sonst stuenden ausgerechnet
  // hier saemtliche Vertragsbeitraege des Haushalts offen.
  const maskAmount = useMaskedAmount();
  const contractOptions: Option<string>[] = (contractsQuery.data ?? [])
    .filter((contract) => contract.direction === "expense")
    .map((contract) => ({
      value: contract.id,
      label: `${contract.name} · ${maskAmount(formatMinorUnits(contract.amount_minor))} ${
        PREMIUM_FREQUENCY_LABEL[contract.frequency as PremiumFrequency] ?? contract.frequency
      }`,
    }));

  /** Der Geldfall als Union bauen - "Vertrag UND Fremdbetrag" ist damit gar
   *  nicht erst formulierbar. */
  const buildPremium = (): PolicyPremiumDraft | string => {
    if (premiumKind === "contract") {
      if (!contractId) return "Bitte den Vertrag auswählen, über den der Beitrag läuft.";
      return { kind: "contract", contractId };
    }
    if (premiumKind === "external") {
      if (!frequency) return "Bitte angeben, in welchem Rhythmus der Beitrag gezahlt wird.";
      if (!payer) return "Bitte angeben, wer den Beitrag zahlt.";
      let premiumMinor: number;
      try {
        premiumMinor = Math.abs(parseAmountToMinorUnits(externalAmount));
      } catch (parseError) {
        return parseError instanceof Error ? parseError.message : "Betrag ungültig.";
      }
      if (premiumMinor <= 0) return "Ein fremd gezahlter Beitrag braucht einen Betrag über null.";
      return { kind: "external", premiumMinor, frequency, payer };
    }
    return { kind: "none" };
  };

  const handleSave = () => {
    if (!lineKey) {
      setError("Bitte eine Sparte wählen – oder ausdrücklich „Sparte noch offen“.");
      return;
    }
    if (insurerName.trim().length === 0) {
      setError("Bitte die Gesellschaft eintragen, bei der die Police läuft.");
      return;
    }
    if (!premiumKind) {
      setError("Bitte angeben, wer den Beitrag zahlt.");
      return;
    }
    const premium = buildPremium();
    if (typeof premium === "string") {
      setError(premium);
      return;
    }
    setError(null);

    const draft: SimplePolicyDraft = {
      lineKey,
      insurerName,
      policyNumber,
      premium,
    };

    createPolicy.mutate(draft, {
      onSuccess: () => router.back(),
      onError: (mutationError) => setError(mutationError.message),
    });
  };

  if (!canEdit) {
    return (
      <>
        <Stack.Screen options={{ headerShown: true, title: "Neue Versicherung" }} />
        <ScreenContainer>
          <View style={styles.readOnly}>
            <Card tone="info" density="compact">
              <Text style={[styles.hint, { color: theme.info }]}>
                Zum Anlegen fehlen die Rechte in diesem Haushalt.
              </Text>
            </Card>
          </View>
        </ScreenContainer>
      </>
    );
  }

  return (
    <>
      {/* headerShown ausdruecklich: der Wurzel-Stack blendet Kopfzeilen
          standardmaessig aus und kennt diese Route (noch) nicht namentlich.
          `presentation: "modal"` steht bewusst NICHT hier - die Darstellungsart
          entscheidet der Navigator beim Aufbau, ein nachtraeglich gesetzter Wert
          bliebe wirkungslos. Sie gehoert in app/_layout.tsx, wo vertrag/neu und
          vorhaben/neu bereits als Modal registriert sind. */}
      <Stack.Screen options={{ headerShown: true, title: "Neue Versicherung" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Text style={[styles.intro, { color: theme.textSecondary }]}>
            Vier Angaben genügen. Fristen, Deckungen und Beteiligte werden in der Webansicht
            nachgetragen – bis dahin zeigt die Police einen angenommenen Termin ohne Countdown.
          </Text>

          {/* (1) Sparte */}
          <View style={styles.fieldBlock}>
            <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>Sparte</Text>
            <OptionList
              options={[{ value: UNKNOWN_LINE_KEY, label: "Sparte noch offen" }]}
              value={lineKey}
              onChange={setLineKey}
            />
            {linesQuery.isLoading ? (
              <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={40} />
            ) : linesQuery.isError ? (
              <InlineQueryState
                status="error"
                title="Spartenkatalog nicht geladen"
                message={linesQuery.error.message}
                onRetry={() => void linesQuery.refetch()}
              />
            ) : (
              lineSections.map((section) => (
                <View key={section.key}>
                  <SectionHeader title={section.title} />
                  <OptionList
                    options={section.data.map((line) => ({
                      value: line.line_key,
                      label: line.label,
                    }))}
                    value={lineKey}
                    onChange={setLineKey}
                  />
                </View>
              ))
            )}
          </View>

          {/* (2) Versicherer */}
          <TextField
            label="Versicherer"
            value={insurerName}
            onChangeText={setInsurerName}
            placeholder="z. B. Allianz Versicherungs-AG"
            autoCapitalize="words"
          />

          {/* (3) Policennummer - freiwillig. Sie ist am Schadenstag die
              wichtigste Angabe, aber sie liegt nicht immer vor; ein
              Pflichtfeld verhinderte hier genau die schnelle Notiz. */}
          <TextField
            label="Policennummer (optional)"
            value={policyNumber}
            onChangeText={setPolicyNumber}
            placeholder="z. B. VS-1234567/01"
            autoCapitalize="characters"
            autoCorrect={false}
          />

          {/* (4) Geldfall */}
          <View style={styles.fieldBlock}>
            <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>Geldfall</Text>
            <OptionList
              options={PREMIUM_KIND_OPTIONS}
              value={premiumKind}
              onChange={setPremiumKind}
            />

            {premiumKind === "contract" ? (
              contractOptions.length > 0 ? (
                <View style={styles.subBlock}>
                  <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>
                    Über diesen Vertrag
                  </Text>
                  <OptionList
                    options={contractOptions}
                    value={contractId}
                    onChange={setContractId}
                  />
                </View>
              ) : (
                <Text style={[styles.subHint, { color: theme.textSecondary }]}>
                  Es ist noch kein laufender Ausgabenvertrag erfasst. Ohne ihn bleibt vorerst „Kein
                  Beitrag hinterlegt“ die richtige Angabe.
                </Text>
              )
            ) : null}

            {premiumKind === "external" ? (
              <View style={styles.subBlock}>
                <TextField
                  label="Betrag"
                  value={externalAmount}
                  onChangeText={setExternalAmount}
                  placeholder="z. B. 148,90"
                  keyboardType="decimal-pad"
                />
                <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>Rhythmus</Text>
                <OptionList options={FREQUENCY_OPTIONS} value={frequency} onChange={setFrequency} />
                <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>Wer zahlt</Text>
                <OptionList options={PAYER_OPTIONS} value={payer} onChange={setPayer} />
                <Text style={[styles.subHint, { color: theme.textSecondary }]}>
                  Ein fremd gezahlter Beitrag zählt nicht in die Fixkosten.
                </Text>
              </View>
            ) : null}
          </View>

          {error ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.hint, { color: theme.warning }]} accessibilityRole="alert">
                {error}
              </Text>
            </Card>
          ) : null}

          <PrimaryButton
            label="Versicherung anlegen"
            onPress={handleSave}
            loading={createPolicy.isPending}
          />
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    gap: space.lg,
    paddingHorizontal: space.lg,
    paddingTop: space.lg,
    paddingBottom: space.xxxl,
  },
  intro: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  fieldBlock: {
    gap: space.sm,
  },
  fieldLabel: {
    fontSize: typeScale.body,
    fontWeight: "600",
  },
  subBlock: {
    gap: space.sm,
    paddingTop: space.sm,
  },
  subHint: {
    fontSize: typeScale.body,
    paddingTop: space.sm,
  },
  hint: {
    fontSize: typeScale.body,
  },
  readOnly: {
    padding: space.lg,
  },
});
