"""El codigo del paquete no puede contener llamadas que interfieran con el usuario (analisis del AST, no de texto)."""
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

# controles positivos: el escaner DEBE detectar cada patron y NO marcar comentarios ni docstrings
for w in FORBIDDEN:
    assert scan(f"import x\nx.{w}(h)\n") == [(2, w)], f"el escaner no detecta x.{w}"
assert scan("import pyautogui\n"), "no detecta import pyautogui"
assert scan('"""nunca SetForegroundWindow"""\n# keybd_event\nx = 1\n') == [], "falso positivo en docstring/comentario"

files = glob.glob(os.path.join(ROOT, "pcsight", "*.py"))
assert files, "no hay codigo que escanear"
bad = {os.path.basename(f): scan(open(f, encoding="utf-8").read()) for f in files}
bad = {k: v for k, v in bad.items() if v}
if bad:
    print("LLAMADAS INTRUSIVAS:", bad); sys.exit(1)
print(f"static verification passed ({len(files)} archivos, {len(FORBIDDEN)} patrones, control positivo ok)")
