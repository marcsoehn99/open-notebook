"""Quiz SurrealQL must not bind names SurrealDB reserves.

SurrealDB 2.7 rejects a client variable named token. The error is
"'token' is a protected variable and cannot be set". Answer and explain
both load the attempt with that bind, so both returned HTTP 500.
"""

import ast
import re
from collections.abc import Sequence
from pathlib import Path

PROTECTED_BIND_NAMES = frozenset(
    {
        "token",
        "session",
        "auth",
        "scope",
        "access",
        "this",
        "parent",
        "value",
        "before",
        "after",
        "event",
        "input",
    }
)

# DEFINE FIELD and DEFINE EVENT read these from the record being written.
# The migration runner does not pass them in as query variables.
SCHEMA_CONTEXT_NAMES = frozenset(
    {
        "this",
        "parent",
        "value",
        "before",
        "after",
        "event",
        "input",
    }
)

_PARAM = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

_ROOT = Path(__file__).resolve().parents[1]

_PYTHON_FILES = (
    _ROOT / "open_notebook" / "domain" / "quiz.py",
    _ROOT / "open_notebook" / "graphs" / "quiz.py",
    _ROOT / "api" / "routers" / "quizzes.py",
    _ROOT / "commands" / "quiz_commands.py",
)

_MIGRATION_FILES = (
    _ROOT / "open_notebook" / "database" / "migrations" / "26.surrealql",
    _ROOT / "open_notebook" / "database" / "migrations" / "26_down.surrealql",
    _ROOT / "open_notebook" / "database" / "migrations" / "27.surrealql",
    _ROOT / "open_notebook" / "database" / "migrations" / "27_down.surrealql",
)


def _static_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
                continue
            if isinstance(piece, ast.FormattedValue):
                parts.append("")
                continue
            return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        if left is None or right is None:
            return None
        return left + right
    return None


def _dict_keys(node: ast.AST) -> set[str] | None:
    if not isinstance(node, ast.Dict):
        return None
    keys: set[str] = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


class _QueryScanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: set[str] = set()
        self._strings: list[dict[str, set[str]]] = [{}]
        self._keys: list[dict[str, set[str]]] = [{}]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._push()
        self.generic_visit(node)
        self._pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._push()
        self.generic_visit(node)
        self._pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        self._remember(node.targets, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._remember((node.target,), node.value)

    def visit_Call(self, node: ast.Call) -> None:
        if _call_name(node) == "repo_query":
            self._note_query(node)
        self.generic_visit(node)

    def _push(self) -> None:
        self._strings.append({})
        self._keys.append({})

    def _pop(self) -> None:
        self._strings.pop()
        self._keys.pop()

    def _remember(self, targets: Sequence[ast.AST], value: ast.AST) -> None:
        text = _static_string(value)
        keys = _dict_keys(value)
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if text is not None:
                self._strings[-1].setdefault(target.id, set()).add(text)
            if keys is not None:
                self._keys[-1].setdefault(target.id, set()).update(keys)

    def _strings_from(self, node: ast.AST) -> set[str]:
        text = _static_string(node)
        if text is not None:
            return {text}
        if isinstance(node, ast.Name):
            found: set[str] = set()
            for scope in self._strings:
                found.update(scope.get(node.id, set()))
            return found
        return set()

    def _keys_from(self, node: ast.AST) -> set[str]:
        keys = _dict_keys(node)
        if keys is not None:
            return keys
        if isinstance(node, ast.Name):
            found: set[str] = set()
            for scope in self._keys:
                found.update(scope.get(node.id, set()))
            return found
        return set()

    def _note_query(self, node: ast.Call) -> None:
        query_node: ast.AST | None = node.args[0] if node.args else None
        bind_node: ast.AST | None = node.args[1] if len(node.args) > 1 else None
        for keyword in node.keywords:
            if keyword.arg == "query_str":
                query_node = keyword.value
            elif keyword.arg == "vars":
                bind_node = keyword.value
        if query_node is not None:
            for text in self._strings_from(query_node):
                for name in _PARAM.findall(text):
                    if name in PROTECTED_BIND_NAMES:
                        self.found.add(name)
        if bind_node is not None:
            for key in self._keys_from(bind_node):
                if key in PROTECTED_BIND_NAMES:
                    self.found.add(key)


def _bound_in_python(path: Path) -> set[str]:
    scanner = _QueryScanner()
    scanner.visit(ast.parse(path.read_text(), filename=str(path)))
    return scanner.found


def _bound_in_migration(path: Path) -> set[str]:
    sql = path.read_text()
    return {
        name
        for name in _PARAM.findall(sql)
        if name in PROTECTED_BIND_NAMES and name not in SCHEMA_CONTEXT_NAMES
    }


def test_quiz_queries_do_not_bind_protected_surreal_names() -> None:
    bound: set[str] = set()
    for path in _PYTHON_FILES:
        bound.update(_bound_in_python(path))
    for path in _MIGRATION_FILES:
        bound.update(_bound_in_migration(path))
    assert bound == set()
