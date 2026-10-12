import { useRouter } from "expo-router";
import { StyleSheet, View } from "react-native";
import { POLICY_STATUS_LABEL, policyTitle } from "../api/insurance";
import type { InsurancePolicy } from "../api/types";
import { space } from "../theme";
import { DeadlineBadge, deadlineAccessibilityLabel } from "./DeadlineBadge";
import { DataRow } from "./ui";

/**
 * Eine Police als Zeile der gruppierten Liste
 * (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 7).
 *
 * Bewusst ein duenner Aufsatz auf DataRow statt einer eigenen Zeile: Hoehe,
 * Druckflaeche, Kachel und Umbruchverhalten sind dort einmal entschieden. Was
 * diese Zeile hinzufuegt, ist ausschliesslich die Zusammenstellung ihrer drei
 * Angaben.
 *
 * Rechts steht die naechste Frist - und zwar durch DeadlineBadge, den einzigen
 * Renderpfad fuer Fristdaten. Sie ersetzt das Chevron: ein Chevron sagt
 * "hier geht es weiter", die Frist sagt, ob es sich lohnt.
 */
export function PolicyRow({ policy }: { policy: InsurancePolicy }) {
  const router = useRouter();
  const title = policyTitle(policy);

  /**
   * Ohne eigene Benennung traegt die Zeile Sparte UND Versicherer. Zwei
   * Hausratpolicen bei verschiedenen Gesellschaften saehen sonst identisch
   * aus - ausgerechnet in der Liste, aus der man am Schadenstag die richtige
   * heraussucht. Hat die Police eine eigene Benennung ("Kfz Kombi"), ist sie
   * bereits unterscheidbar und der Versicherer steht im Detail.
   */
  const heading = title === policy.line_label ? `${title} · ${policy.insurer_name}` : title;

  /**
   * Der Status steht VOR dem Zwecktext, nicht als farbiges Abzeichen daneben:
   * eine gekuendigte Police, die aussieht wie eine laufende, ist die
   * gefaehrlichste Zeile dieser Liste. "aktiv" wird nicht angezeigt - es ist
   * der Normalfall und beantwortet keine Frage.
   */
  const description =
    policy.status === "active"
      ? policy.line_purpose_short
      : `${POLICY_STATUS_LABEL[policy.status]} · ${policy.line_purpose_short}`;

  return (
    // Seitenrand an der Zeile, nicht an der Liste: die Policenliste in
    // app/mehr/versicherungen.tsx scrollt randlos (der Trenner rueckt selbst
    // ein), und DataRow bringt keinen eigenen Seitenabstand mit. Sobald die
    // Zeilen dort in eine <Card>
    // wandern, kann diese Huelle ersatzlos weg.
    <View style={styles.inset}>
      <DataRow
        title={heading}
        subtitle={description}
        // Kachelfarbe aus dem Versicherer, nicht aus der Sparte: in einer nach
        // Sparten gegliederten Liste haetten sonst alle Zeilen eines
        // Abschnitts dieselbe Farbe und die Kachel traege gar nichts mehr bei.
        avatarLabel={policy.insurer_name}
        trailing={<DeadlineBadge policy={policy} align="end" />}
        onPress={() => router.push(`/versicherung/${policy.id}`)}
        accessibilityRole="link"
        // Schriftgewicht, Punktform und Farbe der Frist existieren fuer eine
        // Vorlesehilfe nicht. Ohne diesen Satz klaenge eine geratene Frist
        // genau wie eine geprüfte.
        accessibilityLabel={`${heading}. ${description}. ${deadlineAccessibilityLabel(policy)}`}
        // Die Liste zeichnet ihre Trennlinien selbst (ItemSeparatorComponent),
        // sonst stuenden zwischen zwei Zeilen zwei Linien.
        divider={false}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  inset: {
    paddingHorizontal: space.lg,
  },
});
