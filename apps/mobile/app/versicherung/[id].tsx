import { Stack, useLocalSearchParams } from "expo-router";
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import {
  NOTICE_TERMS_SOURCE_LABEL,
  POLICY_PURPOSE_CAVEAT,
  POLICY_STATUS_LABEL,
  PREMIUM_FREQUENCY_LABEL,
  PREMIUM_PAYER_LABEL,
  policyTitle,
} from "../../src/api/insurance";
import type { InsuranceLineDetail, InsurancePolicy, PremiumFrequency } from "../../src/api/types";
import { DateWithSource, DeadlineBadge } from "../../src/components/DeadlineBadge";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { Card, CardLabel, Money } from "../../src/components/ui";
import { useInsurancePolicy } from "../../src/hooks/useInsurance";
import { leading, MIN_TOUCH, space, typeScale, useTheme } from "../../src/theme";

/**
 * Policendetail (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 2.3 und 7).
 *
 * DIE REIHENFOLGE IST DIE EIGENTLICHE ENTSCHEIDUNG DIESES BILDSCHIRMS.
 *
 * Oben steht der Ernstfallblock: Versicherer, Nummer, Hotline, Status, naechster
 * Termin. Wer eine Police auf dem Telefon oeffnet, tut das ueberwiegend in genau
 * einer Lage - am Unfallort, im Treppenhaus, im Wartezimmer. Dort braucht
 * niemand die Erklaerung einer Sparte, sondern eine Telefonnummer. Der Zweck
 * bleibt, er rutscht eine Etage tiefer.
 *
 * Der Bildschirm ist rein lesend. Bearbeiten, Fristen bestaetigen, Deckungen
 * und Beteiligte pflegen bleiben Web - das ist keine Sparmassnahme, sondern die
 * Grenze aus Abschnitt 7: Nachschlagen gehoert aufs Telefon, Pflege an den
 * Schreibtisch.
 */

/** Kompakte Zeile "Bezeichnung / Wert" des Ernstfallblocks. */
function Field({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  const theme = useTheme();
  return (
    <View style={styles.field}>
      <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>{label}</Text>
      <Text
        style={[
          styles.fieldValue,
          muted ? styles.fieldValueMuted : null,
          { color: muted ? theme.textSecondary : theme.textPrimary },
        ]}
      >
        {value}
      </Text>
    </View>
  );
}

/**
 * Rufnummer als anrufbarer Link.
 *
 * `tel:` bekommt die Nummer ohne Leer- und Bindezeichen, damit auch
 * "0800 1122 3344" waehlbar bleibt; angezeigt wird sie unveraendert so, wie sie
 * in der Police steht - eine normalisierte Nummer waere beim Vergleich mit dem
 * Papier eine zweite Schreibweise.
 */
function PhoneField({
  label,
  name,
  phone,
}: {
  label: string;
  name?: string | null;
  phone: string;
}) {
  const theme = useTheme();
  const dialable = phone.replace(/[^\d+]/g, "");

  return (
    <View style={styles.field}>
      <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>{label}</Text>
      <Pressable
        accessibilityRole="link"
        accessibilityLabel={`${label} ${phone} anrufen`}
        // Grosszuegige Trefferflaeche: dieser Knopf wird unter Stress gedrueckt.
        hitSlop={8}
        onPress={() => void Linking.openURL(`tel:${dialable}`)}
        style={({ pressed }) => [styles.phonePress, { opacity: pressed ? 0.6 : 1 }]}
      >
        <Text style={[styles.phoneValue, { color: theme.positive }]}>{phone}</Text>
      </Pressable>
      {name ? (
        <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>{name}</Text>
      ) : null}
    </View>
  );
}

/** Aufzaehlung aus dem Spartenkatalog. Leere Listen entfallen ersatzlos. */
function BulletList({ title, items }: { title: string; items: string[] }) {
  const theme = useTheme();
  if (items.length === 0) return null;
  return (
    <View style={styles.bulletBlock}>
      <Text style={[styles.bulletTitle, { color: theme.textPrimary }]}>{title}</Text>
      {items.map((item) => (
        <Text key={item} style={[styles.body, { color: theme.textSecondary }]}>
          {"•"} {item}
        </Text>
      ))}
    </View>
  );
}

function frequencyLabel(value: string | null): string {
  if (!value) return "Zahlweise unbekannt";
  return PREMIUM_FREQUENCY_LABEL[value as PremiumFrequency] ?? value;
}

/**
 * Der Beitrag traegt immer seine Herkunft - ein Betrag ohne Herkunft wird in
 * diesem Modul nicht angezeigt (Plan 2.1). Die drei Faelle kommen als Wert
 * `premium.kind` und werden nicht aus dem Vorhandensein von Feldern erraten.
 */
function PremiumBlock({ policy }: { policy: InsurancePolicy }) {
  const theme = useTheme();
  const { premium } = policy;

  if (premium.kind === "contract") {
    return (
      <>
        {typeof premium.contract_amount_minor === "number" ? (
          <Money minorUnits={premium.contract_amount_minor} size="medium" tone="plain" />
        ) : (
          <Text style={[styles.amountMuted, { color: theme.textSecondary }]}>Betrag unbekannt</Text>
        )}
        <Text style={[styles.body, { color: theme.textSecondary }]}>
          {frequencyLabel(premium.contract_frequency ?? null)} · aus dem Vertrag
          {premium.contract_name ? ` „${premium.contract_name}“` : ""}
        </Text>
        <Text style={[styles.body, { color: theme.textSecondary }]}>
          Zählt in den Fixkosten mit. Geändert wird der Betrag am Vertrag, nicht hier.
        </Text>
      </>
    );
  }

  if (premium.kind === "external") {
    return (
      <>
        {typeof premium.external_premium_minor === "number" ? (
          <Money minorUnits={premium.external_premium_minor} size="medium" tone="plain" />
        ) : (
          <Text style={[styles.amountMuted, { color: theme.textSecondary }]}>Betrag unbekannt</Text>
        )}
        <Text style={[styles.body, { color: theme.textSecondary }]}>
          {frequencyLabel(premium.external_frequency ?? null)}
          {premium.premium_payer ? ` · ${PREMIUM_PAYER_LABEL[premium.premium_payer]}` : ""}
        </Text>
        {premium.premium_payer_note ? (
          <Text style={[styles.body, { color: theme.textSecondary }]}>
            {premium.premium_payer_note}
          </Text>
        ) : null}
        {/* Fremdbeitraege bleiben ausdruecklich draussen: sie belasten das
            Haushaltskonto nicht und wuerden die Fixkostenquote verfaelschen. */}
        <Text style={[styles.body, { color: theme.textSecondary }]}>Nicht Teil der Fixkosten.</Text>
      </>
    );
  }

  return (
    <>
      <Text style={[styles.amountMuted, { color: theme.textSecondary }]}>
        Kein Beitrag hinterlegt
      </Text>
      <Text style={[styles.body, { color: theme.textSecondary }]}>
        Wer den Beitrag zahlt, wird in der Webansicht hinterlegt – entweder als Vertrag des
        Haushalts oder als Betrag eines Dritten.
      </Text>
    </>
  );
}

/** Zwecktext der Sparte. Der Vorbehalt steht ÜBER dem Text, den er einordnet -
 *  als Fussnote darunter wirkte er wie Kleingedrucktes (Plan 3.11). */
function PurposeBlock({
  line,
  purposeNote,
}: {
  line: InsuranceLineDetail;
  purposeNote: string | null;
}) {
  const theme = useTheme();

  return (
    <>
      <Text style={[styles.caveat, { color: theme.textSecondary }]}>
        {POLICY_PURPOSE_CAVEAT} Maßgeblich sind Ihre Versicherungsbedingungen.
      </Text>
      <Text style={[styles.lineLabel, { color: theme.textPrimary }]}>
        {line.label}
        {line.gdv_sparte ? ` (GDV ${line.gdv_sparte})` : ""}
      </Text>
      <Text style={[styles.body, { color: theme.textSecondary }]}>{line.purpose_long}</Text>

      <BulletList title="Typischerweise enthalten" items={line.typically_covers} />
      <BulletList title="Typischerweise nicht enthalten" items={line.typically_not_covers} />

      {purposeNote ? (
        <Text style={[styles.body, { color: theme.textPrimary }]}>Ihr Zusatz: {purposeNote}</Text>
      ) : null}

      {/* Die Quelle traegt ihr Pruefdatum. Ein Katalogtext ohne Stand ist eine
          Behauptung mit unbekanntem Alter. */}
      {line.source_checked_on ? (
        <Text style={[styles.source, { color: theme.textMuted }]}>
          Quelle geprüft am {line.source_checked_on}
        </Text>
      ) : null}
    </>
  );
}

export default function VersicherungDetailScreen() {
  const theme = useTheme();
  const { id } = useLocalSearchParams<{ id: string }>();
  const detailQuery = useInsurancePolicy(id ?? null);
  const detail = detailQuery.data ?? null;

  if (!detail) {
    return (
      <>
        <Stack.Screen options={{ headerShown: true, title: "Versicherung" }} />
        <ScreenContainer>
          {detailQuery.isError ? (
            <InlineQueryState
              status="error"
              title="Versicherung nicht geladen"
              message={detailQuery.error.message}
              onRetry={() => void detailQuery.refetch()}
            />
          ) : (
            <InlineQueryState status="loading" skeletonRows={6} skeletonRowHeight={20} />
          )}
        </ScreenContainer>
      </>
    );
  }

  const { policy, line } = detail;

  return (
    <>
      <Stack.Screen options={{ headerShown: true, title: policyTitle(policy) }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content}>
          {/* (1) Der Ernstfallblock - das, wofuer dieser Bildschirm existiert. */}
          <Card accentEdge="warning">
            <CardLabel>Im Ernstfall</CardLabel>
            <Field label="Versicherer" value={policy.insurer_name} />

            {policy.policy_number ? (
              <View style={styles.field}>
                <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>
                  Versicherungsschein
                </Text>
                {/* `selectable` statt eines Kopierknopfs: die App bringt keine
                    Zwischenablage-Bibliothek mit, und die Kernfunktion des
                    Systems (langes Tippen -> Kopieren) funktioniert auf beiden
                    Plattformen. Ein eigener Knopf, der still nichts tut, waere
                    schlechter als der Systemweg mit Hinweis. */}
                <Text
                  selectable
                  style={[styles.policyNumber, { color: theme.textPrimary }]}
                  accessibilityLabel={`Versicherungsschein ${policy.policy_number}`}
                >
                  {policy.policy_number}
                </Text>
                <Text style={[styles.hint, { color: theme.textMuted }]}>
                  Zum Kopieren lange auf die Nummer tippen.
                </Text>
              </View>
            ) : (
              <Field label="Versicherungsschein" value="keine Nummer erfasst" muted />
            )}

            {policy.claims_phone ? (
              <PhoneField label="Schaden melden" phone={policy.claims_phone} />
            ) : (
              <Field label="Schaden melden" value="keine Hotline hinterlegt" muted />
            )}

            {policy.broker_phone ? (
              <PhoneField
                label="Vermittler"
                name={policy.broker_name}
                phone={policy.broker_phone}
              />
            ) : policy.broker_name ? (
              <Field label="Vermittler" value={policy.broker_name} />
            ) : null}

            <Field label="Status" value={POLICY_STATUS_LABEL[policy.status]} />

            <View style={styles.field}>
              <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>
                Nächster Termin
              </Text>
              <DeadlineBadge policy={policy} />
            </View>
          </Card>

          {/* (2) Erst jetzt die Erklaerung. */}
          <Card>
            <CardLabel>Wofür ist diese Art von Versicherung da?</CardLabel>
            <PurposeBlock line={line} purposeNote={policy.purpose_note} />
          </Card>

          {/* (3) Die Frist mit allen drei Daten und ihrer Herleitung. */}
          <Card>
            <CardLabel>Frist</CardLabel>
            <DeadlineBadge policy={policy} variant="full" />
            <View style={styles.deadlineFacts}>
              <DateWithSource
                label="Hauptfälligkeit"
                date={policy.main_due_on}
                source={policy.main_due_source}
              />
              <View style={styles.field}>
                <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>
                  Kündigungsfrist
                </Text>
                <Text style={[styles.fieldValue, { color: theme.textPrimary }]}>
                  {policy.notice_period_months === null
                    ? "Angabe fehlt"
                    : `${policy.notice_period_months} ${
                        policy.notice_period_months === 1 ? "Monat" : "Monate"
                      }`}
                </Text>
                <Text style={[styles.hint, { color: theme.textMuted }]}>
                  {NOTICE_TERMS_SOURCE_LABEL[policy.notice_terms_source]}
                </Text>
              </View>
            </View>
          </Card>

          {/* (4) Beitrag mit Herkunft. */}
          <Card>
            <CardLabel>Beitrag</CardLabel>
            <PremiumBlock policy={policy} />
          </Card>

          {/* Die Grenze wird benannt, statt sie den Nutzer suchen zu lassen. */}
          <Text style={[styles.footerHint, { color: theme.textSecondary }]}>
            Bearbeiten, Termine bestätigen, Deckungen und Beteiligte pflegen: in der Webansicht
            unter „Versicherungen“.
          </Text>
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    paddingBottom: space.xxxl,
    gap: space.lg,
  },
  field: {
    gap: space.hair,
  },
  fieldLabel: {
    fontSize: typeScale.caption,
  },
  fieldValue: {
    fontSize: typeScale.base,
    fontWeight: "600",
  },
  fieldValueMuted: {
    fontWeight: "400",
    fontStyle: "italic",
  },
  body: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  policyNumber: {
    fontSize: typeScale.large,
    fontWeight: "700",
    // Ziffernfolgen lassen sich mit gleichmaessiger Laufweite sicherer mit dem
    // Papier abgleichen.
    fontVariant: ["tabular-nums"],
  },
  hint: {
    fontSize: typeScale.micro,
  },
  phonePress: {
    minHeight: MIN_TOUCH,
    justifyContent: "center",
  },
  phoneValue: {
    fontSize: typeScale.large,
    fontWeight: "700",
  },
  caveat: {
    fontSize: typeScale.body,
    fontStyle: "italic",
  },
  lineLabel: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  bulletBlock: {
    gap: space.hair,
    paddingTop: space.sm,
  },
  bulletTitle: {
    fontSize: typeScale.body,
    fontWeight: "700",
  },
  source: {
    fontSize: typeScale.micro,
    paddingTop: space.sm,
  },
  deadlineFacts: {
    gap: space.md,
  },
  amountMuted: {
    fontSize: typeScale.large,
    fontWeight: "400",
    fontStyle: "italic",
  },
  footerHint: {
    fontSize: typeScale.body,
  },
});
