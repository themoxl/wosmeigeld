import * as Notifications from "expo-notifications";
import { useRouter } from "expo-router";
import { useEffect } from "react";

/**
 * Alle Benachrichtigungstypen, die auf einen einzelnen Vertrag zeigen.
 *
 * `recurring_due` heisst seit Migration 0014 fachlich "Vertrag faellig", der
 * Typname bleibt aber unveraendert: bereits ausgelieferte App-Versionen und
 * bereits auf Geraeten liegende Benachrichtigungen kennen nur diesen Wert.
 * Die drei `contract_*`-Typen kommen aus Phase 4
 * (services/worker/worker/notifications/contract_alerts.py).
 */
const CONTRACT_NOTIFICATION_TYPES = [
  "recurring_due",
  "contract_price_change",
  "contract_payment_missing",
  "contract_term_ending",
] as const;

type ContractNotificationType = (typeof CONTRACT_NOTIFICATION_TYPES)[number];

function isContractNotificationType(value: unknown): value is ContractNotificationType {
  return (
    typeof value === "string" && (CONTRACT_NOTIFICATION_TYPES as readonly string[]).includes(value)
  );
}

/**
 * Benachrichtigungen der Liquiditaetsvorschau
 * (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 5d). Alle vier Ausloeser
 * aus Plan-Abschnitt 9 - erstmals unter dem Puffer, ein bisher unauffaelliger
 * Monat wird kritisch, erwartetes Einkommen bleibt aus, die Daten sind zu alt
 * zum Warnen - plus der Engpassfall selbst.
 *
 * Alle fuenf fuehren auf DIESELBE Seite, und das ist Absicht: die Vorschau
 * ist die vollstaendige Antwort auf jede dieser Meldungen. Eine eigene
 * Detailroute je Code waere fuenf Seiten fuer denselben Sachverhalt. Sie
 * tragen bewusst keine ID: die Vorschau speichert nichts, es gibt also kein
 * Objekt, auf das eine ID zeigen koennte (Leitsatz 1).
 */
const FORECAST_NOTIFICATION_TYPES = [
  "forecast_shortfall",
  "forecast_below_buffer",
  "forecast_month_critical",
  "forecast_income_missing",
  "forecast_data_stale",
] as const;

type ForecastNotificationType = (typeof FORECAST_NOTIFICATION_TYPES)[number];

function isForecastNotificationType(value: unknown): value is ForecastNotificationType {
  return (
    typeof value === "string" && (FORECAST_NOTIFICATION_TYPES as readonly string[]).includes(value)
  );
}

/**
 * Benachrichtigungen des Versicherungsmoduls
 * (docs/VERSICHERUNGEN-PLAN.md 8.3, P2 bis P5 -
 * services/worker/worker/notifications/insurance_alerts.py).
 *
 * Alle vier fuehren auf DIESELBE Detailseite, und zwar auf die einer einzelnen
 * Police: `insurance_deadline_ordinary` ist der Countdown auf eine belegte
 * ordentliche Frist, `insurance_main_due_question` die Bitte, das nur geratene
 * Ankerdatum zu bestaetigen, `insurance_special_termination` der Countdown auf
 * ein laufendes Sonderkuendigungsfenster nach einer Beitragsanpassung (P3),
 * `insurance_claim_followup` der Hinweis auf einen Vorgang ohne Rueckmeldung
 * (P5). Alles davon wird an genau einer Stelle beantwortet - auf der Police.
 * Eine eigene Route je Typ waere viermal derselbe Bildschirm.
 *
 * **Ein hier fehlender Typ ist kein stiller Fehler, sondern eine tote
 * Benachrichtigung:** der Worker versendet sie, der Nutzer tippt sie an, und
 * nichts geschieht. Wer in `insurance_alerts.py` einen fuenften Typ ergaenzt,
 * ergaenzt ihn hier mit.
 *
 * Anders als die Vorschau (oben) tragen sie eine ID: hinter ihnen steht ein
 * gespeichertes Objekt. Bei der zusammengefassten Rueckfrage ist es die erste
 * betroffene Police; die vollstaendige Liste steht auf der Uebersicht. Beim
 * Sonderfenster ist es die Police, nicht die Mitteilung - der Datenteil traegt
 * bewusst keine Vorgangs-ID (siehe INSURANCE_ALLOWED_DATA_KEYS). Beim
 * Schadenvorgang gilt dasselbe, und zwar noch strenger: er kann ganz ohne
 * Police bestehen (der Auffahrunfall, bei dem die Gegenseite reguliert), dann
 * kommt er sogar ohne jede ID - der Zweig unten faengt das mit der Uebersicht
 * auf.
 */
const INSURANCE_NOTIFICATION_TYPES = [
  "insurance_deadline_ordinary",
  "insurance_main_due_question",
  "insurance_special_termination",
  "insurance_claim_followup",
] as const;

type InsuranceNotificationType = (typeof INSURANCE_NOTIFICATION_TYPES)[number];

function isInsuranceNotificationType(value: unknown): value is InsuranceNotificationType {
  return (
    typeof value === "string" && (INSURANCE_NOTIFICATION_TYPES as readonly string[]).includes(value)
  );
}

/**
 * Die einzigen Felder, die im Datenteil einer Versicherungs-Benachrichtigung
 * vorkommen duerfen - deckungsgleich mit dem, was der Worker sendet
 * (`build_push_messages` in insurance_alerts.py: `type`, `policy_id` und bei
 * der zusammengefassten Rueckfrage `policy_count`).
 *
 * Plan 7.1 ist hier strenger als die Vertragsregel: auf dem gesperrten Geraet
 * erscheint WEDER Betrag NOCH Person NOCH Versicherungsart, weil
 * "Berufsunfaehigkeit" oder "Pflegezusatz" eine Gesundheitsangabe ist, die
 * jeder im Raum mitliest. Keines der drei Felder darf also ueberhaupt bis zum
 * Geraet kommen.
 */
const INSURANCE_ALLOWED_DATA_KEYS: readonly string[] = ["type", "policy_id", "policy_count"];

/**
 * Meldet, wenn eine Versicherungs-Benachrichtigung mehr Daten mitbringt als
 * erlaubt.
 *
 * **Das ist kein Routing, sondern eine Reissleine.** Die eigentliche Zusage
 * gibt der Worker und sie wird in services/worker/tests/test_insurance_alerts.py
 * geprueft; diese Stelle faengt den Fall ab, dass jemand dort spaeter ein Feld
 * ergaenzt. Ein zusaetzliches Feld ist gefaehrlich, noch bevor es angezeigt
 * wird: was im Datenteil liegt, ist der Vorrat, aus dem eine kuenftige lokale
 * Benachrichtigung ihren Text anreichern koennte - und die stuende dann auf dem
 * Sperrbildschirm.
 *
 * Geloggt werden ausschliesslich die Feld-NAMEN. Eine Warnung, die den Betrag
 * oder den Namen der Person mitschreibt, waere genau das Leck, das sie meldet.
 */
function warnOnEnrichedInsurancePayload(data: unknown): void {
  if (typeof data !== "object" || data === null) return;
  const unexpected = Object.keys(data).filter((key) => !INSURANCE_ALLOWED_DATA_KEYS.includes(key));
  if (unexpected.length === 0) return;
  console.warn(
    "Versicherungs-Push traegt unerwartete Datenfelder (Sperrbildschirm, Plan 7.1):",
    unexpected.join(", "),
  );
}

/**
 * Leitet beim Antippen einer Push-Benachrichtigung zur passenden Route
 * weiter (Phase 9). Muss innerhalb des Expo-Router-<Stack>-Kontexts
 * aufgerufen werden (braucht useRouter()) - siehe app/_layout.tsx.
 *
 * Der Router liest nur `data` und ruft `router.push` - er baut, plant und
 * veraendert keine Benachrichtigung. Das ist fuer die Versicherungstypen
 * Bedingung, nicht Zufall: sobald hier eine lokale Benachrichtigung mit
 * angereichertem Titel entstuende, stuende der angereicherte Text auf dem
 * gesperrten Geraet, und die Zusage aus Plan 7.1 waere gebrochen. Titel und
 * Rumpf bleiben so, wie der Worker sie neutral gesendet hat.
 */
export function useNotificationResponseRouter(): void {
  const router = useRouter();

  useEffect(() => {
    const subscription = Notifications.addNotificationResponseReceivedListener((response) => {
      const data = response.notification.request.content.data;
      if (isContractNotificationType(data?.type)) {
        const contractId = typeof data.contract_id === "string" ? data.contract_id : null;
        // Ohne Vertrags-ID (aeltere Nachricht, geloeschter Vertrag) bleibt die
        // Vertragsuebersicht der sinnvollste Landeplatz - eine Detailseite zu
        // einer unbekannten ID waere eine Fehlermeldung.
        router.push(contractId ? `/vertrag/${contractId}` : "/vertraege");
        return;
      }
      if (isForecastNotificationType(data?.type)) {
        router.push("/mehr/vorschau");
        return;
      }
      if (isInsuranceNotificationType(data?.type)) {
        warnOnEnrichedInsurancePayload(data);
        const policyId = typeof data.policy_id === "string" ? data.policy_id : null;
        // Ohne Policen-ID (geloeschte Police, aeltere Nachricht) faengt die
        // Uebersicht auf - dieselbe Begruendung wie beim Vertragszweig: eine
        // Detailseite zu einer unbekannten ID waere eine Fehlermeldung.
        router.push(policyId ? `/versicherung/${policyId}` : "/mehr/versicherungen");
      }
    });

    return () => subscription.remove();
  }, [router]);
}
