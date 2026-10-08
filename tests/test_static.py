"""The package code must not contain calls that interfere with the user (AST analysis, not text matching)."""
import ast, glob, os, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = {"SetForegroundWindow", "keybd_event", "SendInput", "SetCursorPos", "mouse_event", "SetActiveWindow",
             "BringWindowToTop", "SwitchToThisWindow", "AttachThreadInput", "pyautogui", "pynput"}

def scan(src):
    hits = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Attribute) and n.attr in FORBIDDEN: hits.append((n.lineno, n.attr))
        elif isinstance(n, ast.Name) and n.id in FORBIDDEN: hits.append((n.lineno, n.id))
        elif isinstance(n, ast.alias) and n.name.split(".")[0] in FORBIDDEN: hits.append((0, n.name))
        elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in FORBIDDEN: hits.append((n.lineno, n.module))
    return hits

# positive controls: the scanner MUST detect each pattern and must NOT flag comments or docstrings
for w in FORBIDDEN:
    assert scan(f"import x\nx.{w}(h)\n") == [(2, w)], f"the scanner does not detect x.{w}"
assert scan("import pyautogui\n"), "does not detect import pyautogui"
assert scan('"""never SetForegroundWindow"""\n# keybd_event\nx = 1\n') == [], "false positive on a docstring/comment"

files = glob.glob(os.path.join(ROOT, "pc_control", "*.py"))
assert files, "there is no code to scan"
bad = {os.path.basename(f): scan(open(f, encoding="utf-8").read()) for f in files}
bad = {k: v for k, v in bad.items() if v}
if bad:
    print("INTRUSIVE CALLS:", bad); sys.exit(1)
print(f"static verification passed ({len(files)} files, {len(FORBIDDEN)} patterns, positive control ok)")
