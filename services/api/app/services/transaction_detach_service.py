"""Loest alles, was auf eine Buchung zeigt, BEVOR sie aus dem Bestand genommen
wird.

WARUM ES DIESEN DIENST GIBT
---------------------------
Buchungen werden nie geloescht, sondern archiviert (`transactions.is_archived`).
Die Buchung verschwindet damit aus Listen, Budgets, Auswertungen und Salden -
ihre Verknuepfungen verschwinden aber nicht mit. Ohne Aufraeumen behielte ein
Vertrag eine Faelligkeit im Zustand 'matched' und behauptete eine Zahlung, deren
Beleg nicht mehr im Bestand steht; ein Erstattungsvorgang rechnete weiter mit
einer Buchung, die es fuer den Nutzer nicht mehr gibt; eine Staging-Zeile bliebe
als Dublette einer Buchung stehen, die niemand mehr findet.

Der naheliegende Gegenentwurf - jede lesende Abfrage auf `not is_archived`
filtern - wurde verworfen. Eine Bestandsaufnahme ueber alle Module fand fuenfzehn
Lesepfade, die genau diesen Filter vergessen hatten. Ein Filter muss an jeder
kuenftigen Stelle erneut mitgedacht werden und wird irgendwann wieder vergessen;
das Aufraeumen an der Quelle haelt die Zusage strukturell: KEINE Zeile verweist
auf eine archivierte Buchung. Wer danach eine neue Auswertung schreibt, kann sich
darauf verlassen, ohne es zu wissen.

WAS BEWUSST STEHEN BLEIBT
-------------------------
Alles, was der Buchung selbst gehoert statt auf sie zu zeigen:
  - `transaction_splits` samt Kategorie- und Sparzielzuordnung. Sie sind Teil der
    Buchung und werden mit ihr archiviert; die Sparzielsumme zaehlt ausdruecklich
    nur nicht-archivierte Buchungen (goal_service.fetch_goal_deposits_by_goal).
  - Alles, was eine Tatsache ueber ein ANDERES Objekt festhaelt: dass sich ein
    Vertragsbetrag geaendert hat, bleibt wahr, auch wenn der Beleg dafuer
    verschwindet. Dort faellt nur der Verweis weg, nicht die Zeile.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text


class TransactionDetachForbiddenError(Exception):
    """Der Verweis liegt in einem Bereich, den dieser Nutzer nicht raeumen darf.

    Der Vorgang wird dann ganz abgebrochen statt halb ausgefuehrt: eine halb
    geloeste Buchung waere schlimmer als eine, die stehen bleibt."""


# Was geloest wurde, in der Sprache der Folge und nicht in der der Tabelle: den
# Nutzer interessiert, dass eine Faelligkeit wieder offen ist - nicht, dass eine
# Zeile in contract_occurrences zurueckgesetzt wurde.
DETACH_LABELS: dict[str, str] = {
    "umbuchung": "Umbuchungspaare getrennt",
    "umbuchungskennzeichen": "Umbuchungskennzeichen zurueckgenommen",
    "vertrag_faelligkeit": "Vertragsfaelligkeiten wieder offen",
    "vertrag_betragshistorie": "Betragsaenderungen ohne Beleg",
    "vertrag_zuordnung": "Vertragszuordnungen aufgehoben",
    "erstattung": "Erstattungszuordnungen entfernt",
    "versicherung": "Versicherungszahlungen entfernt",
    "schadenanker": "Fristanker nur noch geschaetzt",
    "einkauf": "Einkaufsverknuepfungen geloest",
    "importzeile": "Dublettenmarkierungen zurueckgenommen",
}

# Jede Spalte im Schema, die auf eine Buchung zeigt, mit der Entscheidung, was
# beim Entfernen der Buchung mit ihr geschieht. Die Liste ist nicht nur
# Dokumentation: tests/test_transaction_detach_completeness.py liest den
# Datenbankkatalog und schlaegt fehl, sobald eine Spalte dazukommt, die hier
# nicht steht. Damit kann eine neue Tabelle nicht mehr stillschweigend
# durchrutschen - genau so sind die fuenfzehn Lesepfade entstanden, die dieses
# Modul behebt.
#
# "geloest"  - der Verweis wird beim Entfernen aufgeraeumt (unten in dieser Datei)
# "gehoert"  - die Zeile gehoert der Buchung und wird mit ihr archiviert
# "herkunft" - die Zeile haelt fest, WOHER etwas kam; sie zu loesen zerstoerte
#              den Nachweis. Ihre Lesepfade filtern stattdessen selbst.
REFERENCE_DECISIONS: dict[tuple[str, str], str] = {
    ("transfer_links", "outgoing_transaction_id"): "geloest",
    ("transfer_links", "incoming_transaction_id"): "geloest",
    ("contract_occurrences", "transaction_id"): "geloest",
    ("contract_amount_history", "source_transaction_id"): "geloest",
    ("contract_assignment_overrides", "transaction_id"): "geloest",
    ("health_transaction_allocations", "transaction_id"): "geloest",
    ("insurance_claim_payments", "transaction_id"): "geloest",
    ("insurance_claims", "negotiations_closed_transaction_id"): "geloest",
    ("shopping_purchase_links", "transaction_id"): "geloest",
    ("import_rows", "duplicate_of_transaction_id"): "geloest",
    # Teil der Buchung selbst - Kategorie, Betrag und Sparzielanteil werden mit
    # ihr archiviert. Die Sparzielsumme zaehlt ausdruecklich nur
    # nicht-archivierte Buchungen (goal_service.fetch_goal_deposits_by_goal).
    ("transaction_splits", "transaction_id"): "gehoert",
    # Welche Staging-Zeile zu welcher Buchung wurde - der Herkunftsnachweis des
    # Imports. Ohne ihn liesse sich hinterher nicht mehr sagen, aus welcher
    # Datei eine Buchung stammt.
    ("import_rows", "committed_transaction_id"): "herkunft",
    # Dismissals refer to a reviewed version; scans only compare active bookings.
    ("transaction_duplicate_dismissals", "first_transaction_id"): "herkunft",
    ("transaction_duplicate_dismissals", "second_transaction_id"): "herkunft",
    # Protokoll der Kategorieentscheidungen (Regel/Historie/KI). Es haelt fest,
    # WARUM etwas so kategorisiert wurde, und dient dem Lernen ueber den
    # Empfaenger hinweg; die Vorschlagsliste filtert selbst
    # (ai_proposal_service._OPEN_PROPOSAL_SQL: "and not t.is_archived").
    ("classification_decisions", "transaction_id"): "herkunft",
    # Ausdrueckliche Entscheidung des Vorhaben-Moduls, im Architekturplan als
    # P4-Abnahmekriterium festgehalten: "Archivierung entfernt sie aus Ist ohne
    # Linkverlust". Die Ist-Summe filtert deshalb selbst
    # (project_service._actuals_by_cost_item).
    ("project_cost_actuals", "transaction_id"): "herkunft",
    # Die Erstattungsmarke haengt an genau dieser Buchung; ihre Listen- und
    # Summenpfade filtern bereits auf nicht-archivierte Buchungen
    # (reimbursement_mark_service).
    ("transaction_reimbursement_marks", "transaction_id"): "herkunft",
}


def as_labelled(detached: dict[str, int]) -> list[dict[str, Any]]:
    """Formt das Ergebnis fuer die Ausgabe an den Nutzer um."""
    return [
        {"kind": schluessel, "label": DETACH_LABELS[schluessel], "count": anzahl}
        for schluessel, anzahl in sorted(detached.items())
        if schluessel in DETACH_LABELS
    ]


async def existing_tables(session: AsyncSession, namen: set[str]) -> set[str]:
    """Welche dieser Tabellen gibt es in DIESER Datenbank?

    Die Frage ist nicht theoretisch: eine Installation kann hinter dem Schema
    zurueckliegen. In einer aelteren Installation fehlten die Tabellen des
    Schadenmoduls vollstaendig - eine Abfrage darauf haette dort nicht "nichts gefunden",
    sondern das Loeschen JEDER Buchung mit einem Fehler abgebrochen, weil
    Postgres schon beim Planen ueber den unbekannten Namen stolpert.

    Bewusst zur Laufzeit gefragt statt beim Start zwischengespeichert: die
    Antwort aendert sich, sobald das Schema nachgezogen wird, und ein
    Zwischenspeicher haette einen Neustart noetig gemacht, um das zu bemerken.
    Die Abfrage ist ein einzelner Katalogzugriff.
    """
    return {
        str(row.name)
        for row in (
            await session.execute(
                text(
                    "select t.name from unnest(cast(:namen as text[])) as t(name) "
                    "where to_regclass('public.' || t.name) is not null"
                ),
                {"namen": sorted(namen)},
            )
        ).all()
    }


async def detach_transactions(
    session: AsyncSession, household_id: UUID, tx_ids: list[Any]
) -> dict[str, int]:
    """Loest saemtliche Verweise auf die genannten Buchungen.

    Aufzurufen unmittelbar VOR dem Archivieren, in derselben Transaktion: ein
    Zwischenzustand, in dem die Buchung schon aus dem Bestand ist und die
    Verknuepfung noch zeigt, soll gar nicht erst entstehen.

    Tabellen, die es in dieser Datenbank nicht gibt, werden uebersprungen - siehe
    existing_tables. Das ist keine Nachsicht gegenueber Schema-Drift, sondern die
    Bedingung dafuer, dass eine aeltere Installation ueberhaupt weiterlaeuft:
    was nicht existiert, kann auch nicht auf eine Buchung zeigen.

    Gibt je Aufraeumart die Anzahl zurueck - das geht so in das Audit-Ereignis und
    in die Rueckmeldung an den Nutzer.
    """
    # Lokal importiert: beide Dienste haengen ihrerseits am Buchungsbestand, ein
    # Modulimport auf Dateiebene schluesse den Kreis.
    from app.services import insurance_claim_service, reimbursement_service

    if not tx_ids:
        return {}

    vorhanden = await existing_tables(session, {tabelle for tabelle, _ in REFERENCE_DECISIONS})

    geloest: dict[str, int] = {}
    params: dict[str, Any] = {"household_id": str(household_id), "ids": tx_ids}

    def _zaehle(schluessel: str, result: Any) -> None:
        anzahl = cast(CursorResult[Any], result).rowcount
        if anzahl > 0:
            geloest[schluessel] = anzahl

    # --- Umbuchungen ---------------------------------------------------------
    # Die Gegenbuchung auf dem anderen Konto bleibt bestehen und steht danach
    # wieder unverknuepft da. Sie mitzuarchivieren waere ein Uebergriff: sie ist
    # von diesem Vorgang nicht betroffen.
    #
    # ENTSCHEIDEND ist das Zuruecksetzen von is_transfer auf der Gegenbuchung
    # (dasselbe tut DELETE /v1/transfers/link/{id}, routes/transfers.py). Ohne
    # das behielte sie das Umbuchungskennzeichen ohne Gegenstueck - und jede
    # Auswertung filtert Umbuchungen heraus (budget_service, analytics_service,
    # insights_service, reports). Die Buchung waere im Bestand sichtbar, in
    # Budget und Berichten aber unsichtbar, und ueber die Oberflaeche nicht mehr
    # zu reparieren: die Link-ID ist geloescht, is_transfer laesst sich per PATCH
    # nicht setzen, und ein neuer Link scheitert an der Eindeutigkeit.
    aufgeloeste_paare = (
        await session.execute(
            text(
                "delete from transfer_links where household_id = :household_id "
                "and (outgoing_transaction_id = any(:ids) "
                "or incoming_transaction_id = any(:ids)) "
                "returning outgoing_transaction_id, incoming_transaction_id"
            ),
            params,
        )
    ).all()
    beteiligte = {str(zeile.outgoing_transaction_id) for zeile in aufgeloeste_paare} | {
        str(zeile.incoming_transaction_id) for zeile in aufgeloeste_paare
    }
    if aufgeloeste_paare:
        geloest["umbuchung"] = len(aufgeloeste_paare)

    # Umbuchungen AUS DEM IMPORT haben gar keine transfer_links-Zeile: sowohl
    # commit_import_run als auch die Review-Inbox setzen is_transfer auf beiden
    # Seiten per UPDATE, ohne je zu verknuepfen (einziger Schreiber von
    # transfer_links ist POST /v1/transfers/link). Ueber die Abfrage oben waere
    # die Gegenbuchung deshalb nie gefunden worden.
    #
    # Auffindbar ist sie ueber die Staging-Zeile, die die Umbuchung erkannt hat:
    # sie kennt beide Seiten - ihre eigene Buchung in committed_transaction_id
    # und die Gegenbuchung in duplicate_of_transaction_id.
    aus_dem_import = (
        await session.execute(
            text(
                "select committed_transaction_id, duplicate_of_transaction_id "
                "from import_rows "
                "where household_id = :household_id "
                "and duplicate_of_transaction_id is not null "
                "and (committed_transaction_id = any(:ids) "
                "     or duplicate_of_transaction_id = any(:ids))"
            ),
            params,
        )
    ).all()
    entfernte = {str(tx) for tx in tx_ids}
    for zeile in aus_dem_import:
        paar = {str(zeile.committed_transaction_id), str(zeile.duplicate_of_transaction_id)}
        # Nur die Gegenseite anfassen - die entfernte Buchung wird ohnehin
        # archiviert, und eine Zeile, deren beide Seiten gehen, hat keine
        # Ueberlebende.
        beteiligte |= paar - entfernte - {"None"}

    if beteiligte:
        # `not exists` als Sicherung: eine Buchung, die ueber ein ANDERES, noch
        # bestehendes Paar verknuepft ist, darf ihr Kennzeichen behalten.
        _zaehle(
            "umbuchungskennzeichen",
            await session.execute(
                text(
                    "update transactions t set is_transfer = false "
                    "where t.household_id = :household_id and t.id = any(:ids) "
                    "and t.is_transfer "
                    "and not exists (select 1 from transfer_links l "
                    "                 where l.outgoing_transaction_id = t.id "
                    "                    or l.incoming_transaction_id = t.id)"
                ),
                {"household_id": str(household_id), "ids": sorted(beteiligte)},
            ),
        )

    # --- Vertraege -----------------------------------------------------------
    # Zwei Arten von Faelligkeit, zwei Behandlungen.
    #
    # (a) Vom Abgleich erkannt: die Faelligkeit gehoert zum Raster des Vertrags
    #     (der 1. des Monats, der 15. des Quartals ...). Sie bleibt stehen und
    #     faellt auf 'missing' zurueck - nach dem Entfernen des Belegs ist das
    #     schlicht die Wahrheit: die Periode gab es, bezahlt ist sie nicht mehr.
    #
    # (b) Von Hand zugeordnet ("Umhaengen", contract_service.apply_reassign):
    #     dort ist due_date der BUCHUNGSTAG, keine geplante Faelligkeit. Als
    #     'missing' stehengelassen behauptete sie eine Faelligkeit zum 17.06.,
    #     die es im Raster nie gab - der Vertrag zeigte eine erfundene offene
    #     Zahlung, und repair_missing_periods suchte fortan Kandidaten dafuer.
    #     Sie wird deshalb geloescht. apply_reassign selbst macht es beim
    #     Umhaengen genauso.
    #
    # Erkennbar sind (b) an ihrer Handzuordnung in contract_assignment_overrides
    # - die wird weiter unten geloest, muss hier also VORHER gelesen werden.
    #
    # KEIN Zurueckdrehen von contracts.next_due_date: der Abgleich findet offene
    # Fehlperioden ohnehin unabhaengig davon
    # (contract_matching_service.repair_missing_periods liest genau
    # "status = 'missing' and transaction_id is null"). Ein least() auf
    # next_due_date haette dagegen ueber alle dazwischenliegenden Perioden
    # zurueckgesetzt und den Motor gezwungen, sie erneut zu bewerten - beim
    # Entfernen einer alten Buchung waeren so Jahre an Faelligkeiten neu
    # aufgerollt und reihenweise als 'missing' festgeschrieben worden.
    handzuordnungen = {
        str(zeile.transaction_id)
        for zeile in (
            await session.execute(
                text(
                    "select transaction_id from contract_assignment_overrides "
                    "where household_id = :household_id and transaction_id = any(:ids)"
                ),
                params,
            )
        ).all()
    }
    if handzuordnungen:
        _zaehle(
            "vertrag_faelligkeit",
            await session.execute(
                text(
                    "delete from contract_occurrences "
                    "where household_id = :household_id and transaction_id = any(:ids)"
                ),
                {"household_id": str(household_id), "ids": sorted(handzuordnungen)},
            ),
        )

    vom_abgleich = [tx for tx in tx_ids if str(tx) not in handzuordnungen]
    if vom_abgleich:
        anzahl = cast(
            CursorResult[Any],
            await session.execute(
                text(
                    "update contract_occurrences set status = 'missing', "
                    "transaction_id = null, booked_date = null, amount_minor = null, "
                    "amount_delta_minor = null, days_delta = null, match_score = null, "
                    "matched_at = null "
                    "where household_id = :household_id and transaction_id = any(:ids)"
                ),
                {"household_id": str(household_id), "ids": vom_abgleich},
            ),
        ).rowcount
        if anzahl > 0:
            geloest["vertrag_faelligkeit"] = geloest.get("vertrag_faelligkeit", 0) + anzahl

    # Die Betragshistorie bleibt: dass sich der Betrag geaendert hat, ist eine
    # Tatsache ueber den Vertrag und nicht ueber die Buchung. Nur der Verweis auf
    # den Beleg faellt weg.
    _zaehle(
        "vertrag_betragshistorie",
        await session.execute(
            text(
                "update contract_amount_history set source_transaction_id = null "
                "where household_id = :household_id and source_transaction_id = any(:ids)"
            ),
            params,
        ),
    )

    # Eine Handzuordnung ist eine Aussage ueber genau diese Buchung. Ohne sie
    # gibt es nichts mehr auszusagen.
    _zaehle(
        "vertrag_zuordnung",
        await session.execute(
            text(
                "delete from contract_assignment_overrides "
                "where household_id = :household_id and transaction_id = any(:ids)"
            ),
            params,
        ),
    )

    # --- Erstattungen --------------------------------------------------------
    # Die Zuordnung geht mit, und der Vorgang wird danach neu gerechnet: sonst
    # stuende er weiter mit dem vollen "Erhalten"-Betrag da und gaelte als
    # abgeschlossen.
    zuordnungen = (
        (
            await session.execute(
                text(
                    "select distinct case_id, allocation_type "
                    "from health_transaction_allocations "
                    "where household_id = :household_id and transaction_id = any(:ids)"
                ),
                params,
            )
        ).all()
        if "health_transaction_allocations" in vorhanden
        else []
    )
    if zuordnungen:
        _zaehle(
            "erstattung",
            await session.execute(
                text(
                    "delete from health_transaction_allocations "
                    "where household_id = :household_id and transaction_id = any(:ids)"
                ),
                params,
            ),
        )
        # Die Zeilen des Erstattungsmoduls stehen unter einer eigenen
        # Zugriffsregel (health_can_manage_person): ein Haushaltsmitglied ohne
        # Gesundheitsfreigabe SIEHT die Zuordnung nicht und darf sie nicht
        # loeschen. Das DELETE oben traefe dann still keine Zeile, die Buchung
        # wuerde trotzdem archiviert - und der Vorgang rechnete auf ewig mit
        # Geld, das im Bestand nicht mehr steht. Wer nicht aufraeumen darf, darf
        # die Buchung auch nicht entfernen; die Ausnahme rollt alles zurueck.
        verbliebene = (
            await session.execute(
                text(
                    "select count(*) from health_transaction_allocations "
                    "where household_id = :household_id and transaction_id = any(:ids)"
                ),
                params,
            )
        ).scalar_one()
        if int(verbliebene) > 0:
            raise TransactionDetachForbiddenError(
                "Diese Buchung ist einem Erstattungsvorgang zugeordnet, auf den du "
                "keinen Zugriff hast. Sie laesst sich deshalb nicht entfernen - "
                "sonst rechnete der Vorgang weiter mit einem Betrag, dessen Beleg "
                "verschwunden ist. Bitte jemanden mit Zugriff auf das "
                "Erstattungsmodul die Zuordnung aufheben lassen."
            )
        for zeile in zuordnungen:
            await reimbursement_service._reconcile_case_after_allocation(
                session, household_id, UUID(str(zeile.case_id)), str(zeile.allocation_type)
            )

    # --- Versicherungsschaeden ----------------------------------------------
    # Die Zahlungszeile wird GELOESCHT, nicht nur vom Beleg geloest. Grund:
    # insurance_claim_payments.transaction_id darf null sein und bedeutet dann
    # etwas Bestimmtes - eine Direktzahlung des Versicherers an einen Dritten,
    # die in _reconcile paid_to_third_party setzt. Bloss den Verweis zu leeren
    # machte aus einer entfernten Buchung stillschweigend eine Direktzahlung und
    # liesse den Betrag weiter als reguliert gelten.
    betroffene_schaeden = (
        (
            await session.execute(
                text(
                    "select distinct claim_id from insurance_claim_payments "
                    "where household_id = :household_id and transaction_id = any(:ids)"
                ),
                params,
            )
        ).all()
        if "insurance_claim_payments" in vorhanden
        else []
    )
    if betroffene_schaeden:
        _zaehle(
            "versicherung",
            await session.execute(
                text(
                    "delete from insurance_claim_payments "
                    "where household_id = :household_id and transaction_id = any(:ids)"
                ),
                params,
            ),
        )
        for zeile in betroffene_schaeden:
            await insurance_claim_service._reconcile(
                session, household_id, UUID(str(zeile.claim_id))
            )

    # Der Fristanker des Schadenvorgangs: das Datum, an dem die Verhandlungen
    # als abgeschlossen gelten, und damit die Grundlage des
    # Sonderkuendigungsrechts. Steht es auf "belegt", haengt es an genau dieser
    # Buchung.
    #
    # Das Schema hat den Fall vorgesehen - der Trigger validate_insurance_claim
    # stuft die Sicherheit von 'belegt' auf 'geschaetzt' zurueck, sobald der
    # Verweis null wird ("Faellt der Kontoumsatz weg (Import zurueckgenommen,
    # Buchung geloescht), bleibt das Datum stehen - aber es ist nur noch
    # geschaetzt"). Ausgeloest wurde das nie: die ON-DELETE-SET-NULL-Regel des
    # Fremdschluessels greift nur beim echten Loeschen, und geloescht wird hier
    # nichts. Das UPDATE unten loest den Trigger dagegen aus.
    if "insurance_claims" in vorhanden:
        _zaehle(
            "schadenanker",
            await session.execute(
                text(
                    "update insurance_claims set negotiations_closed_transaction_id = null "
                    "where household_id = :household_id "
                    "and negotiations_closed_transaction_id = any(:ids)"
                ),
                params,
            ),
        )

    # --- Einkaeufe -----------------------------------------------------------
    _zaehle(
        "einkauf",
        await session.execute(
            text(
                "delete from shopping_purchase_links "
                "where household_id = :household_id and transaction_id = any(:ids)"
            ),
            params,
        ),
    )

    # --- Noch nicht uebernommene Importzeilen --------------------------------
    # Eine Staging-Zeile, die als Dublette oder Umbuchungskandidat GEGEN eine
    # dieser Buchungen markiert wurde, hat ihren Grund verloren: das Gegenstueck
    # steht nicht mehr im Bestand. Sie faellt deshalb auf 'new' zurueck.
    #
    # Das ist mehr als Kosmetik. Vorher blieb die Warnung in der Review-Inbox
    # stehen und zaehlte im Gesamtzaehler mit; bestaetigte der Nutzer sie, wurde
    # die Zeile auf 'skipped' gesetzt und die Buchung fehlte am Ende ganz - der
    # Betrag galt als "schon vorhanden", war aber nirgends. Beim
    # Umbuchungskandidaten war es spiegelbildlich: die Bestaetigung legte eine
    # Umbuchung ohne Gegenseite an, die aus Budget und Berichten faellt.
    #
    # Nur offene Zeilen: 'committed' und 'skipped' sind abgeschlossene Vorgaenge,
    # die nachtraeglich zu veraendern hiesse, Entscheidungen umzuschreiben.
    _zaehle(
        "importzeile",
        await session.execute(
            text(
                "update import_rows set status = 'new', duplicate_of_transaction_id = null "
                "where household_id = :household_id "
                "and duplicate_of_transaction_id = any(:ids) "
                "and status in ('duplicate_exact', 'duplicate_possible', 'transfer_candidate')"
            ),
            params,
        ),
    )

    return geloest
