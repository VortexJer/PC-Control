"""El codigo del paquete no puede contener llamadas que interfieran con el usuario."""
import glob, os, re, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = ["SetForegroundWindow", "keybd_event", "SendInput", "SetCursorPos", "mouse_event",
             "SetActiveWindow", "BringWindowToTop", "SwitchToThisWindow", "AttachThreadInput", "pyautogui", "pynput"]

def scan(text):
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        hits += [(n, w) for w in FORBIDDEN if re.search(w + r"\b", code)]
    return hits

# control positivo: el escaner DEBE detectar cada patron prohibido
for w in FORBIDDEN:
    assert scan(f"x = win32gui.{w}(h)\n") == [(1, w)], f"el escaner no detecta {w}"
assert scan("# SetForegroundWindow solo en comentario\n") == [], "falso positivo en comentario"

files = glob.glob(os.path.join(ROOT, "pcsight", "*.py"))
assert files, "no hay codigo que escanear"
bad = {os.path.basename(f): scan(open(f, encoding="utf-8").read()) for f in files}
bad = {k: v for k, v in bad.items() if v}
if bad:
    print("LLAMADAS INTRUSIVAS:", bad); sys.exit(1)
print(f"static verification passed ({len(files)} archivos, {len(FORBIDDEN)} patrones, control positivo ok)")
