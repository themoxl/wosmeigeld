"""Schranke "keine festen Grenzen im KI-Code" (Port von
sia_public/build/ki_feste_grenzen_selftest.js, AST statt Regex).

Im KI-Code steht keine Zahl, die ein Antwortbudget, eine Zeichen-/Tokenmenge
fuer das Modell, eine Rundenzahl oder eine Frist festlegt. Jede solche Grenze
kommt aus dem geladenen Fenster, aus einer Messung oder aus einer
Haushaltseinstellung (deren Vorgabe nur im Datenbankschema steht). Diese
Schranke findet neue feste Zahlen, bevor sie jemand bemerkt:

  * max_tokens / max_output_tokens / max_completion_tokens mit Zahl
  * Modulkonstanten mit TOKENS/CHARS im Namen und einer Zahl
  * len(...) // n bzw. len(...) / n (Zeichen/Token-Faustregel)
  * reasoning= / "reasoning_effort" mit festem Wert ausserhalb des Kerns (E2)
  * entfernte Begriffe (escalated_token_budget, _ESTIMATED_PROMPT_TOKENS,
    _TRUNCATION_*, ai_max_tokens, reasoning_budget, max_tokens_override,
    _MAX_ANSWER_TOKENS, ai_request_timeout_seconds)
  * feste Slices ([:N], [-N:]) - auf Promptmaterial heisst das "still
    abgeschnitten"
  * range(<Zahl>) als Rundenzahl
  * Timeout(<Zahl>) bzw. timeout=<Zahl>

Ausnahmen nur mit Kommentar `# fest: <Grund>` auf derselben Zeile oder auf
einer reinen Kommentarzeile direkt davor - jeweils fuer die Zeile des Befunds
oder die erste Zeile seiner Anweisung (E8: Datensparsamkeit, Anzeige,
Vertragsdefinitionen). Der Test gibt jede Ausnahme mit Grund aus.

Eine Uebergangsliste gibt es nicht mehr (K2 abgeschlossen): jede KI-Datei
wird streng geprueft. Der Pruefbereich nennt die Aufrufstellen ausserhalb von
app/llm, app/assistant und ai_*.py ausdruecklich - frueher deckte das Muster
nur ai_*.py ab, und umgestellte Dateien fielen beim Streichen aus der
Uebergangsliste still aus der Pruefung. Eine dort genannte Datei, die es
nicht mehr gibt, laesst den Test scheitern, statt still zu verschwinden.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[3]

# KI-Code: alle Regeln. Muster fuer ganze KI-Bereiche ...
_SCOPE: Final[tuple[str, ...]] = (
    "services/api/app/llm/**/*.py",
    "services/api/app/assistant/**/*.py",
    "services/api/app/services/ai_*.py",
    "services/worker/worker/ai/**/*.py",
)

# ... und jede KI-Aufrufstelle ausserhalb davon ausdruecklich (muss es geben).
_CALL_SITES: Final[tuple[str, ...]] = (
    "services/api/app/api/v1/routes/assistant.py",
    "services/api/app/api/v1/routes/ai.py",
    "services/api/app/services/import_mapping_ai.py",
    "services/api/app/services/duplicate_ai_service.py",
    "services/api/app/services/booked_duplicate_ai_service.py",
    "services/api/app/services/category_suggestion_service.py",
    "services/api/app/services/contract_ai_crosscheck_service.py",
    "services/api/app/services/grocery_ai_service.py",
    "services/api/app/services/insights_service.py",
    "services/api/app/services/project_cost_ai_suggestion_service.py",
    "services/worker/worker/health_documents/processor.py",
)

# Der Kern bildet Reasoning-Einstellungen auf Transportwerte ab (off -> none);
# dort sind feste Reasoning-Werte die Abbildung selbst, nicht ein Uebersteuern.
_CORE: Final = "services/api/app/llm/"

_BUDGET_NAMES: Final = frozenset({"max_tokens", "max_output_tokens", "max_completion_tokens"})
_REASONING_NAMES: Final = frozenset({"reasoning", "reasoning_effort"})
_REMOVED_NAMES: Final = frozenset(
    {
        "escalated_token_budget",
        "_ESTIMATED_PROMPT_TOKENS",
        "ai_max_tokens",
        "reasoning_budget",
        "max_tokens_override",
        "_MAX_ANSWER_TOKENS",
        # Umgebungsrueckfall fuer das Zeitbudget je Anfrage: die Vorgabe steht
        # jetzt im Schema (ai_household_settings.request_timeout_seconds).
        "ai_request_timeout_seconds",
        # Uebergang K2 (geloescht): Kennwert "keine Grenze der Aufrufstelle".
        "LEGACY_UNCAPPED",
    }
)
_REMOVED_PREFIX: Final = "_TRUNCATION_"
_CONSTANT_NAME: Final = re.compile(r"TOKENS|CHARS")
_FEST: Final = re.compile(r"#\s*fest:\s*(\S.*)$")


@dataclass(frozen=True)
class Befund:
    path: str
    line: int
    rule: str
    source: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.rule}: {self.source}"


def _is_number(node: ast.AST | None) -> bool:
    """Zahlenliteral, auch negativ oder als Ausdruck nur aus Zahlen (25 * 1024)."""
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return isinstance(node.value, int | float) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        return _is_number(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_number(node.left) and _is_number(node.right)
    return False


def _is_text(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _name_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript) and _is_text(node.slice):
        value = node.slice
        assert isinstance(value, ast.Constant)
        return str(value.value)
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: str, lines: list[str], core: bool) -> None:
        self.path = path
        self.lines = lines
        self.core = core
        self.found: list[tuple[int, int, str]] = []  # (Zeile, Anweisungszeile, Regel)
        self._statement_line = 0
        self._depth = 0

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.stmt):
            previous = self._statement_line
            self._statement_line = node.lineno
            super().generic_visit(node)
            self._statement_line = previous
            return
        super().generic_visit(node)

    def _hit(self, node: ast.AST, rule: str) -> None:
        line = getattr(node, "lineno", self._statement_line)
        self.found.append((line, self._statement_line or line, rule))

    # --- Funktionen/Klassen: Modulebene merken ---------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_removed(node, node.name)
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_removed(node, node.name)
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    # --- Namen --------------------------------------------------------------
    def _check_removed(self, node: ast.AST, name: str | None) -> None:
        if name and (name in _REMOVED_NAMES or name.startswith(_REMOVED_PREFIX)):
            self._hit(node, f"entfernter Begriff {name}")

    def visit_Name(self, node: ast.Name) -> None:
        self._check_removed(node, node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._check_removed(node, node.attr)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self._check_removed(node, node.arg)
        self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> None:
        self._check_removed(node, node.name)
        self._check_removed(node, node.asname)

    # --- Zuweisungen --------------------------------------------------------
    def _check_assignment(self, node: ast.stmt, target: ast.AST, value: ast.AST | None) -> None:
        name = _name_of(target)
        if name is None or value is None:
            return
        if name in _BUDGET_NAMES and _is_number(value):
            self._hit(node, "Antwortbudget als Zahl")
        if (
            self._depth == 0
            and isinstance(target, ast.Name)
            and name == name.upper()
            and _CONSTANT_NAME.search(name)
            and _is_number(value)
        ):
            self._hit(node, f"Konstante {name} mit fester Zahl")
        if not self.core and name in _REASONING_NAMES and _is_text(value):
            self._hit(node, "Reasoning fest gesetzt")

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._check_assignment(node, target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_assignment(node, node.target, node.value)
        self.generic_visit(node)

    # --- Aufrufe ------------------------------------------------------------
    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg in _BUDGET_NAMES and _is_number(node.value):
            self._hit(node.value, "Antwortbudget als Zahl")
        if not self.core and node.arg in _REASONING_NAMES and _is_text(node.value):
            self._hit(node.value, "Reasoning fest gesetzt")
        if node.arg == "timeout" and _is_number(node.value):
            self._hit(node.value, "Frist als Zahl")
        self._check_removed(node.value, node.arg)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = _name_of(node.func)
        if func == "range" and any(
            _is_number(arg) and not _is_small(arg) for arg in node.args
        ):
            self._hit(node, "Rundenzahl als Zahl")
        if func == "Timeout" and (
            any(_is_number(arg) for arg in node.args)
            or any(_is_number(keyword.value) for keyword in node.keywords)
        ):
            self._hit(node, "Frist als Zahl")
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=False):
            if not _is_text(key):
                continue
            assert isinstance(key, ast.Constant)
            if key.value in _BUDGET_NAMES and _is_number(value):
                self._hit(value, "Antwortbudget als Zahl")
            if not self.core and key.value in _REASONING_NAMES and _is_text(value):
                self._hit(value, "Reasoning fest gesetzt")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if (
            isinstance(node.op, ast.FloorDiv | ast.Div)
            and isinstance(node.left, ast.Call)
            and _name_of(node.left.func) == "len"
            and _is_number(node.right)
        ):
            self._hit(node, "Zeichen/Token-Quotient")
        self.generic_visit(node)

    def visit_Slice(self, node: ast.Slice) -> None:
        # "Die ersten N" ([:N], [0:N]) oder "die letzten N" ([-N:]). Ein- und
        # zweistellige Ausschnitte wie [1:-1] (Anfuehrungszeichen weg) oder
        # [:-1] sind keine Laengengrenze.
        first_n = _literal(node.upper)
        last_n = _literal(node.lower)
        lower_start = node.lower is None or _literal(node.lower) == 0
        if (first_n is not None and first_n >= 2 and lower_start) or (
            last_n is not None and last_n <= -2 and node.upper is None
        ):
            self._hit(node, "feste Textgrenze (Slice)")
        self.generic_visit(node)


def _literal(node: ast.AST | None) -> float | None:
    """Wert eines Zahlenliterals (auch negativ), sonst None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return None if isinstance(node.value, bool) else float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _literal(node.operand)
        return -inner if inner is not None else None
    return None


def _is_small(node: ast.AST) -> bool:
    """range(0, ...) / range(1, ...) setzen nur den Start, keine Rundenzahl."""
    return isinstance(node, ast.Constant) and node.value in (0, 1)


def _fest_reason(lines: list[str], *candidates: int) -> str | None:
    """Grund aus `# fest: ...` auf der Zeile selbst oder auf einer reinen
    Kommentarzeile direkt davor (ein Kommentar am Ende der VORIGEN
    Anweisung gilt nicht fuer die naechste)."""
    for line in candidates:
        same = line - 1
        if 0 <= same < len(lines):
            match = _FEST.search(lines[same])
            if match:
                return match.group(1).strip()
        before = line - 2
        if 0 <= before < len(lines) and lines[before].lstrip().startswith("#"):
            match = _FEST.search(lines[before])
            if match:
                return match.group(1).strip()
    return None


def _scan(path: Path) -> tuple[list[Befund], list[str]]:
    relative = path.relative_to(_REPO).as_posix()
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    scanner = _Scanner(relative, lines, core=relative.startswith(_CORE))
    scanner.visit(ast.parse(source, filename=relative))
    befunde: list[Befund] = []
    ausnahmen: list[str] = []
    seen: set[tuple[int, str]] = set()
    for line, statement_line, rule in scanner.found:
        if (line, rule) in seen:
            continue
        seen.add((line, rule))
        reason = _fest_reason(lines, line, statement_line)
        if reason is not None:
            ausnahmen.append(f"{relative}:{line} {rule} - {reason}")
            continue
        text = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        befunde.append(Befund(relative, line, rule, text))
    return befunde, ausnahmen


def _files() -> list[Path]:
    paths: set[Path] = set()
    for pattern in _SCOPE:
        paths.update(path for path in _REPO.glob(pattern) if path.is_file())
    paths.update(_REPO / entry for entry in _CALL_SITES)
    return sorted(paths)


def _scan_all() -> tuple[list[Befund], list[str]]:
    befunde: list[Befund] = []
    exceptions: list[str] = []
    for path in _files():
        found, ausnahmen = _scan(path)
        befunde.extend(found)
        exceptions.extend(ausnahmen)
    return befunde, exceptions


def test_every_named_call_site_exists() -> None:
    """Umbenannt oder verschoben? Dann hier nachziehen - sonst fiele die
    Datei still aus der Pruefung."""
    missing = [entry for entry in _CALL_SITES if not (_REPO / entry).is_file()]
    assert missing == [], f"Pruefbereich nennt fehlende Dateien: {missing}"


def test_the_scope_covers_every_ai_call_site() -> None:
    """Jede Datei, die den Gateway oder die Antwortpruefung des Kerns
    benutzt, liegt im Pruefbereich - auch eine neue Aufrufstelle."""
    scanned = {path.resolve() for path in _files()}
    markers = re.compile(
        r"\bllm\.(?:chat|chat_structured|answers)\b|from app\.llm\.answers import"
        r"|from app\.llm\.gateway import|\banswers\.ask\("
    )
    roots = [_REPO / "services" / "api" / "app", _REPO / "services" / "worker" / "worker"]
    outside = sorted(
        path.relative_to(_REPO).as_posix()
        for root in roots
        for path in root.rglob("*.py")
        if path.resolve() not in scanned
        and markers.search(path.read_text(encoding="utf-8"))
    )
    assert outside == [], f"KI-Aufrufstellen ausserhalb des Pruefbereichs: {outside}"


def test_no_fixed_limits_in_ai_code() -> None:
    befunde, exceptions = _scan_all()
    if exceptions:
        print(f"\nAusnahmen mit Grund ({len(exceptions)}):\n  " + "\n  ".join(exceptions))
    assert befunde == [], "Feste Grenzen im KI-Code:\n  " + "\n  ".join(map(str, befunde))


def _probe(source: str, *, core: bool = False) -> list[str]:
    scanner = _Scanner("probe.py", source.splitlines(), core=core)
    scanner.visit(ast.parse(source))
    return [rule for _line, _statement, rule in scanner.found]


def test_the_rules_catch_what_they_promise() -> None:
    """Die Schranke selbst: jede Regel trifft ihr Beispiel, nicht aber die
    erlaubte Form daneben."""
    assert _probe("body = {'max_tokens': 4000}") == ["Antwortbudget als Zahl"]
    assert _probe("body = {'max_tokens': budget.max_tokens}") == []
    assert _probe("x = replace(s, max_output_tokens=512)") == ["Antwortbudget als Zahl"]
    assert _probe("MAX_PROMPT_CHARS = 12_000") == ["Konstante MAX_PROMPT_CHARS mit fester Zahl"]
    assert _probe("_FORMAT_TOKENS: Final = 25 * 60") == [
        "Konstante _FORMAT_TOKENS mit fester Zahl"
    ]
    assert _probe("def f():\n    LOCAL_TOKENS = 3\n") == []
    assert _probe("n = len(prompt) // 4") == ["Zeichen/Token-Quotient"]
    assert _probe("n = len(prompt) / 4.0") == ["Zeichen/Token-Quotient"]
    assert _probe("x = replace(s, reasoning='off')") == ["Reasoning fest gesetzt"]
    assert _probe("x = replace(s, reasoning='off')", core=True) == []
    assert _probe("x = escalated_token_budget(a, b)") == [
        "entfernter Begriff escalated_token_budget"
    ]
    assert _probe("_TRUNCATION_FLOOR = 3") == ["entfernter Begriff _TRUNCATION_FLOOR"]
    assert _probe("short = memo[:9000]") == ["feste Textgrenze (Slice)"]
    assert _probe("tail = memo[-300:]") == ["feste Textgrenze (Slice)"]
    assert _probe("rest = parts[1:]") == []
    assert _probe("inner = value[1:-1]") == []
    assert _probe("head = value[:-1]") == []
    assert _probe("for i in range(3): pass") == ["Rundenzahl als Zahl"]
    assert _probe("for i in range(1, len(x)): pass") == []
    assert _probe("t = httpx.Timeout(30.0)") == ["Frist als Zahl"]
    assert _probe("t = httpx.Timeout(s.timeout, connect=2.0)") == ["Frist als Zahl"]
    assert _probe("c = httpx.AsyncClient(timeout=5)") == ["Frist als Zahl"]
    assert _probe("t = httpx.Timeout(settings.request_timeout_seconds)") == []


def test_a_fest_comment_exempts_and_is_reported() -> None:
    source = (
        "# fest: genau drei Beispielzeilen (Datensparsamkeit)\n"
        "SAMPLE_CHARS = 3\n"
        "MORE_CHARS = 4  # fest: Anzeige\n"
        "OTHER_CHARS = 5\n"
    )
    path = _REPO / "services" / "api" / "tests" / "_probe_fest.py"
    path.write_text(source, encoding="utf-8")
    try:
        befunde, ausnahmen = _scan(path)
    finally:
        path.unlink()
    assert [befund.line for befund in befunde] == [4]
    assert len(ausnahmen) == 2
    assert "Datensparsamkeit" in ausnahmen[0]
