"""Grenztest des KI-Kerns: app/llm importiert nie app.api, app.services oder FastAPI.

Der Kern wird vom Worker direkt geladen (PYTHONPATH). Zoege er die
API-Schicht oder FastAPI mit, haenge jeder Batch-Lauf an Routen, Abhaengigkeiten
und Request-Kontext - genau die Kopplung, die die frueheren Worker-Kopien
vermeiden sollten. Zwei Pruefungen:

1. statisch: kein Import-Statement in app/llm nennt ein verbotenes Paket;
2. zur Laufzeit: ein frischer Interpreter, der nur `app.llm` laedt, hat danach
   weder fastapi noch app.api noch app.services in sys.modules (faengt auch
   Umwege ueber app.core/app.schemas ab).
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[1]
_CORE = _API_ROOT / "app" / "llm"
_FORBIDDEN = ("app.api", "app.services", "fastapi")


def _is_forbidden(module: str) -> bool:
    return any(module == name or module.startswith(name + ".") for name in _FORBIDDEN)


def _imports(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
            # `from app import services` ist derselbe Weg in anderer Schreibweise.
            found.extend((node.lineno, f"{node.module}.{alias.name}") for alias in node.names)
    return found


def test_core_files_exist() -> None:
    names = {path.name for path in _CORE.glob("*.py")}
    assert {"window.py", "budget.py", "measurements.py", "gateway.py", "reasons.py"} <= names


def test_no_core_module_imports_the_api_layer() -> None:
    offenders: list[str] = []
    for path in sorted(_CORE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, module in _imports(tree):
            if _is_forbidden(module):
                offenders.append(f"{path.relative_to(_API_ROOT)}:{line} importiert {module}")
    assert offenders == [], "\n".join(offenders)


def test_loading_the_core_does_not_pull_in_the_api_layer() -> None:
    probe = (
        "import json, sys\n"
        "import app.llm, app.llm.classification\n"
        f"forbidden = {list(_FORBIDDEN)!r}\n"
        "hits = sorted(m for m in sys.modules if any(m == f or m.startswith(f + '.') "
        "for f in forbidden))\n"
        "print(json.dumps(hits))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=_API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == []
