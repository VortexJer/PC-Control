"""Verifica clic y tecleo en segundo plano contra una ventana de prueba PROPIA.

Criterio duro: el foco y el raton no pueden cambiar ni una vez. Este test no inyecta ninguna entrada.
"""
import json, os, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import core, win32api, win32gui, win32process

state = os.path.join(tempfile.gettempdir(), "pcsight_test_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
fg0, cur0 = win32gui.GetForegroundWindow(), win32api.GetCursorPos()
violations = []

def watch(where):
    if win32gui.GetForegroundWindow() != fg0: violations.append(f"foco cambio en: {where}")
    if win32api.GetCursorPos() != cur0: violations.append(f"raton cambio en: {where}")
    return not violations

def find(pid):
    out = []
    win32gui.EnumWindows(lambda h, _: out.append(h) if win32gui.GetWindowText(h) == "pcsight-test"
                         and win32process.GetWindowThreadProcessId(h)[1] == pid else None, None)
    return out[0] if out else None

p = subprocess.Popen(["powershell.exe", "-STA", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                      "-File", os.path.join(HERE, "test_app.ps1"), state], creationflags=0x08000000)
try:
    hw = None
    for _ in range(100):
        time.sleep(0.1)
        hw = find(p.pid)
        if hw and not watch("nacimiento de la ventana"): break
        if hw: break
    assert hw, "no aparecio la ventana de prueba"
    time.sleep(0.6)
    if not watch("tras abrir la ventana de prueba"):
        print("ABORTO: la ventana de prueba altero el foco/raton:", violations); raise SystemExit(1)
    r = core.look(str(hw)); print("LOOK:", r["mode"], r["tokens"], "tok |", r["why"]); print(r.get("text", ""))
    items = core._state[hw]["items"]; by = lambda s: next(i for i, x in items.items() if s in x.name)
    btn, edt, chk = by("Pulsar"), by("Campo"), by("opcion")
    res = {}
    def case(name, fn, key, cond):
        before = S()[key]; out = fn(); time.sleep(0.5); after = S()[key]
        res[name] = bool(cond(before, after)); watch(name)
        print(f"{name:22} {key}: {before!r} -> {after!r} | {out}")
    case("click boton (id)", lambda: core.click(str(hw), btn), "clicks", lambda b, a: a == b + 1)
    case("click casilla (id)", lambda: core.click(str(hw), chk), "checked", lambda b, a: a is True)
    case("escribir (id)", lambda: core.type_text(str(hw), "hola", target=edt), "text", lambda b, a: a == "hola")
    case("escribir mas (id)", lambda: core.type_text(str(hw), " mundo", target=edt), "text", lambda b, a: a == "hola mundo")
    case("reemplazar (id)", lambda: core.type_text(str(hw), "nuevo", target=edt, replace=True), "text", lambda b, a: a == "nuevo")
    x0, y0, x1, y1 = items[btn].rect
    case("click por coordenadas", lambda: core.click(str(hw), ((x0 + x1) // 2, (y0 + y1) // 2)), "clicks", lambda b, a: a == b + 1)
    print("\nRESULTADOS:", res)
    print("INTERFERENCIA:", violations or "ninguna (foco y raton intactos)")
finally:
    p.terminate()
