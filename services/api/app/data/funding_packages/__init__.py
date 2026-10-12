"""Mitgelieferte Foerderpakete (Import-Plan S1).

Aufbau wie `rule_packages/`: explizite Importe, literales Registry-Dict, kein
`pkgutil`-Scan. Ein Paket bleibt ein einfaches `dict` -- validiert wird erst an
der API-Grenze durch `FundingPackagePayload` (app/schemas/funding.py).

Unterschied zu Regelpaketen: jedes Programm traegt Herausgeber, Quelle,
Recherchestand und Frischeablauf. Ein Foerderprogramm ohne Quelle und
Ablaufdatum ist dasselbe Problem wie ein Angebot ohne "gueltig bis".

Der kuratierte Katalog aus `0001_schema.sql` (KfW 124/300, BayernLabo, L-Bank
Z15) bleibt davon unberuehrt -- er gehoert dem Code und ist fuer niemanden
schreibbar. Diese Pakete landen beim Import im Katalog des jeweiligen Haushalts.
"""

from typing import Any

from .bafa_heizung import PACKAGE as bafa_heizung_package
from .bayernlabo_eigenwohnraum import PACKAGE as bayernlabo_eigenwohnraum_package
from .kfw_jung_kauft_alt import PACKAGE as kfw_jung_kauft_alt_package
from .kfw_sanierung import PACKAGE as kfw_sanierung_package

FundingPackageData = dict[str, Any]

BUILTIN_PACKAGES: dict[str, FundingPackageData] = {
    "bayernlabo_eigenwohnraum": bayernlabo_eigenwohnraum_package,
    "kfw_sanierung": kfw_sanierung_package,
    "kfw_jung_kauft_alt": kfw_jung_kauft_alt_package,
    "bafa_heizung": bafa_heizung_package,
}


def get_package(package_id: str) -> FundingPackageData | None:
    return BUILTIN_PACKAGES.get(package_id)


def list_packages() -> list[FundingPackageData]:
    return list(BUILTIN_PACKAGES.values())
