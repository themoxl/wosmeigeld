import base64
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.core.errors import extract_sqlstate
from app.schemas.category import (
    BulkCategoryInput,
    BulkCategoryItem,
    BulkCategoryResult,
    TransactionCategoryUpdate,
)
from app.schemas.provenance import TransactionProvenance
from app.schemas.transaction import (
    BulkAssignGoalInput,
    BulkAssignGoalResult,
    BulkCategorizeInput,
    BulkCategorizeResult,
    Transaction,
    TransactionConflict,
    TransactionCreate,
    TransactionDetail,
    TransactionDirection,
    TransactionPage,
    TransactionSort,
    TransactionSplit,
    TransactionSplitsUpdate,
    TransactionUpdate,
    UnassignedGoalSplitCandidate,
)
from app.services import payee_link_service, provenance_service
from app.services.category_service import resolve_category_id, resolve_category_ids
from app.services.transaction_detach_service import (
    TransactionDetachForbiddenError,
    detach_transactions,
)
from app.services.worker_dispatch import enqueue_contract_reconcile

router = APIRouter(prefix="/transactions", tags=["transactions"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
# Schreibende Endpunkte: 403 statt stillem Nichtstun, wenn die Rolle nicht
# schreiben darf (siehe app/auth/dependencies.py).
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]

# Phase 2: Splits werden ueber PUT .../splits gepflegt (haelt die Invariante
# "Summe aller Splits = Betrag der Transaktion", Abschnitt 6.4, an genau
# einer Stelle). category_id/is_split werden aus den Splits abgeleitet: bei
# genau einem Split die Kategorie dieses Splits, sonst null (Register zeigt
# dann "Split").
_TRANSACTION_SELECT = """
select
  t.id, t.account_id, t.booked_date, t.value_date, t.amount_minor, t.currency,
  t.payee_label, t.note, t.status, t.is_transfer, t.is_archived, t.version,
  case when coalesce(s.split_count, 0) = 1 then s.only_category_id else null end as category_id,
  coalesce(s.split_count, 0) > 1 as is_split
from transactions t
left join (
  select transaction_id, count(*) as split_count,
         (array_agg(category_id))[1] as only_category_id
  from transaction_splits
  group by transaction_id
) s on s.transaction_id = t.id
"""


async def _assert_account_in_household(
    session: AsyncSession, account_id: UUID, household_id: UUID
) -> None:
    row = (
        await session.execute(
            text("select 1 from accounts where id = :account_id and household_id = :household_id"),
            {"account_id": str(account_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account_not_found")


async def _load_transaction_detail(
    session: AsyncSession, transaction_id: UUID, household_id: UUID
) -> TransactionDetail:
    row = (
        await session.execute(
            text(_TRANSACTION_SELECT + " where t.id = :id and t.household_id = :household_id"),
            {"id": str(transaction_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transaction_not_found")

    splits_result = await session.execute(
        text(
            "select id, category_id, goal_id, amount_minor, note from transaction_splits "
            "where transaction_id = :id order by created_at"
        ),
        {"id": str(transaction_id)},
    )
    splits = [TransactionSplit(**s._mapping) for s in splits_result]
    return TransactionDetail(**row._mapping, splits=splits)


# --- Phase 9: Optimistische Nebenlaeufigkeitskontrolle ----------------------
# Der erwartete Versionsstand darf auf zwei Wegen kommen: als If-Match-Header
# (HTTP-konform, spiegelt den ETag der Leseantwort - fuer Web-Clients und
# Proxies das erwartbare Verfahren) oder als Feld expected_version im Body
# (praktisch fuer die Offline-Write-Queue der App, die ihre Requests komplett
# als JSON persistiert und keine Header mitschleppen will). Fehlt beides,
# schreibt die Route ungeprueft wie bisher - Bestandsclients bleiben lauffaehig.

IfMatchHeader = Annotated[str | None, Header(alias="If-Match")]


def _etag(version: int) -> str:
    """ETag-Darstellung einer Buchungsversion (starkes Entity-Tag)."""
    return f'"{version}"'


def _resolve_expected_version(if_match: str | None, body_version: int | None) -> int | None:
    """Fuehrt If-Match-Header und expected_version aus dem Body zusammen.

    Unterstuetzt wird genau ein Entity-Tag mit der Versionsnummer ("5", W/"5"
    oder bloss 5). Tag-Listen und '*' lehnt die Route bewusst ab, statt sie
    stillschweigend als "keine Pruefung" zu behandeln - ein missverstandener
    Header duerfte sonst genau die Aenderung ueberschreiben, vor der er
    schuetzen soll."""
    if if_match is None:
        return body_version

    candidate = if_match.strip()
    if candidate.startswith("W/"):
        candidate = candidate[2:].strip()
    candidate = candidate.strip('"')
    if not candidate.isdigit() or int(candidate) < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_if_match")

    header_version = int(candidate)
    if body_version is not None and body_version != header_version:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="if_match_body_version_mismatch"
        )
    return header_version


async def _version_conflict(
    session: AsyncSession, transaction_id: UUID, household_id: UUID, expected_version: int
) -> JSONResponse:
    """Antwort auf ein bedingtes UPDATE, das keine Zeile getroffen hat.

    Existiert die Buchung im Haushalt gar nicht, bleibt es beim bisherigen 404
    (das erledigt _load_transaction_detail). Sonst liegt ein echter Konflikt
    vor und die Antwort liefert den vollstaendigen Serverstand samt aktueller
    Version mit, damit der Client der Nutzerin beide Staende zeigen und danach
    mit der richtigen Version erneut schreiben kann."""
    current = await _load_transaction_detail(session, transaction_id, household_id)
    conflict = TransactionConflict(
        detail="transaction_archived" if current.is_archived else "version_conflict",
        expected_version=expected_version,
        current_version=current.version,
        current=current,
    )
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=conflict.model_dump(mode="json"),
        headers={"ETag": _etag(current.version)},
    )


# Postgres-Fehlerklassen fuer die Regex-Suche unten: ein ungueltiges Muster
# (2201B) und ein durch statement_timeout abgebrochenes Muster (57014) sollen
# als verstaendliche 422 beim Client ankommen statt als rohe 500.
_INVALID_REGEX_SQLSTATE = "2201B"
_QUERY_CANCELED_SQLSTATE = "57014"


@router.get("", response_model=list[Transaction])
async def list_transactions(
    ctx: HouseholdSession,
    account_id: UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    search: Annotated[str | None, Query(max_length=160)] = None,
    # Eigener Parameter statt einer Heuristik auf `search` (etwa "faengt mit ^
    # an, ist also wohl Regex"): eine erkannte-oder-nicht-Grenze anhand von
    # Sonderzeichen ist fuer den Nutzer nicht vorhersagbar - siehe die
    # bestehende, ungesicherte Heuristik in
    # rules_service._matches_rule_pattern, die genau daran krankt.
    search_regex: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[Transaction]:
    session, household_id = ctx
    term = (search or "").strip()
    regex_term = (search_regex or "").strip()
    if term and regex_term:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="search_and_search_regex_mutually_exclusive",
        )

    query = _TRANSACTION_SELECT + " where t.household_id = :household_id and not t.is_archived "
    params: dict[str, object] = {"household_id": str(household_id), "limit": limit}
    if account_id is not None:
        query += "and t.account_id = :account_id "
        params["account_id"] = str(account_id)
    if from_date is not None:
        query += "and t.booked_date >= :from_date "
        params["from_date"] = from_date
    if to_date is not None:
        query += "and t.booked_date <= :to_date "
        params["to_date"] = to_date
    if term:
        # Dieselben Textspalten wie die Volltextsuche der Auswertungen
        # (analytics_service._SEARCH_FIELDS), abzueglich der Split-Notiz: das
        # Register liest Buchungen, nicht Splits, und ein zusaetzlicher Join
        # wuerde Zeilen vervielfachen.
        #
        # Die Spaltenliste steht fest im Code, der Suchtext gelangt
        # ausschliesslich als gebundener Parameter in das Statement.
        query += (
            "and (coalesce(t.payee_label, '') ilike :search escape '\\' "
            "or coalesce(t.payee_raw, '') ilike :search escape '\\' "
            "or coalesce(t.purpose_raw, '') ilike :search escape '\\' "
            "or coalesce(t.note, '') ilike :search escape '\\') "
        )
        params["search"] = f"%{_escape_like(term)}%"
    if regex_term:
        # Dieselben Spalten wie oben, ~* statt ilike: Postgres' eigener
        # Regex-Operator, case-insensitive. Das Muster geht ausschliesslich
        # als gebundener Parameter mit - kein Zusammenbauen des Statements aus
        # Nutzertext.
        query += (
            "and (coalesce(t.payee_label, '') ~* :search_regex "
            "or coalesce(t.payee_raw, '') ~* :search_regex "
            "or coalesce(t.purpose_raw, '') ~* :search_regex "
            "or coalesce(t.note, '') ~* :search_regex) "
        )
        params["search_regex"] = regex_term
    query += "order by t.booked_date desc, t.created_at desc limit :limit"

    if not regex_term:
        result = await session.execute(text(query), params)
        return [Transaction(**row._mapping) for row in result]

    # Regex-Zweig eigens abgesichert: ein von Hand getipptes Muster kann
    # syntaktisch ungueltig oder - seltener, aber bei Postgres' Regex-Engine
    # nicht ausgeschlossen - teuer sein. Der Savepoint (begin_nested) haelt
    # einen Fehler von der umgebenden Request-Transaktion fern, genau wie beim
    # bestehenden DBAPIError-Fang weiter unten in dieser Datei; das
    # statement_timeout gilt nur innerhalb des Savepoints und damit nur fuer
    # diese eine Abfrage.
    try:
        async with session.begin_nested():
            await session.execute(text("set local statement_timeout = '3000'"))
            result = await session.execute(text(query), params)
            rows = list(result)
    except DBAPIError as exc:
        sqlstate = extract_sqlstate(exc)
        if sqlstate == _INVALID_REGEX_SQLSTATE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="invalid_regex_pattern",
            ) from exc
        if sqlstate == _QUERY_CANCELED_SQLSTATE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="regex_pattern_too_expensive",
            ) from exc
        raise
    return [Transaction(**row._mapping) for row in rows]


def _escape_like(value: str) -> str:
    """Maskiert ILIKE-Platzhalter im Suchtext.

    Ohne das waere eine Suche nach "100%" eine Suche nach allem."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- Mobile-Redesign: cursorbasiertes Paging --------------------------------
# docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 5 "Daten/API": die neue
# Buchungsliste laedt per useInfiniteQuery nach, statt einmalig limit=100 zu
# lesen und lokal weiterzufiltern (dabei fehlten Treffer ausserhalb der ersten
# 100 Zeilen). GET /v1/transactions oben bleibt fuer Bestandsclients
# unveraendert; dieser Endpunkt ist rein additiv.
#
# Eigene SELECT-Liste statt Wiederverwendung von _TRANSACTION_SELECT: sie
# traegt zusaetzlich created_at (Tie-Breaker jeder Sortierung) und payee_key
# (fallgleichgestellter Empfaenger fuer sort=payee_asc). Beide Spalten landen
# nicht im Transaction-Antwortmodell - Pydantic ignoriert ueberzaehlige
# Feldnamen aus row._mapping standardmaessig.
_TRANSACTION_PAGE_SELECT = """
select
  t.id, t.account_id, t.booked_date, t.value_date, t.amount_minor, t.currency,
  t.payee_label, t.note, t.status, t.is_transfer, t.is_archived, t.version,
  case when coalesce(s.split_count, 0) = 1 then s.only_category_id else null end as category_id,
  coalesce(s.split_count, 0) > 1 as is_split,
  t.created_at,
  lower(coalesce(t.payee_label, '')) as payee_key
from transactions t
left join (
  select transaction_id, count(*) as split_count,
         (array_agg(category_id))[1] as only_category_id
  from transaction_splits
  group by transaction_id
) s on s.transaction_id = t.id
"""

# lower(coalesce(payee_label, '')) taucht zweimal auf (ORDER BY und WHERE des
# Cursors): ein SELECT-Alias (payee_key) ist im WHERE derselben Abfrage nicht
# sichtbar, weil WHERE vor der Aufloesung der SELECT-Liste ausgewertet wird -
# also steht der volle Ausdruck hier als Konstante, statt ihn zweimal von Hand
# synchron zu halten.
_PAYEE_KEY_SQL = "lower(coalesce(t.payee_label, ''))"

_CursorKind = Literal["date", "datetime", "uuid", "int", "text"]


@dataclass(frozen=True)
class _SortColumn:
    # Ausdruck fuer ORDER BY/WHERE (z. B. "t.booked_date").
    sql: str
    # Alias in der SELECT-Liste, um den Wert aus der letzten Zeile fuer den
    # naechsten Cursor auszulesen (z. B. "booked_date").
    key: str
    kind: _CursorKind


@dataclass(frozen=True)
class _SortSpec:
    order_sql: str
    # "<" bei absteigender, ">" bei aufsteigender Sortierung - siehe
    # _cursor_where_clause fuer die Begruendung des Row-Value-Vergleichs.
    op: Literal["<", ">"]
    columns: tuple[_SortColumn, ...]


# Jede Sortierung erhaelt dieselben stabilen Tie-Breaker (created_at, dann id)
# hinter dem fachlichen Sortierschluessel - ohne sie waeren zwei Buchungen mit
# gleichem Datum/Betrag/Empfaenger in wechselnder Reihenfolge auf
# aufeinanderfolgenden Seiten vertreten (oder wuerden je nach Zufall
# uebersprungen/doppelt geliefert).
_PAGE_SORT_SPECS: dict[TransactionSort, _SortSpec] = {
    "date_desc": _SortSpec(
        order_sql="t.booked_date desc, t.created_at desc, t.id desc",
        op="<",
        columns=(
            _SortColumn(sql="t.booked_date", key="booked_date", kind="date"),
            _SortColumn(sql="t.created_at", key="created_at", kind="datetime"),
            _SortColumn(sql="t.id", key="id", kind="uuid"),
        ),
    ),
    "date_asc": _SortSpec(
        order_sql="t.booked_date asc, t.created_at asc, t.id asc",
        op=">",
        columns=(
            _SortColumn(sql="t.booked_date", key="booked_date", kind="date"),
            _SortColumn(sql="t.created_at", key="created_at", kind="datetime"),
            _SortColumn(sql="t.id", key="id", kind="uuid"),
        ),
    ),
    "amount_desc": _SortSpec(
        order_sql="t.amount_minor desc, t.booked_date desc, t.created_at desc, t.id desc",
        op="<",
        columns=(
            _SortColumn(sql="t.amount_minor", key="amount_minor", kind="int"),
            _SortColumn(sql="t.booked_date", key="booked_date", kind="date"),
            _SortColumn(sql="t.created_at", key="created_at", kind="datetime"),
            _SortColumn(sql="t.id", key="id", kind="uuid"),
        ),
    ),
    "amount_asc": _SortSpec(
        order_sql="t.amount_minor asc, t.booked_date asc, t.created_at asc, t.id asc",
        op=">",
        columns=(
            _SortColumn(sql="t.amount_minor", key="amount_minor", kind="int"),
            _SortColumn(sql="t.booked_date", key="booked_date", kind="date"),
            _SortColumn(sql="t.created_at", key="created_at", kind="datetime"),
            _SortColumn(sql="t.id", key="id", kind="uuid"),
        ),
    ),
    "payee_asc": _SortSpec(
        order_sql=f"{_PAYEE_KEY_SQL} asc, t.booked_date asc, t.created_at asc, t.id asc",
        op=">",
        columns=(
            _SortColumn(sql=_PAYEE_KEY_SQL, key="payee_key", kind="text"),
            _SortColumn(sql="t.booked_date", key="booked_date", kind="date"),
            _SortColumn(sql="t.created_at", key="created_at", kind="datetime"),
            _SortColumn(sql="t.id", key="id", kind="uuid"),
        ),
    ),
}

_INVALID_CURSOR_DETAIL = "invalid_cursor"


def _cursor_encode(sort: TransactionSort, values: tuple[object, ...]) -> str:
    """Baut den opaken Cursor aus den Sortierspalten der letzten Zeile.

    Der Sortname ist Teil der Payload: ein Cursor, der mit einer anderen
    Sortierung erzeugt wurde, wird beim Decodieren erkannt und abgelehnt,
    statt eine bedeutungslose Seite zurueckzugeben (siehe _cursor_decode)."""
    encodable: list[object] = []
    for value in values:
        if isinstance(value, UUID):
            encodable.append(str(value))
        elif isinstance(value, datetime | date):
            encodable.append(value.isoformat())
        else:
            encodable.append(value)
    payload = json.dumps([sort, *encodable], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _cursor_decode(cursor: str, sort: TransactionSort, spec: _SortSpec) -> tuple[object, ...]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=_INVALID_CURSOR_DETAIL
        ) from exc

    if (
        not isinstance(data, list)
        or not data
        or data[0] != sort
        or len(data) - 1 != len(spec.columns)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=_INVALID_CURSOR_DETAIL
        )

    try:
        values: list[object] = []
        for raw_value, column in zip(data[1:], spec.columns, strict=True):
            if column.kind == "date":
                values.append(date.fromisoformat(raw_value))
            elif column.kind == "datetime":
                values.append(datetime.fromisoformat(raw_value))
            elif column.kind == "uuid":
                values.append(UUID(str(raw_value)))
            elif column.kind == "int":
                values.append(int(raw_value))
            else:
                values.append(str(raw_value))
        return tuple(values)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=_INVALID_CURSOR_DETAIL
        ) from exc


def _cursor_where_clause(
    spec: _SortSpec, cursor_values: tuple[object, ...], params: dict[str, object]
) -> str:
    """Keyset- statt Offset-Bedingung: '(a, b, c) < (x, y, z)' ist bei
    Postgres ein lexikografischer Zeilenvergleich. Er liefert exakt die
    Zeilen, die in derselben ORDER BY nach der letzten Zeile der Vorseite
    kommen - vorausgesetzt, alle Spalten sortieren in dieselbe Richtung
    (deshalb ein gemeinsames op je Sortierung statt einer Spalte fuer Spalte
    aufgebauten OR-Kette, die bei jedem Tie-Breaker waechst)."""
    lhs = ", ".join(column.sql for column in spec.columns)
    placeholders: list[str] = []
    for i, (column, value) in enumerate(zip(spec.columns, cursor_values, strict=True)):
        name = f"cursor_{i}"
        params[name] = value
        # cast(...) statt ":name::uuid" - SQLAlchemys text() verwechselt einen
        # Bind-Parameter unmittelbar vor "::" sonst mit Text (siehe dieselbe
        # Begruendung in analytics_service.py).
        placeholders.append(f"cast(:{name} as uuid)" if column.kind == "uuid" else f":{name}")
    return f"and ({lhs}) {spec.op} ({', '.join(placeholders)}) "


@router.get("/page", response_model=TransactionPage)
async def list_transactions_page(
    ctx: HouseholdSession,
    account_id: UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    search: Annotated[str | None, Query(max_length=160)] = None,
    category_id: UUID | None = None,
    uncategorized: bool = False,
    direction: TransactionDirection | None = None,
    sort: TransactionSort = "date_desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=4000)] = None,
) -> TransactionPage:
    """Additiver, cursorbasierter Paging-Endpunkt fuer die neue mobile
    Buchungsliste (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 5). GET
    /v1/transactions oben bleibt fuer Bestandsclients unveraendert - dieser
    Endpunkt darf Filterverhalten und Antwortform eigenstaendig entwickeln.

    category_id und uncategorized sind exklusiv: eine Buchung landet nur bei
    genau einer der beiden Anfragen im Ergebnis. Beide pruefen ueber EXISTS
    gegen transaction_splits, damit auch Split-Buchungen getroffen werden, bei
    denen die Buchungszeile selbst keine einzelne category_id traegt."""
    session, household_id = ctx

    if category_id is not None and uncategorized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="category_id_and_uncategorized_mutually_exclusive",
        )

    spec = _PAGE_SORT_SPECS[sort]
    params: dict[str, object] = {"household_id": str(household_id)}
    query = (
        _TRANSACTION_PAGE_SELECT
        + " where t.household_id = :household_id and not t.is_archived "
    )

    if account_id is not None:
        query += "and t.account_id = :account_id "
        params["account_id"] = str(account_id)
    if from_date is not None:
        query += "and t.booked_date >= :from_date "
        params["from_date"] = from_date
    if to_date is not None:
        query += "and t.booked_date <= :to_date "
        params["to_date"] = to_date

    term = (search or "").strip()
    if term:
        # Dieselben Textspalten wie GET /v1/transactions ("Empfaenger, Rohzweck
        # und Notiz" aus dem Redesign-Plan, plus payee_raw fuer den Bank-Rohtext
        # - alle vier tragen bereits ein trigram-GIN-Index, siehe 0001_schema.sql).
        query += (
            "and (coalesce(t.payee_label, '') ilike :search escape '\\' "
            "or coalesce(t.payee_raw, '') ilike :search escape '\\' "
            "or coalesce(t.purpose_raw, '') ilike :search escape '\\' "
            "or coalesce(t.note, '') ilike :search escape '\\') "
        )
        params["search"] = f"%{_escape_like(term)}%"

    if category_id is not None:
        query += (
            "and exists (select 1 from transaction_splits ts_filter "
            "where ts_filter.transaction_id = t.id and ts_filter.category_id = :category_id) "
        )
        params["category_id"] = str(category_id)
    elif uncategorized:
        query += (
            "and not exists (select 1 from transaction_splits ts_filter "
            "where ts_filter.transaction_id = t.id and ts_filter.category_id is not null) "
        )

    if direction == "income":
        query += "and t.amount_minor > 0 "
    elif direction == "expense":
        query += "and t.amount_minor < 0 "

    if cursor is not None:
        cursor_values = _cursor_decode(cursor, sort, spec)
        query += _cursor_where_clause(spec, cursor_values, params)

    # limit + 1: eine zusaetzlich gelesene Zeile verraet, ob eine weitere
    # Seite folgt, ohne ein separates COUNT(*) zu benoetigen.
    query += f"order by {spec.order_sql} limit :limit"
    params["limit"] = limit + 1

    result = await session.execute(text(query), params)
    rows = list(result)

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = [Transaction(**row._mapping) for row in page_rows]

    next_cursor: str | None = None
    if has_more and page_rows:
        last_mapping = page_rows[-1]._mapping
        cursor_values = tuple(last_mapping[column.key] for column in spec.columns)
        next_cursor = _cursor_encode(sort, cursor_values)

    return TransactionPage(items=items, next_cursor=next_cursor)


@router.post("", response_model=Transaction, status_code=201)
async def create_transaction(
    payload: TransactionCreate,
    ctx: HouseholdEditorSession,
    response: Response,
    user: CurrentUser,
) -> Transaction:
    session, household_id = ctx
    await _assert_account_in_household(session, payload.account_id, household_id)

    # Vor dem ersten INSERT pruefen: eine haushaltsfremde oder archivierte
    # Kategorie soll nicht erst die Buchung anlegen und dann am Trigger
    # scheitern (docs/KATEGORISIERUNGSPLAN.md, "Zentraler Kategorie-Resolver").
    resolved_category_id = await resolve_category_id(session, household_id, payload.category_id)

    currency = payload.currency
    if currency is None:
        account_row = (
            await session.execute(
                text("select currency from accounts where id = :id"),
                {"id": str(payload.account_id)},
            )
        ).one()
        currency = account_row.currency

    # Handanlage verknuepft denselben Weg wie der Import (Plan 19.2): der
    # eingetippte Empfaengertext wird gegen die bestehenden Empfaenger
    # aufgeloest, damit der Vertragsabgleich ueber identity_score 0,80 kommt.
    # TransactionCreate kennt kein payee_id-Feld, hier kann also nie eine
    # bestehende Zuordnung ueberschrieben werden. Ein unbekannter Empfaenger
    # wird bewusst nicht angelegt (Begruendung: payee_link_service).
    payee_id = await payee_link_service.resolve_payee_id(
        session, household_id, payload.payee_label
    )

    transaction_id = (
        await session.execute(
            text(
                "insert into transactions (household_id, account_id, booked_date, amount_minor, "
                "currency, payee_id, payee_label, note) "
                "values (:household_id, :account_id, :booked_date, :amount_minor, :currency, "
                "cast(:payee_id as uuid), :payee_label, :note) returning id"
            ),
            {
                "household_id": str(household_id),
                "account_id": str(payload.account_id),
                "booked_date": payload.booked_date,
                "amount_minor": payload.amount_minor,
                "currency": currency,
                "payee_id": str(payee_id) if payee_id else None,
                "payee_label": payload.payee_label,
                "note": payload.note,
            },
        )
    ).scalar_one()

    await session.execute(
        text(
            "insert into transaction_splits "
            "(household_id, transaction_id, goal_id, amount_minor, "
            f"{provenance_service.CATEGORY_ASSIGNMENT_INSERT_COLUMNS}) "
            "values (:household_id, :transaction_id, :goal_id, :amount_minor, "
            f"{provenance_service.CATEGORY_ASSIGNMENT_INSERT_VALUES})"
        ),
        {
            "household_id": str(household_id),
            "transaction_id": transaction_id,
            "goal_id": str(payload.goal_id) if payload.goal_id else None,
            "amount_minor": payload.amount_minor,
            **provenance_service.manual_assignment(resolved_category_id, user.id),
        },
    )

    detail = await _load_transaction_detail(session, transaction_id, household_id)
    response.headers["ETag"] = _etag(detail.version)
    return Transaction(**detail.model_dump(exclude={"splits"}))


@router.get("/{transaction_id}", response_model=TransactionDetail)
async def get_transaction(
    transaction_id: UUID, ctx: HouseholdSession, response: Response
) -> TransactionDetail:
    session, household_id = ctx
    detail = await _load_transaction_detail(session, transaction_id, household_id)
    response.headers["ETag"] = _etag(detail.version)
    return detail


@router.get("/{transaction_id}/provenance", response_model=TransactionProvenance)
async def get_transaction_provenance(
    transaction_id: UUID, ctx: HouseholdSession
) -> TransactionProvenance:
    """Beantwortet "warum ist diese Buchung hier" aus gespeicherten Feldern.

    Liefert den Bankrohtext, die Importherkunft und je Split, wodurch die
    Kategorie zustande kam (Regel inkl. getroffenem Kriterium, KI-Entscheid
    inkl. Modell und Konfidenz, oder Mensch). Rein lesend und deshalb fuer
    jedes Haushaltsmitglied zugaenglich, nicht nur fuer Editoren."""
    session, household_id = ctx
    provenance = await provenance_service.load_transaction_provenance(
        session, household_id, transaction_id
    )
    if provenance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="transaction_not_found"
        )
    return provenance


@router.patch(
    "/{transaction_id}",
    response_model=TransactionDetail,
    responses={409: {"model": TransactionConflict, "description": "Versionskonflikt"}},
)
async def update_transaction(
    transaction_id: UUID,
    payload: TransactionUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> TransactionDetail | JSONResponse:
    session, household_id = ctx
    expected_version = _resolve_expected_version(if_match, payload.expected_version)
    updates = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    if not updates:
        detail = await _load_transaction_detail(session, transaction_id, household_id)
        response.headers["ETag"] = _etag(detail.version)
        return detail

    if "account_id" in updates:
        await _assert_account_in_household(session, updates["account_id"], household_id)
        updates["account_id"] = str(updates["account_id"])

    if "payee_label" in updates:
        # Seit der Import payee_id schreibt (Plan 19.2), waere eine stehen
        # gebliebene Zuordnung aktiv falsch: die Buchung haenge an Empfaenger A
        # und traege die Beschriftung B. identity_score wuerde dafuer 0,80 an
        # den FALSCHEN Vertrag vergeben und classify_hit koennte daraus eine
        # Preisaenderung machen - also den Sollbetrag eines fremden Vertrags
        # ueberschreiben. Deshalb wird hier neu aufgeloest und bewusst auf NULL
        # gesetzt, wenn zur neuen Beschriftung kein Empfaenger passt.
        resolved = await payee_link_service.resolve_payee_id(
            session, household_id, updates["payee_label"]
        )
        updates["payee_id"] = str(resolved) if resolved is not None else None

    set_clause = ", ".join(f"{column} = :{column}" for column in updates)
    updates["id"] = str(transaction_id)
    updates["household_id"] = str(household_id)
    # version und updated_at pflegt der Trigger transactions_bump_version
    # (Migration 0004) - hier bewusst nicht mehr selbst setzen, damit es genau
    # eine Stelle gibt, die den Zaehler kennt.
    query = f"update transactions set {set_clause} where id = :id and household_id = :household_id"
    if expected_version is not None:
        # Der Client hat sich auf einen Stand festgelegt: zusaetzlich zur
        # Version auch archivierte Buchungen aussperren. Ein PATCH wuerde eine
        # anderswo geloeschte Buchung sonst unbemerkt wiederbeleben.
        query += " and version = :expected_version and not is_archived"
        updates["expected_version"] = expected_version
    query += " returning id"

    result = await session.execute(text(query), updates)
    if result.first() is None:
        if expected_version is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="transaction_not_found"
            )
        return await _version_conflict(session, transaction_id, household_id, expected_version)

    detail = await _load_transaction_detail(session, transaction_id, household_id)
    response.headers["ETag"] = _etag(detail.version)
    return detail


@router.delete("/{transaction_id}", status_code=204)
async def delete_transaction(
    transaction_id: UUID, ctx: HouseholdEditorSession, background_tasks: BackgroundTasks
) -> None:
    """Nimmt eine Buchung aus dem Bestand (archivieren, nicht loeschen) und loest
    dabei alles, was auf sie zeigt.

    Frueher raeumte diese Stelle nur Umbuchungspaare und Einkaufsverknuepfungen
    ab. Alles andere blieb zeigen: die Vertragsfaelligkeit stand weiter auf
    'bezahlt', der Erstattungsvorgang rechnete weiter mit dem Betrag, der
    Schadenvorgang galt weiter als reguliert, und eine noch offene Importzeile
    warnte weiter vor einer Dublette zu einer Buchung, die niemand mehr fand. Eine
    Bestandsaufnahme ueber alle Module fand fuenfzehn Lesepfade, die das nicht
    abfingen - deshalb wird an der Quelle aufgeraeumt statt in jeder Abfrage
    gefiltert (siehe transaction_detach_service)."""
    session, household_id = ctx

    # Vor dem Archivieren, in derselben Transaktion: ein Zwischenzustand, in dem
    # die Buchung schon aus dem Bestand ist und die Verknuepfung noch zeigt, soll
    # gar nicht erst entstehen.
    try:
        detached = await detach_transactions(session, household_id, [str(transaction_id)])
    except TransactionDetachForbiddenError as exc:
        # 403 und nicht 404: die Buchung gibt es, sie ist nur nicht von
        # dieser Rolle zu entfernen. Ein 404 waere hier eine Luege, die den
        # Nutzer nach einer Buchung suchen liesse, die er vor sich sieht.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    # updated_at (und version) pflegt der Trigger aus Migration 0004.
    result = await session.execute(
        text(
            "update transactions set is_archived = true "
            "where id = :id and household_id = :household_id returning id"
        ),
        {"id": str(transaction_id), "household_id": str(household_id)},
    )
    if result.first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transaction_not_found")

    # Wurde eine Vertragsfaelligkeit wieder geoeffnet, muessen next_due_date und
    # die Fehlzaehler nachziehen. Als Hintergrundaufgabe, damit die Nachricht
    # erst rausgeht, wenn der Commit geschrieben ist - sonst saehe der Worker die
    # Faelligkeit noch als zugeordnet (dieselbe Ueberlegung wie beim Import,
    # routes/imports.py._enqueue_reconcile).
    if any(schluessel.startswith("vertrag") for schluessel in detached):
        background_tasks.add_task(
            enqueue_contract_reconcile, household_id, context=f"transaction={transaction_id}"
        )


@router.put(
    "/{transaction_id}/splits",
    response_model=TransactionDetail,
    responses={409: {"model": TransactionConflict, "description": "Versionskonflikt"}},
)
async def replace_transaction_splits(
    transaction_id: UUID,
    payload: TransactionSplitsUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    user: CurrentUser,
    if_match: IfMatchHeader = None,
) -> TransactionDetail | JSONResponse:
    """Ersetzt Betrag und Splits einer Buchung atomar - die Modellvalidierung
    von TransactionSplitsUpdate garantiert bereits sum(splits) == amount_minor
    (Architekturplan Abschnitt 6.4)."""
    session, household_id = ctx
    expected_version = _resolve_expected_version(if_match, payload.expected_version)

    # Bewusst vor dem Versions-UPDATE: eine ungueltige Kategorie in irgendeinem
    # Split soll den Request abbrechen, bevor die Buchungsversion hochgezaehlt
    # wurde - sonst haette ein fehlgeschlagener Request andere Geraete in einen
    # Phantomkonflikt geschickt.
    resolved_category_ids = await resolve_category_ids(
        session, household_id, [split.category_id for split in payload.splits]
    )

    params: dict[str, object] = {
        "amount_minor": payload.amount_minor,
        "id": str(transaction_id),
        "household_id": str(household_id),
    }
    # "version = version + 1" ist hier notwendig, weil sich fachlich die Splits
    # aendern, die Buchungszeile selbst aber gleich bleiben kann (gleicher
    # Betrag, nur andere Kategorien). Der Trigger wuerde das als No-Op werten
    # und nicht hochzaehlen; die explizite Zuweisung erklaert ihm, dass es eine
    # echte Aenderung ist. Den neuen Wert bestimmt weiterhin er.
    query = (
        "update transactions set amount_minor = :amount_minor, version = version + 1 "
        "where id = :id and household_id = :household_id"
    )
    if expected_version is not None:
        query += " and version = :expected_version and not is_archived"
        params["expected_version"] = expected_version
    query += " returning id"

    updated = (await session.execute(text(query), params)).first()
    if updated is None:
        if expected_version is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="transaction_not_found"
            )
        return await _version_conflict(session, transaction_id, household_id, expected_version)

    await session.execute(
        text("delete from transaction_splits where transaction_id = :id"),
        {"id": str(transaction_id)},
    )
    for split, resolved_split_category_id in zip(
        payload.splits, resolved_category_ids, strict=True
    ):
        await session.execute(
            text(
                "insert into transaction_splits "
                "(household_id, transaction_id, goal_id, amount_minor, note, "
                f"{provenance_service.CATEGORY_ASSIGNMENT_INSERT_COLUMNS}) "
                "values (:household_id, :transaction_id, :goal_id, "
                ":amount_minor, :note, "
                f"{provenance_service.CATEGORY_ASSIGNMENT_INSERT_VALUES})"
            ),
            {
                "household_id": str(household_id),
                "transaction_id": str(transaction_id),
                "goal_id": str(split.goal_id) if split.goal_id else None,
                "amount_minor": split.amount_minor,
                "note": split.note,
                **provenance_service.manual_assignment(resolved_split_category_id, user.id),
            },
        )

    detail = await _load_transaction_detail(session, transaction_id, household_id)
    response.headers["ETag"] = _etag(detail.version)
    return detail


@router.post("/bulk-categorize", response_model=BulkCategorizeResult)
async def bulk_categorize(
    payload: BulkCategorizeInput, ctx: HouseholdEditorSession, user: CurrentUser
) -> BulkCategorizeResult:
    """Bulk-Kategorisierung (Abschnitt 10.3, Buchungsregister:
    'Mehrfachauswahl und Bulk-Kategorisierung'). Wirkt nur auf Buchungen mit
    genau einem Split - Split-Buchungen muessen einzeln bearbeitet werden,
    da unklar waere, welcher Split die neue Kategorie erhalten soll.

    Bleibt fuer Bestandsclients unveraendert erhalten (Web ruft ihn weiterhin
    auf); die Einzelrueckmeldung je Buchung liefert
    POST /v1/transactions/bulk-category/preview|apply."""
    session, household_id = ctx

    resolved_category_id = await resolve_category_id(session, household_id, payload.category_id)

    result = await session.execute(
        text(
            f"update transaction_splits ts set {provenance_service.CATEGORY_ASSIGNMENT_SET_SQL} "
            "from transactions t "
            "where ts.transaction_id = t.id "
            "and t.household_id = :household_id "
            "and t.id = any(:transaction_ids) "
            "and (select count(*) from transaction_splits where transaction_id = t.id) = 1 "
            "returning t.id"
        ),
        {
            "household_id": str(household_id),
            "transaction_ids": [str(tid) for tid in payload.transaction_ids],
            **provenance_service.manual_assignment(resolved_category_id, user.id),
        },
    )
    updated_ids = {row.id for row in result}
    return BulkCategorizeResult(
        updated=len(updated_ids), skipped=len(payload.transaction_ids) - len(updated_ids)
    )


# --- Kategoriezuordnung an Buchungen ----------------------------------------
# docs/KATEGORISIERUNGSPLAN.md, Abschnitte "Kategorie an Buchungen aendern" und
# "Manuelle Mehrfachzuordnung". Die eingehende Kategorie-ID laeuft in jedem
# Pfad zuerst durch resolve_category_id(); geschrieben wird ausschliesslich der
# zurueckgegebene Wert (ein Merge-Tombstone wird dabei auf sein aktives Ziel
# kanonisiert).

TRANSACTION_HAS_MULTIPLE_SPLITS = "transaction_has_multiple_splits"

# Meldung des Triggers reject_archived_category_reference()
# (supabase/migrations/0001_schema.sql).
_CATEGORY_ARCHIVED_MESSAGE = "CATEGORY_ARCHIVED"
_CHECK_VIOLATION_SQLSTATE = "23514"


def _is_archived_category_error(exc: DBAPIError) -> bool:
    """Erkennt die Ablehnung durch den Archiv-Trigger.

    Der Fall ist im Bulk erreichbar, obwohl die Zielkategorie beim Auflaufen
    des Requests noch aktiv war: zwischen Vorschau und Anwendung kann sie
    archiviert worden sein. Dann wird nur diese Zeile uebersprungen, statt den
    ganzen Vorgang scheitern zu lassen."""
    if extract_sqlstate(exc) != _CHECK_VIOLATION_SQLSTATE:
        return False
    return _CATEGORY_ARCHIVED_MESSAGE in str(getattr(exc, "orig", exc))


def _as_uuid(value: object) -> UUID | None:
    return UUID(str(value)) if value is not None else None


async def _load_single_split_id(session: AsyncSession, transaction_id: UUID) -> UUID | None:
    """Gibt die Split-ID zurueck, wenn die Buchung genau einen Split hat.

    Bei mehreren Splits waere unklar, welcher die neue Gesamtkategorie
    erhalten soll - eine Gesamtkategorie darf eine vorhandene Aufteilung
    niemals still ueberschreiben (Plan: "Einzelbuchung Web")."""
    rows = (
        await session.execute(
            text("select id from transaction_splits where transaction_id = :id"),
            {"id": str(transaction_id)},
        )
    ).all()
    return UUID(str(rows[0].id)) if len(rows) == 1 else None


@router.put(
    "/{transaction_id}/category",
    response_model=TransactionDetail,
    responses={409: {"model": TransactionConflict, "description": "Versionskonflikt"}},
)
async def update_transaction_category(
    transaction_id: UUID,
    payload: TransactionCategoryUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    user: CurrentUser,
    if_match: IfMatchHeader = None,
) -> TransactionDetail | JSONResponse:
    """Aendert die Kategorie genau eines Splits (category_id: null entfernt sie).

    Der erwartete Versionsstand darf wie ueberall im Modul als If-Match-Header
    oder als expected_version im Body kommen."""
    session, household_id = ctx
    expected_version = _resolve_expected_version(if_match, payload.expected_version)

    existing = (
        await session.execute(
            text(
                "select is_archived from transactions "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(transaction_id), "household_id": str(household_id)},
        )
    ).first()
    # Fremde und archivierte Buchungen sind hier gleichermassen 404: eine
    # geloeschte Buchung nachtraeglich zu kategorisieren wuerde sie fachlich
    # wiederbeleben, ohne dass es jemand angefordert hat.
    if existing is None or existing.is_archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transaction_not_found")

    split_id = await _load_single_split_id(session, transaction_id)
    if split_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=TRANSACTION_HAS_MULTIPLE_SPLITS
        )

    resolved_category_id = await resolve_category_id(session, household_id, payload.category_id)

    # Wie in replace_transaction_splits: die Buchungszeile selbst aendert sich
    # nicht, der Trigger wuerde das als No-Op werten. Die explizite Zuweisung
    # macht die Kategorieaenderung fuer andere Geraete sichtbar.
    params: dict[str, object] = {
        "id": str(transaction_id),
        "household_id": str(household_id),
    }
    query = (
        "update transactions set version = version + 1 "
        "where id = :id and household_id = :household_id"
    )
    if expected_version is not None:
        query += " and version = :expected_version and not is_archived"
        params["expected_version"] = expected_version
    query += " returning id"

    updated = (await session.execute(text(query), params)).first()
    if updated is None:
        if expected_version is None:  # pragma: no cover - Existenz ist oben geprueft
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="transaction_not_found"
            )
        return await _version_conflict(session, transaction_id, household_id, expected_version)

    await session.execute(
        text(
            f"update transaction_splits set {provenance_service.CATEGORY_ASSIGNMENT_SET_SQL} "
            "where id = :split_id"
        ),
        {
            "split_id": str(split_id),
            **provenance_service.manual_assignment(resolved_category_id, user.id),
        },
    )

    detail = await _load_transaction_detail(session, transaction_id, household_id)
    response.headers["ETag"] = _etag(detail.version)
    return detail


async def _load_bulk_snapshots(
    session: AsyncSession, household_id: UUID, transaction_ids: list[UUID]
) -> dict[UUID, dict[str, Any]]:
    """Ein einziger Blick auf Version, Archivstatus und Splitlage aller
    angefragten Buchungen - 500 Einzelabfragen waeren pro Vorgang 500
    Roundtrips. Vorschau und Anwendung benutzen dieselbe Abfrage, damit die
    Vorschau nicht nach anderen Regeln urteilt als das Anwenden."""
    result = await session.execute(
        text(
            "select t.id as transaction_id, t.version, t.is_archived, "
            "count(ts.id) as split_count, "
            "(array_agg(ts.id))[1] as split_id, "
            "(array_agg(ts.category_id))[1] as split_category_id "
            "from transactions t "
            "left join transaction_splits ts on ts.transaction_id = t.id "
            "where t.household_id = :household_id and t.id = any(:transaction_ids) "
            "group by t.id, t.version, t.is_archived"
        ),
        {
            "household_id": str(household_id),
            "transaction_ids": [str(tid) for tid in transaction_ids],
        },
    )
    return {UUID(str(row.transaction_id)): dict(row._mapping) for row in result}


async def _apply_bulk_category(
    session: AsyncSession,
    household_id: UUID,
    snapshot: dict[str, Any],
    target_category_id: UUID | None,
    actor_user_id: UUID,
) -> BulkCategoryItem:
    """Schreibt genau eine Buchung des Bulk-Vorgangs.

    Laeuft in einem Savepoint: eine zwischenzeitlich archivierte Zielkategorie
    laesst nur diese Zeile scheitern, waehrend die uebrigen Aenderungen des
    Vorgangs bestehen bleiben (Plan: "ein Konflikt blockiert nicht alle
    uebrigen gueltigen Aenderungen")."""
    transaction_id = UUID(str(snapshot["transaction_id"]))
    params = {
        "id": str(transaction_id),
        "household_id": str(household_id),
        "expected_version": int(snapshot["version"]),
    }
    bumped_version: int | None = None
    try:
        async with session.begin_nested():
            bumped = (
                await session.execute(
                    text(
                        "update transactions set version = version + 1 "
                        "where id = :id and household_id = :household_id "
                        "and version = :expected_version and not is_archived "
                        "returning version"
                    ),
                    params,
                )
            ).first()
            if bumped is not None:
                bumped_version = int(bumped.version)
                await session.execute(
                    text(
                        "update transaction_splits set "
                        f"{provenance_service.CATEGORY_ASSIGNMENT_SET_SQL} "
                        "where id = :split_id"
                    ),
                    {
                        "split_id": str(snapshot["split_id"]),
                        **provenance_service.manual_assignment(
                            target_category_id, actor_user_id
                        ),
                    },
                )
    except DBAPIError as exc:
        if not _is_archived_category_error(exc):
            raise
        return BulkCategoryItem(
            transaction_id=transaction_id, status="skipped", reason="category_inactive"
        )

    if bumped_version is None:
        # Zwischen Snapshot und Schreibvorgang hat jemand anders geschrieben.
        current = await _load_transaction_detail(session, transaction_id, household_id)
        return BulkCategoryItem(
            transaction_id=transaction_id,
            status="conflict",
            reason="version_conflict",
            current_version=current.version,
        )

    return BulkCategoryItem(
        transaction_id=transaction_id, status="applied", current_version=bumped_version
    )


async def _bulk_category_items(
    session: AsyncSession,
    household_id: UUID,
    payload: BulkCategoryInput,
    *,
    write: bool,
    actor_user_id: UUID,
) -> list[BulkCategoryItem]:
    """Gemeinsame Logik von Vorschau und Anwendung.

    Die Zielkategorie wird genau einmal vorab aufgeloest: eine unbekannte oder
    archivierte Zielkategorie ist ein Aufruferfehler (404/409 fuer den ganzen
    Request) und kein Grund, 500 Buchungen einzeln zu ueberspringen. Der
    Vorschaumodus fuehrt bewusst kein einziges UPDATE aus - die
    Request-Transaktion wird am Ende committet, ein Schreibvorgang bliebe also
    bestehen."""
    target_category_id = await resolve_category_id(session, household_id, payload.category_id)
    snapshots = await _load_bulk_snapshots(session, household_id, payload.transaction_ids)

    items: list[BulkCategoryItem] = []
    for transaction_id in payload.transaction_ids:
        snapshot = snapshots.get(transaction_id)
        if snapshot is None:
            items.append(
                BulkCategoryItem(
                    transaction_id=transaction_id, status="skipped", reason="not_found"
                )
            )
            continue

        current_version = int(snapshot["version"])
        if snapshot["is_archived"]:
            items.append(
                BulkCategoryItem(
                    transaction_id=transaction_id,
                    status="skipped",
                    reason="archived_transaction",
                    current_version=current_version,
                )
            )
            continue

        if int(snapshot["split_count"]) != 1:
            items.append(
                BulkCategoryItem(
                    transaction_id=transaction_id,
                    status="skipped",
                    reason="split_transaction",
                    current_version=current_version,
                )
            )
            continue

        expected_version = payload.expected_versions.get(transaction_id)
        if expected_version is not None and expected_version != current_version:
            items.append(
                BulkCategoryItem(
                    transaction_id=transaction_id,
                    status="conflict",
                    reason="version_conflict",
                    current_version=current_version,
                )
            )
            continue

        if _as_uuid(snapshot["split_category_id"]) == target_category_id:
            items.append(
                BulkCategoryItem(
                    transaction_id=transaction_id,
                    status="unchanged",
                    reason="already_assigned",
                    current_version=current_version,
                )
            )
            continue

        if not write:
            # Vorschau meldet denselben Status, den die Anwendung liefern
            # wuerde - die Oberflaeche zeigt damit exakt "wird geaendert".
            items.append(
                BulkCategoryItem(
                    transaction_id=transaction_id,
                    status="applied",
                    current_version=current_version,
                )
            )
            continue

        items.append(
            await _apply_bulk_category(
                session, household_id, snapshot, target_category_id, actor_user_id
            )
        )

    return items


def _bulk_result(items: list[BulkCategoryItem]) -> BulkCategoryResult:
    return BulkCategoryResult(
        applied=sum(1 for item in items if item.status == "applied"),
        unchanged=sum(1 for item in items if item.status == "unchanged"),
        skipped=sum(1 for item in items if item.status == "skipped"),
        conflict=sum(1 for item in items if item.status == "conflict"),
        items=items,
    )


@router.post("/bulk-category/preview", response_model=BulkCategoryResult)
async def preview_bulk_category(
    payload: BulkCategoryInput, ctx: HouseholdEditorSession, user: CurrentUser
) -> BulkCategoryResult:
    """Zeigt je Buchung, was das Anwenden taete - ohne etwas zu schreiben."""
    session, household_id = ctx
    return _bulk_result(
        await _bulk_category_items(
            session, household_id, payload, write=False, actor_user_id=user.id
        )
    )


@router.post("/bulk-category/apply", response_model=BulkCategoryResult)
async def apply_bulk_category(
    payload: BulkCategoryInput, ctx: HouseholdEditorSession, user: CurrentUser
) -> BulkCategoryResult:
    """Wendet die Mehrfachzuordnung an und meldet jede Buchung einzeln.
    Die Obergrenze von 500 Buchungen prueft bereits BulkCategoryInput (422)."""
    session, household_id = ctx
    return _bulk_result(
        await _bulk_category_items(
            session, household_id, payload, write=True, actor_user_id=user.id
        )
    )


@router.get("/splits/unassigned-goal", response_model=list[UnassignedGoalSplitCandidate])
async def list_unassigned_goal_splits(
    ctx: HouseholdSession,
    account_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=300)] = 100,
) -> list[UnassignedGoalSplitCandidate]:
    """Kandidaten fuer die rueckwirkende Sparziel-Zuordnung (Architekturplan
    6.7, Option (c) neben der direkten Zuordnung am Split via PUT .../splits):
    alle Splits ohne goal_id, damit bereits gebuchte Transaktionen nachtraeglich
    einem Sparziel zugeordnet werden koennen."""
    session, household_id = ctx
    query = (
        "select ts.id as split_id, ts.transaction_id, t.account_id, t.booked_date, "
        "ts.amount_minor, t.payee_label, ts.category_id, ts.note "
        "from transaction_splits ts join transactions t on t.id = ts.transaction_id "
        "where ts.household_id = :household_id and ts.goal_id is null and not t.is_archived "
    )
    params: dict[str, object] = {"household_id": str(household_id), "limit": limit}
    if account_id is not None:
        query += "and t.account_id = :account_id "
        params["account_id"] = str(account_id)
    query += "order by t.booked_date desc, ts.created_at desc limit :limit"

    result = await session.execute(text(query), params)
    return [UnassignedGoalSplitCandidate(**row._mapping) for row in result]


@router.post("/splits/assign-goal", response_model=BulkAssignGoalResult)
async def assign_goal_to_splits(
    payload: BulkAssignGoalInput, ctx: HouseholdEditorSession
) -> BulkAssignGoalResult:
    """Ordnet mehrere Splits auf einmal einem Sparziel zu (goal_id: null loest
    die Zuordnung wieder)."""
    session, household_id = ctx

    if payload.goal_id is not None:
        goal_row = (
            await session.execute(
                text("select 1 from savings_goals where id = :id and household_id = :household_id"),
                {"id": str(payload.goal_id), "household_id": str(household_id)},
            )
        ).first()
        if goal_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal_not_found")

    result = await session.execute(
        text(
            "update transaction_splits set goal_id = :goal_id "
            "where household_id = :household_id and id = any(:split_ids) "
            "returning id"
        ),
        {
            "goal_id": str(payload.goal_id) if payload.goal_id else None,
            "household_id": str(household_id),
            "split_ids": [str(sid) for sid in payload.split_ids],
        },
    )
    updated_ids = {row.id for row in result}
    return BulkAssignGoalResult(
        updated=len(updated_ids), skipped=len(payload.split_ids) - len(updated_ids)
    )
