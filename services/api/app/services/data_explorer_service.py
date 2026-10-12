"""Datenbank-Explorer: alle Haushaltstabellen sehen, durchblaettern, loeschen.

Warum es das braucht: die Anwendung hat inzwischen 79 haushaltsbezogene
Tabellen, aber nur fuer einen Teil davon eine Oberflaeche mit Loeschfunktion.
Was ueber einen Import, einen KI-Lauf oder einen halb fertigen Versuch in die
Datenbank gekommen ist, war bisher teilweise nur noch mit einem SQL-Werkzeug
von aussen wieder herauszubekommen. Das ist bei eigenen Daten die falsche
Antwort.

Die drei Regeln, die dieses Modul sicher machen:

1. TABELLEN- UND SPALTENNAMEN KOMMEN NIE VOM CLIENT in eine Abfrage. Sie
   werden gegen den Katalog der Datenbank geprueft und nur dann eingesetzt,
   wenn sie dort exakt so vorkommen. Werte gehen ausschliesslich als
   gebundene Parameter.
2. JEDE ABFRAGE IST HAUSHALTSGEBUNDEN. Nur Tabellen mit `household_id` sind
   ueberhaupt sichtbar, und jede Bedingung enthaelt sie. RLS greift zusaetzlich
   - das hier ist die zweite Schranke, nicht die einzige.
3. GELOESCHT WIRD NUR MIT VORSCHAU. Ein `on delete cascade` reicht in diesem
   Schema bis zu vier Ebenen tief: eine Essenswoche nimmt Mahlzeiten,
   Einkaufslisten, deren Posten und die Kaufverknuepfungen mit. Wer das nicht
   vorher sieht, loescht eine Woche und verliert einen Monat.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Obergrenze fuer eine Seite. Der Explorer ist zum Anschauen da, nicht zum
# Exportieren - wer alles braucht, nimmt den Export der jeweiligen Domaene.
MAX_PAGE_SIZE: Final = 200
DEFAULT_PAGE_SIZE: Final = 50

# Wie tief die Kaskadenvorschau verfolgt wird. Vier Ebenen decken das tiefste
# vorhandene Gefaelle ab (meal_weeks -> shopping_lists -> shopping_items);
# die Grenze steht trotzdem da, damit ein spaeter eingefuehrter Zyklus nicht
# zu einer endlosen Abfragekette wird.
MAX_CASCADE_DEPTH: Final = 4

# Wie viele Schluessel je Ebene weitergereicht werden. Darueber wird nur noch
# gezaehlt, nicht mehr verfolgt - die Aussage "mehr als 5.000 betroffen" ist
# fuer die Entscheidung genauso gut wie eine exakte Zahl, und eine Abfrage mit
# 200.000 gebundenen UUIDs ist keine.
MAX_CASCADE_KEYS: Final = 5_000

# Tabellen, die zwar household_id tragen, aber im Explorer nichts zu suchen
# haben. Bewusst kurz gehalten - der Explorer soll zeigen, was da ist, nicht
# kuratieren.
_HIDDEN_TABLES: Final = frozenset(
    {
        # Das Audit-Log ist der Nachweis darueber, wer was geloescht hat.
        # Es aus dem Explorer heraus loeschbar zu machen hiesse, die
        # Nachvollziehbarkeit des Loeschens selbst loeschbar zu machen.
        "audit_log",
        # Verschluesselte API-Schluessel der KI-Anbieter (app/llm/keys.py).
        # Nichts zum Anschauen oder Bearbeiten - und ohne Eintrag im Katalog
        # wandern sie auch nicht in Sicherungen; nach einer Wiederherstellung
        # wird der Schluessel neu hinterlegt.
        "ai_provider_keys",
    }
)

# Fachliche Gruppierung fuer die Oberflaeche. Praefixbasiert, damit eine neu
# hinzugekommene Tabelle automatisch am richtigen Ort landet, statt
# stillschweigend unter "Sonstiges" zu verschwinden.
_GROUP_RULES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("Buchungen & Konten", ("transaction", "account", "transfer", "payee", "balance")),
    ("Import", ("import", "staging")),
    ("Kategorien & Regeln", ("category", "rule", "classification", "taxonomy")),
    ("Vertraege & Fixkosten", ("contract",)),
    ("Budget & Berichte", ("budget", "report")),
    ("Ziele & Vermoegen", ("goal", "wishlist", "asset", "debt", "networth")),
    ("Vorhaben", ("project", "funding", "scenario", "cost")),
    ("Lebensmittel", ("meal", "dish", "ingredient", "shopping", "grocery")),
    ("KI", ("ai_", "llm", "insight", "suggestion")),
    ("Haushalt & System", ("household", "member", "invite", "device", "profile", "provenance")),
)


class ExplorerError(ValueError):
    """Anfrage passt nicht zum Katalog - unbekannte Tabelle, unbekannte Spalte.
    Traegt einen Satz, der der Oberflaeche direkt vorgelegt werden kann."""


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    is_nullable: bool
    is_primary_key: bool


@dataclass(frozen=True)
class ForeignKey:
    """Ein Verweis von `child_table.child_column` auf `parent_table`."""

    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    # 'CASCADE' | 'SET NULL' | 'NO ACTION' | 'RESTRICT'
    delete_rule: str


@dataclass(frozen=True)
class TableInfo:
    name: str
    group: str
    columns: list[ColumnInfo]
    primary_key: str | None

    @property
    def is_deletable(self) -> bool:
        """Loeschen braucht einen einspaltigen Primaerschluessel. Tabellen mit
        zusammengesetztem oder fehlendem Schluessel bleiben lesbar - eine Zeile
        ohne eindeutige Adresse zu loeschen waere geraten."""
        return self.primary_key is not None


@dataclass
class Catalog:
    tables: dict[str, TableInfo] = field(default_factory=dict)
    # Kindtabellen je Elterntabelle. Nur einspaltige Verweise: die
    # Kaskadenvorschau kann deren betroffene Zeilen exakt verfolgen.
    children: dict[str, list[ForeignKey]] = field(default_factory=dict)
    # Alle Tabellenabhaengigkeiten, einschliesslich zusammengesetzter
    # Fremdschluessel. Fuer die Reihenfolge beim kompletten Haushaltsleeren
    # genuegt die Kante Kind -> Eltern; einzelne Spalten sind dort irrelevant.
    dependencies: set[tuple[str, str]] = field(default_factory=set)


# Der Katalog aendert sich nur mit dem Schema, also einmal je Prozess lesen.
# Kein TTL: eine Schemaaenderung im laufenden Betrieb gibt es in diesem
# Projekt nicht, und ein Neustart ist der ehrlichere Weg als eine Zahl, die
# irgendwann von selbst wieder stimmt.
_catalog: Catalog | None = None


def _group_for(table_name: str) -> str:
    for label, prefixes in _GROUP_RULES:
        if any(prefix in table_name for prefix in prefixes):
            return label
    return "Sonstiges"


async def load_catalog(session: AsyncSession, *, refresh: bool = False) -> Catalog:
    """Liest Tabellen, Spalten, Primaerschluessel und Fremdschluessel aus dem
    Katalog der Datenbank.

    Der Katalog ist die einzige Quelle fuer erlaubte Bezeichner. Was hier nicht
    steht, kommt in keine Abfrage."""
    global _catalog
    if _catalog is not None and not refresh:
        return _catalog

    column_rows = (
        await session.execute(
            text(
                "select c.table_name, c.column_name, c.data_type, c.is_nullable "
                "from information_schema.columns c "
                "join information_schema.tables t "
                "  on t.table_schema = c.table_schema and t.table_name = c.table_name "
                "where c.table_schema = 'public' and t.table_type = 'BASE TABLE' "
                "order by c.table_name, c.ordinal_position"
            )
        )
    ).all()

    primary_keys = {
        row.table_name: row.column_name
        for row in (
            await session.execute(
                text(
                    "select tc.table_name, kcu.column_name "
                    "from information_schema.table_constraints tc "
                    "join information_schema.key_column_usage kcu "
                    "  on kcu.constraint_name = tc.constraint_name "
                    "  and kcu.table_schema = tc.table_schema "
                    "where tc.table_schema = 'public' "
                    "  and tc.constraint_type = 'PRIMARY KEY' "
                    # Nur einspaltige Schluessel: bei zusammengesetzten liefert
                    # die Abfrage mehrere Zeilen, und die Tabelle bleibt hier
                    # ohne Eintrag - also lesbar, aber nicht loeschbar.
                    "group by tc.table_name, kcu.column_name "
                    "having count(*) = 1"
                )
            )
        ).all()
    }

    # Fremdschluessel ueber pg_catalog statt information_schema: die
    # Sichten dort liefern bei mehrspaltigen Constraints ein Kreuzprodukt aus
    # Kind- und Elternspalten, und daraus laesst sich die Zuordnung nicht mehr
    # sauber rekonstruieren. pg_constraint traegt die Spaltenlisten geordnet.
    fk_rows = (
        await session.execute(
            text(
                "select child.relname as child_table, "
                "       child_att.attname as child_column, "
                "       parent.relname as parent_table, "
                "       parent_att.attname as parent_column, "
                "       array_length(con.conkey, 1) as column_count, "
                "       case con.confdeltype when 'c' then 'CASCADE' "
                "            when 'n' then 'SET NULL' when 'd' then 'SET DEFAULT' "
                "            when 'r' then 'RESTRICT' else 'NO ACTION' end as delete_rule "
                "from pg_constraint con "
                "join pg_class child on child.oid = con.conrelid "
                "join pg_class parent on parent.oid = con.confrelid "
                "join pg_namespace ns on ns.oid = child.relnamespace "
                "join lateral unnest(con.conkey, con.confkey) as cols(child_attnum, parent_attnum) "
                "  on true "
                "join pg_attribute child_att "
                "  on child_att.attrelid = con.conrelid and child_att.attnum = cols.child_attnum "
                "join pg_attribute parent_att "
                "  on parent_att.attrelid = con.confrelid "
                "  and parent_att.attnum = cols.parent_attnum "
                "where con.contype = 'f' and ns.nspname = 'public' "
            )
        )
    ).all()

    by_table: dict[str, list[ColumnInfo]] = {}
    for row in column_rows:
        by_table.setdefault(row.table_name, []).append(
            ColumnInfo(
                name=row.column_name,
                data_type=row.data_type,
                is_nullable=row.is_nullable == "YES",
                is_primary_key=primary_keys.get(row.table_name) == row.column_name,
            )
        )

    tables: dict[str, TableInfo] = {}
    for name, columns in by_table.items():
        # Nur haushaltsbezogene Tabellen. Ohne household_id gibt es keine
        # Bedingung, mit der sich die Sicht auf den eigenen Haushalt begrenzen
        # laesst - und ohne die hat der Explorer hier nichts verloren.
        if not any(column.name == "household_id" for column in columns):
            continue
        if name in _HIDDEN_TABLES:
            continue
        tables[name] = TableInfo(
            name=name,
            group=_group_for(name),
            columns=columns,
            primary_key=primary_keys.get(name),
        )

    children: dict[str, list[ForeignKey]] = {}
    dependencies: set[tuple[str, str]] = set()
    for row in fk_rows:
        dependencies.add((str(row.child_table), str(row.parent_table)))
        # Fuer die Kaskadenvorschau reicht eine einzelne Spaltenbedingung bei
        # zusammengesetzten Fremdschluesseln nicht aus. Die Loeschreihenfolge
        # kennt die Tabellenkante ueber `dependencies` trotzdem.
        if int(row.column_count) != 1:
            continue
        key = ForeignKey(
            child_table=row.child_table,
            child_column=row.child_column,
            parent_table=row.parent_table,
            parent_column=row.parent_column,
            delete_rule=row.delete_rule,
        )
        children.setdefault(row.parent_table, []).append(key)

    _catalog = Catalog(tables=tables, children=children, dependencies=dependencies)
    logger.info(
        "Datenbank-Explorer: %d Haushaltstabellen im Katalog, %d davon loeschbar",
        len(tables),
        sum(1 for table in tables.values() if table.is_deletable),
    )
    return _catalog


async def require_table(session: AsyncSession, table_name: str) -> TableInfo:
    catalog = await load_catalog(session)
    table = catalog.tables.get(table_name)
    if table is None:
        raise ExplorerError(
            f"Die Tabelle „{table_name}“ gibt es nicht oder sie ist nicht "
            "haushaltsbezogen."
        )
    return table


async def table_counts(session: AsyncSession, household_id: UUID) -> dict[str, int]:
    """Zeilenzahl je Tabelle fuer diesen Haushalt.

    Eine einzige Abfrage per UNION ALL statt 79 Rundreisen. Die Tabellennamen
    stammen aus dem Katalog und sind damit gepruefte Bezeichner; der einzige
    Wert im Statement ist die Haushalts-ID und der ist gebunden."""
    catalog = await load_catalog(session)
    if not catalog.tables:
        return {}
    parts = [
        f'select {_literal(name)} as tabelle, count(*)::bigint as anzahl '
        f'from "{name}" where household_id = :household_id'
        for name in sorted(catalog.tables)
    ]
    rows = (
        await session.execute(
            text(" union all ".join(parts)), {"household_id": str(household_id)}
        )
    ).all()
    return {row.tabelle: int(row.anzahl) for row in rows}


def _literal(value: str) -> str:
    """Bezeichner als SQL-Zeichenkette. Nur fuer Werte aus dem Katalog - der
    Ersatz doppelter Apostrophe ist die zweite Schranke, nicht die erste."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


@dataclass(frozen=True)
class RowPage:
    rows: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


async def fetch_rows(
    session: AsyncSession,
    household_id: UUID,
    table_name: str,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    order_by: str | None = None,
    descending: bool = True,
    search: str | None = None,
) -> RowPage:
    """Eine Seite Zeilen. Sortier- und Suchspalten werden gegen den Katalog
    geprueft, bevor sie in das Statement kommen."""
    table = await require_table(session, table_name)
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)

    column_names = {column.name for column in table.columns}
    if order_by is not None and order_by not in column_names:
        raise ExplorerError(f"Die Spalte „{order_by}“ gibt es in {table_name} nicht.")
    if order_by is None:
        # Ohne Vorgabe die juengsten zuerst - das ist fast immer das, was man
        # sehen will, wenn man eine Tabelle aufmacht.
        for candidate in ("created_at", "updated_at", table.primary_key or ""):
            if candidate in column_names:
                order_by = candidate
                break
    order_clause = (
        f'order by "{order_by}" {"desc" if descending else "asc"} nulls last'
        if order_by
        else ""
    )

    params: dict[str, Any] = {"household_id": str(household_id)}
    where = ["household_id = :household_id"]
    if search and search.strip():
        # Nur ueber Textspalten suchen. Ein Suchbegriff gegen eine
        # uuid- oder numeric-Spalte zu casten wuerde die Abfrage entweder
        # abbrechen lassen oder jeden Index umgehen.
        text_columns = [
            column.name
            for column in table.columns
            if column.data_type in ("text", "character varying", "character")
        ]
        if text_columns:
            params["search"] = f"%{search.strip()}%"
            joined = " or ".join(f'"{name}"::text ilike :search' for name in text_columns)
            where.append(f"({joined})")

    where_clause = " and ".join(where)
    total = int(
        (
            await session.execute(
                text(f'select count(*)::bigint as anzahl from "{table_name}" where {where_clause}'),
                params,
            )
        ).one().anzahl
    )
    rows = (
        await session.execute(
            text(
                f'select * from "{table_name}" where {where_clause} {order_clause} '
                "limit :limit offset :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()

    return RowPage(
        rows=[dict(row._mapping) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@dataclass(frozen=True)
class CascadeEffect:
    table: str
    # Wie viele Zeilen dieser Tabelle mitgehen.
    rows: int
    # 'delete' = wird mitgeloescht, 'detach' = verliert nur den Verweis.
    effect: str
    # Ueber welche Spalte der Verweis laeuft - macht nachvollziehbar, warum
    # ausgerechnet diese Tabelle betroffen ist.
    via_column: str
    depth: int


async def preview_delete(
    session: AsyncSession,
    household_id: UUID,
    table_name: str,
    ids: list[str],
) -> list[CascadeEffect]:
    """Was ein Loeschen ausser den gewaehlten Zeilen noch mitnimmt.

    Das ist die eigentliche Sicherung dieses Moduls. `on delete cascade` reicht
    hier bis zu vier Ebenen: eine Essenswoche nimmt Mahlzeiten, Einkaufslisten,
    deren Posten und die Kaufverknuepfungen mit. Ohne Vorschau loescht man eine
    Woche und verliert einen Monat.

    Gemeldet werden beide Wirkungen getrennt: was mitgeloescht wird
    (`delete`) und was nur seinen Verweis verliert (`detach`, bei
    `on delete set null`). Das zweite ist harmlos, sieht aber ohne Erklaerung
    beunruhigend aus - und umgekehrt darf das erste nicht darin untergehen."""
    await require_table(session, table_name)
    if not ids:
        return []

    catalog = await load_catalog(session)
    effects: list[CascadeEffect] = []
    seen: set[str] = {table_name}
    # (Tabelle, Schluessel dieser Ebene, Tiefe)
    frontier: list[tuple[str, list[str], int]] = [(table_name, ids, 0)]

    while frontier:
        parent_table, parent_ids, depth = frontier.pop(0)
        if depth >= MAX_CASCADE_DEPTH or not parent_ids:
            continue
        for link in catalog.children.get(parent_table, []):
            if link.delete_rule not in ("CASCADE", "SET NULL"):
                continue
            child = catalog.tables.get(link.child_table)
            if child is None:
                continue

            count_row = (
                await session.execute(
                    text(
                        f'select count(*)::bigint as anzahl from "{link.child_table}" '
                        f'where household_id = :household_id '
                        f'and "{link.child_column}" = any(:ids)'
                    ),
                    {"household_id": str(household_id), "ids": parent_ids},
                )
            ).one()
            affected = int(count_row.anzahl)
            if affected == 0:
                continue

            effects.append(
                CascadeEffect(
                    table=link.child_table,
                    rows=affected,
                    effect="delete" if link.delete_rule == "CASCADE" else "detach",
                    via_column=link.child_column,
                    depth=depth + 1,
                )
            )

            # Nur echte Loeschungen pflanzen sich fort. Eine Zeile, die bloss
            # ihren Verweis verliert, bleibt stehen - ihre eigenen Kinder sind
            # damit nicht betroffen.
            if link.delete_rule != "CASCADE" or child.primary_key is None:
                continue
            # Zyklenschutz: jede Tabelle wird hoechstens einmal verfolgt.
            if link.child_table in seen:
                continue
            seen.add(link.child_table)
            if affected > MAX_CASCADE_KEYS:
                continue
            child_ids = [
                str(row[0])
                for row in (
                    await session.execute(
                        text(
                            f'select "{child.primary_key}" from "{link.child_table}" '
                            f'where household_id = :household_id '
                            f'and "{link.child_column}" = any(:ids)'
                        ),
                        {"household_id": str(household_id), "ids": parent_ids},
                    )
                ).all()
            ]
            frontier.append((link.child_table, child_ids, depth + 1))

    effects.sort(key=lambda item: (item.effect != "delete", -item.rows))
    return effects


async def fetch_rows_by_ids(
    session: AsyncSession, household_id: UUID, table_name: str, ids: list[str]
) -> list[dict[str, Any]]:
    """Die betroffenen Zeilen selbst - fuer den Schnappschuss im Audit-Log.
    Was geloescht wurde, muss danach noch benennbar sein."""
    table = await require_table(session, table_name)
    if table.primary_key is None or not ids:
        return []
    rows = (
        await session.execute(
            text(
                f'select * from "{table_name}" where household_id = :household_id '
                f'and "{table.primary_key}" = any(:ids)'
            ),
            {"household_id": str(household_id), "ids": ids},
        )
    ).all()
    return [dict(row._mapping) for row in rows]


# Beim Leeren stehen bleiben. Die Liste ist nicht Geschmackssache, sondern
# folgt dem, was das Schema ohnehin schuetzt - nachgerechnet ueber
# information_schema.role_table_grants und pg_policies:
#
#   household_members       Die Zugehoerigkeit selbst. Wer sie loescht, sperrt
#                           sich aus dem eigenen Haushalt aus und kann ihn
#                           danach nicht einmal mehr oeffnen, um den Fehler zu
#                           sehen.
#   categories              Kein DELETE-Recht fuer `authenticated` - sie werden
#   category_groups         ausdruecklich ueber ihren Lebenszyklus verwaltet
#                           (archivieren, zusammenfuehren; siehe
#                           category_operations_service), nicht roh geloescht.
#                           Nebeneffekt und Absicht zugleich: der Kategorien-
#                           baum ist Einrichtungsarbeit und ueberlebt das
#                           Leeren, der Haushalt bleibt sofort benutzbar.
#   audit_log               Anfuegend. Der Nachweis darueber, was geschehen
#   ai_task_events          ist, darf nicht Teil dessen sein, was ein Vorgang
#   contract_merge_changes  wegraeumen kann.
#
# Ein Loeschversuch auf diesen Tabellen endet in der Datenbank mit SQLSTATE
# 42501 und liesse den ganzen Vorgang scheitern - sie zu ueberspringen ist
# deshalb kein Nachgeben, sondern die einzige Lesart, die mit dem Schema
# vereinbar ist.
PURGE_KEEP: Final = frozenset(
    {
        "household_members",
        "categories",
        "category_groups",
        "audit_log",
        "ai_task_events",
        "contract_merge_changes",
    }
)


def deletion_order(catalog: Catalog) -> list[str]:
    """Reihenfolge, in der sich alle Haushaltstabellen leeren lassen.

    Kinder vor Eltern. Noetig, weil 21 der Fremdschluessel weder CASCADE noch
    SET NULL sind - bei falscher Reihenfolge bricht das Leeren mittendrin ab
    und laesst einen halb geraeumten Haushalt zurueck.

    Selbstbezuege (categories, contracts, project_cost_items,
    project_scenarios) bleiben unberuecksichtigt: ein einzelnes DELETE, das
    alle Zeilen einer Tabelle trifft, raeumt Eltern und Kinder gemeinsam ab -
    die Pruefung greift erst am Ende der Anweisung, und dann ist beides weg.
    """
    parents: dict[str, set[str]] = {name: set() for name in catalog.tables}
    for child_table, parent_table in catalog.dependencies:
        if child_table in catalog.tables and parent_table in catalog.tables:
            if child_table != parent_table:
                parents[child_table].add(parent_table)

    offen = set(catalog.tables)
    order: list[str] = []
    while offen:
        frei = sorted(
            name
            for name in offen
            if not any(name in parents[other] for other in offen if other != name)
        )
        if not frei:
            # Kann mit dem heutigen Schema nicht eintreten (nachgerechnet: 79
            # Tabellen, keine Zyklen). Falls doch je einer entsteht, lieber
            # sichtbar scheitern als eine halbe Reihenfolge liefern.
            raise ExplorerError(
                "Zwischen den Tabellen besteht ein Fremdschluessel-Zyklus - es gibt "
                f"keine sichere Loeschreihenfolge fuer: {', '.join(sorted(offen))}."
            )
        order.extend(frei)
        offen.difference_update(frei)
    return order


async def purge_household(session: AsyncSession, household_id: UUID) -> dict[str, int]:
    """Leert alle Haushaltstabellen. Gibt zurueck, was je Tabelle wegging.

    Laeuft in der Transaktion des Aufrufers: entweder ist danach alles weg
    oder nichts. Ein Abbruch mitten im Vorgang darf keinen halb geraeumten
    Haushalt hinterlassen, in dem Buchungen ohne Konten und Mahlzeiten ohne
    Woche stehen."""
    catalog = await load_catalog(session)
    entfernt: dict[str, int] = {}
    for table_name in deletion_order(catalog):
        if table_name in PURGE_KEEP:
            continue
        # `returning 1` statt rowcount: der Rueckgabetyp von session.execute
        # ist generisch und traegt rowcount nicht im Typ (dieselbe Loesung wie
        # in den Routen, siehe grocery.py).
        geloescht = (
            await session.execute(
                text(f'delete from "{table_name}" where household_id = :household_id returning 1'),
                {"household_id": str(household_id)},
            )
        ).all()
        if geloescht:
            entfernt[table_name] = len(geloescht)
    return entfernt


async def delete_rows(
    session: AsyncSession, household_id: UUID, table_name: str, ids: list[str]
) -> int:
    """Loescht die gewaehlten Zeilen. Gibt zurueck, wie viele es wirklich
    waren - eine Zeile, die inzwischen weg ist, ist kein Fehler."""
    table = await require_table(session, table_name)
    if table.primary_key is None:
        raise ExplorerError(
            f"„{table_name}“ hat keinen einspaltigen Primaerschluessel und kann "
            "hier nicht zeilenweise geloescht werden."
        )
    if not ids:
        return 0
    rows = (
        await session.execute(
            text(
                f'delete from "{table_name}" where household_id = :household_id '
                f'and "{table.primary_key}" = any(:ids) returning "{table.primary_key}"'
            ),
            {"household_id": str(household_id), "ids": ids},
        )
    ).all()
    return len(rows)
