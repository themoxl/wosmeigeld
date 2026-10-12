from typing import Any

from .baumarkt import PACKAGE as baumarkt_package
from .drogerie import PACKAGE as drogerie_package
from .energie import PACKAGE as energie_package
from .gastronomie import PACKAGE as gastronomie_package
from .mobilfunk import PACKAGE as mobilfunk_package
from .online_shopping import PACKAGE as online_shopping_package
from .streaming import PACKAGE as streaming_package
from .supermarkt import PACKAGE as supermarkt_package
from .tankstellen import PACKAGE as tankstellen_package
from .versicherungen import PACKAGE as versicherungen_package

# Ein Paket bleibt bewusst ein einfaches dict: die Pakete sind reine Daten und
# werden erst an der API-Grenze ueber `RulePackage`/`RulePackageMeta`
# validiert (siehe app/schemas/rules.py).
RulePackageData = dict[str, Any]

BUILTIN_PACKAGES: dict[str, RulePackageData] = {
    "supermarkt": supermarkt_package,
    "drogerie": drogerie_package,
    "energie": energie_package,
    "versicherungen": versicherungen_package,
    "online_shopping": online_shopping_package,
    "gastronomie": gastronomie_package,
    "mobilfunk": mobilfunk_package,
    "streaming": streaming_package,
    "tankstellen": tankstellen_package,
    "baumarkt": baumarkt_package,
}

def get_package(package_id: str) -> RulePackageData | None:
    return BUILTIN_PACKAGES.get(package_id)

def list_package_metas() -> list[RulePackageData]:
    return [
        {
            "id": pkg["id"],
            "name": pkg["name"],
            "description": pkg["description"],
            "icon": pkg["icon"],
            "rule_count": pkg.get("rule_count", len(pkg.get("rules", [])))
        }
        for pkg in BUILTIN_PACKAGES.values()
    ]
