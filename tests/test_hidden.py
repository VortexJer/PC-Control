"""Una app abierta de cero por pcsight vive en el escritorio oculto: NO se ve nunca, ni un instante.

Mide desde el escritorio del usuario cada ~3 ms durante todo el arranque: no debe existir ninguna ventana de la
app ahi, y el foco nunca debe caer en un proceso nuestro. Dentro del escritorio oculto: se lee (arbol y captura)
y se maneja (clic y escritura) igual que cualquier ventana.
"""
import json, os, sys, tempfile, threading, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pcsight import core
from pcsight.hidden import DESK
import numpy as np
from PIL import Image
import win32gui, win32process

state = os.path.join(tempfile.gettempdir(), "pcsight_hidden_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
OURS = set()
log = {"samples": 0, "on_user_desktop": 0, "activated": False}
stop = threading.Event()

def sampler():
    while not stop.is_set():
        fg = win32gui.GetForegroundWindow()
        if fg and win32process.GetWindowThreadProcessId(fg)[1] in OURS: log["activated"] = True
        n = []
        win32gui.EnumWindows(lambda h, _: n.append(h) if win32gui.GetWindowText(h) == "pcsight-test" else None, None)
        log["samples"] += 1; log["on_user_desktop"] += len(n)
        time.sleep(0.003)

th = threading.Thread(target=sampler, daemon=True); th.start()
cmd = f'powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -File "{os.path.join(HERE, "test_app.ps1")}" "{state}"'
checks = {}
try:
    pid = DESK.launch(cmd); OURS.add(pid)
    hw = None
    for _ in range(100):
        hw = next((h for h, t, p in DESK.windows() if p == pid), None)
        if hw: break
        time.sleep(0.1)
    time.sleep(0.5)
    stop.set(); th.join()
    assert hw, "la ventana no aparecio en el escritorio oculto"
    checks["nunca en el escritorio del usuario"] = log["on_user_desktop"] == 0
    checks["nunca activo el foco"] = not log["activated"]
    print(f"muestras desde el escritorio del usuario: {log['samples']} | veces que la ventana estuvo ahi: {log['on_user_desktop']}")

    lk = DESK.run(lambda: core.look(str(hw)))
    checks["se lee (arbol)"] = lk["mode"] == "uia" and "Pulsar" in (lk.get("text") or "")
    im = DESK.run(lambda: core.look(str(hw), "image"))
    checks["se lee (captura no vacia)"] = bool(im.get("image")) and float(np.array(Image.open(im["image"])).std()) > 5

    items = core._state[hw]["items"]
    ids = {x.name: i for i, x in items.items()}
    btn = next(i for n, i in ids.items() if "Pulsar" in n); edt = next(i for n, i in ids.items() if "Campo" in n)
    DESK.run(lambda: core.look(str(hw)))                       # ids frescos
    items = core._state[hw]["items"]; btn = next(i for i, x in items.items() if x.name == "Pulsar"); edt = next(i for i, x in items.items() if "Campo" in x.name)
    DESK.run(lambda: core.click(str(hw), btn)); time.sleep(0.4)
    checks["clic en la app oculta"] = S()["clicks"] == 1
    DESK.run(lambda: core.type_text(str(hw), "hola", target=edt)); time.sleep(0.4)
    checks["escritura en la app oculta"] = S()["text"] == "hola"
    ch = DESK.run(lambda: core.changes(str(hw)))
    checks["solo se envia el cambio"] = "Clicks: 1" in ch
    print("cambio tras actuar:", ch)
finally:
    stop.set()
    DESK.close()

for k, v in checks.items():
    print(("OK   " if v else "FAIL ") + k)
ok = all(checks.values())
print("hidden verification passed" if ok else "hidden verification FAILED")
sys.exit(0 if ok else 1)
