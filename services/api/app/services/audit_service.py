"""Audit-Service: schreibt Aenderungen als versionierte Ereignisse (Phase 4).

Erfuellt das Abnahmekriterium "Regelaenderungen sind auditierbar". Der Kern
(`record_audit_event`) ist bewusst entitaetsunabhaengig gehalten, damit spaeter
weitere Entitaeten ohne Schemaaenderung andocken koennen; angebunden ist
aktuell ausschliesslich die Klassifikationsregel (`RULE_ENTITY_TYPE`).

Die Versionsnummer selbst zaehlt der Datenbank-Trigger
`classification_rules_version_trg` hoch (siehe
supabase/migrations/0001_schema.sql); der Service haelt lediglich den
Zustand vor und nach der Aenderung fest.
"""

import json
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

AuditAction = Literal[
    "create",
    "update",
    "delete",
    # Ab der Kategorienverwaltung (Migration 0007 weitet den CHECK auf
    # audit_log.action entsprechend auf). Ein Merge oder eine Archivierung mit
    # Referenzaufloesung ist fachlich kein "update" - ohne eigene Verben waere
    # im Log nicht mehr erkennbar, was tatsaechlich passiert ist.
    "archive",
    "restore",
    "move",
    "reorder",
    "merge",
    "taxonomy_apply",
    # Angenommener KI-Kategorievorschlag (Migration 0010): legt Kategorien an
    # und ordnet die zugehoerigen Buchungen zu.
    "suggestion_apply",
    # Rueckwirkender Regellauf ueber den Buchungsbestand. Traegt die Regel als
    # Entitaet, die veraenderten Buchungen im Metadatenfeld.
    "rule_apply",
    # Vertraege Phase 5 (Abschnitt 19.7): eine Zusammenfuehrung rueckgaengig
    # gemacht bzw. eine Buchung manuell einem Vertrag zu- oder abgeordnet -
    # beides mehr als "update" und nicht dasselbe wie "merge".
    "merge_undo",
    "reassign",
    # Importzentrum: ein abgebrochener bzw. mit korrigierter Spaltenzuordnung
    # erneut eingelesener Lauf. Als "update" gefuehrt waere hinterher nicht
    # mehr erkennbar, dass die Staging-Zeilen des Laufs verworfen und neu
    # aufgebaut wurden - genau die Frage, die ein Abweichen von der
    # hochgeladenen Datei spaeter erklaeren muss.
    "cancel",
    "reparse",
]

RULE_ENTITY_TYPE = "classification_rule"
CATEGORY_ENTITY_TYPE = "category"
CATEGORY_GROUP_ENTITY_TYPE = "category_group"

# Spaltenliste des Regel-Schnappschusses - identisch zu den Feldern, die die
# API nach aussen gibt, plus der Versionsnummer.
RULE_SNAPSHOT_COLUMNS = (
    "id, household_id, name, priority, is_active, match_payee_raw, match_payee_exact_word, "
    "match_purpose, match_purpose_exact_word, exclude_payee_raw, exclude_purpose, "
    "match_min_amount_minor, match_max_amount_minor, match_amount_mode, match_account_id, "
    "action_payee_id, action_category_id, action_set_transfer, action_note, "
    "action_reimbursable, action_expectation_mode, action_expectation_rate, "
    "action_expectation_minor, source, version"
)


def _to_jsonb(snapshot: dict[str, Any] | None) -> str | None:
    """Serialisiert einen Schnappschuss fuer die jsonb-Spalte. `default=str`
    faengt UUID-, Datums- und Decimal-Werte ab, ohne dass jeder Aufrufer sie
    vorher von Hand konvertieren muss."""
    if snapshot is None:
        return None
    return json.dumps(snapshot, default=str, ensure_ascii=False)


async def record_audit_event(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    entity_type: str,
    entity_id: UUID,
    action: AuditAction,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    correlation_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Schreibt ein einzelnes Audit-Ereignis in der laufenden Transaktion des
    Aufrufers - das Ereignis wird also genau dann sichtbar, wenn auch die
    auditierte Aenderung committet wird.

    `correlation_id` klammert die Einzelereignisse eines zusammengesetzten
    Vorgangs (Merge, Archivierung mit Referenzaufloesung, Paket-Upgrade) zu
    einem lesbaren Vorgang."""
    await session.execute(
        text(
            "insert into audit_log (household_id, actor_user_id, entity_type, entity_id, "
            "action, before, after, correlation_id, metadata) "
            "values (:household_id, :actor_user_id, :entity_type, :entity_id, :action, "
            "cast(:before as jsonb), cast(:after as jsonb), :correlation_id, "
            "cast(:metadata as jsonb))"
        ),
        {
            "household_id": str(household_id),
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "action": action,
            "before": _to_jsonb(before),
            "after": _to_jsonb(after),
            "correlation_id": str(correlation_id) if correlation_id else None,
            "metadata": _to_jsonb(metadata),
        },
    )


async def fetch_rule_snapshot(
    session: AsyncSession, household_id: UUID, rule_id: UUID
) -> dict[str, Any] | None:
    """Liest den aktuellen Zustand einer Klassifikationsregel als Schnappschuss
    fuer das Audit-Log. Gibt None zurueck, wenn die Regel nicht (mehr) zum
    Haushalt gehoert."""
    res = await session.execute(
        text(
            f"select {RULE_SNAPSHOT_COLUMNS} from classification_rules "
            "where id = :id and household_id = :household_id"
        ),
        {"id": str(rule_id), "household_id": str(household_id)},
    )
    row = res.first()
    if row is None:
        return None
    return dict(row._mapping)


async def record_rule_event(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    rule_id: UUID,
    action: AuditAction,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Bequemlichkeits-Wrapper fuer Regelereignisse - haelt den Entitaetstyp an
    einer Stelle fest, damit Auswertungen nicht auf getippte Strings treffen."""
    await record_audit_event(
        session,
        household_id,
        actor_user_id,
        RULE_ENTITY_TYPE,
        rule_id,
        action,
        before=before,
        after=after,
    )


async def record_category_event(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    category_id: UUID,
    action: AuditAction,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    correlation_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Ereignis zu einer Kategorie. Den Schnappschuss liefert
    `category_service.fetch_category_snapshot()`."""
    await record_audit_event(
        session,
        household_id,
        actor_user_id,
        CATEGORY_ENTITY_TYPE,
        category_id,
        action,
        before=before,
        after=after,
        correlation_id=correlation_id,
        metadata=metadata,
    )


async def record_category_group_event(
    session: AsyncSession,
    household_id: UUID,
    actor_user_id: UUID | None,
    group_id: UUID,
    action: AuditAction,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    correlation_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await record_audit_event(
        session,
        household_id,
        actor_user_id,
        CATEGORY_GROUP_ENTITY_TYPE,
        group_id,
        action,
        before=before,
        after=after,
        correlation_id=correlation_id,
        metadata=metadata,
    )
