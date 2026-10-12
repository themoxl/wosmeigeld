"""Herkunft einer Kategoriezuordnung schreiben und lesen.

Hintergrund (docs/ANALYSE-EXPLORER-PLAN.md, Baustein A): `transaction_splits`
trug bis Migration 0008 keine Information darueber, wie die Kategorie
zustande kam. Der Regeltreffer beim Import wurde berechnet und sofort
verworfen, die KI schrieb nur in `classification_decisions`, und die manuellen
Pfade schrieben ein blankes `category_id`. Die Frage "warum ist diese Buchung
in dieser Kategorie" war damit nicht beantwortbar.

Dieses Modul haelt das SET-Fragment und die Parameter an genau einer Stelle.
Die Alternative - dieselben sieben Spalten in sechs Routen einzeln zu
schreiben - hat schon einmal dazu gefuehrt, dass ein Schreibpfad die
Versionierung vergisst; das soll sich bei der Herkunft nicht wiederholen.

Geschrieben wird ausschliesslich hierueber; gelesen wird die Herkunft von
`GET /v1/transactions/{id}/provenance`.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.provenance import (
    ImportOrigin,
    SplitProvenance,
    TransactionProvenance,
)

CategorySource = Literal["unset", "unknown", "manual", "import", "rule", "ai", "history"]

# SET-Fragment fuer ein UPDATE auf transaction_splits. Bewusst inklusive
# category_id: Kategorie und Herkunft duerfen nie getrennt geschrieben werden,
# sonst entsteht ein Split, dessen Herkunft eine andere Kategorie beschreibt.
CATEGORY_ASSIGNMENT_SET_SQL: Final = (
    "category_id = :category_id, "
    "category_source = :category_source, "
    "category_rule_id = :category_rule_id, "
    "category_decision_id = :category_decision_id, "
    "category_confidence = :category_confidence, "
    "category_matched_on = cast(:category_matched_on as jsonb), "
    "categorized_at = case when :category_source = 'unset' then null else now() end, "
    "categorized_by = :categorized_by"
)

# Spaltenliste und Platzhalter fuer ein INSERT derselben Felder.
CATEGORY_ASSIGNMENT_INSERT_COLUMNS: Final = (
    "category_id, category_source, category_rule_id, category_decision_id, "
    "category_confidence, category_matched_on, categorized_at, categorized_by"
)
CATEGORY_ASSIGNMENT_INSERT_VALUES: Final = (
    ":category_id, :category_source, :category_rule_id, :category_decision_id, "
    ":category_confidence, cast(:category_matched_on as jsonb), "
    "case when :category_source = 'unset' then null else now() end, :categorized_by"
)


def _base_params(category_id: UUID | None) -> dict[str, Any]:
    return {
        "category_id": str(category_id) if category_id else None,
        "category_source": "unset",
        "category_rule_id": None,
        "category_decision_id": None,
        "category_confidence": None,
        "category_matched_on": None,
        "categorized_by": None,
    }


def manual_assignment(
    category_id: UUID | None,
    actor_user_id: UUID | None,
    *,
    source: CategorySource = "manual",
) -> dict[str, Any]:
    """Parameter fuer eine Zuordnung durch einen Menschen.

    Wird die Kategorie entfernt (category_id=None), faellt die Herkunft auf
    'unset' zurueck - eine Buchung ohne Kategorie hat keine Herkunft, und ein
    stehengebliebener Regelverweis waere schlicht falsch."""
    params = _base_params(category_id)
    if category_id is not None:
        params["category_source"] = source
        params["categorized_by"] = str(actor_user_id) if actor_user_id else None
    return params


def rule_assignment(
    category_id: UUID,
    rule_id: UUID,
    rule_name: str,
    criteria: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    """Parameter fuer eine Zuordnung durch eine Klassifikationsregel.

    `rule_name` und die Kriterien werden mitgeschrieben statt nur die ID:
    die Regel kann spaeter umbenannt, geaendert oder geloescht werden, die
    Begruendung an der Buchung muss trotzdem lesbar bleiben."""
    params = _base_params(category_id)
    params["category_source"] = "rule"
    params["category_rule_id"] = str(rule_id)
    params["category_matched_on"] = json.dumps(
        {"rule_name": rule_name, "criteria": criteria}, ensure_ascii=False
    )
    return params


# classification_decisions.source -> transaction_splits.category_source.
#
# Nicht jeder Vorschlag stammt vom Modell: der Vorfilter des Stapellaufs
# (services/worker/worker/ai/known_payees.py) leitet die Kategorie aus dem
# eigenen Bestand ab und schreibt seinen Entscheid mit source='history' und
# ohne model_name. Wuerde die Uebernahme das pauschal als 'ai' protokollieren,
# behauptete die Herkunftsansicht eine Modellaussage, die es nie gab - und die
# Kennzahl "wie gut ist die KI" zaehlte Treffer mit, an denen sie nicht
# beteiligt war.
#
# Alles Unbekannte gilt als 'ai': das ist der gesamte Bestand bisheriger
# Entscheide, und ein neuer, hier noch nicht bedachter Wert darf keine
# Zuordnung verhindern.
_DECISION_SOURCE_TO_CATEGORY_SOURCE: Final[dict[str, CategorySource]] = {
    "ai": "ai",
    "history": "history",
}

# Entscheidungsherkuenfte, die eine Uebernahme in der Review-Inbox belegen
# koennen. 'rule' und 'manual' stehen bewusst nicht dabei: solche Entscheide
# schreibt heute niemand, und sie haetten am Split eine eigene, genauere
# Herkunft (rule_assignment/manual_assignment).
_PROPOSAL_DECISION_SOURCES: Final = ("ai", "history")


def decision_assignment(
    category_id: UUID,
    decision_id: UUID | None,
    confidence: float | None,
    *,
    decision_source: str = "ai",
    actor_user_id: UUID | None = None,
) -> dict[str, Any]:
    """Parameter fuer die Uebernahme eines Vorschlags aus
    `classification_decisions` - gleich ob vom Modell oder aus dem Bestand.

    `actor_user_id` bleibt gesetzt, weil ein Vorschlag heute nur durch eine
    menschliche Bestaetigung an die Buchung gelangt - wer bestaetigt hat, ist
    Teil der Herkunft."""
    params = _base_params(category_id)
    params["category_source"] = _DECISION_SOURCE_TO_CATEGORY_SOURCE.get(decision_source, "ai")
    params["category_decision_id"] = str(decision_id) if decision_id else None
    params["category_confidence"] = confidence
    params["categorized_by"] = str(actor_user_id) if actor_user_id else None
    return params


def ai_assignment(
    category_id: UUID,
    decision_id: UUID | None,
    confidence: float | None,
    *,
    actor_user_id: UUID | None = None,
) -> dict[str, Any]:
    """Parameter fuer die Uebernahme eines KI-Vorschlags (Sonderfall von
    `decision_assignment` fuer Aufrufer, die ohnehin nur Modellentscheide
    kennen)."""
    return decision_assignment(
        category_id,
        decision_id,
        confidence,
        decision_source="ai",
        actor_user_id=actor_user_id,
    )


async def find_matching_decision(
    session: AsyncSession, household_id: UUID, transaction_id: UUID, category_id: UUID
) -> tuple[UUID, float, str] | None:
    """Sucht den juengsten Vorschlag, der genau diese Kategorie fuer diese
    Buchung vorgeschlagen hat. Rueckgabe: (Entscheid-ID, Konfidenz, Herkunft).

    Damit laesst sich die Uebernahme in der Review-Inbox als 'ai' bzw.
    'history' statt als 'manual' protokollieren, ohne dass der Client
    behaupten muesste, woher der Vorschlag kam. Gibt es keinen passenden
    Entscheid, war es eine eigene Wahl des Nutzers - dann bleibt es 'manual'.

    Die Herkunft wird mitgegeben statt angenommen: seit dem Vorfilter fuer
    bekannte Empfaenger gibt es Vorschlaege, an denen kein Modell beteiligt
    war."""
    row = (
        await session.execute(
            text(
                "select id, confidence, source from classification_decisions "
                "where household_id = :household_id and transaction_id = :transaction_id "
                "and category_id = :category_id and source = any(:sources) "
                "order by created_at desc limit 1"
            ),
            {
                "household_id": str(household_id),
                "transaction_id": str(transaction_id),
                "category_id": str(category_id),
                "sources": list(_PROPOSAL_DECISION_SOURCES),
            },
        )
    ).first()
    if row is None:
        return None
    return row.id, float(row.confidence), str(row.source)


async def load_transaction_provenance(
    session: AsyncSession, household_id: UUID, transaction_id: UUID
) -> TransactionProvenance | None:
    """Traegt alles zusammen, was zur Herkunft einer Buchung bekannt ist.

    Kategorienamen werden bewusst OHNE Archivfilter aufgeloest: eine
    historische Zuordnung auf eine inzwischen archivierte Kategorie ist der
    Normalfall und darf in der Herkunftsansicht nicht als Luecke erscheinen
    (Kategorisierungsplan, "Historie, Budgets und Berichte")."""
    tx = (
        await session.execute(
            text(
                "select t.id, t.payee_label, t.note, t.payee_raw, t.purpose_raw, "
                "t.bank_transaction_code, t.source, t.external_reference, "
                "t.fingerprint, t.import_run_id, ir.filename as import_filename, "
                "ir.detected_format as import_format, ir.created_at as import_created_at, "
                # Skalare Unterabfrage statt JOIN: zeigten je zwei Staging-Zeilen
                # auf dieselbe Buchung, wuerde ein JOIN die Buchung verdoppeln.
                "(select r.row_number from import_rows r "
                " where r.committed_transaction_id = t.id "
                " order by r.row_number asc limit 1) as import_row_number "
                "from transactions t "
                "left join import_runs ir on ir.id = t.import_run_id "
                "where t.id = :id and t.household_id = :household_id"
            ),
            {"id": str(transaction_id), "household_id": str(household_id)},
        )
    ).first()
    if tx is None:
        return None

    split_rows = (
        await session.execute(
            text(
                "select s.id, s.amount_minor, s.category_id, c.name as category_name, "
                "c.is_archived as category_is_archived, cg.name as category_group_name, "
                "s.category_source, s.category_rule_id, r.name as rule_name, "
                "r.priority as rule_priority, r.is_active as rule_is_active, "
                "s.category_decision_id, d.model_name, d.reason_code, "
                "s.category_confidence, s.category_matched_on, s.categorized_at, "
                "s.categorized_by "
                "from transaction_splits s "
                "left join categories c on c.id = s.category_id "
                "left join category_groups cg on cg.id = c.group_id "
                "left join classification_rules r on r.id = s.category_rule_id "
                "left join classification_decisions d on d.id = s.category_decision_id "
                "where s.transaction_id = :transaction_id "
                "and s.household_id = :household_id "
                "order by s.created_at asc, s.id asc"
            ),
            {"transaction_id": str(transaction_id), "household_id": str(household_id)},
        )
    ).all()

    splits = [
        SplitProvenance(
            split_id=s.id,
            amount_minor=s.amount_minor,
            category_id=s.category_id,
            category_name=s.category_name,
            category_group_name=s.category_group_name,
            category_is_archived=bool(s.category_is_archived)
            if s.category_is_archived is not None
            else None,
            category_source=s.category_source,
            rule_id=s.category_rule_id,
            rule_name=s.rule_name,
            rule_priority=s.rule_priority,
            rule_is_active=s.rule_is_active,
            matched_on=s.category_matched_on,
            decision_id=s.category_decision_id,
            model_name=s.model_name,
            reason_code=s.reason_code,
            confidence=float(s.category_confidence)
            if s.category_confidence is not None
            else None,
            categorized_at=s.categorized_at,
            categorized_by=s.categorized_by,
        )
        for s in split_rows
    ]

    import_origin = None
    if tx.import_run_id is not None:
        import_origin = ImportOrigin(
            import_run_id=tx.import_run_id,
            filename=tx.import_filename,
            detected_format=tx.import_format,
            row_number=tx.import_row_number,
            imported_at=tx.import_created_at,
        )

    return TransactionProvenance(
        transaction_id=tx.id,
        source=tx.source,
        payee_label=tx.payee_label,
        payee_raw=tx.payee_raw,
        purpose_raw=tx.purpose_raw,
        note=tx.note,
        bank_transaction_code=tx.bank_transaction_code,
        external_reference=tx.external_reference,
        fingerprint=tx.fingerprint,
        import_origin=import_origin,
        splits=splits,
    )
