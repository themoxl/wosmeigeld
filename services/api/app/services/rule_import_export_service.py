"""Regel-Import und -Export samt Duplikatpruefung.

Die frueheste Fassung verglich nur zeichengleiche `match_payee_raw` und
ignorierte dabei die Zielkategorie. Damit galt `(?i)\\b(AMAZON|EBAY)\\b`
neben einer bestehenden Regel `Amazon` als neu, und dasselbe Paket liess sich
mehrfach installieren, sobald die Schreibweise auch nur in der Gross-
/Kleinschreibung abwich - der Regelbestand wuchs um Eintraege, die sich
gegenseitig die Buchungen wegnehmen.

Die Pruefung kennt deshalb drei Abstufungen (`RuleImportItemStatus`):

  * `duplicate` - gleiche Bedingung UND gleiche Aktion. Der Import brauchte
    nichts; wird ohne Zutun uebersprungen.
  * `conflict`  - gleiche Bedingung, ANDERE Zielkategorie. Beide Regeln
    stritten um dieselben Buchungen, gewinnen wuerde die hoehere Prioritaet.
    Abgewaehlt, aber bewusst nicht verboten: eine Umkategorisierung kann
    gewollt sein.
  * `overlap`   - die Muster teilen sich mindestens einen Suchbegriff, ohne
    deckungsgleich zu sein (`Amazon` gegen `(AMAZON|EBAY)`). Genau der Fall
    "20x eine aehnliche Regel".

Alles andere ist `new`. Nur `new` ist in der Oberflaeche vorbelegt.
"""

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.data import rule_packages
from app.schemas.rules import (
    PortableRule,
    RuleImportItemStatus,
    RuleImportPreview,
    RuleImportPreviewItem,
    RuleImportResult,
    RulePackage,
)

# Bruchstuecke unter drei Zeichen fliegen aus dem Begriffsvergleich: das "b"
# aus "\\b", das "i" aus "(?i)" oder ein "de" wuerden sonst fast jede Regel mit
# fast jeder anderen verwandt aussehen lassen.
_MIN_TERM_LENGTH = 3

# Trennt an allem, was kein Wortzeichen ist - damit zerfaellt ein Regex-Muster
# ohne eigenen Parser in seine Suchbegriffe. `re.UNICODE` ist bei str-Mustern
# Standard und hier nur zur Betonung gesetzt: "Müller" darf nicht am Umlaut
# zerbrechen.
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize_pattern(pattern: str | None) -> str:
    """Vergleichsform eines Musters: klein, ohne Rand- und Mehrfachleerzeichen.

    Danach sind ` (?i)\\bREWE\\b ` und `(?i)\\brewe\\b` dasselbe Muster. Die
    Gross-/Kleinschreibung darf hier verschwinden, weil die Regel-Engine sie
    ohnehin ignoriert (rules_service._matches_rule_pattern arbeitet mit
    re.IGNORECASE)."""
    if pattern is None:
        return ""
    return " ".join(pattern.strip().lower().split())


def pattern_terms(pattern: str | None) -> frozenset[str]:
    """Die Suchbegriffe eines Musters, unabhaengig von seiner Schreibweise.

    `(?i)\\b(AMAZON|EBAY)\\b` liefert {"amazon", "ebay"}, `Amazon` liefert
    {"amazon"} - die Schnittmenge ist der Grund, aus dem die Vorschau eine
    aehnliche Regel als solche benennen kann. Bewusst kein Regex-Parser: die
    Muster sind Haendlerlisten, keine Grammatiken."""
    if not pattern:
        return frozenset()
    return frozenset(
        token
        for token in _NON_WORD_RE.split(pattern.strip().lower())
        if len(token) >= _MIN_TERM_LENGTH
    )


def _condition_signature(rule: Any) -> tuple[Any, ...]:
    """Alles, was darueber entscheidet, WELCHE Buchungen eine Regel trifft.

    Zwei Regeln mit derselben Signatur greifen auf exakt derselben Menge -
    entweder ist die zweite ueberfluessig (gleiche Aktion) oder sie streitet
    mit der ersten (andere Aktion)."""
    account = getattr(rule, "match_account_id", None)
    return (
        normalize_pattern(getattr(rule, "match_payee_raw", None)),
        bool(getattr(rule, "match_payee_exact_word", False)),
        normalize_pattern(getattr(rule, "match_purpose", None)),
        bool(getattr(rule, "match_purpose_exact_word", False)),
        normalize_pattern(getattr(rule, "exclude_payee_raw", None)),
        normalize_pattern(getattr(rule, "exclude_purpose", None)),
        getattr(rule, "match_min_amount_minor", None),
        getattr(rule, "match_max_amount_minor", None),
        getattr(rule, "match_amount_mode", None) or "inside",
        str(account) if account else None,
    )


def _action_signature(rule: Any, category_id: UUID | None) -> tuple[Any, ...]:
    """Was eine Regel schreibt. `action_note` und `action_payee_id` bleiben
    aussen vor: der rueckwirkende Lauf schreibt sie nicht, und eine abweichende
    Notiz macht aus einer doppelten Regel keine neue Aussage."""
    return (
        str(category_id) if category_id else None,
        bool(getattr(rule, "action_set_transfer", False)),
    )


@dataclass(frozen=True)
class ExistingRule:
    """Eine bereits vorhandene Regel in der Vergleichsform der Pruefung."""

    id: UUID | None
    name: str
    category_name: str | None
    condition: tuple[Any, ...]
    action: tuple[Any, ...]
    payee_terms: frozenset[str]
    purpose_terms: frozenset[str]
    # Position im Paket statt im Bestand - nur fuer Duplikate innerhalb
    # derselben Datei gesetzt.
    package_index: int | None = None


def _as_existing(
    rule: Any,
    *,
    rule_id: UUID | None,
    name: str,
    category_id: UUID | None,
    category_name: str | None,
    package_index: int | None = None,
) -> ExistingRule:
    return ExistingRule(
        id=rule_id,
        name=name,
        category_name=category_name,
        condition=_condition_signature(rule),
        action=_action_signature(rule, category_id),
        payee_terms=pattern_terms(getattr(rule, "match_payee_raw", None)),
        purpose_terms=pattern_terms(getattr(rule, "match_purpose", None)),
        package_index=package_index,
    )


async def load_existing_rules(
    db: AsyncSession, household_id: UUID
) -> list[ExistingRule]:
    """Alle Regeln des Haushalts in Vergleichsform - eine Abfrage fuer den
    ganzen Import statt einer je einzuspielender Regel."""
    res = await db.execute(
        text(
            "SELECT cr.id, cr.name, cr.match_payee_raw, cr.match_payee_exact_word, "
            "cr.match_purpose, cr.match_purpose_exact_word, cr.exclude_payee_raw, "
            "cr.exclude_purpose, cr.match_min_amount_minor, cr.match_max_amount_minor, "
            "cr.match_amount_mode, cr.match_account_id, cr.action_category_id, "
            "cr.action_set_transfer, c.name AS category_name "
            "FROM classification_rules cr "
            "LEFT JOIN categories c ON c.id = cr.action_category_id "
            "WHERE cr.household_id = :household_id"
        ),
        {"household_id": str(household_id)},
    )
    return [
        _as_existing(
            row,
            rule_id=row.id,
            name=row.name,
            category_id=row.action_category_id,
            category_name=row.category_name,
        )
        for row in res
    ]


def classify_rule(
    rule: Any, category_id: UUID | None, existing: list[ExistingRule]
) -> tuple[RuleImportItemStatus, ExistingRule | None, list[str]]:
    """Vergleicht eine einzuspielende Regel mit dem Bestand.

    Rangfolge: duplicate > conflict > overlap > new. Der erste exakte Treffer
    beendet die Suche, sonst gewinnt der schwerwiegendste Fund - ein Konflikt
    ist wichtiger zu wissen als eine blosse Ueberschneidung."""
    condition = _condition_signature(rule)
    action = _action_signature(rule, category_id)
    payee_terms = pattern_terms(getattr(rule, "match_payee_raw", None))
    purpose_terms = pattern_terms(getattr(rule, "match_purpose", None))

    conflict: ExistingRule | None = None
    overlap: tuple[ExistingRule, list[str]] | None = None

    for candidate in existing:
        if candidate.condition == condition:
            if candidate.action == action:
                return "duplicate", candidate, []
            if conflict is None:
                conflict = candidate
            continue
        if conflict is not None or overlap is not None:
            continue
        shared = sorted(
            (payee_terms & candidate.payee_terms)
            | (purpose_terms & candidate.purpose_terms)
        )
        if shared:
            overlap = (candidate, shared)

    if conflict is not None:
        return "conflict", conflict, []
    if overlap is not None:
        return "overlap", overlap[0], overlap[1]
    return "new", None, []


async def resolve_category_key(
    db: AsyncSession, household_id: UUID, key: str
) -> tuple[UUID | None, str | None]:
    """Loest einen action_category_key auf (category_id, category_name) auf.

    Reihenfolge:
    1. Treffer auf categories.semantic_key im Haushalt
    2. Rueckfall: Name, ohne Ruecksicht auf Gross-/Kleinschreibung
    Ohne Treffer (None, None).
    """
    if not key:
        return None, None

    res = await db.execute(
        text(
            "SELECT id, name FROM categories "
            "WHERE household_id = :household_id AND semantic_key = :key AND NOT is_archived "
            "LIMIT 1"
        ),
        {"household_id": str(household_id), "key": key},
    )
    row = res.first()
    if row:
        return row.id, row.name

    res = await db.execute(
        text(
            "SELECT id, name FROM categories "
            "WHERE household_id = :household_id AND lower(name) = lower(:key) AND NOT is_archived "
            "LIMIT 1"
        ),
        {"household_id": str(household_id), "key": key},
    )
    row = res.first()
    if row:
        return row.id, row.name

    return None, None


async def find_duplicate_rule(
    db: AsyncSession, household_id: UUID, match_payee_raw: str | None
) -> UUID | None:
    """Erste Regel mit demselben Haendlermuster, unabhaengig von der
    Schreibweise. Nur noch fuer Aufrufer ausserhalb des Imports; der Import
    selbst vergleicht ueber `classify_rule` die ganze Regel."""
    normalized = normalize_pattern(match_payee_raw)
    if not normalized:
        return None
    res = await db.execute(
        text(
            "SELECT id FROM classification_rules "
            "WHERE household_id = :household_id "
            "AND lower(btrim(match_payee_raw)) = :match_payee_raw "
            "LIMIT 1"
        ),
        {"household_id": str(household_id), "match_payee_raw": normalized},
    )
    row = res.first()
    return row.id if row else None


class _CategoryResolver:
    """Loest Kategorieschluessel je Paket genau einmal auf - ein Paket nennt
    denselben Schluessel oft in mehreren Regeln."""

    def __init__(
        self,
        db: AsyncSession,
        household_id: UUID,
        overrides: dict[str, str] | None,
    ) -> None:
        self._db = db
        self._household_id = household_id
        self._overrides = overrides or {}
        self._cache: dict[str, tuple[UUID | None, str | None]] = {}

    async def resolve(self, key: str | None) -> tuple[UUID | None, str | None]:
        if not key:
            return None, None
        if key in self._cache:
            return self._cache[key]

        resolved: tuple[UUID | None, str | None] = (None, None)
        override_id = self._overrides.get(key)
        if override_id:
            res = await self._db.execute(
                text(
                    "SELECT id, name FROM categories "
                    "WHERE id = :id AND household_id = :household_id AND NOT is_archived"
                ),
                {"id": override_id, "household_id": str(self._household_id)},
            )
            row = res.first()
            if row:
                resolved = (row.id, row.name)

        if resolved[0] is None:
            resolved = await resolve_category_key(self._db, self._household_id, key)

        self._cache[key] = resolved
        return resolved


def _preview_item(
    index: int,
    rule: PortableRule,
    category_id: UUID | None,
    category_name: str | None,
    status: RuleImportItemStatus,
    match: ExistingRule | None,
    shared_terms: list[str],
) -> RuleImportPreviewItem:
    in_package = (
        status == "duplicate" and match is not None and match.package_index is not None
    )
    return RuleImportPreviewItem(
        index=index,
        rule_name=rule.name,
        match_payee_raw=rule.match_payee_raw,
        category_key=rule.action_category_key,
        resolved_category_id=category_id,
        resolved_category_name=category_name,
        status=status,
        is_duplicate=status == "duplicate",
        duplicate_rule_id=match.id if match and status == "duplicate" else None,
        existing_rule_name=match.name if match else None,
        existing_category_name=match.category_name if match else None,
        shared_terms=shared_terms,
        in_package_duplicate=in_package,
        recommended=status == "new",
    )


async def preview_import(
    db: AsyncSession,
    household_id: UUID,
    package: RulePackage,
    category_overrides: dict[str, str] | None = None,
) -> RuleImportPreview:
    """Zeigt je Regel, was ein Import taete - ohne zu schreiben."""
    resolver = _CategoryResolver(db, household_id, category_overrides)
    existing = await load_existing_rules(db, household_id)
    # Bereits gesehene Regeln DIESES Pakets. Ein Paket, das denselben Eintrag
    # zweimal mitbringt, wuerde sonst zweimal als "neu" durchgehen.
    from_package: list[ExistingRule] = []

    items: list[RuleImportPreviewItem] = []
    mappable = 0
    unmapped = 0
    counts: dict[str, int] = {"new": 0, "duplicate": 0, "conflict": 0, "overlap": 0}

    for index, rule in enumerate(package.rules):
        category_id, category_name = await resolver.resolve(rule.action_category_key)
        status, match, shared = classify_rule(
            rule, category_id, existing + from_package
        )

        if rule.action_category_key:
            if category_id:
                mappable += 1
            else:
                unmapped += 1

        counts[status] += 1
        items.append(
            _preview_item(index, rule, category_id, category_name, status, match, shared)
        )
        from_package.append(
            _as_existing(
                rule,
                rule_id=None,
                name=rule.name,
                category_id=category_id,
                category_name=category_name,
                package_index=index,
            )
        )

    return RuleImportPreview(
        package_name=package.name,
        total_rules=len(package.rules),
        mappable=mappable,
        unmapped=unmapped,
        duplicates=counts["duplicate"],
        conflicts=counts["conflict"],
        overlaps=counts["overlap"],
        recommended=counts["new"],
        items=items,
    )


_INSERT_RULE_SQL = (
    "INSERT INTO classification_rules (household_id, name, priority, is_active, "
    "match_payee_raw, match_payee_exact_word, match_purpose, match_purpose_exact_word, "
    "exclude_payee_raw, exclude_purpose, match_min_amount_minor, match_max_amount_minor, "
    "match_amount_mode, match_account_id, action_payee_id, action_category_id, "
    "action_set_transfer, action_note, source) "
    "VALUES (:household_id, :name, :priority, :is_active, :match_payee_raw, "
    ":match_payee_exact_word, :match_purpose, :match_purpose_exact_word, "
    ":exclude_payee_raw, :exclude_purpose, :match_min_amount_minor, "
    ":match_max_amount_minor, :match_amount_mode, :match_account_id, "
    ":action_payee_id, :action_category_id, :action_set_transfer, :action_note, :source)"
)


async def execute_import(
    db: AsyncSession,
    household_id: UUID,
    package: RulePackage,
    category_overrides: dict[str, str] | None = None,
    skip_duplicates: bool = True,
    skip_indices: list[int] | None = None,
) -> RuleImportResult:
    """Spielt die Regeln des Pakets ein (source='imported').

    Jede Regel wird in einem eigenen Savepoint geschrieben. Ohne ihn nimmt ein
    einziger Indexkonflikt die ganze Transaktion mit, und ein Paket aus zehn
    Regeln bliebe wegen einer bereits vorhandenen komplett aussen vor."""
    resolver = _CategoryResolver(db, household_id, category_overrides)
    existing = await load_existing_rules(db, household_id)
    deselected = set(skip_indices or [])

    created = 0
    skipped_duplicates = 0
    skipped_deselected = 0
    errors: list[str] = []

    for index, rule in enumerate(package.rules):
        if index in deselected:
            skipped_deselected += 1
            continue

        category_id, category_name = await resolver.resolve(rule.action_category_key)
        status, _, _ = classify_rule(rule, category_id, existing)
        if status == "duplicate" and skip_duplicates:
            skipped_duplicates += 1
            continue

        try:
            async with db.begin_nested():
                await db.execute(
                    text(_INSERT_RULE_SQL),
                    {
                        "household_id": str(household_id),
                        "name": rule.name,
                        "priority": rule.priority,
                        "is_active": rule.is_active,
                        "match_payee_raw": rule.match_payee_raw,
                        "match_payee_exact_word": rule.match_payee_exact_word,
                        "match_purpose": rule.match_purpose,
                        "match_purpose_exact_word": rule.match_purpose_exact_word,
                        "exclude_payee_raw": rule.exclude_payee_raw,
                        "exclude_purpose": rule.exclude_purpose,
                        "match_min_amount_minor": rule.match_min_amount_minor,
                        "match_max_amount_minor": rule.match_max_amount_minor,
                        "match_amount_mode": rule.match_amount_mode,
                        "match_account_id": None,
                        "action_payee_id": None,
                        "action_category_id": str(category_id) if category_id else None,
                        "action_set_transfer": rule.action_set_transfer,
                        "action_note": rule.action_note,
                        "source": "imported",
                    },
                )
        except IntegrityError:
            # Der partielle Unique-Index classification_rules_payee_category_uniq
            # hat zugeschlagen: dieselbe einfache Haendler/Kategorie-Regel gibt
            # es schon. Das ist kein Fehler, sondern genau der Fall, den die
            # Vorschau meldet - nur ueber einen Weg, den sie nicht sieht
            # (z. B. eine parallel angelegte Regel).
            skipped_duplicates += 1
            continue
        except Exception as exc:  # noqa: BLE001 - Grund gehoert in die Antwort
            errors.append(f"Fehler bei Regel '{rule.name}': {exc}")
            continue

        created += 1
        # Ab jetzt Teil des Bestands: eine spaetere identische Regel desselben
        # Pakets ist damit ein Duplikat.
        existing.append(
            _as_existing(
                rule,
                rule_id=None,
                name=rule.name,
                category_id=category_id,
                category_name=category_name,
            )
        )

    return RuleImportResult(
        created=created,
        skipped=skipped_duplicates + skipped_deselected,
        skipped_duplicates=skipped_duplicates,
        skipped_deselected=skipped_deselected,
        errors=errors,
    )


async def package_install_counts(
    db: AsyncSession, household_id: UUID
) -> dict[str, int]:
    """Je eingebautem Paket die Zahl der Regeln, die es im Haushalt schon gibt.

    Verglichen wird nur das normalisierte Haendlermuster und bewusst nicht die
    Zielkategorie: die Frage ist "habe ich dieses Paket schon installiert?",
    nicht "zeigt jede Regel noch dorthin, wo sie einmal hinzeigte?"."""
    res = await db.execute(
        text(
            "SELECT DISTINCT lower(btrim(match_payee_raw)) AS pattern "
            "FROM classification_rules "
            "WHERE household_id = :household_id AND match_payee_raw IS NOT NULL"
        ),
        {"household_id": str(household_id)},
    )
    known = {row.pattern for row in res if row.pattern}

    counts: dict[str, int] = {}
    for package_id, package in rule_packages.BUILTIN_PACKAGES.items():
        counts[package_id] = sum(
            1
            for rule in package.get("rules", [])
            if normalize_pattern(rule.get("match_payee_raw")) in known
        )
    return counts


async def export_rules(
    db: AsyncSession,
    household_id: UUID,
    rule_ids: list[UUID] | None = None,
    package_name: str = "Meine Regeln",
    description: str = "",
) -> RulePackage:
    """Exportiert Regeln als portables RulePackage.
    Loest Kategorie-UUIDs zurueck in semantic_keys (oder Namen, wenn es keinen
    semantic_key gibt)."""
    query = (
        "SELECT cr.*, c.semantic_key, c.name as category_name "
        "FROM classification_rules cr "
        "LEFT JOIN categories c ON cr.action_category_id = c.id "
        "WHERE cr.household_id = :household_id"
    )
    params: dict[str, Any] = {"household_id": str(household_id)}

    if rule_ids:
        query += " AND cr.id = ANY(:rule_ids)"
        params["rule_ids"] = [str(r) for r in rule_ids]

    res = await db.execute(text(query), params)

    rules = []
    for row in res:
        cat_key = row.semantic_key if row.semantic_key else row.category_name

        rules.append(
            PortableRule(
                name=row.name,
                match_payee_raw=row.match_payee_raw,
                match_payee_exact_word=row.match_payee_exact_word,
                match_purpose=row.match_purpose,
                match_purpose_exact_word=row.match_purpose_exact_word,
                exclude_payee_raw=row.exclude_payee_raw,
                exclude_purpose=row.exclude_purpose,
                match_min_amount_minor=row.match_min_amount_minor,
                match_max_amount_minor=row.match_max_amount_minor,
                match_amount_mode=row.match_amount_mode,
                action_category_key=cat_key,
                action_set_transfer=row.action_set_transfer,
                action_note=row.action_note,
                priority=row.priority,
                is_active=row.is_active,
            )
        )

    return RulePackage(name=package_name, description=description, rules=rules)
