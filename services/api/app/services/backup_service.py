"""Sicherung und Wiederherstellung eines Haushalts als eine einzige Datei.

Abgrenzung zu den beiden anderen Wegen im Projekt:

- `infra/backup/` sichert die ganze Maschine, verschluesselt, mit Restoretest.
- `scripts/db_snapshot.py` sichert die ganze Entwicklungsdatenbank ueber
  Docker und `pg_dump`.
- Dieses Modul sichert **einen Haushalt** und ist das einzige der drei, das
  ohne Werkzeug auf dem Rechner auskommt: Herunterladen im Browser, spaeter
  wieder hochladen. Genau das, was in der Oberflaeche gebraucht wird.

Das Format ist ein ZIP mit `manifest.json` und je Tabelle einer JSON-Datei.
Bewusst kein `pg_dump`-Archiv: das setzte ein passendes `pg_dump` auf dem
Server voraus und waere an dessen Version gebunden.

Warum die Umwandlung in der Datenbank passiert und nicht in Python
--------------------------------------------------------------------
Export ist `to_jsonb(zeile)`, Import ist `jsonb_populate_recordset`. Beide
Richtungen macht Postgres selbst, mit demselben Typwissen. Ein eigener
Konverter in Python muesste UUID, Datum, Zeitstempel mit Zeitzone, `numeric`,
`jsonb`, Felder und Aufzaehlungstypen einzeln behandeln - und bei `numeric`
faengt man sich ueber Pythons `float` einen Rundungsfehler ein, der bei Geld
nicht passieren darf.

Der JSON-Text wird deshalb auch nie durch Pythons `json` geparst, sondern so,
wie Postgres ihn liefert, in die Datei geschrieben und beim Einspielen
unveraendert zurueckgereicht. `json.loads("2.30")` ergaebe ein `float`, und
damit waere die Genauigkeit weg, bevor irgendjemand sie pruefen koennte.

Nachgemessen an einem gefuellten Haushalt (rund 1000 Buchungen, 55 gefuellte Tabellen):
jede Tabelle geht bitgleich durch den Weg hin und zurueck, Aufzaehlungstypen
und Felder eingeschlossen.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import data_explorer_service as explorer

logger = logging.getLogger(__name__)

FORMAT_KENNUNG: Final = "finanzplaner-haushaltssicherung"
FORMAT_VERSION: Final = 1

MANIFEST_NAME: Final = "manifest.json"
TABELLEN_ORDNER: Final = "tabellen"

# Eine Sicherung dieses Haushalts ist unkomprimiert rund 7 MB gross. Die
# Grenze liegt weit darueber, damit sie nur greift, wenn etwas grundsaetzlich
# nicht stimmt - und nicht, wenn jemand viel benutzt hat.
MAX_ARCHIV_BYTES: Final = 512 * 1024 * 1024
MAX_ENTPACKT_BYTES: Final = 2 * 1024 * 1024 * 1024

# audit_log ist im Datenbank-Explorer versteckt, damit es dort niemand
# loeschen kann (siehe _HIDDEN_TABLES dort). In eine Sicherung gehoert es
# trotzdem: es ist der Nachweis darueber, wer was geaendert hat, und beim
# Aufsetzen einer neuen Instanz waere er sonst unwiederbringlich weg.
ZUSAETZLICH_SICHERN: Final = frozenset({"audit_log"})

# Mitgliedschaften und Einladungen werden gesichert, aber NIE zurueckgespielt.
# Wer Zugriff auf einen Haushalt hat, ist eine Frage der Gegenwart, nicht des
# Sicherungszeitpunkts: eine alte Mitgliederliste einzuspielen hiesse, jemanden
# wieder hereinzulassen, der entfernt wurde - oder sich selbst auszusperren.
# Sie liegen in der Datei, damit nachvollziehbar bleibt, wer damals Zugriff
# hatte.
NIE_EINSPIELEN: Final = frozenset({"household_members", "household_invites"})

# Anfuegende Journale: die RLS-Regeln lassen dort INSERT zu, aber weder UPDATE
# noch DELETE - ein Protokoll, das der Schreiber nachtraeglich aendern kann,
# waere als Nachweis wertlos. Beim Einspielen werden sie deshalb ergaenzt und
# nicht ersetzt; vorhandene Zeilen bleiben unangetastet.
NUR_ERGAENZEN: Final = frozenset({"audit_log", "ai_task_events", "contract_merge_changes"})

# Auf diesen Tabellen ist das DELETE-Recht der Anwendungsrolle entzogen
# (`revoke delete on categories from authenticated`), ein Leeren vor dem
# Einspielen also unmoeglich. Stattdessen wird Zeile fuer Zeile ueberschrieben.
# Was der Haushalt heute zusaetzlich hat und die Sicherung nicht kennt, bleibt
# stehen - das wird im Ergebnis ausgewiesen, statt es zu verschweigen.
UEBERSCHREIBEN: Final = frozenset({"categories", "category_groups"})


class BackupError(ValueError):
    """Die Datei passt nicht zu dieser Anwendung. Traegt einen Satz, der der
    Oberflaeche unveraendert vorgelegt werden kann."""


def _kurzfassung(fehler: Exception) -> str:
    """Die erste Zeile einer Datenbankmeldung.

    Der volle Text schleppt Anweisung, Position und Verweis mit - in einer
    Oberflaeche ist das Rauschen, im Protokoll steht es ohnehin vollstaendig."""
    urspruenglich = getattr(fehler, "orig", None) or fehler
    return str(urspruenglich).strip().splitlines()[0][:200]


@dataclass(frozen=True)
class TabellenPlan:
    """Eine Tabelle und wie mit ihr beim Einspielen umgegangen wird."""

    name: str
    primary_key: str | None
    spalten: tuple[str, ...]
    selbstbezuege: tuple[str, ...]
    nutzerspalten: tuple[str, ...]

    @property
    def wird_eingespielt(self) -> bool:
        return self.name not in NIE_EINSPIELEN


@dataclass
class Sicherungsplan:
    """Alle Haushaltstabellen in Einfuegereihenfolge, samt Bauplan je Tabelle."""

    tabellen: dict[str, TabellenPlan] = field(default_factory=dict)
    # Eltern vor Kindern. Loeschreihenfolge rueckwaerts.
    einfuege_reihenfolge: list[str] = field(default_factory=list)
    schema_fingerabdruck: str = ""


async def _nutzerspalten(session: AsyncSession) -> dict[str, tuple[str, ...]]:
    """Spalten, die auf `auth.users` zeigen.

    37 Spalten im Schema verweisen dorthin. Beim Einspielen in dieselbe
    Instanz sind das gueltige Verweise. Beim Einspielen in eine frisch
    aufgesetzte Instanz gibt es die Nutzer dort nicht mehr - der Fremdschluessel
    schluege fehl und risse die ganze Wiederherstellung mit sich. Solche
    Verweise werden dann geleert (alle sind `on delete set null`, also
    nullbar); die Sachdaten haengen nicht daran.
    """
    rows = (
        await session.execute(
            text(
                "select child.relname as tabelle, att.attname as spalte "
                "from pg_constraint con "
                "join pg_class child on child.oid = con.conrelid "
                "join pg_namespace ns on ns.oid = child.relnamespace "
                "join pg_class parent on parent.oid = con.confrelid "
                "join pg_namespace pns on pns.oid = parent.relnamespace "
                "join lateral unnest(con.conkey) as k(attnum) on true "
                "join pg_attribute att "
                "  on att.attrelid = con.conrelid and att.attnum = k.attnum "
                "where con.contype = 'f' and ns.nspname = 'public' "
                "  and pns.nspname = 'auth' and parent.relname = 'users' "
                "  and not att.attnotnull"
            )
        )
    ).all()
    treffer: dict[str, list[str]] = {}
    for row in rows:
        treffer.setdefault(str(row.tabelle), []).append(str(row.spalte))
    return {name: tuple(sorted(spalten)) for name, spalten in treffer.items()}


async def _versteckte_tabellen(session: AsyncSession) -> dict[str, tuple[str, ...]]:
    """Spalten der Tabellen, die der Explorer-Katalog auslaesst.

    Der Katalog dort blendet `audit_log` aus, weil es in jener Oberflaeche
    loeschbar waere. Fuer eine Sicherung wird es gebraucht - also hier
    nachgeholt statt den Katalog fuer alle Aufrufer umzustellen."""
    if not ZUSAETZLICH_SICHERN:
        return {}
    rows = (
        await session.execute(
            text(
                "select c.table_name, c.column_name "
                "from information_schema.columns c "
                "join information_schema.tables t "
                "  on t.table_schema = c.table_schema and t.table_name = c.table_name "
                "where c.table_schema = 'public' and t.table_type = 'BASE TABLE' "
                "  and c.table_name = any(:namen) "
                "order by c.table_name, c.ordinal_position"
            ),
            {"namen": sorted(ZUSAETZLICH_SICHERN)},
        )
    ).all()
    treffer: dict[str, list[str]] = {}
    for row in rows:
        treffer.setdefault(str(row.table_name), []).append(str(row.column_name))
    return {name: tuple(spalten) for name, spalten in treffer.items()}


async def lade_plan(session: AsyncSession) -> Sicherungsplan:
    """Baut aus dem Datenbankkatalog den Bauplan fuer Sichern und Einspielen."""
    katalog = await explorer.load_catalog(session)
    nutzerspalten = await _nutzerspalten(session)
    versteckt = await _versteckte_tabellen(session)

    # Selbstbezuege je Tabelle: eine Kategorie, die auf eine andere Kategorie
    # zeigt. Beim Einfuegen ist die Zielzeile womoeglich noch nicht da.
    selbst: dict[str, list[str]] = {}
    for fremdschluessel in (fk for liste in katalog.children.values() for fk in liste):
        if fremdschluessel.child_table == fremdschluessel.parent_table:
            selbst.setdefault(fremdschluessel.child_table, []).append(
                fremdschluessel.child_column
            )

    spalten_je_tabelle: dict[str, tuple[str, ...]] = {
        name: tuple(spalte.name for spalte in info.columns)
        for name, info in katalog.tables.items()
    }
    spalten_je_tabelle.update(versteckt)

    primaerschluessel: dict[str, str | None] = {
        name: info.primary_key for name, info in katalog.tables.items()
    }
    for name in versteckt:
        # Die versteckten Tabellen stehen nicht im Katalog; ihr Schluessel wird
        # nur fuer das Ergaenzen gebraucht und heisst im ganzen Schema `id`.
        primaerschluessel.setdefault(name, "id")

    tabellen = {
        name: TabellenPlan(
            name=name,
            primary_key=primaerschluessel.get(name),
            spalten=spalten,
            selbstbezuege=tuple(sorted(selbst.get(name, ()))),
            nutzerspalten=nutzerspalten.get(name, ()),
        )
        for name, spalten in spalten_je_tabelle.items()
    }

    # Loeschreihenfolge ist Kinder vor Eltern; zum Einfuegen genau andersherum.
    # Die versteckten Tabellen haengt der Katalog nicht ein - sie kommen ans
    # Ende, wo ihre Elterntabellen bereits stehen.
    reihenfolge = [
        name for name in reversed(explorer.deletion_order(katalog)) if name in tabellen
    ]
    reihenfolge += [name for name in sorted(versteckt) if name not in reihenfolge]

    return Sicherungsplan(
        tabellen=tabellen,
        einfuege_reihenfolge=reihenfolge,
        schema_fingerabdruck=_fingerabdruck(tabellen),
    )


def _fingerabdruck(tabellen: dict[str, TabellenPlan]) -> str:
    """Kurzfassung des Schemas.

    Steht im Manifest, damit beim Einspielen auffaellt, dass die Sicherung aus
    einem anderen Schemastand stammt. Kein Hinderungsgrund - fehlende Spalten
    werden beim Einspielen zu NULL, unbekannte ignoriert -, aber die
    Oberflaeche soll es sagen koennen, statt es zu verschweigen."""
    zeilen = sorted(
        f"{name}:{','.join(plan.spalten)}" for name, plan in tabellen.items()
    )
    return "sha256:" + hashlib.sha256("\n".join(zeilen).encode("utf-8")).hexdigest()[:16]


async def haushaltsname(session: AsyncSession, household_id: UUID) -> str:
    row = (
        await session.execute(
            text("select name from households where id = :id"), {"id": str(household_id)}
        )
    ).one_or_none()
    return str(row.name) if row else "Unbekannt"


async def exportiere_haushalt(
    session: AsyncSession, household_id: UUID
) -> tuple[bytes, dict[str, int]]:
    """Sichert den ganzen Haushalt in ein ZIP. Gibt Datei und Zeilenzahlen zurueck.

    Gelesen wird unter denselben RLS-Regeln wie jeder andere Aufruf. Das ist
    keine Einschraenkung, sondern die Absicherung: was die Regeln nicht
    hergeben, kann auch nicht in eine Sicherung geraten, und ein Mitglied kann
    ueber diesen Weg keinen fremden Haushalt herausziehen."""
    plan = await lade_plan(session)
    puffer = io.BytesIO()
    zeilen_je_tabelle: dict[str, int] = {}

    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archiv:
        for name in plan.einfuege_reihenfolge:
            # `::text` direkt in der Datenbank: der JSON-Text geht unveraendert
            # in die Datei, ohne Umweg ueber Pythons Zahlentypen.
            rohtext = (
                await session.execute(
                    text(
                        f'select coalesce(jsonb_agg(to_jsonb(t)), \'[]\'::jsonb)::text, '
                        f'count(*)::bigint from "{name}" t where household_id = :hid'
                    ),
                    {"hid": str(household_id)},
                )
            ).one()
            anzahl = int(rohtext[1])
            if anzahl == 0:
                # Leere Tabellen kommen nicht in die Datei. Beim Einspielen ist
                # "nicht enthalten" und "leer" dasselbe, und 37 leere Dateien
                # machen eine Sicherung nur unuebersichtlich.
                continue
            zeilen_je_tabelle[name] = anzahl
            archiv.writestr(f"{TABELLEN_ORDNER}/{name}.json", str(rohtext[0]))

        manifest = {
            "format": FORMAT_KENNUNG,
            "version": FORMAT_VERSION,
            "erstellt_am": datetime.now(UTC).isoformat(),
            "haushalt": {
                "id": str(household_id),
                "name": await haushaltsname(session, household_id),
            },
            "schema_fingerabdruck": plan.schema_fingerabdruck,
            "tabellen": dict(sorted(zeilen_je_tabelle.items())),
            "zeilen_gesamt": sum(zeilen_je_tabelle.values()),
        }
        archiv.writestr(
            MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2)
        )

    daten = puffer.getvalue()
    logger.info(
        "Haushaltssicherung erstellt: %d Tabellen, %d Zeilen, %.1f KB",
        len(zeilen_je_tabelle),
        sum(zeilen_je_tabelle.values()),
        len(daten) / 1024,
    )
    return daten, zeilen_je_tabelle


def _oeffne_archiv(daten: bytes) -> zipfile.ZipFile:
    if len(daten) > MAX_ARCHIV_BYTES:
        raise BackupError(
            f"Die Datei ist {len(daten) / 1024 / 1024:.0f} MB gross und damit "
            "groesser als erwartet. Bitte pruefen, ob es wirklich eine "
            "Haushaltssicherung ist."
        )
    try:
        archiv = zipfile.ZipFile(io.BytesIO(daten))
    except zipfile.BadZipFile as fehler:
        raise BackupError(
            "Die Datei ist kein lesbares ZIP-Archiv. Eine Haushaltssicherung "
            "endet auf .zip und stammt aus „Sicherung herunterladen“."
        ) from fehler

    # Ein ZIP kann beim Entpacken um ein Vielfaches wachsen. Die Groesse steht
    # im Verzeichnis und laesst sich pruefen, bevor irgendetwas gelesen wird.
    entpackt = sum(eintrag.file_size for eintrag in archiv.infolist())
    if entpackt > MAX_ENTPACKT_BYTES:
        raise BackupError(
            "Das Archiv wuerde beim Entpacken unverhaeltnismaessig gross. "
            "Es wird nicht gelesen."
        )
    return archiv


def lies_manifest(daten: bytes) -> dict[str, Any]:
    """Liest das Manifest und prueft, dass die Datei zu dieser Anwendung passt."""
    archiv = _oeffne_archiv(daten)
    if MANIFEST_NAME not in archiv.namelist():
        raise BackupError(
            "In dem Archiv fehlt die manifest.json. Es stammt nicht aus "
            "„Sicherung herunterladen“."
        )
    try:
        manifest = json.loads(archiv.read(MANIFEST_NAME).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as fehler:
        raise BackupError("Die manifest.json im Archiv ist beschaedigt.") from fehler

    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_KENNUNG:
        raise BackupError(
            "Das Archiv ist keine Haushaltssicherung dieser Anwendung."
        )
    version = manifest.get("version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise BackupError(
            f"Die Sicherung hat das Format {version}, diese Anwendung kennt "
            f"hoechstens {FORMAT_VERSION}. Sie stammt aus einer neueren Fassung."
        )
    return manifest


def _tabellen_im_archiv(archiv: zipfile.ZipFile) -> dict[str, str]:
    """Tabellenname -> Eintragsname im Archiv."""
    gefunden: dict[str, str] = {}
    for eintrag in archiv.namelist():
        if not eintrag.startswith(f"{TABELLEN_ORDNER}/") or not eintrag.endswith(".json"):
            continue
        name = eintrag[len(TABELLEN_ORDNER) + 1 : -len(".json")]
        # Kein Pfadanteil: ein Eintrag wie `tabellen/../../etc/passwd` waere
        # ein Angriff auf den Leser, kein Tabellenname.
        if "/" in name or "\\" in name or not name:
            continue
        gefunden[name] = eintrag
    return gefunden


@dataclass(frozen=True)
class TabellenVorschau:
    tabelle: str
    zeilen_in_datei: int
    zeilen_im_haushalt: int
    behandlung: str  # 'ersetzen' | 'ueberschreiben' | 'ergaenzen' | 'uebersprungen'
    hinweis: str | None = None


@dataclass(frozen=True)
class Vorschau:
    haushalt_name: str
    quelle_haushalt_name: str
    quelle_haushalt_id: str
    erstellt_am: str
    schema_passt: bool
    zeilen_in_datei: int
    zeilen_im_haushalt: int
    tabellen: list[TabellenVorschau]
    unbekannte_tabellen: list[str]


async def vorschau_einspielen(
    session: AsyncSession, household_id: UUID, daten: bytes
) -> Vorschau:
    """Was ein Einspielen dieser Datei bewirken wuerde - ohne etwas zu aendern.

    Pflichtschritt vor dem Einspielen: die Zahl, die zaehlt, ist nicht "wie
    viel kommt herein", sondern "wie viel ist danach weg"."""
    manifest = lies_manifest(daten)
    archiv = _oeffne_archiv(daten)
    plan = await lade_plan(session)
    im_archiv = _tabellen_im_archiv(archiv)
    bestand = await explorer.table_counts(session, household_id)

    zeilen_manifest: dict[str, int] = manifest.get("tabellen") or {}
    eintraege: list[TabellenVorschau] = []
    unbekannt: list[str] = []

    for name in plan.einfuege_reihenfolge:
        in_datei = int(zeilen_manifest.get(name, 0))
        im_haushalt = int(bestand.get(name, 0))
        if in_datei == 0 and im_haushalt == 0:
            continue
        if name in NIE_EINSPIELEN:
            behandlung, hinweis = (
                "uebersprungen",
                "Zugriffsrechte werden nie aus einer Sicherung gesetzt.",
            )
        elif name in NUR_ERGAENZEN:
            behandlung, hinweis = (
                "ergaenzen",
                "Anfuegendes Protokoll - vorhandene Zeilen bleiben stehen.",
            )
        elif name in UEBERSCHREIBEN:
            behandlung, hinweis = (
                "ueberschreiben",
                "Wird Zeile fuer Zeile ersetzt; heute zusaetzlich Vorhandenes bleibt.",
            )
        else:
            behandlung, hinweis = "ersetzen", None
        eintraege.append(
            TabellenVorschau(
                tabelle=name,
                zeilen_in_datei=in_datei,
                zeilen_im_haushalt=im_haushalt,
                behandlung=behandlung,
                hinweis=hinweis,
            )
        )

    for name in sorted(im_archiv):
        if name not in plan.tabellen:
            unbekannt.append(name)

    eintraege.sort(key=lambda e: (-max(e.zeilen_in_datei, e.zeilen_im_haushalt), e.tabelle))
    haushalt = manifest.get("haushalt") or {}

    return Vorschau(
        haushalt_name=await haushaltsname(session, household_id),
        quelle_haushalt_name=str(haushalt.get("name") or "Unbekannt"),
        quelle_haushalt_id=str(haushalt.get("id") or ""),
        erstellt_am=str(manifest.get("erstellt_am") or ""),
        schema_passt=manifest.get("schema_fingerabdruck") == plan.schema_fingerabdruck,
        zeilen_in_datei=int(manifest.get("zeilen_gesamt") or 0),
        zeilen_im_haushalt=sum(bestand.values()),
        tabellen=eintraege,
        unbekannte_tabellen=unbekannt,
    )


@dataclass(frozen=True)
class Ergebnis:
    haushalt_name: str
    geloeschte_zeilen: int
    eingespielte_zeilen: int
    je_tabelle: dict[str, int]
    uebersprungene_tabellen: list[str]
    geleerte_nutzerverweise: int
    schema_passt: bool


async def spiele_ein(
    session: AsyncSession, household_id: UUID, daten: bytes
) -> Ergebnis:
    """Ersetzt die Daten dieses Haushalts durch den Stand aus der Datei.

    Laeuft in der Transaktion des Aufrufers - entweder steht danach der Stand
    aus der Sicherung, oder es hat sich nichts geaendert. Ein halb eingespielter
    Haushalt mit Buchungen ohne Konten waere schlimmer als ein Fehlschlag.

    Reihenfolge und Begruendung:

    1. Haushalt leeren (`purge_household`), Eltern nach Kindern.
    2. Einfuegen in umgekehrter Reihenfolge, also Eltern vor Kindern. Ohne das
       schluege jeder Fremdschluessel fehl, dessen Zielzeile noch fehlt -
       `session_replication_role = replica` waere der Ausweg, den darf die
       Anwendungsrolle aber nicht setzen (kein Superuser).
    3. Selbstbezuege in einem zweiten Durchgang. Eine Kategorie, die auf eine
       andere Kategorie derselben Tabelle zeigt, laesst sich nicht in einer
       einzigen Einfuegung aufloesen: die Zielzeile kann in derselben Ladung
       weiter hinten stehen. Deshalb erst mit leerem Verweis einfuegen, dann
       nachtragen. Drei der fuenf Selbstbezuege im Schema sind nicht
       `deferrable`, ein Aufschieben der Pruefung scheidet also aus.
    """
    manifest = lies_manifest(daten)
    archiv = _oeffne_archiv(daten)
    plan = await lade_plan(session)
    im_archiv = _tabellen_im_archiv(archiv)

    entfernt = await explorer.purge_household(session, household_id)

    je_tabelle: dict[str, int] = {}
    uebersprungen: list[str] = []
    geleerte_verweise = 0

    for name in plan.einfuege_reihenfolge:
        eintrag = im_archiv.get(name)
        if eintrag is None:
            continue
        tabelle = plan.tabellen[name]
        if not tabelle.wird_eingespielt:
            uebersprungen.append(name)
            continue

        rohtext = archiv.read(eintrag).decode("utf-8")
        if not rohtext.strip() or rohtext.strip() == "[]":
            continue

        # Die Haushalts-ID wird ueberschrieben statt uebernommen: damit laesst
        # sich eine Sicherung auch in einen neu angelegten Haushalt einspielen.
        # Alle uebrigen Schluessel sind UUIDs und bleiben, wie sie waren - die
        # Verweise untereinander stimmen dadurch weiterhin.
        ersetzungen: dict[str, Any] = {"household_id": str(household_id)}
        for spalte in tabelle.selbstbezuege:
            ersetzungen[spalte] = None

        # Der Tabellenname gehoert in die Fehlermeldung. Ohne ihn steht am Ende
        # nur "Zugriff verweigert" ueber einem Vorgang, der 91 Tabellen
        # anfasst - und niemand weiss, welche gemeint ist.
        try:
            eingefuegt, geleert = await _fuege_ein_mit_rueckfall(
                session, tabelle, rohtext, ersetzungen
            )
            if tabelle.selbstbezuege:
                await _trage_selbstbezuege_nach(session, tabelle, rohtext, household_id)
        except SQLAlchemyError as fehler:
            logger.warning(
                "Einspielen der Tabelle %s fehlgeschlagen: %s", name, fehler, exc_info=True
            )
            raise BackupError(
                f"Die Tabelle „{name}“ liess sich nicht einspielen: "
                f"{_kurzfassung(fehler)}. Es wurde nichts geaendert."
            ) from fehler

        geleerte_verweise += geleert
        if eingefuegt:
            je_tabelle[name] = eingefuegt

    logger.info(
        "Haushaltssicherung eingespielt: %d Zeilen entfernt, %d eingespielt",
        sum(entfernt.values()),
        sum(je_tabelle.values()),
    )

    return Ergebnis(
        haushalt_name=await haushaltsname(session, household_id),
        geloeschte_zeilen=sum(entfernt.values()),
        eingespielte_zeilen=sum(je_tabelle.values()),
        je_tabelle=dict(sorted(je_tabelle.items())),
        uebersprungene_tabellen=uebersprungen,
        geleerte_nutzerverweise=geleerte_verweise,
        schema_passt=manifest.get("schema_fingerabdruck") == plan.schema_fingerabdruck,
    )


def _liegt_an_fehlendem_nutzer(fehler: IntegrityError) -> bool:
    """Ist der Fremdschluessel gescheitert, weil das Nutzerkonto fehlt?

    Postgres nennt die Zieltabelle in der Detailzeile: „Key (created_by)=(...)
    is not present in table "users".“ Nur dieser Fall rechtfertigt einen
    zweiten Versuch - bei jedem anderen Fremdschluessel waere ein stilles
    Leeren der Spalte Datenverlust ohne Ansage."""
    urspruenglich = getattr(fehler, "orig", None)
    diagnose = getattr(urspruenglich, "diag", None)
    detail = getattr(diagnose, "message_detail", "") or ""
    return 'table "users"' in detail


async def _fuege_ein_mit_rueckfall(
    session: AsyncSession,
    tabelle: TabellenPlan,
    rohtext: str,
    ersetzungen: dict[str, Any],
) -> tuple[int, int]:
    """Fuegt eine Tabelle ein und gibt (Zeilen, geleerte Nutzerverweise) zurueck.

    Warum nicht vorher geprueft wird, ob es die genannten Nutzer gibt:
    `auth.users` ist fuer die Anwendungsrolle nicht lesbar - ein
    `select ... from auth.users` endet mit „permission denied for table
    users“. Es gibt also keinen Weg, das vor dem Versuch zu wissen.

    Deshalb andersherum: erst der ehrliche Versuch mit allen Verweisen, und
    nur wenn die Datenbank genau diesen Fremdschluessel ablehnt, ein zweiter
    Durchgang ohne ihn. Der Sicherungspunkt sorgt dafuer, dass der Fehlschlag
    nicht die ganze Transaktion mitreisst - ohne ihn waere nach dem ersten
    abgelehnten Verweis die komplette Wiederherstellung verloren.

    Im Normalfall - Sicherung und Ziel in derselben Instanz - greift der
    Rueckfall nie, und es bleibt bei einer einzigen Anweisung."""
    if not tabelle.nutzerspalten:
        return await _fuege_ein(session, tabelle, rohtext, ersetzungen), 0

    sicherungspunkt = await session.begin_nested()
    try:
        anzahl = await _fuege_ein(session, tabelle, rohtext, ersetzungen)
    except IntegrityError as fehler:
        await sicherungspunkt.rollback()
        if not _liegt_an_fehlendem_nutzer(fehler):
            raise
        logger.info(
            "Tabelle %s: Nutzerverweise zeigen ins Leere (Sicherung aus einer "
            "anderen Instanz), sie werden geleert.",
            tabelle.name,
        )
    else:
        await sicherungspunkt.commit()
        return anzahl, 0

    ohne_nutzer = {**ersetzungen, **{spalte: None for spalte in tabelle.nutzerspalten}}
    return (
        await _fuege_ein(session, tabelle, rohtext, ohne_nutzer),
        len(tabelle.nutzerspalten),
    )


async def _fuege_ein(
    session: AsyncSession,
    tabelle: TabellenPlan,
    rohtext: str,
    ersetzungen: dict[str, Any],
) -> int:
    """Schreibt eine Tabelle aus dem JSON-Text zurueck.

    `jsonb_populate_recordset` erledigt die Typumwandlung und ist dabei
    nachsichtig in genau die richtige Richtung: Schluessel, die es als Spalte
    nicht mehr gibt, werden ignoriert; Spalten, die die Sicherung nicht kennt,
    werden NULL. Eine Sicherung von vor einer Schemaaenderung laesst sich damit
    einspielen, ohne dass jemand sie von Hand anpasst."""
    zusatz = ""
    if tabelle.name in NUR_ERGAENZEN and tabelle.primary_key:
        # Anfuegende Protokolle: kein UPDATE erlaubt, also stillschweigend
        # ueberspringen, was schon dasteht.
        zusatz = f'on conflict ("{tabelle.primary_key}") do nothing'
    elif tabelle.name in UEBERSCHREIBEN and tabelle.primary_key:
        setzen = ", ".join(
            f'"{spalte}" = excluded."{spalte}"'
            for spalte in tabelle.spalten
            if spalte != tabelle.primary_key
        )
        zusatz = f'on conflict ("{tabelle.primary_key}") do update set {setzen}'

    anweisung = text(
        f'insert into "{tabelle.name}" '
        f'select * from jsonb_populate_recordset(null::"{tabelle.name}", ('
        f"  select coalesce(jsonb_agg(zeile || cast(:ersetzungen as jsonb)), '[]'::jsonb) "
        f"  from jsonb_array_elements(cast(:rohtext as jsonb)) as zeile"
        f")) {zusatz} returning 1"
    )
    zeilen = (
        await session.execute(
            anweisung,
            {"rohtext": rohtext, "ersetzungen": json.dumps(ersetzungen)},
        )
    ).all()
    return len(zeilen)


async def _trage_selbstbezuege_nach(
    session: AsyncSession, tabelle: TabellenPlan, rohtext: str, household_id: UUID
) -> None:
    """Setzt die im ersten Durchgang geleerten Verweise auf sich selbst.

    Jetzt stehen alle Zeilen der Tabelle, das Ziel ist also da."""
    if not tabelle.primary_key:
        return
    for spalte in tabelle.selbstbezuege:
        await session.execute(
            text(
                f'update "{tabelle.name}" as ziel '
                f'set "{spalte}" = quelle."{spalte}" '
                f'from jsonb_populate_recordset(null::"{tabelle.name}", cast(:rohtext as jsonb)) '
                f"  as quelle "
                f'where ziel."{tabelle.primary_key}" = quelle."{tabelle.primary_key}" '
                f"  and ziel.household_id = :hid "
                f'  and quelle."{spalte}" is not null'
            ),
            {"rohtext": rohtext, "hid": str(household_id)},
        )
