import asyncio
import logging
import sys

import dramatiq
from periodiq import cron

from worker.ai.orchestrator import process_classification_run
from worker.ai.task_orchestrator import drain_queue, recover_queues
from worker.broker import setup_broker
from worker.contracts.orchestrator import run_reconcile, run_reconcile_all
from worker.imports.orchestrator import process_import_run
from worker.imports.processor import fail_stale_import_runs
from worker.insurance.scan import run_insurance_scan_all
from worker.job_context import job_id_var
from worker.notifications.contract_alerts import check_contract_alerts
from worker.notifications.due_recurring import check_due_recurring
from worker.notifications.forecast_alerts import check_forecast_alerts
from worker.notifications.insurance_alerts import check_insurance_alerts
from worker.tracing import job_span

if sys.platform == "win32":
    # psycopg (async) kann nicht mit Windows' Default-ProactorEventLoop
    # arbeiten (siehe services/api/app/main.py fuer dieselbe Begruendung).
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Muss vor den @dramatiq.actor-Dekoratoren stehen: Dramatiq bindet jeden Actor
# beim Dekorieren fest an den dann aktiven Broker. Die Prozess-Entrypoints
# (`dramatiq worker.actors`, `periodiq -v worker.actors`, siehe Dockerfile und
# docker-compose.yml) importieren genau dieses Modul als Broker-Modul und
# erwarten danach einen konfigurierten Broker; der API-Prozess kommt ueber
# app/api/v1/routes/{imports,ai}.py hierher, um Nachrichten einzureihen.
# setup_broker() ueberschreibt dabei ausdruecklich keinen bereits gesetzten
# Broker - ein Import dieses Moduls erzwingt also nie eine Redis-Konfiguration
# (siehe worker/broker.py und services/api/tests/conftest.py).
broker = setup_broker()

logger = logging.getLogger(__name__)


@dramatiq.actor(max_retries=3)
def ping(message: str = "pong") -> None:
    """Referenzactor fuer den Phase-0-Stack."""
    logger.info("ping actor called: %s", message)


@dramatiq.actor(max_retries=0, time_limit=5 * 60 * 1000)
def process_import(import_run_id: str, filename: str, file_content_b64: str) -> None:
    """Verarbeitet einen Importlauf: Format erkennen, parsen,
    Dublettenerkennung, Staging-Zeilen schreiben (Architekturplan Abschnitt
    7.3). Kein automatischer Retry - ein fehlgeschlagener Import bekommt
    einen Fehlerstatus, den der Nutzer sieht, statt stillschweigend erneut
    (evtl. mit denselben Fehlern) zu laufen."""
    token = job_id_var.set(import_run_id)
    try:
        with job_span("process_import", import_run_id=import_run_id, filename=filename):
            asyncio.run(process_import_run(import_run_id, filename, file_content_b64))
    finally:
        job_id_var.reset(token)


@dramatiq.actor(max_retries=0, time_limit=5 * 60 * 1000, periodic=cron("*/5 * * * *"))
def recover_stale_import_runs() -> None:
    """Alle 5 Minuten: Importlaeufe ohne Lebenszeichen beenden.

    Gegenstueck zu recover_ai_task_queues, aus demselben Grund: bricht der
    Worker mitten in der Verarbeitung weg, bleibt der Lauf auf 'analysing'
    stehen (bzw. auf 'pending', wenn ihn nie jemand abgeholt hat). Der Nutzer
    sieht dann einen Fortschritt, der nie endet, und kommt an die Datei nur
    ueber einen zweiten Upload heran - loeschen sperrt die API bei laufenden
    Status. Erst ein Fehlerstatus macht den Lauf wieder loeschbar und
    wiederholbar.

    max_retries=0 wie bei recover_ai_task_queues: der Lauf ist idempotent und
    kommt in fuenf Minuten ohnehin wieder."""
    failed = asyncio.run(fail_stale_import_runs())
    if failed:
        logger.warning("Importlaeufe ohne Lebenszeichen beendet: %s", len(failed))


@dramatiq.actor(max_retries=0, time_limit=6 * 60 * 60 * 1000)
def process_classification_batch(run_id: str) -> None:
    """Verarbeitet einen Batch-Klassifikationslauf (siehe worker/ai/processor.py).
    max_retries=0: dieselbe Begruendung wie process_import - ein fehlgeschlagener
    Lauf soll sichtbar als 'failed' enden statt teilweise bereits geschriebene
    Klassifikationen (llm_calls/classification_decisions/processed_count) blind
    erneut zu verarbeiten. Der Lauf umfasst bewusst alle offenen Buchungen und
    hat deshalb kein kuenstliches Zeilen- oder Monatslimit. Das grosszuegige
    Zeitlimit bleibt eine letzte Sicherung gegen einen dauerhaft blockierten
    Worker; Fortschritt und Abbruchwunsch werden je Buchung persistiert."""
    asyncio.run(process_classification_run(run_id))


@dramatiq.actor(max_retries=0, time_limit=6 * 60 * 60 * 1000)
def drain_ai_task_queue(household_id: str) -> None:
    """Verarbeitet die persistente KI-Queue eines Haushalts in DB-Reihenfolge."""
    token = job_id_var.set(f"ai-queue:{household_id}")
    try:
        with job_span("drain_ai_task_queue", household_id=household_id):
            asyncio.run(drain_queue(household_id))
    finally:
        job_id_var.reset(token)


@dramatiq.actor(max_retries=0, time_limit=5 * 60 * 1000, periodic=cron("* * * * *"))
def recover_ai_task_queues() -> None:
    """Weckt verlorene Queues und beendet Tasks mit veraltetem Heartbeat."""
    household_ids = asyncio.run(recover_queues())
    for household_id in household_ids:
        drain_ai_task_queue.send(str(household_id))


@dramatiq.actor(max_retries=1, time_limit=10 * 60 * 1000)
def reconcile_contracts(household_id: str) -> None:
    """Gleicht die Vertraege eines Haushalts gegen seine Buchungen ab (Phase 2
    des Vertragsplans, siehe worker/contracts/reconcile.py). Eingereiht nach
    jedem Import-Commit: frisch uebernommene Buchungen sind der einzige Anlass,
    zu dem sich das Ergebnis ausserhalb des naechtlichen Laufs aendern kann.

    max_retries=1 statt 0 wie bei process_import und
    process_classification_batch: dort wuerde ein Wiederholungsversuch einen
    sichtbaren Fehlerstatus ueberschreiben bzw. Teilergebnisse doppelt
    schreiben. Hier gibt es keinen Zustand, den der Nutzer sieht, und der Lauf
    ist idempotent (Faelligkeiten haengen an Vertrag + Faelligkeitsdatum, der
    Versionstrigger vergleicht Werte) - ein zweiter Versuch ist unschaedlich."""
    asyncio.run(run_reconcile(household_id))


@dramatiq.actor(max_retries=1, time_limit=30 * 60 * 1000, periodic=cron("0 3 * * *"))
def reconcile_all_contracts() -> None:
    """Taeglich 03:00: Vertragsabgleich ueber alle Haushalte.

    Das ist der eigentliche Zweck des Motors: eine ausgebliebene Zahlung
    erzeugt per Definition keine Buchung und damit auch keinen Import - ohne
    einen zeitgesteuerten Lauf faellt ein stillschweigend beendetes Abo
    niemals auf. 03:00 liegt bewusst vor notify_due_recurring (08:00), damit
    der Push-Lauf bereits auf fortgeschriebenen next_due_date-Werten arbeitet."""
    asyncio.run(run_reconcile_all())


@dramatiq.actor(max_retries=1, time_limit=30 * 60 * 1000, periodic=cron("30 3 * * *"))
def scan_insurance_policies() -> None:
    """Taeglich 03:30: naechtlicher Versicherungslauf ueber alle Haushalte mit
    Policen (P2, siehe worker/insurance/scan.py und Plan Abschnitt 8.2).

    03:30 hat zwei Nachbarn und einen eigenen Grund:
    - reconcile_all_contracts (03:00) muss davor liegen: der Lauf repariert
      Policen auf zusammengefuehrten Vertraegen und meldet Statusdivergenzen
      zwischen Police und Vertrag. Beides liest den Vertragszustand, den der
      Abgleich in derselben Nacht gerade fortgeschrieben hat.
    - notify_insurance_alerts (08:45) liest, was hier entstanden ist - vor
      allem die umgehaengten contract_id-Werte, die darueber entscheiden,
      welcher der beiden Fristen-Pushes zustaendig ist.

    **Warum ein eigener Actor und kein dritter Schritt in reconcile_contracts,
    wie Plan 8.2 ihn vorsieht:** der Vertragsabgleich laeuft ueber
    `list_reconcilable_households`, also ueber Haushalte mit mindestens einem
    laufenden Vertrag. Eine Police, deren Beitrag der Arbeitgeber zahlt oder
    die beitragsfrei ist, hat per Definition keinen Vertrag; ein Haushalt mit
    ausschliesslich solchen Policen kaeme in dieser Liste nie vor und wuerde nie
    geprueft. Genau diese Faelle sind der Grund, warum die Police ein eigenes
    Fachobjekt mit eigenem Schluessel ist - sie duerfen nicht an der
    Haushaltsauswahl des Vertragsmotors haengen.

    max_retries=1 wie reconcile_all_contracts und nicht 0 wie die
    Push-Actors: der Lauf schreibt nur eine einzige Sache (contract_id vom
    Grabstein auf den Zielvertrag) und ist darin idempotent - ein zweiter
    Versuch findet die Police bereits am Ziel und aendert nichts. Es gibt hier
    keinen Versand, der sich wiederholen koennte."""
    asyncio.run(run_insurance_scan_all())


@dramatiq.actor(max_retries=1, time_limit=2 * 60 * 1000, periodic=cron("0 8 * * *"))
def notify_due_recurring() -> None:
    """Taeglich 08:00: Push-Benachrichtigung fuer in Kuerze faellige Fixkosten
    (Phase 9, Architekturplan Abschnitt 13). Geplant durch den separaten
    `periodiq`-Scheduler-Prozess (siehe docker-compose.yml), ausgefuehrt vom
    normalen Worker-Prozess wie jeder andere Actor."""
    asyncio.run(check_due_recurring())


@dramatiq.actor(max_retries=0, time_limit=5 * 60 * 1000, periodic=cron("30 8 * * *"))
def notify_contract_alerts() -> None:
    """Taeglich 08:30: Push-Benachrichtigungen zu Preisaenderung, ausgebliebener
    Zahlung und ablaufender Kuendigungsfrist (Phase 4, siehe
    worker/notifications/contract_alerts.py). Ein Actor fuer alle drei Arten -
    es ist eine einzige Abfrage, drei Actors waeren nur dreimal so viele
    Fehlerquellen.

    08:30 hat zwei Nachbarn: reconcile_all_contracts (03:00) schreibt die
    Ereignisse, die dieser Lauf liest - laege er davor, ginge jeder Hinweis
    einen Tag zu spaet raus. Und notify_due_recurring (08:00) hat eine halbe
    Stunde Vorsprung, damit die taeglichen Fixkostenerinnerungen nicht mit den
    Vertragshinweisen in einer einzigen Benachrichtigungstraube auf dem
    Sperrbildschirm landen (Zeitlimit dort: zwei Minuten).

    max_retries=0 als einziger Unterschied zu den uebrigen periodischen Actors:
    die Ereignisse werden erst NACH dem Versand als benachrichtigt markiert
    (sonst verschluckte ein Abbruch die Nachricht dauerhaft). Ein
    Wiederholungsversuch nach einem Fehler zwischen Versand und Markierung
    stellte dieselben Nachrichten erneut zu - und eine doppelte Push-Nachricht
    ist die einzige Fehlerart, die der Nutzer unmittelbar sieht. Verloren geht
    dabei nichts: was unmarkiert bleibt, holt der Lauf am naechsten Morgen."""
    asyncio.run(check_contract_alerts())


@dramatiq.actor(max_retries=0, time_limit=5 * 60 * 1000, periodic=cron("45 8 * * *"))
def notify_insurance_alerts() -> None:
    """Taeglich 08:45: Push zu ordentlichen Kuendigungsfristen von Policen ohne
    Vertrag und Rueckfragen zu angenommenen Ankerdaten (P2, siehe
    worker/notifications/insurance_alerts.py und Plan Abschnitt 8.3).

    08:45, weil 08:00 notify_due_recurring und 08:30 notify_contract_alerts
    belegt sind; die Staffelung verhindert eine Nachrichtentraube auf dem
    Sperrbildschirm. Der Abstand zu 08:30 ist zugleich fachlich noetig: fuer
    eine Police MIT Vertrag sendet contract_alerts bereits contract_term_ending,
    dieser Lauf ergaenzt ausschliesslich die Policen OHNE Vertrag.

    max_retries=0 aus demselben Grund wie bei notify_contract_alerts: notified_at
    wird erst NACH dem Versand geschrieben (sonst verschluckte ein Abbruch die
    Nachricht dauerhaft). Ein Wiederholungsversuch nach einem Fehler dazwischen
    stellte dieselben Nachrichten erneut zu, und eine doppelte Push-Nachricht ist
    die einzige Fehlerart, die der Nutzer unmittelbar sieht. Verloren geht dabei
    nichts: was unmarkiert bleibt, holt der Lauf am naechsten Morgen."""
    asyncio.run(check_insurance_alerts())


@dramatiq.actor(max_retries=0, time_limit=30 * 60 * 1000, periodic=cron("0 13 * * *"))
def notify_forecast_alerts() -> None:
    """Taeglich 13:00: Push-Benachrichtigungen zur Liquiditaetsvorschau (Stufe
    5d, siehe worker/notifications/forecast_alerts.py). Ein Actor fuer alle
    fuenf Typen - sie fallen aus DEMSELBEN Rechenlauf, fuenf Actors waeren
    fuenf Vorschauberechnungen je Haushalt und Nacht.

    13:00 hat drei Nachbarn und einen eigenen Grund:
    - reconcile_all_contracts (03:00) muss davor liegen: der Lauf liest
      missed_periods und die fortgeschriebenen Faelligkeiten, aus denen die
      Vorschau ihre Zahlungen bildet. Davor gerechnet waere sie einen Tag alt.
    - notify_due_recurring (08:00) und notify_contract_alerts (08:30) bilden
      bereits einen Morgenblock. Ein dritter Lauf um 09:00 haette drei Pushes
      in einer Stunde ergeben - genau die Traube, die die gestaffelten Zeiten
      vermeiden sollen.
    - Der Mittag ist zudem der sachlich richtige Zeitpunkt: wer erfaehrt, dass
      sein Konto in Kuerze unter den Puffer faellt, kann am selben Werktag noch
      handeln (umbuchen, Bank anrufen). Eine Abendmeldung verschoebe die
      Reaktion um einen Tag.

    Zeitlimit 30 Minuten wie reconcile_all_contracts und nicht zwei wie
    notify_due_recurring: dies ist der einzige Benachrichtigungslauf, der je
    Haushalt eine vollstaendige Vorschau rechnet.

    max_retries=0 aus demselben Grund wie notify_contract_alerts: der Zustand
    wird erst NACH dem Versand fortgeschrieben. Ein Wiederholungsversuch nach
    einem Fehler dazwischen stellte dieselben Nachrichten erneut zu; verloren
    geht nichts, denn der Zustandswechsel steht am naechsten Tag noch genauso
    an."""
    asyncio.run(check_forecast_alerts())
