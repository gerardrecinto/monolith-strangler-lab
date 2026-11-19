"""Checks for mistakes that AI-assisted code often contains and that a type checker or linter may let through.

Rules: hallucinated-import, no-such-attribute, sql-injection, hardcoded-secret, swallowed-error,
dangerous-call, layering. Usage: python -m review.gate PATH [PATH ...]
"""
import ast
import importlib
import importlib.util
import pathlib
import re
import sys

STDLIB = sys.stdlib_module_names
SECRET_NAME = re.compile(r"(password|passwd|secret|token|api_?key)", re.I)
PLACEHOLDER = re.compile(r"^(changeme|example|xxx+|\*+|<.*>|\$\{.*\}|)$", re.I)
# A directory name mapped to the top-level modules its files must not import.
LAYERING = {"services": {"legacy", "facade"}, "legacy": {"services", "facade"}}


def _finding(path, node, rule, msg):
    return {"path": str(path), "line": getattr(node, "lineno", 0), "rule": rule, "message": msg}


def check_source(src, path="<src>"):
    tree = ast.parse(src)
    parts = set(pathlib.PurePath(path).parts)
    forbidden = set().union(*(m for d, m in LAYERING.items() if d in parts)) if parts & LAYERING.keys() else set()
    out, stdlib_aliases = [], {}

    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [(a.name, a.asname or a.name.split(".")[0]) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [(node.module, None)]
        for full, alias in names:
            top = full.split(".")[0]
            if top in forbidden:
                out.append(_finding(path, node, "layering", f"{top} may not be imported from this directory"))
            elif top not in STDLIB and importlib.util.find_spec(top) is None:
                out.append(_finding(path, node, "hallucinated-import", f"module {top} is not installed or in the standard library"))
            elif top in STDLIB and alias and "." not in full:
                stdlib_aliases[alias] = full

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in stdlib_aliases:
            mod = importlib.import_module(stdlib_aliases[node.value.id])
            if not hasattr(mod, node.attr):
                out.append(_finding(path, node, "no-such-attribute", f"{stdlib_aliases[node.value.id]} has no attribute {node.attr}"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("execute", "executemany", "executescript") and node.args and _built_string(node.args[0]):
                out.append(_finding(path, node, "sql-injection", "SQL built from a string; pass parameters instead"))
            if node.func.attr == "system" and isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                out.append(_finding(path, node, "dangerous-call", "os.system runs a shell"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
            out.append(_finding(path, node, "dangerous-call", f"{node.func.id}() on data"))
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    out.append(_finding(path, node, "dangerous-call", "shell=True"))
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                name = t.id if isinstance(t, ast.Name) else getattr(t, "attr", "")
                if (SECRET_NAME.search(name) and isinstance(value, ast.Constant) and isinstance(value.value, str)
                        and not PLACEHOLDER.match(value.value)):
                    out.append(_finding(path, node, "hardcoded-secret", f"{name} is set to a literal"))
        if isinstance(node, ast.ExceptHandler):
            body_is_empty = all(isinstance(s, (ast.Pass, ast.Continue)) for s in node.body)
            if node.type is None or body_is_empty:
                out.append(_finding(path, node, "swallowed-error", "bare except or an except block that does nothing"))
    return out


def _built_string(arg):
    if isinstance(arg, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in arg.values)
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Mod)):
        return not (isinstance(arg.left, ast.Constant) and isinstance(arg.right, ast.Constant))
    return isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "format"


def check_paths(paths):
    out = []
    for p in paths:
        p = pathlib.Path(p)
        for f in sorted(p.rglob("*.py") if p.is_dir() else [p]):
            if ".venv" in f.parts or "tests" in f.parts:
                continue
            out.extend(check_source(f.read_text(), f))
    return out


def main(argv=None):
    findings = check_paths(argv if argv is not None else sys.argv[1:])
    for f in findings:
        print(f"{f['path']}:{f['line']} {f['rule']} {f['message']}")
    print("review gate: clean" if not findings else f"review gate: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
