"""Personen eines Haushalts (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 6).

Warum ein eigenes Objekt und kein Freitextfeld an `contracts`: der Zweck der
Stufe ist das Gruppieren der Einnahmen. "Anna", "anna" und "Anna M." waeren als
Freitext drei Personen, und die Gruppierung damit wertlos. Der partielle
Unique-Index `household_people_name_uniq` auf `(household_id, lower(name))`
macht die Namensgleichheit zur Regel; `person_service` uebersetzt seinen
Verstoss in eine verstaendliche Meldung.

Bewusst KEIN Zwang zu einem Benutzerzugang: Partner und Kinder haben in aller
Regel keinen eigenen Login, sollen aber trotzdem als Einnahmequelle zaehlen.
`member_user_id` ist die optionale Bruecke, falls doch einer existiert.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# 80 Zeichen wie der CHECK an household_people.name - die Grenze steht damit
# an beiden Enden gleich und ergibt eine 422 statt eines Datenbankfehlers.
_NAME_FIELD = Field(min_length=1, max_length=80)

# Der Sortierschluessel ist eine reine Anzeigereihenfolge; eine Obergrenze
# haelt die Zahl im Bereich, den eine Auswahlliste noch sinnvoll ordnet.
_SORT_ORDER_FIELD = Field(default=0, ge=0, le=9999)


def _clean_name(value: str) -> str:
    """Vereinheitlicht Leerraum vor dem Vergleich.

    Ohne diesen Schritt waeren "Anna  M." und "Anna M." zwei verschiedene
    `lower(name)` und der Unique-Index liesse beide zu - genau die
    Doppelperson, die die Stufe verhindern soll.

    Ein Name aus lauter Leerzeichen genuegt `min_length=1` und waere nach dem
    Zusammenfassen leer; die Pruefung wiederholt sich hier deshalb NACH der
    Normalisierung, sonst schluege erst der CHECK in der Datenbank zu.
    """
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("Der Name darf nicht leer sein")
    return cleaned


class PersonCreate(BaseModel):
    name: str = _NAME_FIELD
    # Nur ein aktives Mitglied desselben Haushalts ist zulaessig; geprueft wird
    # das im Dienst, weil der Fremdschluessel auf auth.users allein einen
    # beliebigen fremden Nutzer durchliesse.
    member_user_id: UUID | None = None
    sort_order: int = _SORT_ORDER_FIELD

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return _clean_name(value)


class PersonUpdate(BaseModel):
    """Teilaenderung. Nicht mitgeschickte Felder bleiben unangetastet.

    Anders als bei `ContractUpdate` gibt es hier keine `clear_fields`-Liste:
    `member_user_id` ist das einzige leerbare Feld, und ein ausdruecklich
    mitgeschicktes `null` ist ueber `model_dump(exclude_unset=True)` von "nicht
    angefasst" unterscheidbar. Eine zweite Liste waere fuer ein Feld Ballast.
    """

    name: str | None = Field(default=None, min_length=1, max_length=80)
    member_user_id: UUID | None = None
    sort_order: int | None = Field(default=None, ge=0, le=9999)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        return _clean_name(value) if value is not None else None


class Person(BaseModel):
    id: UUID
    household_id: UUID
    name: str
    member_user_id: UUID | None
    sort_order: int
    # Loeschen ist ein Soft-Delete: ein `person_id` an einem Vertrag darf nicht
    # ins Leere zeigen. `is_active = false` gibt zugleich den Namen wieder frei,
    # weil der Unique-Index nur aktive Zeilen umfasst.
    is_active: bool
    created_at: datetime
    updated_at: datetime
