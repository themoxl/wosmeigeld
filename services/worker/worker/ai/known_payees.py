"""Vorfilter des Stapellaufs: bekannte Empfaenger ohne Modellaufruf zuordnen.

Ausgangslage: ein erheblicher Teil der offenen Buchungen traegt einen
Empfaenger, der im Bestand laengst kategorisiert ist. Diese
Buchungen kosten je einen vollen LM-Studio-Aufruf (1-8s, ~7.000 Token
Kategorienliste), um eine Antwort zu erzeugen, die bereits in der eigenen
Datenbank steht.

WARUM EIGENES MODUL UND NICHT IN classifier.py:
`worker/ai/classifier.py` ist der Zwilling von
`services/api/app/services/ai_service.py` und muss dort wortgleich bleiben
(docs/KI-KATEGORISIERUNG.md). Der Vorfilter gehoert aber bewusst NUR in den
Stapellauf: `POST /v1/ai/classify` ist der Knopf "jetzt das Modell fragen" -
dort erwartet der Nutzer einen echten Modellaufruf, keine Wiederholung dessen,
was er schon weiss. Neben dem Zwilling statt in ihm hinein zu bauen haelt die
Zwillingsregel unberuehrt: Prompttext, JSON-Schema, Sentinelwerte und die
Reason Codes der Modellklassifikation aendern sich durch dieses Modul nicht.

DIE DREI REGELN DES VORFILTERS:

1. NUR MENSCHLICHE ODER REGELBASIERTE VORBILDER (`_PRECEDENT_SOURCES`).
   Eine von der KI stammende Zuordnung ist als Vorbild ausgeschlossen. Sonst
   entstuende eine Rueckkopplung: das Modell ordnet "WWW.MUSTERFOTO.DE"
   einmal falsch zu, der Vorfilter liest diese Zuordnung als Wahrheit und
   wiederholt den Fehler bei jeder weiteren Buchung desselben Empfaengers -
   ohne dass das Modell je die Gelegenheit bekaeme, es besser zu machen. Das
   ist die wichtigste Sicherung dieses Moduls.
   Ausdruecklich ausgeschlossen ist deshalb auch 'history': das ist die
   Herkunft, die eine uebernommene Zuordnung DIESES Vorfilters traegt. Ein
   Vorschlag darf sich nicht selbst zum Beleg erklaeren.
   'unknown' bleibt ebenfalls aussen vor - der Wert stammt aus dem Backfill
   fuer Splits, die vor Einfuehrung der Herkunftsspalten kategorisiert wurden,
   und sagt woertlich "Herkunft unbekannt". Ein Vorfilter, der auf Unbekanntes
   baut, waere keine Ableitung mehr, sondern eine Vermutung.

2. EINDEUTIGKEIT. Ein Schluessel wandert nur in den Index, wenn ALLE
   qualifizierten Zuordnungen dieses Empfaengers auf dieselbe Kategorie zeigen.
   Zwei verschiedene Kategorien (typisch: Amazon als Haushalt und als
   Geschenk) heissen: das Modell soll entscheiden, nicht der Vorfilter raten.
   Die Eindeutigkeit wird VOR dem Aussortieren archivierter Kategorien
   geprueft - sonst wuerde aus zwei widersprechenden Vorbildern durch das
   Archivieren des einen ein scheinbar eindeutiger Fall.

3. EXAKTER VERGLEICH, KEINE AEHNLICHKEIT. Normalisiert werden nur
   Schreibweise und Satzzeichen (`_payee_compare_key`). "REWE SAGT DANKE" und
   "Rewe Markt GmbH" bleiben verschiedene Empfaenger. Alles andere waere eine
   Heuristik, die genau das tut, was Leitplanke 8 verbietet: raten.

WAS DER VORFILTER NICHT TUT: Er schreibt keine Kategorie an die Buchung. Er
erzeugt einen Eintrag in `classification_decisions` - denselben Vorschlag, den
sonst das Modell erzeugt haette, nur mit `source = 'history'` und ohne
`model_name`. Uebernommen wird er wie jeder andere Vorschlag durch den Nutzer
("Alle sicheren uebernehmen" oder Einzelbestaetigung).

SELBSTKORREKTUR: Korrigiert der Nutzer eine so entstandene Zuordnung, wird
seine Korrektur zu einem Vorbild mit Herkunft 'manual' - die alte, falsche
Zuordnung bleibt es (sofern sie ueberhaupt eine war) ebenfalls. Damit hat der
Empfaenger zwei verschiedene Kategorien und faellt nach Regel 2 aus dem Index:
beim naechsten Lauf entscheidet wieder das Modell. Der Vorfilter kann denselben
falschen Wert also nicht ein zweites Mal vorschlagen, ohne dass dafuer eine
Sonderbehandlung noetig waere.

KEINE VOR-SCHREIB-AUFLOESUNG: Anders als der Modellpfad ruft dieses Modul kein
`resolve_for_write()`. Es braucht es nicht - der Index enthaelt ausschliesslich
aktive Kategorien in aktiven Gruppen und keine Merge-Tombstones, und weil hier
nur ein Vorschlag entsteht, loest ihn `ai_proposal_service` bei der Uebernahme
ohnehin erneut auf (Ergebnis dort: 'category_inactive' statt einer falschen
Zuordnung).
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from worker.ai.classifier import ClassificationResult

# Reason Code des Vorfilters. Bewusst sprechend und klar von den Reason Codes
# der Modellklassifikation getrennt: eine Auswertung muss "kam ohne Modell
# zustande" von "Modell hat entschieden" unterscheiden koennen.
KNOWN_PAYEE_REASON_CODE: Final = "known_payee"

# Herkunft in classification_decisions.source. 'history' ist im CHECK der
# Tabelle seit jeher erlaubt (supabase/migrations/0001_schema.sql), wurde aber
# von keinem Schreibpfad je benutzt - beide Klassifikationszwillinge schreiben
# hart 'ai'. Genau dieser Fall war gemeint.
KNOWN_PAYEE_SOURCE: Final = "history"

# Hoch, aber nicht 1.0.
#
# Hoch, weil hinter dem Vorschlag keine Schaetzung steht, sondern eine
# ausdrueckliche Entscheidung des Nutzers zu genau diesem Empfaenger, und weil
# er sonst nutzlos waere: "Alle sicheren uebernehmen" filtert gegen
# `auto_categorize_min_confidence` (Vorgabe 0.85). Ein Wert darunter hiesse,
# den Vorschlag zu erzeugen und im selben Atemzug unbrauchbar zu machen.
#
# Nicht 1.0, weil der Schluss "gleicher Empfaenger, also gleiche Kategorie"
# eine Ableitung bleibt, keine Gewissheit: derselbe Haendler kann fachlich in
# eine andere Kategorie gehoeren (Amazon: Haushalt oder Geschenk). 1.0 wuerde
# eine Sicherheit behaupten, die keine Datenlage hergibt, und in Auswertungen
# nicht mehr von einer Handzuordnung unterscheidbar sein.
KNOWN_PAYEE_CONFIDENCE: Final = 0.95

# Herkuenfte, die als Vorbild zaehlen (siehe Regel 1 im Moduldocstring).
# 'import' = die Kategorie stand in der importierten Datei, 'contract' = sie
# stammt aus einem vom Nutzer angelegten Vertrag. Beides sind Angaben des
# Nutzers bzw. seiner Daten, keine Modellausgabe.
_PRECEDENT_SOURCES: Final = ("manual", "rule")

# Ein Durchgang ueber den kategorisierten Bestand, EINMAL pro Lauf. Die
# Eindeutigkeit wird in Python entschieden und nicht per group by/having,
# damit die Herkunft der Entscheidung im Code lesbar bleibt und dieselbe
# Abfrage beide Angaben liefert (Vorbild UND Waehlbarkeit der Kategorie).
#
# Der household_id-Filter steht an jedem Tabellenbezug ausdruecklich: der
# Worker verbindet sich ohne RLS (siehe worker/db.py).
_PRECEDENT_SQL: Final = (
    "select t.payee_label, s.category_id, "
    "(not c.is_archived and not cg.is_archived "
    "and c.merged_into_category_id is null) as is_selectable "
    "from transactions t "
    "join transaction_splits s on s.transaction_id = t.id "
    "and s.household_id = t.household_id "
    "join categories c on c.id = s.category_id and c.household_id = t.household_id "
    "join category_groups cg on cg.id = c.group_id and cg.household_id = c.household_id "
    "where t.household_id = :household_id "
    "and not t.is_archived "
    # Umbuchungen bleiben aussen vor: der Stapellauf klassifiziert
    # ausschliesslich Nicht-Umbuchungen (_UNCLASSIFIED_SQL). Ein Vorbild aus
    # einer Umbuchung koennte deshalb nur schaden - es zoege eine echte
    # Ausgabe in die interne Uebertragung.
    "and not t.is_transfer "
    "and s.category_id is not null "
    "and s.category_source = any(:precedent_sources) "
    "and t.payee_label is not null and btrim(t.payee_label) <> ''"
)


def _payee_compare_key(label: str | None) -> str:
    """Vergleichsschluessel fuer Empfaengernamen.

    Zwilling zu `app/services/rules_service.py::_recurring_payee_key` - der
    Ausdruck ist wortgleich zu halten. Der Worker kann `app.*` nicht
    importieren (eigenes Paket, anderes Trust-Modell, siehe worker/db.py);
    dieselbe bewusste Duplikation wie bei `mask_pii_text` und
    `resolve_for_write` in classifier.py.

    Normalisiert werden nur Schreibweise und Satzzeichen. Aehnlich klingende
    Empfaenger werden NICHT heuristisch zusammengelegt - ein Vorfilter, der
    "REWE" und "REWE Getraenke" gleichsetzt, waere kein Nachschlagen mehr,
    sondern ein Rateschritt ohne Modell.

    Zusaetzlich zum API-Zwilling: leere Eingaben ergeben den leeren Schluessel.
    Der wird nie in den Index aufgenommen und trifft deshalb auch nie."""
    if not label:
        return ""
    return re.sub(r"[^\w]+", " ", label.casefold(), flags=re.UNICODE).strip()


@dataclass(frozen=True)
class KnownPayeeIndex:
    """Nachschlagewerk fuer einen Lauf: normalisierter Empfaenger -> Kategorie.

    `ambiguous_keys` traegt die bewusst NICHT aufgenommenen Empfaenger. Sie
    wird fuer die Zuordnung nicht gebraucht, macht aber pruefbar (und im Test
    sichtbar), dass ein widerspruechlicher Bestand still uebergangen und nicht
    versehentlich zugunsten irgendeiner Kategorie aufgeloest wurde."""

    category_by_key: dict[str, UUID]
    ambiguous_keys: frozenset[str]

    def lookup(self, payee_label: str | None) -> UUID | None:
        key = _payee_compare_key(payee_label)
        if not key:
            return None
        return self.category_by_key.get(key)


async def load_known_payees(session: AsyncSession, household_id: UUID) -> KnownPayeeIndex:
    """Baut den Index EINMAL pro Stapellauf.

    Dasselbe Muster wie `load_categories()`: eine Abfrage zu Laufbeginn statt
    einer je Buchung. Bei 171 offenen Buchungen waere die zweite Variante 171
    Abfragen fuer eine Antwort, die sich waehrend des Laufs nicht aendert."""
    rows = list(
        await session.execute(
            text(_PRECEDENT_SQL),
            {
                "household_id": str(household_id),
                "precedent_sources": list(_PRECEDENT_SOURCES),
            },
        )
    )

    categories_by_key: dict[str, set[str]] = defaultdict(set)
    selectable: dict[str, bool] = {}
    for row in rows:
        key = _payee_compare_key(row.payee_label)
        if not key:
            continue
        category_id = str(row.category_id)
        categories_by_key[key].add(category_id)
        # Dieselbe Kategorie kommt vielfach vor; der Zustand ist je Kategorie
        # gleich, das letzte Schreiben also unschaedlich.
        selectable[category_id] = bool(row.is_selectable)

    index: dict[str, UUID] = {}
    ambiguous: set[str] = set()
    for key, category_ids in categories_by_key.items():
        if len(category_ids) != 1:
            # Regel 2: widerspruechliche Historie ist keine Auskunft.
            ambiguous.add(key)
            continue
        only = next(iter(category_ids))
        if not selectable.get(only, False):
            # Seit dem Vorbild archiviert oder gemergt: ein Vorschlag auf eine
            # nicht mehr waehlbare Kategorie waere Arbeit fuer den Papierkorb.
            continue
        index[key] = UUID(only)

    return KnownPayeeIndex(category_by_key=index, ambiguous_keys=frozenset(ambiguous))


async def classify_known_payee(
    session: AsyncSession,
    household_id: UUID,
    transaction_id: UUID,
    payee_label: str | None,
    known: KnownPayeeIndex,
    ai_task_id: UUID | None = None,
) -> ClassificationResult | None:
    """Vorschlag aus dem Bestand statt aus dem Modell.

    Rueckgabe None heisst "hier weiss der Bestand nichts" - der Aufrufer
    schickt die Buchung dann wie bisher an das Modell. Nur bei einem Treffer
    wird geschrieben, und dann genau eine Zeile: der Vorschlag selbst. Eine
    `llm_calls`-Zeile entsteht bewusst NICHT - es gab keinen Aufruf, und eine
    Nullzeile dort wuerde jede Laufzeit- und Tokenauswertung verfaelschen."""
    category_id = known.lookup(payee_label)
    if category_id is None:
        return None

    payee_name = (payee_label or "").strip()
    await session.execute(
        text(
            "insert into classification_decisions (household_id, transaction_id, "
            "payee_name, category_id, confidence, reason_code, source, model_name, "
            "ai_task_id) "
            "values "
            # model_name bleibt NULL: kein Modell beteiligt. Ein Platzhalter
            # wie 'history' waere eine Falschaussage in der Herkunftsansicht.
            "(:household_id, :tx_id, :payee_name, :cat_id, :conf, :reason, :source, null, "
            ":ai_task_id)"
        ),
        {
            "household_id": str(household_id),
            "tx_id": str(transaction_id),
            # Unmaskiert: der Wert stammt aus der eigenen Datenbank und
            # verlaesst sie nicht. mask_pii_text() schuetzt den Weg zum
            # Modell - den gibt es hier gerade nicht.
            "payee_name": payee_name,
            "cat_id": str(category_id),
            "conf": KNOWN_PAYEE_CONFIDENCE,
            "reason": KNOWN_PAYEE_REASON_CODE,
            "source": KNOWN_PAYEE_SOURCE,
            "ai_task_id": str(ai_task_id) if ai_task_id else None,
        },
    )

    return ClassificationResult(
        payee_name=payee_name,
        category_id=category_id,
        confidence=KNOWN_PAYEE_CONFIDENCE,
        reason_code=KNOWN_PAYEE_REASON_CODE,
        source=KNOWN_PAYEE_SOURCE,
        model_name=None,
    )
