"""Child process that runs model-written verification code under restrictions.

Reads JSON {"code": ...} on stdin and writes JSON {"ok": bool, "result"|"error": str} on stdout.
The code must assign its final value to a variable named `result`.
"""

import ast
import json
import sys

ALLOWED_MODULES = {
    "sympy", "math", "cmath", "fractions", "itertools", "functools", "collections",
    "decimal", "statistics", "random", "operator",
}
BANNED_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr",
    "setattr", "delattr", "input", "breakpoint", "help", "exit", "quit", "memoryview", "type",
    "object", "super", "classmethod", "staticmethod", "property",
}
SAFE_BUILTINS = [
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "frozenset",
    "int", "isinstance", "len", "list", "map", "max", "min", "pow", "print", "range", "reversed",
    "round", "set", "sorted", "str", "sum", "tuple", "zip", "complex", "iter", "next", "chr",
    "ord", "hex", "bin", "oct", "slice", "hash", "True", "False", "None", "Exception",
    "ValueError", "ZeroDivisionError", "StopIteration", "ArithmeticError", "IndexError", "KeyError",
]


def _root(name: str) -> str:
    return name.split(".")[0]


def validate(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _root(alias.name) not in ALLOWED_MODULES:
                    raise ValueError(f"import of {alias.name} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.level or _root(node.module or "") not in ALLOWED_MODULES:
                raise ValueError(f"import from {node.module} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"access to {node.attr} is not allowed")
        elif isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("__")):
            raise ValueError(f"use of {node.id} is not allowed")


def hardcoded(tree: ast.AST) -> bool:
    """True if every assignment to result is a bare literal, e.g. `result = 26`."""
    values = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "result" for t in node.targets)
    ]
    literal = (ast.Constant, ast.UnaryOp)
    return bool(values) and all(isinstance(v, literal) and not isinstance(getattr(v, "operand", None), ast.Name) for v in values)


def describe(value) -> str:
    import sympy as sp

    if isinstance(value, sp.Interval):
        left = "(" if value.left_open else "["
        right = ")" if value.right_open else "]"
        return f"{left}{sp.sstr(value.start)}, {sp.sstr(value.end)}{right}"
    if isinstance(value, sp.Union):
        return " U ".join(describe(a) for a in value.args)
    if isinstance(value, (sp.FiniteSet, set, frozenset, list, tuple)):
        items = list(value)
        try:
            items = sorted(items, key=lambda x: float(sp.N(x)))
        except Exception:
            pass
        return "{" + ", ".join(describe(x) for x in items) + "}"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value
    return sp.sstr(sp.sympify(value))


def main() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    except Exception:
        pass

    payload = json.loads(sys.stdin.read())
    code = payload["code"]
    try:
        tree = ast.parse(code)
        validate(tree)
        import builtins
        import importlib

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level or _root(name) not in ALLOWED_MODULES:
                raise ImportError(f"import of {name} is not allowed")
            return importlib.__import__(name, globals, locals, fromlist, level)

        safe = {k: getattr(builtins, k) for k in SAFE_BUILTINS if hasattr(builtins, k)}
        safe["__import__"] = guarded_import
        namespace = {"__builtins__": safe}
        import contextlib
        import io

        # Snippets often print as well as assign; keep that out of our JSON reply.
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(tree, "<verification>", "exec"), namespace)
        if "result" not in namespace:
            raise ValueError("the code did not assign a variable named result")
        if hardcoded(tree):
            raise ValueError("the code assigns result a literal value instead of computing it")
        out = {"ok": True, "result": describe(namespace["result"])}
    except Exception as exc:
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
