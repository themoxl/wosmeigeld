"""Regelkatalog des Bedarfsabgleichs (docs/VERSICHERUNGEN-PLAN.md, 10.3, P6).

Diese Datei ist ein DATENPAKET, kein Rechenweg. Sie sagt, WANN eine Sparte
einschlaegig ist und WORAUF diese Aussage sich stuetzt; ausgewertet wird sie von
`app/services/insurance_needs_service.py`, getypt ist sie durch
`app/schemas/insurance.InsuranceRule` (dort als `Rule` exportiert). Kein
Sprachmodell entscheidet hier etwas und formuliert hier etwas: jeder Satz, den
der Nutzer im Register "Schutz" liest, steht als Zeichenkette in dieser Datei
und traegt seine Fundstelle daneben.

Vier Leitsaetze, in dieser Reihenfolge verbindlich:

1. **Regelbasiert und nachvollziehbar.** Jeder Befund nennt `rule_key`, die
   ausloesenden Angaben, den Begruendungssatz, die Quelle und deren Pruefdatum.
2. **Kein Schutzscore.** Keine Punkte, keine Note, kein Gesamtbalken. Der
   Katalog nennt einzelne begruendete Befunde - oder er schweigt.
3. **Keine Beratung, kein Vertrieb.** Kein Tarifvergleich, kein Wechsel, kein
   Anbietername, keine Preisaussage. Eine Regel sagt "hierfuer besteht kein
   Schutz" und nennt die Sparte, nicht den Verkaeufer.
4. **Fehlende Angabe schlaegt vermutete Luecke.** Fehlt ein Merkmal, wertet der
   Auswerter dreiwertig aus und meldet `unclear` samt Namen der fehlenden
   Angabe. Ein Befund ueber einen ungepflegten Bestand ist schaedlich.

## Schreibregel fuer `headline` und `rationale`

Dieselbe wie fuer die Zwecktexte des Spartenkatalogs (Plan 3.11), weil dieselben
Redaktionstests darueber laufen: keine Anrede, keine Wertung, keine Superlative,
keine Pflichtaussage ohne Fundstelle im selben Feld. Eine Wertung darf nur als
zugeschriebene Fremdaussage mit Quelle und Datum vorkommen ("Stiftung Warentest
setzt an, dass ..."), nie als eigene Empfehlung. Paragraphen stehen
ausgeschrieben ("Paragraph 823 Absatz 1 BGB"), wortgleich zum Seed.

## Vokabular

Bedingungsseite: `all`, `any` und ein Vergleich `{"fact": ..., <op>: ...}` mit
genau einem Operator aus `in`, `gt`, `lt`, `eq`, `is_null`. Die leere Bedingung
ist die Zeile "immer" aus 10.3. Die Merkmalsnamen sind die des Auswerters
(`FACT_LABELS` in `insurance_needs_service`); ein Name, den er nicht kennt,
erzeugt dort eine Rueckfrage und keine Luecke.

Zielseite: `target_metric` aus der geschlossenen Liste in 10.1, dazu `special`
fuer die beiden Ausschlussarten aus 10.3 ("Person setzt aus", "Police zaehlt
nicht als Erfuellung") und ihr Spiegelbild aus R24. Welche Zielfelder eine
Metrik verlangt und welche sie nicht tragen darf, prueft der Modellvalidator von
`InsuranceRule` beim Laden - eine Regel, die dagegen verstoesst, faellt aus und
erzeugt keinen Befund.

`depends_on_line_key` traegt die Anschlussbedingungen, die 10.3 in Prosa
schreibt: "R2 erfuellt", "R4 erfuellt", "Hausrat vorhanden". Alle drei meinen
dasselbe - die Anschlussregel prueft eine Hoehe und setzt deshalb die Police
voraus, deren Fehlen die Basisregel bereits meldet. `required_facts` traegt die
Pflichtangaben, die 10.3 als "... is not null" schreibt: fehlt eine, lautet das
Ergebnis `unclear` und nennt sie, statt die Regel schweigend fallen zu lassen.

## Nicht uebernommene Zeilen aus 10.3

Eine Regel, die nie auswerten kann, ist schlimmer als eine fehlende - eine
fehlende meldet keine Luecke, das ist die sichere Richtung (10.3).

* **R5** (Krankentagegeld, Berufshaftpflicht): `daily_sickness_allowance` und
  `professional_liability` gehoeren zu Katalogwelle 3 und sind nicht geseedet.
  Pruefung 5 des Seed-Tests verlangt einen existierenden `line_key`; die
  Merkmale (`employment_status`, `side_business`) waeren da, die Sparten nicht.
* **R14a** (Tierhalterhaftpflicht neben Katze oder Kleintier): das geschlossene
  Operatorvokabular kennt keine Verneinung. "Nur Katze oder Kleintier" ist ohne
  `not` nicht formulierbar; `{"fact": "pets", "in": ["cat", "small_animal"]}`
  traefe auch den Haushalt mit Hund UND Katze und behauptete dort das Gegenteil
  des Richtigen - ein schaedlicher Befund, und ausgerechnet auf Stufe S4.
* **R19** (Photovoltaikanlage): das Merkmal `has_photovoltaic` gibt es, eine
  gepruefte Fundstelle fuer die Bedarfsaussage nicht - die Seite, die der
  Spartenkatalog dafuer nennt, antwortet mit 404. Ohne Fundstelle waere die
  Regel eine Meinung; mit einer geprueften Quelle ist sie nachtragbar.

## Quellen

Jede Zeile traegt `source_url` und `source_checked_on`. Alle Fundstellen sind am
15.08.2026 einzeln abgerufen und auf die Aussage geprueft worden, die hier
zitiert wird. Die Verweise des Spartenkatalogs (`insurance_lines.source_url`)
sind dabei ausdruecklich NICHT uebernommen worden: ein Teil davon geht ins Leere.
Ohne Quelle steht allein R7b - eine Regel, die andere Regeln aussetzen laesst,
trifft keine Bedarfsaussage, fuer die es eine Fundstelle geben koennte.
"""

from datetime import date
from typing import Any, Final

from app.schemas.insurance import Rule

# ---------------------------------------------------------------------------
# Bedingungsbau
# ---------------------------------------------------------------------------
# Die Helfer erzeugen ausschliesslich dicts - keine Verzweigung, keine Rechnung,
# keine Auswertung. Sie stehen hier, weil verschachtelte dict-Literale ueber
# neunundzwanzig Regeln weder les- noch pruefbar bleiben.

Condition = dict[str, Any]


def _all(*bedingungen: Condition) -> Condition:
    return {"all": list(bedingungen)}


def _any(*bedingungen: Condition) -> Condition:
    return {"any": list(bedingungen)}


def _in(merkmal: str, werte: tuple[str, ...]) -> Condition:
    return {"fact": merkmal, "in": list(werte)}


def _eq(merkmal: str, wert: str | int | bool) -> Condition:
    return {"fact": merkmal, "eq": wert}


def _gt(merkmal: str, wert: int) -> Condition:
    return {"fact": merkmal, "gt": wert}


def _lt(merkmal: str, wert: int) -> Condition:
    return {"fact": merkmal, "lt": wert}


# "immer" aus 10.3. Der Auswerter liest die leere Bedingung als wahr; sie ist
# der einzige Weg, "ohne Vorbedingung" zu schreiben, ohne ein Merkmal zu
# erfinden, das es nicht gibt.
IMMER: Final[Condition] = {}

# Erwerbsstatus, bei dem der Ausfall des Erwerbseinkommens ueberhaupt zaehlt.
_ERWERBSTAETIG: Final[tuple[str, ...]] = (
    "employed",
    "civil_servant",
    "apprentice",
    "self_employed",
)

# ---------------------------------------------------------------------------
# Fundstellen
# ---------------------------------------------------------------------------
# Pruefdatum aller Zeilen dieses Katalogs. Eine Konstante statt neunundzwanzig
# Wiederholungen: geprueft wurde an einem Tag, ein spaeterer Nachtrag traegt sein
# eigenes Datum.
GEPRUEFT: Final[date] = date(2026, 8, 15)

_BGB_823 = "https://www.gesetze-im-internet.de/bgb/__823.html"
_BGB_828 = "https://www.gesetze-im-internet.de/bgb/__828.html"
_BGB_833 = "https://www.gesetze-im-internet.de/bgb/__833.html"
_BGB_836 = "https://www.gesetze-im-internet.de/bgb/__836.html"
_BGB_1967 = "https://www.gesetze-im-internet.de/bgb/__1967.html"
_BJAGDG_17 = "https://www.gesetze-im-internet.de/bjagdg/__17.html"
_BBHV_46 = "https://www.gesetze-im-internet.de/bbhv/__46.html"
_LUFTVG_43 = "https://www.gesetze-im-internet.de/luftvg/__43.html"
_PFLVG_1 = "https://www.gesetze-im-internet.de/pflvg/__1.html"
_SGB_V_55 = "https://www.gesetze-im-internet.de/sgb_5/__55.html"
_SGB_XI_23 = "https://www.gesetze-im-internet.de/sgb_11/__23.html"
_VVG_165 = "https://www.gesetze-im-internet.de/vvg_2008/__165.html"
_VVG_193 = "https://www.gesetze-im-internet.de/vvg_2008/__193.html"
_WHG_89 = "https://www.gesetze-im-internet.de/whg_2009/__89.html"
_ZPO_91 = "https://www.gesetze-im-internet.de/zpo/__91.html"

_VZ = "https://www.verbraucherzentrale.de/wissen/geld-versicherungen/weitere-versicherungen"
_VZ_AUSLAND = f"{_VZ}/auslandskrankenversicherung-122288"
_VZ_BU = f"{_VZ}/berufsunfaehigkeit-wie-sie-sich-gegen-verlust-des-einkommens-absichern-13931"
_VZ_HAUSRAT = f"{_VZ}/hausratversicherung-auf-den-wert-der-einrichtung-kommts-an-13889"
_VZ_HOCHWASSER = f"{_VZ}/hochwasserschaeden-am-haus-das-sollten-sie-jetzt-tun-62827"
_VZ_KFZ = f"{_VZ}/kfzversicherung-pflicht-fuer-alle-halterinnen-von-kraftfahrzeugen-13890"
_VZ_SCHULENDE = f"{_VZ}/diese-versicherungen-sind-zum-ende-der-schulzeit-wichtig-12883"
_VZ_TIERE = f"{_VZ}/haftpflichtversicherung-fuer-haustiere-33442"
_WARENTEST_HAFTPFLICHT = "https://www.test.de/thema/haftpflichtversicherungen/"


# ---------------------------------------------------------------------------
# Der Katalog
# ---------------------------------------------------------------------------

INSURANCE_RULES: Final[tuple[Rule, ...]] = (
    # ---------------------------------------------------------------- Gesundheit
    Rule(
        rule_key="R1",
        risk_field="health",
        line_key="health_statutory",
        alternative_line_keys=("health_private",),
        priority="S0",
        scope="person",
        condition=IMMER,
        target_metric="policy_exists",
        headline="Krankenversicherung nicht erfasst",
        rationale=(
            "Personen mit Wohnsitz im Inland sind nach Paragraph 193 Absatz 3 VVG "
            "verpflichtet, eine Krankheitskostenversicherung abzuschliessen und "
            "aufrechtzuerhalten. Erfasst ist fuer diese Person weder eine gesetzliche "
            "noch eine private Krankenversicherung."
        ),
        source_url=_VVG_193,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R1a",
        risk_field="health",
        line_key="long_term_care_mandatory",
        priority="S0",
        scope="person",
        condition=_eq("health_coverage", "pkv"),
        target_metric="policy_exists",
        headline="Private Pflegepflichtversicherung nicht erfasst",
        rationale=(
            "Wer privat krankenversichert ist, ist nach Paragraph 23 Absatz 1 SGB XI "
            "verpflichtet, bei demselben Unternehmen einen Vertrag zur Absicherung des "
            "Risikos der Pflegebeduerftigkeit abzuschliessen und aufrechtzuerhalten. Als "
            "Versichertenstatus ist privat erfasst, eine private Pflegepflicht"
            "versicherung dagegen nicht."
        ),
        source_url=_SGB_XI_23,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R16",
        risk_field="health",
        line_key="travel_health",
        priority="S1",
        condition=_in("travel_frequency", ("occasional", "frequent")),
        target_metric="policy_exists",
        headline="Auslandsreise-Krankenversicherung nicht erfasst",
        rationale=(
            "Bei einer Krankenhausbehandlung ausserhalb der Europaeischen Union zahlen "
            "die gesetzlichen Krankenkassen nach Angaben der Verbraucherzentrale nicht; "
            "auch innerhalb Europas werden nicht alle Kosten uebernommen (Stand "
            "26.05.2026). Als Reisehaeufigkeit sind gelegentliche oder haeufige "
            "Auslandsreisen erfasst, eine Auslandsreise-Krankenversicherung nicht."
        ),
        source_url=_VZ_AUSLAND,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R22",
        risk_field="health",
        line_key="health_private",
        priority="S0",
        scope="person",
        condition=_eq("health_coverage", "pkv_beihilfe"),
        depends_on_line_key="health_private",
        required_facts=("beihilfe_rate_bp",),
        target_metric="benefit_rate_formula",
        target_formula="beihilfe_complement",
        headline="Erstattungssatz deckt den offen bleibenden Anteil nicht",
        rationale=(
            "Der Beihilfebemessungssatz betraegt nach Paragraph 46 Absatz 2 BBhV je nach "
            "Personengruppe 50, 70 oder 80 Prozent der beihilfefaehigen Aufwendungen; der "
            "verbleibende Anteil wird ueblicherweise ueber einen Restkostentarif der "
            "privaten Krankenversicherung getragen. Der erfasste Erstattungssatz liegt "
            "unter dem Anteil, der nach dem erfassten Bemessungssatz offen bleibt."
        ),
        source_url=_BBHV_46,
        source_checked_on=GEPRUEFT,
        note=(
            "Der Restkostentarif ist das Spiegelbild des Bemessungssatzes; die Formel "
            "rechnet in Basispunkten (10000 minus Bemessungssatz)."
        ),
    ),
    Rule(
        rule_key="R23",
        risk_field="health",
        line_key="dental_supplementary",
        priority="S3",
        scope="person",
        condition=_eq("health_coverage", "gkv"),
        target_metric="policy_exists",
        headline="Zahnzusatzversicherung nicht erfasst",
        rationale=(
            "Bei Zahnersatz besteht in der gesetzlichen Krankenversicherung nach "
            "Paragraph 55 Absatz 1 SGB V ein Anspruch auf befundbezogene Festzuschuesse; "
            "der darueber hinausgehende Teil der Rechnung bleibt offen. Als "
            "Versichertenstatus ist gesetzlich erfasst, eine Zahnzusatzversicherung "
            "dagegen nicht."
        ),
        source_url=_SGB_V_55,
        source_checked_on=GEPRUEFT,
    ),
    # ------------------------------------------------ Einkommen und Hinterbliebene
    Rule(
        rule_key="R4",
        risk_field="income",
        line_key="occupational_disability",
        priority="S1",
        scope="person",
        condition=_in("employment_status", _ERWERBSTAETIG),
        target_metric="policy_exists",
        headline="Berufsunfaehigkeitsversicherung nicht erfasst",
        rationale=(
            "Die gesetzliche Rente wegen Erwerbsminderung knuepft an die "
            "Leistungsfaehigkeit auf dem allgemeinen Arbeitsmarkt an, nicht an den "
            "zuletzt ausgeuebten Beruf; die Verbraucherzentrale weist darauf hin, dass "
            "diese Rente im Durchschnitt unter dem Grundsicherungsniveau liegt (Stand "
            "02.01.2025). Diese Person erzielt Erwerbseinkommen; eine "
            "Berufsunfaehigkeitsversicherung ist nicht erfasst."
        ),
        source_url=_VZ_BU,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R4a",
        risk_field="income",
        line_key="occupational_disability",
        priority="S2",
        scope="person",
        condition=_in("employment_status", _ERWERBSTAETIG),
        depends_on_line_key="occupational_disability",
        required_facts=("net_income_minor",),
        target_metric="sum_insured_formula",
        target_formula="net_income_annual_x5",
        headline="Vereinbarte Leistung unter dem Vergleichswert",
        rationale=(
            "Als Richtwert fuer die abzusichernde Rente nennt die Verbraucherzentrale "
            "rund 80 Prozent des heutigen Nettoeinkommens, weil die laufenden Ausgaben "
            "nach Eintritt der Berufsunfaehigkeit weiterlaufen (Stand 02.01.2025). "
            "Verglichen wird die erfasste Leistung mit fuenf Jahresnettoeinkommen dieser "
            "Person; der erfasste Wert liegt darunter."
        ),
        source_url=_VZ_BU,
        source_checked_on=GEPRUEFT,
        note=(
            "Anschlussregel: sie prueft die Hoehe und setzt deshalb die Police voraus, "
            "deren Fehlen R4 bereits meldet. Ohne depends_on_line_key stuende dieselbe "
            "Luecke zweimal in der Liste."
        ),
    ),
    Rule(
        rule_key="R7",
        risk_field="income",
        line_key="term_life",
        priority="S1",
        condition=_any(_gt("children_count", 0), _gt("open_debt_minor", 0)),
        required_facts=("open_debt_minor",),
        target_metric="sum_insured_formula",
        target_formula="open_debt",
        headline="Todesfallsumme unter der offenen Darlehenssumme",
        rationale=(
            "Der Erbe haftet nach Paragraph 1967 Absatz 1 BGB fuer die "
            "Nachlassverbindlichkeiten; offene Darlehen gehen damit auf die "
            "Hinterbliebenen ueber. Im Haushalt sind Kinder oder offene Darlehen "
            "erfasst. Verglichen wird die erfasste Todesfallsumme mit der Summe der "
            "offenen Darlehen aus dem Vermoegensmodul."
        ),
        source_url=_BGB_1967,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R7b",
        risk_field="other",
        line_key=None,
        priority=None,
        scope="person",
        condition=_eq("is_deceased", True),
        special="person_suspends",
        headline="Personenbezogene Regeln setzen aus",
        rationale=(
            "Fuer diese Person ist ein Sterbedatum erfasst. Personenbezogene Regeln - "
            "Krankenversicherung, Berufsunfaehigkeit, Hinterbliebenenschutz - werten "
            "deshalb nicht mehr aus und erzeugen kein Ergebnis statt einer Luecke."
        ),
        note=(
            "Ausschlussart 1 aus 10.3 und die einzige Zeile ohne Fundstelle: sie "
            "behauptet nichts ueber Versicherungen, sie nimmt eine Person aus den "
            "personenbezogenen Regeln heraus."
        ),
    ),
    # --------------------------------------------------------------------- Haftung
    Rule(
        rule_key="R2",
        risk_field="liability",
        line_key="private_liability",
        priority="S1",
        condition=IMMER,
        target_metric="policy_exists",
        headline="Privathaftpflichtversicherung nicht erfasst",
        rationale=(
            "Wer einem anderen widerrechtlich einen Schaden zufuegt, ist nach Paragraph "
            "823 Absatz 1 BGB zum Ersatz verpflichtet; diese Haftung ist der Hoehe nach "
            "nicht begrenzt und reicht in kuenftiges Einkommen hinein. Im Haushalt ist "
            "keine Privathaftpflichtversicherung erfasst."
        ),
        source_url=_BGB_823,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R2a",
        risk_field="liability",
        line_key="private_liability",
        priority="S1",
        condition=IMMER,
        depends_on_line_key="private_liability",
        target_metric="sum_insured_min",
        target_value=1_000_000_000,
        warn_value=500_000_000,
        headline="Deckungssumme unter 10 Millionen Euro",
        rationale=(
            "Fuer den Grundschutz einer Privathaftpflichtversicherung setzt Stiftung "
            "Warentest an, dass der Versicherer mindestens bis zu 10 Millionen Euro "
            "Entschaedigung pauschal fuer Personen- und Sachschaeden bietet (Stand "
            "15.08.2026). Die erfasste Deckungssumme liegt darunter."
        ),
        source_url=_WARENTEST_HAFTPFLICHT,
        source_checked_on=GEPRUEFT,
        note=(
            "warn_value ist die Anzeigeabstufung aus 10.3 und steht bewusst nicht im "
            "Begruendungstext: gepruefte Fremdaussage ist die Zehn-Millionen-Schwelle, "
            "die fuenf Millionen sind eine Vorgabe des Plans."
        ),
    ),
    Rule(
        rule_key="R3",
        risk_field="liability",
        line_key="private_liability",
        priority="S2",
        condition=_all(_gt("children_count", 0), _lt("youngest_person_age_years", 7)),
        target_metric="coverage_included",
        target_coverage_key="tortless_children",
        headline="Baustein fuer deliktunfaehige Kinder nicht erfasst",
        rationale=(
            "Wer das siebente Lebensjahr nicht vollendet hat, ist nach Paragraph 828 "
            "Absatz 1 BGB fuer einen Schaden nicht verantwortlich; die Geschaedigten "
            "gehen dann leer aus, solange den Aufsichtspflichtigen kein Vorwurf trifft. "
            "Manche Tarife der Privathaftpflichtversicherung ersetzen solche Schaeden "
            "trotzdem, wenn der Baustein vereinbart ist. Im Haushalt lebt ein Kind unter "
            "sieben Jahren; der Baustein ist nicht erfasst."
        ),
        source_url=_BGB_828,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R3b",
        risk_field="liability",
        line_key="private_liability",
        priority="S2",
        scope="person",
        condition=_any(_eq("education_ended", True), _eq("moved_out", True)),
        target_metric="policy_exists",
        headline="Eigene Privathaftpflichtversicherung nach Ende der Mitversicherung",
        rationale=(
            "Mitversichert sind erwachsene Kinder in der Privathaftpflichtversicherung "
            "der Eltern nach den meisten Tarifen waehrend der Schulzeit und der "
            "unmittelbar anschliessenden Erstausbildung; mit deren Abschluss oder mit "
            "dem Erreichen einer Altersgrenze wird nach Angaben der Verbraucherzentrale "
            "ein eigener Vertrag noetig (Stand 17.02.2026). Fuer diese Person sind das "
            "Ende der Erstausbildung oder ein eigener Hausstand erfasst, eine eigene "
            "Police dagegen nicht."
        ),
        source_url=_VZ_SCHULENDE,
        source_checked_on=GEPRUEFT,
        note=(
            "10.3 nennt hier den Baustein 'mitversicherte_kinder'. Einen solchen "
            "coverage_key gibt es im Katalog nicht, und gemeint ist auch nicht ein "
            "Baustein am Vertrag der Eltern, sondern die eigene Police dieser Person - "
            "deshalb policy_exists mit scope='person'."
        ),
    ),
    Rule(
        rule_key="R11a",
        risk_field="liability",
        line_key="builder_liability",
        additional_line_keys=("construction_works",),
        priority="S1",
        condition=_eq("construction_active", True),
        target_metric="policy_exists",
        headline="Bauvorhaben ohne Bauherrenhaftpflicht- und Bauleistungsversicherung",
        rationale=(
            "Wer die Verkehrssicherungspflicht auf der eigenen Baustelle verletzt, ist "
            "nach Paragraph 823 Absatz 1 BGB zum Ersatz des daraus entstehenden Schadens "
            "verpflichtet; die Pflicht bleibt beim Bauherrn, auch wenn Betriebe "
            "beauftragt sind. Die Gefahr fuer das Bauwerk traegt nach Paragraph 644 "
            "Absatz 1 Satz 1 BGB bis zur Abnahme der Unternehmer, danach der Bauherr. Im "
            "Haushalt ist ein laufendes Bauvorhaben erfasst; Bauherrenhaftpflicht- und "
            "Bauleistungsversicherung fehlen."
        ),
        source_url=_BGB_823,
        source_checked_on=GEPRUEFT,
        note=(
            "Zwei Sparten, die BEIDE fehlen koennen - deshalb additional_line_keys und "
            "nicht alternative_line_keys: eine vorhandene Bauherrenhaftpflicht darf die "
            "fehlende Bauleistungsversicherung nicht als erledigt melden."
        ),
    ),
    Rule(
        rule_key="R12",
        risk_field="liability",
        line_key="landowner_liability",
        priority="S1",
        condition=_eq("housing_form", "owner_rented_out"),
        target_metric="policy_exists",
        headline="Haus- und Grundbesitzerhaftpflichtversicherung nicht erfasst",
        rationale=(
            "Beim Einsturz eines Gebaeudes oder der Abloesung von Gebaeudeteilen ist "
            "nach Paragraph 836 Absatz 1 BGB der Besitzer des Grundstuecks zum Ersatz "
            "verpflichtet, wenn die Ursache in fehlerhafter Errichtung oder mangelhafter "
            "Unterhaltung liegt. Als Wohnform ist vermietetes Eigentum erfasst; die "
            "Privathaftpflichtversicherung schliesst vermietete Objekte nach ihren "
            "Bedingungen ueblicherweise aus."
        ),
        source_url=_BGB_836,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R13",
        risk_field="liability",
        line_key="water_damage_liability",
        priority="S1",
        condition=_eq("has_oil_tank", True),
        target_metric="policy_exists",
        headline="Gewaesserschadenhaftpflichtversicherung nicht erfasst",
        rationale=(
            "Gelangen aus einer Anlage Stoffe in ein Gewaesser und veraendern dessen "
            "Beschaffenheit nachteilig, ist nach Paragraph 89 Absatz 2 WHG der Betreiber "
            "der Anlage zum Ersatz des Schadens verpflichtet, und zwar ohne Verschulden. "
            "Im Haushalt ist ein Oeltank erfasst, eine Gewaesserschaden"
            "haftpflichtversicherung nicht."
        ),
        source_url=_WHG_89,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R14",
        risk_field="liability",
        line_key="animal_liability",
        priority="S1",
        condition=_in("pets", ("dog", "horse")),
        target_metric="policy_exists",
        headline="Tierhalterhaftpflichtversicherung nicht erfasst",
        rationale=(
            "Wer ein Tier haelt, ist nach Paragraph 833 Satz 1 BGB zum Ersatz des durch "
            "das Tier verursachten Schadens verpflichtet, und zwar ohne Verschulden. Die "
            "Verbraucherzentrale weist darauf hin, dass Hunde und Pferde in der "
            "Privathaftpflichtversicherung nicht mitversichert sind (Stand 22.01.2025). "
            "Im Haushalt ist ein Hund oder ein Pferd erfasst, eine "
            "Tierhalterhaftpflichtversicherung nicht."
        ),
        source_url=_VZ_TIERE,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R18",
        risk_field="liability",
        line_key="drone_liability",
        priority="S0",
        condition=_eq("has_drone", True),
        target_metric="policy_exists",
        headline="Drohnenhaftpflichtversicherung nicht erfasst",
        rationale=(
            "Der Halter eines Luftfahrzeugs ist nach Paragraph 43 Absatz 2 LuftVG "
            "verpflichtet, zur Deckung seiner Haftung eine Haftpflichtversicherung zu "
            "unterhalten; unbemannte Fluggeraete zaehlen nach Paragraph 1 Absatz 2 "
            "LuftVG zu den Luftfahrzeugen. Im Haushalt ist eine Drohne erfasst, eine "
            "Halterhaftpflichtversicherung dafuer nicht."
        ),
        source_url=_LUFTVG_43,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R20",
        risk_field="liability",
        line_key="hunting_liability",
        priority="S0",
        condition=_eq("has_hunting_license", True),
        target_metric="policy_exists",
        headline="Jagdhaftpflichtversicherung nicht erfasst",
        rationale=(
            "Der Jagdschein ist nach Paragraph 17 Absatz 1 Nummer 4 BJagdG zu versagen, "
            "solange keine Jagdhaftpflichtversicherung ueber mindestens 500.000 Euro "
            "fuer Personenschaeden und 50.000 Euro fuer Sachschaeden besteht. Im "
            "Haushalt ist ein Jagdschein erfasst, eine Jagdhaftpflichtversicherung nicht."
        ),
        source_url=_BJAGDG_17,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R21",
        risk_field="liability",
        line_key="boat_liability",
        priority="S0",
        condition=_eq("has_boat", True),
        target_metric="policy_exists",
        headline="Bootshaftpflichtversicherung nicht erfasst",
        rationale=(
            "Die Haftung fuer Schaeden, die mit einem Boot verursacht werden, folgt "
            "Paragraph 823 Absatz 1 BGB und ist der Hoehe nach nicht begrenzt; "
            "Wassersportfahrzeuge sind in der Privathaftpflichtversicherung nach ihren "
            "Bedingungen ueblicherweise nicht eingeschlossen. Im Haushalt ist ein Boot "
            "erfasst, eine Bootshaftpflichtversicherung nicht."
        ),
        source_url=_BGB_823,
        source_checked_on=GEPRUEFT,
    ),
    # -------------------------------------------------------------------- Eigentum
    Rule(
        rule_key="R10",
        risk_field="property",
        line_key="building",
        priority="S1",
        condition=_in("housing_form", ("owner_occupied", "owner_rented_out")),
        target_metric="policy_exists",
        headline="Wohngebaeudeversicherung nicht erfasst",
        rationale=(
            "Schaeden am Gebaeude traegt bei Wohneigentum die Eigentuemerseite selbst; "
            "die Verbraucherzentrale ordnet die Regulierung solcher Schaeden der "
            "Wohngebaeudeversicherung zu (Stand 14.08.2026). Als Wohnform ist "
            "Wohneigentum erfasst, eine Wohngebaeudeversicherung nicht."
        ),
        source_url=_VZ_HOCHWASSER,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R11",
        risk_field="property",
        line_key="elemental",
        priority="S1",
        condition=_in("housing_form", ("owner_occupied", "owner_rented_out")),
        target_metric="coverage_included",
        target_coverage_key="elemental",
        headline="Elementarschutz nicht erfasst",
        rationale=(
            "Schaeden am Haus durch Ueberschwemmung reguliert die Wohngebaeude"
            "versicherung nach Angaben der Verbraucherzentrale nur, wenn zusaetzlich "
            "eine Elementarschadenversicherung abgeschlossen ist; fuer Schaeden am "
            "Hausrat gilt derselbe Zusatzbaustein (Stand 14.08.2026). Als Wohnform ist "
            "Wohneigentum erfasst; weder ein Elementarbaustein noch eine eigenstaendige "
            "Elementarpolice sind vorhanden."
        ),
        source_url=_VZ_HOCHWASSER,
        source_checked_on=GEPRUEFT,
        note=(
            "Baustein oder eigenstaendige Police sind nach Plan 3.9 derselbe "
            "Sachverhalt; Zielsparte und Bausteinschluessel heissen deshalb gleich."
        ),
    ),
    Rule(
        rule_key="R15",
        risk_field="property",
        line_key="household_contents",
        priority="S2",
        condition=_gt("contents_value_minor", 0),
        required_facts=("liquid_reserve_minor",),
        target_metric="policy_exists",
        headline="Hausratversicherung nicht erfasst",
        rationale=(
            "Nach einem Totalschaden waere der Hausrat aus eigenen Mitteln zu ersetzen. "
            "Als Orientierung fuer die Versicherungssumme nennt die Verbraucherzentrale "
            "meist 650 Euro je Quadratmeter Wohnflaeche (Stand 10.11.2025). Im Haushalt "
            "ist ein Hausratwert erfasst, eine Hausratversicherung nicht."
        ),
        source_url=_VZ_HAUSRAT,
        source_checked_on=GEPRUEFT,
        note=(
            "10.3 vergleicht hier Hausratwert und Ruecklage. Ein Vergleich zweier "
            "Merkmale ist im geschlossenen Vokabular nicht ausdrueckbar (nur Merkmal "
            "gegen festen Wert), und eine erfundene Schwelle waere schlimmer als der "
            "Verzicht. Die Ruecklage steht deshalb als Pflichtangabe: fehlt sie, lautet "
            "das Ergebnis 'Angabe fehlt' - wie in Referenzprofil A."
        ),
    ),
    Rule(
        rule_key="R15a",
        risk_field="property",
        line_key="household_contents",
        priority="S1",
        condition=IMMER,
        depends_on_line_key="household_contents",
        required_facts=("living_area_sqm",),
        target_metric="sum_insured_formula",
        target_formula="contents_value_per_sqm",
        headline="Versicherungssumme unter dem Orientierungswert",
        rationale=(
            "Als Mindestversicherungssumme nennt die Verbraucherzentrale meist 650 Euro "
            "je Quadratmeter Wohnflaeche (Stand 10.11.2025). Die erfasste "
            "Versicherungssumme liegt fuer die erfasste Wohnflaeche darunter; bei "
            "Unterversicherung wird die Entschaedigung nach den vereinbarten Bedingungen "
            "anteilig gekuerzt."
        ),
        source_url=_VZ_HAUSRAT,
        source_checked_on=GEPRUEFT,
    ),
    # ----------------------------------------------------------------- Mobilitaet
    Rule(
        rule_key="R8",
        risk_field="mobility",
        line_key="motor_liability",
        priority="S0",
        condition=_gt("vehicle_count", 0),
        target_metric="policy_exists",
        headline="Kfz-Haftpflichtversicherung nicht erfasst",
        rationale=(
            "Der Halter eines Fahrzeugs mit regelmaessigem Standort im Inland ist nach "
            "Paragraph 1 PflVG verpflichtet, eine Haftpflichtversicherung abzuschliessen "
            "und aufrechtzuerhalten. Im Haushalt ist mindestens ein Fahrzeug erfasst, "
            "eine Kfz-Haftpflichtversicherung nicht."
        ),
        source_url=_PFLVG_1,
        source_checked_on=GEPRUEFT,
    ),
    Rule(
        rule_key="R8a",
        risk_field="mobility",
        line_key="motor_liability",
        priority="S0",
        condition=_gt("vehicle_count", 0),
        target_metric="policy_exists",
        special="policy_not_counted",
        policy_status_filter=("dormant",),
        headline="Ruhende Kfz-Police erfuellt die Pflicht nicht",
        rationale=(
            "Eine ruhend gestellte Kfz-Versicherung gilt nach den Bedingungen "
            "ueblicherweise nur fuer das abgemeldete Fahrzeug waehrend der Abmeldung. "
            "Die Pflicht nach Paragraph 1 PflVG trifft den Halter eines Fahrzeugs mit "
            "regelmaessigem Standort im Inland; eine ruhende Police zaehlt im Abgleich "
            "deshalb nicht als vorhandener Schutz."
        ),
        source_url=_PFLVG_1,
        source_checked_on=GEPRUEFT,
        note="Ausschlussart 2 aus 10.3: die Police zaehlt nicht als Erfuellung.",
    ),
    Rule(
        rule_key="R9a",
        risk_field="mobility",
        line_key="motor_comprehensive",
        priority="S4",
        condition=_all(_gt("vehicle_count", 0), _gt("vehicle_max_age_years", 10)),
        target_metric="not_needed",
        headline="Vollkaskodeckung bei einem aelteren Fahrzeug",
        rationale=(
            "Die Verbraucherzentrale nennt die Vollkaskoversicherung fuer teure Neuwagen "
            "sowie fuer finanzierte und geleaste Fahrzeuge und verweist fuer aeltere "
            "Fahrzeuge auf die Teilkaskoversicherung (Stand 04.11.2025). Erfasst sind "
            "ein Fahrzeug mit einem Alter ueber zehn Jahren und eine Vollkaskodeckung."
        ),
        source_url=_VZ_KFZ,
        source_checked_on=GEPRUEFT,
        note=(
            "Stufe S4: neutral und eingeklappt, nie als Kuendigungsaufforderung (3.10, "
            "17.3). Die Vollkasko als Baustein 'full_casco' ist nach 3.9 derselbe "
            "Sachverhalt wie die eigenstaendige Police."
        ),
    ),
    # ---------------------------------------------------------------------- Recht
    Rule(
        rule_key="R17",
        risk_field="legal",
        line_key="legal_expenses",
        priority="S2",
        condition=IMMER,
        target_metric="policy_exists",
        headline="Rechtsschutzversicherung nicht erfasst",
        rationale=(
            "Im Zivilprozess hat nach Paragraph 91 Absatz 1 Satz 1 ZPO die unterliegende "
            "Partei die Kosten des Rechtsstreits zu tragen, einschliesslich der Kosten "
            "der Gegenseite. Eine Rechtsschutzversicherung uebernimmt diese Kosten im "
            "Rahmen der vereinbarten Bausteine; im Haushalt ist keine erfasst."
        ),
        source_url=_ZPO_91,
        source_checked_on=GEPRUEFT,
    ),
    # -------------------------------------------------------------------- Vorsorge
    Rule(
        rule_key="R24",
        risk_field="retirement",
        line_key="pension_private",
        priority=None,
        condition=IMMER,
        special="policy_counts",
        policy_status_filter=("paid_up",),
        headline="Beitragsfrei gestellte Vorsorgepolice zaehlt als vorhanden",
        rationale=(
            "Eine Versicherung kann nach Paragraph 165 Absatz 1 VVG in eine "
            "praemienfreie Versicherung umgewandelt werden; der Vertrag besteht dann mit "
            "verringerter Leistung fort. Beitragsfrei gestellte Policen zaehlen im "
            "Abgleich deshalb als vorhanden; massgeblich ist die erfasste beitragsfreie "
            "Leistung."
        ),
        source_url=_VVG_165,
        source_checked_on=GEPRUEFT,
        note=(
            "Das Spiegelbild der Ausschlussarten: R24 meldet keine Luecke, sondern "
            "haelt fest, dass ein beitragsfrei gestellter Vertrag Schutz ist. Eine "
            "Vorsorgeluecke waere ohne Rentenauskunft nicht begruendbar und steht "
            "deshalb in keiner Regel."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Zugriff
# ---------------------------------------------------------------------------

RULES_BY_KEY: Final[dict[str, Rule]] = {regel.rule_key: regel for regel in INSURANCE_RULES}


def line_keys_used() -> frozenset[str]:
    """Alle Spartenschluessel des Katalogs - Pruefung 5 des Seed-Tests."""
    schluessel: set[str] = set()
    for regel in INSURANCE_RULES:
        if regel.line_key is not None:
            schluessel.add(regel.line_key)
        schluessel.update(regel.alternative_line_keys)
        schluessel.update(regel.additional_line_keys)
        if regel.depends_on_line_key is not None:
            schluessel.add(regel.depends_on_line_key)
    return frozenset(schluessel)


def coverage_keys_used() -> frozenset[str]:
    return frozenset(
        regel.target_coverage_key
        for regel in INSURANCE_RULES
        if regel.target_coverage_key is not None
    )


def facts_used() -> frozenset[str]:
    """Alle Merkmale, die der Auswerter fuer diesen Katalog liefern muss."""
    return frozenset(name for regel in INSURANCE_RULES for name in regel.facts)
