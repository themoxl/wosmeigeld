"""Die Zusage aus Plan 7.1 fuer die vier Versicherungs-Push-Typen.

**Diese Datei prueft genau eine Sache, dafuer vollstaendig: auf dem gesperrten
Geraet erscheint WEDER Betrag NOCH Person NOCH Versicherungsart.** Sie ist
strenger als `contract_alerts.py:29-31`, das nur Betraege verbietet, und der
Grund steht in Plan 7.1: "Berufsunfaehigkeit" oder "Pflegezusatz" ist eine
Gesundheitsangabe, die jeder im Raum mitliest. Beim Sonderkuendigungsfenster
(P3) ist es die schaerfste Stelle des Moduls - der Anlass IST eine
Beitragserhoehung, und der naheliegende Satz "Die PKV steigt um 48 EUR" traegt
alle drei verbotenen Angaben auf einmal. Beim Schadenvorgang (P5) kommt eine
vierte verbotene Angabe hinzu, die es bei den ersten drei Typen nicht gab: der
HERGANG. "Wasserschaden Kueche wartet auf Rueckmeldung" nennt weder Betrag noch
Person noch Sparte - und verraet trotzdem, was in der Wohnung passiert ist.

Bewusst ohne Datenbank. Die Zusage haengt an vier reinen Funktionen und an
`build_push_messages`; ein Haushalt in Postgres wuerde nichts beweisen, was
diese fuenf nicht schon zeigen, und die Pruefung soll auch dann laufen, wenn
gerade kein Supabase steht. Was die Abfragen aus der Datenbank holen, ist
Gegenstand des Fachdienstes in services/api - hier landet davon ohnehin nichts
im Text.

Die Gegenprobe zur Reissleine auf der Geraeteseite steht in
apps/mobile/src/notifications/useNotificationResponseRouter.ts
(`INSURANCE_ALLOWED_DATA_KEYS`): dort werden unerwartete Datenfelder gemeldet,
hier wird verhindert, dass sie entstehen.
"""

import re
from datetime import date, datetime, timedelta

from worker.notifications.insurance_alerts import (
    ClaimFollowupAlert,
    DeadlineAlert,
    QuestionAlert,
    SpecialTerminationAlert,
    build_push_messages,
    claim_followup_body,
    current_stage,
    deadline_body,
    is_new_stage,
    question_body,
    special_termination_body,
)

# Tageszahlen, mit denen ein Fenster im Betrieb wirklich vorkommt: die drei
# Stufen der Staffel (sofort/7/2), der Tag davor und danach - und negative
# Werte, weil das Handlungsdatum vor dem rechtlichen Fristende liegt und die
# Stufe "sofort" genau dafuer da ist.
_TAGESZAHLEN = (-30, -3, -1, 0, 1, 2, 3, 7, 8, 14, 30, 60)

# Was ein Betrag im Text bedeuten wuerde: "16,70", "1.234,00", "48 EUR",
# "48 Euro", "48 €" - oder eine Quote, die den Betrag mittelbar verraet.
_BETRAG = re.compile(r"\d[\d.]*,\d{2}|\d+\s*(?:EUR|Euro|€)|€|%|\bProzent\b", re.IGNORECASE)

# Stellvertreter fuer das, was ein Haushalt an Namen fuehrt: der Titel des
# Haushalts, Vornamen und Nachnamen versicherter Personen, Rollenbezeichnungen.
# Keiner davon darf je in einen Text geraten - die Funktionen nehmen sie gar
# nicht erst entgegen, und dieser Test haelt das fest.
_PERSONEN = ("Anna", "Ben", "Muster", "Musterhaushalt", "Mutter", "Kind", "Sohn", "Tochter")

# Sparten- und Versichererwoerter. Die obere Haelfte ist der eigentliche Grund
# fuer die verschaerfte Regel: jedes dieser Woerter ist auf einem
# Sperrbildschirm eine Gesundheitsangabe.
_VERSICHERUNGSART = (
    "Berufsunfaehig",
    "Pflege",
    "Kranken",
    "PKV",
    "Zahn",
    "Unfall",
    "Hinterbliebene",
    "Risikoleben",
    "Sterbegeld",
    "Hausrat",
    "Haftpflicht",
    "Rechtsschutz",
    "Kfz",
    "Gebaeude",
    "Reise",
    "Tier",
    "Ruerup",
    "Riester",
    "Allianz",
    "Barmenia",
    "Debeka",
    "HUK",
    "AXA",
)

# Der Hergang eines Schadens - die vierte verbotene Angabe, die es erst mit P5
# gibt. Sie faellt durch die drei Listen oben hindurch: "Wasserschaden Kueche"
# ist weder Betrag noch Person noch Sparte, sagt auf dem Sperrbildschirm aber,
# was in der Wohnung passiert ist. Und "Einbruch" sagt zusaetzlich, dass
# jemand da war.
_HERGANG = (
    "Wasserschaden",
    "Leitungswasser",
    "Kueche",
    "Küche",
    "Bad",
    "Keller",
    "Einbruch",
    "Diebstahl",
    "gestohlen",
    "Brand",
    "Feuer",
    "Sturm",
    "Hagel",
    "Glasbruch",
    "Marder",
    "Auffahr",
    "Fahrrad",
    "Handy",
    "Werkstatt",
    "Rueckstau",
)

# Deckungsgleich mit INSURANCE_ALLOWED_DATA_KEYS in
# apps/mobile/src/notifications/useNotificationResponseRouter.ts. Was hier nicht
# steht, kommt gar nicht erst bis aufs Geraet - und kann deshalb auch keine
# kuenftige lokale Benachrichtigung anreichern.
_ERLAUBTE_DATENSCHLUESSEL = {"type", "policy_id", "policy_count"}


def _pruefe_neutral(text: str, *, zahlen: tuple[int, ...] = ()) -> None:
    """Ein Push-Text traegt keinen Betrag, keine Person, keine Sparte, keinen Hergang.

    `zahlen` sind die Werte, die der Text nennen DARF - die Tageszahl eines
    Countdowns, die Anzahl betroffener Policen oder die Wartezeit eines
    Vorgangs. Jede andere Ziffernfolge waere eine Angabe aus der Police oder
    aus der Schadenakte, und genau die soll hier nicht stehen.
    """
    assert not _BETRAG.search(text), f"Betrag oder Quote im Push-Text: {text!r}"
    for name in _PERSONEN:
        assert name.lower() not in text.lower(), f"Person im Push-Text: {text!r}"
    for wort in _VERSICHERUNGSART:
        assert wort.lower() not in text.lower(), f"Versicherungsart im Push-Text: {text!r}"
    for wort in _HERGANG:
        assert wort.lower() not in text.lower(), f"Hergang im Push-Text: {text!r}"
    erlaubt = {str(abs(zahl)) for zahl in zahlen}
    gefunden = set(re.findall(r"\d+", text))
    assert gefunden <= erlaubt, f"Unerwartete Zahl im Push-Text: {text!r}"


# ---------------------------------------------------------------------------
# Plan 7.1: der Text
# ---------------------------------------------------------------------------


def test_sonderkuendigungstext_nennt_weder_betrag_noch_person_noch_versicherungsart() -> None:
    """Der P3-Text, ueber die ganze Bandbreite vorkommender Tageszahlen."""
    for tage in _TAGESZAHLEN:
        _pruefe_neutral(special_termination_body(tage), zahlen=(tage,))


def test_fristentext_und_rueckfrage_bleiben_ebenso_neutral() -> None:
    """Dieselbe Zusage fuer die beiden P2-Typen - sie teilen den Sperrbildschirm."""
    for tage in _TAGESZAHLEN:
        _pruefe_neutral(deadline_body(tage), zahlen=(tage,))
    for anzahl in (1, 2, 5, 17):
        _pruefe_neutral(question_body(anzahl), zahlen=(anzahl,))


def test_sonderkuendigungstext_kennt_nur_vier_saetze() -> None:
    """Aus dem Text kann nichts herauskommen, was nicht hineingegeben wurde.

    Die Gegenprobe zur Wortliste oben: statt zu fragen, ob ein verbotenes Wort
    vorkommt, wird hier gezeigt, dass ueberhaupt nur vier Saetze entstehen
    koennen - drei Konstanten und eine mit eingesetzter Tageszahl. Ein spaeter
    eingebauter Zusatz ("... bei Ihrer Hausratversicherung") fiele hier auf,
    auch wenn er in keiner Liste steht.
    """
    saetze = {special_termination_body(tage) for tage in range(-400, 400)}
    schablonen = {re.sub(r"\d+", "N", satz) for satz in saetze}
    assert schablonen == {
        "Ein Sonderkuendigungsrecht endet in Kuerze. Heute handeln.",
        "Ein Sonderkuendigungsrecht laeuft heute ab.",
        "Ein Sonderkuendigungsrecht laeuft morgen ab.",
        "Ein Sonderkuendigungsrecht laeuft in N Tagen ab.",
    }


def test_vorgangstext_nennt_weder_betrag_noch_person_noch_versicherungsart_noch_hergang() -> None:
    """Der P5-Text, ueber beide Anlaesse und die ganze Bandbreite an Wartezeiten.

    Die vierte verbotene Angabe ist hier die entscheidende: der naheliegende
    Satz waere "Wasserschaden Kueche (1.240 EUR) wartet seit 41 Tagen auf
    Rueckmeldung" - er traegt Betrag UND Hergang, und der Hergang sagt mehr
    ueber die Wohnung, als der Nutzer im Wartezimmer vorlesen moechte.
    """
    for tage in (0, 1, 2, 30, 31, 41, 400):
        _pruefe_neutral(claim_followup_body(tage, action_overdue=False), zahlen=(tage,))
        _pruefe_neutral(claim_followup_body(tage, action_overdue=True), zahlen=(tage,))


def test_vorgangstext_kennt_nur_drei_saetze() -> None:
    """Die Gegenprobe zur Wortliste - wie beim Sonderfenster.

    Statt zu fragen, ob ein verbotenes Wort vorkommt, wird gezeigt, dass
    ueberhaupt nur drei Saetze entstehen koennen. Ein spaeter eingebauter
    Zusatz ("... zu Ihrem Wasserschaden") fiele hier auf, auch wenn das Wort in
    keiner Liste steht.
    """
    saetze = {
        claim_followup_body(tage, action_overdue=ueberfaellig)
        for tage in range(-10, 400)
        for ueberfaellig in (False, True)
    }
    schablonen = {re.sub(r"\d+", "N", satz) for satz in saetze}
    assert schablonen == {
        "Zu einem Vorgang ist ein vorgemerkter Schritt faellig.",
        "Ein Vorgang wartet auf Rueckmeldung.",
        "Ein Vorgang wartet seit N Tagen auf Rueckmeldung.",
    }


def test_verstrichenes_handlungsdatum_wird_nicht_zum_countdown() -> None:
    """"In -3 Tagen" waere Unsinn, "heute" eine Behauptung ueber einen
    verpassten Termin. Der Text sagt stattdessen, dass es eilt."""
    for tage in (-1, -3, -30):
        satz = special_termination_body(tage)
        assert satz == "Ein Sonderkuendigungsrecht endet in Kuerze. Heute handeln."
        assert "-" not in satz


# ---------------------------------------------------------------------------
# Plan 7.1: der Datenteil
# ---------------------------------------------------------------------------


def _daten(nachricht: dict[str, object]) -> dict[str, object]:
    """Der Datenteil einer Nachricht, typsicher ausgepackt."""
    daten = nachricht["data"]
    assert isinstance(daten, dict), nachricht
    return daten


def _nachrichten() -> list[dict[str, object]]:
    """Alle vier Typen in einem Durchgang, ein Haushalt mit einem Geraet."""
    haushalt = "11111111-1111-4111-8111-111111111111"
    return [
        dict(nachricht)
        for nachricht in build_push_messages(
            [DeadlineAlert(household_id=haushalt, policy_id="p-1", days_left=14, stage_days=14)],
            [
                QuestionAlert(household_id=haushalt, policy_id="p-2"),
                QuestionAlert(household_id=haushalt, policy_id="p-3"),
            ],
            [
                SpecialTerminationAlert(
                    household_id=haushalt,
                    notice_id="n-1",
                    policy_id="p-4",
                    days_left=7,
                    stage_days=7,
                )
            ],
            {haushalt: ["ExponentPushToken[abc]"]},
            [
                ClaimFollowupAlert(
                    household_id=haushalt,
                    claim_id="c-1",
                    policy_id="p-5",
                    days_waiting=41,
                    action_overdue=False,
                    stage_days=0,
                ),
                # Der Vorgang gegen eine fremde Versicherung: keine eigene
                # Police, also auch keine ID im Datenteil.
                ClaimFollowupAlert(
                    household_id=haushalt,
                    claim_id="c-2",
                    policy_id=None,
                    days_waiting=33,
                    action_overdue=True,
                    stage_days=0,
                ),
            ],
        )
    ]


def test_datenteil_traegt_nur_die_drei_erlaubten_schluessel() -> None:
    for nachricht in _nachrichten():
        assert set(_daten(nachricht)) <= _ERLAUBTE_DATENSCHLUESSEL, nachricht


def test_sonderfenster_traegt_die_police_und_nicht_den_vorgang() -> None:
    """Die Vorgangs-ID bleibt aussen vor - schon der Vorrat im Datenteil ist
    das, woraus eine kuenftige lokale Benachrichtigung ihren Text anreichern
    koennte. Die Police fuehrt ohnehin auf die Seite, auf der sie steht."""
    sonderfenster = [
        _daten(nachricht)
        for nachricht in _nachrichten()
        if _daten(nachricht)["type"] == "insurance_special_termination"
    ]
    assert len(sonderfenster) == 1
    assert set(sonderfenster[0]) == {"type", "policy_id"}
    assert sonderfenster[0]["policy_id"] == "p-4"


def test_vorgang_traegt_keine_vorgangs_id_und_ohne_police_gar_keine() -> None:
    """Dieselbe Zurueckhaltung wie beim Sonderfenster, eine Stufe strenger.

    `claim_id` steht in KEINER Nachricht - der Router routet auf die Police,
    und was im Datenteil liegt, ist der Vorrat fuer eine kuenftige lokale
    Benachrichtigung. Ein Vorgang ohne eigene Police faehrt deshalb ganz ohne
    ID; ein Platzhalter waere eine Detailseite zu einer erfundenen Kennung.
    """
    vorgaenge = [
        _daten(nachricht)
        for nachricht in _nachrichten()
        if _daten(nachricht)["type"] == "insurance_claim_followup"
    ]
    assert len(vorgaenge) == 2
    assert {"claim_id", "c-1", "c-2"}.isdisjoint(
        {str(wert) for daten in vorgaenge for wert in (*daten, *daten.values())}
    )
    assert {frozenset(daten) for daten in vorgaenge} == {
        frozenset({"type", "policy_id"}),
        frozenset({"type"}),
    }


def test_titel_und_rumpf_jeder_nachricht_bleiben_neutral() -> None:
    """Nicht nur der Rumpf: der Titel steht auf dem Sperrbildschirm zuerst.

    Deckt alle vier Typen ab - `_nachrichten` baut sie in einem Durchgang.
    """
    for nachricht in _nachrichten():
        _pruefe_neutral(str(nachricht["title"]))
        _pruefe_neutral(str(nachricht["body"]), zahlen=(14, 7, 2, 41, 33))


def test_ohne_geraet_entsteht_keine_nachricht() -> None:
    """Kein Token, keine Nachricht - und damit auch nichts zu markieren."""
    haushalt = "22222222-2222-4222-8222-222222222222"
    nachrichten = build_push_messages(
        [DeadlineAlert(household_id=haushalt, policy_id="p-1", days_left=3, stage_days=3)],
        [QuestionAlert(household_id=haushalt, policy_id="p-2")],
        [
            SpecialTerminationAlert(
                household_id=haushalt,
                notice_id="n-1",
                policy_id="p-3",
                days_left=0,
                stage_days=0,
            )
        ],
        {},
    )
    assert nachrichten == []


def test_jedes_sonderfenster_bleibt_einzeln() -> None:
    """Anders als die Rueckfrage wird das Fenster nicht je Haushalt
    zusammengefasst: jedes hat seinen eigenen Tag, und eine gemeinsame Zahl
    verschluckte die Dringlichkeit des knappsten."""
    haushalt = "33333333-3333-4333-8333-333333333333"
    nachrichten = build_push_messages(
        [],
        [],
        [
            SpecialTerminationAlert(
                household_id=haushalt,
                notice_id="n-1",
                policy_id="p-1",
                days_left=7,
                stage_days=7,
            ),
            SpecialTerminationAlert(
                household_id=haushalt,
                notice_id="n-2",
                policy_id="p-2",
                days_left=2,
                stage_days=2,
            ),
        ],
        {haushalt: ["ExponentPushToken[abc]"]},
    )
    assert len(nachrichten) == 2
    assert {nachricht["body"] for nachricht in nachrichten} == {
        "Ein Sonderkuendigungsrecht laeuft in 7 Tagen ab.",
        "Ein Sonderkuendigungsrecht laeuft in 2 Tagen ab.",
    }


# ---------------------------------------------------------------------------
# Die Staffel sofort/7/2
# ---------------------------------------------------------------------------


def test_stufe_sofort_greift_auch_bei_verstrichenem_handlungsdatum() -> None:
    """Der Unterschied zur ordentlichen Frist: dort bricht der Lauf bei einer
    negativen Tageszahl ab, hier ist die Stufe 0 genau dafuer da. Zwischen
    Handlungsdatum und rechtlichem Fristende liegen beim Sonderfenster nur
    wenige Tage - wer heute noch faxen kann, soll die Nachricht bekommen."""
    assert current_stage(-5, [0, 7, 2]) == 0
    assert current_stage(0, [0, 7, 2]) == 0
    assert current_stage(1, [0, 7, 2]) == 2
    assert current_stage(2, [0, 7, 2]) == 2
    assert current_stage(5, [0, 7, 2]) == 7
    assert current_stage(7, [0, 7, 2]) == 7
    # Ausserhalb der weitesten Stufe schweigt der Lauf: ein Fenster, das erst
    # in acht Tagen dringend wird, ist heute keine Nachricht wert.
    assert current_stage(8, [0, 7, 2]) is None


def test_jede_stufe_meldet_sich_einmal() -> None:
    """`notified_at` ist kein Schalter, sondern der Zeitpunkt der letzten
    Nachricht: die 7er-Stufe hat schon gesendet, die 2er noch nicht."""
    handlungsdatum = date(2026, 12, 1)
    stufe_sieben = handlungsdatum - timedelta(days=7)
    stufe_zwei = handlungsdatum - timedelta(days=2)
    gesendet_in_stufe_sieben = datetime(2026, 11, 24, 8, 45)

    assert is_new_stage(None, stufe_sieben) is True
    assert is_new_stage(gesendet_in_stufe_sieben, stufe_sieben) is False
    assert is_new_stage(gesendet_in_stufe_sieben, stufe_zwei) is True
