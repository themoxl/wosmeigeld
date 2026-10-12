import { Redirect } from "expo-router";

// Manuelle Buchungsanlage entfaellt aus der Mobile-App
// (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 5, "Keine manuelle
// Neuanlage"): der kanonische Zufluss neuer Buchungen ist der
// Kontoauszug-Import. Dieser Deep Link bleibt fuer eine Uebergangsrelease
// erhalten (z. B. alte Homescreen-Verknuepfungen, Push-Ziele), leitet aber
// nur noch zum Import weiter statt ein Formular zu zeigen.
//
// API-POST (createTransaction) und die Verarbeitung bereits persistierter
// Offline-Create-Eintraege bleiben bewusst funktionsfaehig (siehe
// ../src/hooks/useTransactionMutations.ts und ../src/offline/queueProcessor.ts)
// - nur dieser UI-Eingang fuer NEUE manuelle Buchungen verschwindet.
export default function NeueBuchungScreen() {
  return <Redirect href="/mehr/import" />;
}
