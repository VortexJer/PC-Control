"""Una app abierta de cero por pcsight NO SE VE: nace sin activarse, se aparca fuera de todos los monitores
y al fondo, y aun asi se puede leer (arbol + captura) y devolver a su sitio.

Se mide lo que importa al usuario: que la ventana no intersecte con ningun monitor (da igual que haya o no
otras ventanas encima). Muestrea el foco y la posicion cada ~5 ms durante el arranque para medir el parpadeo.
"""
import os, sys, tempfile, threading, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pcsight import core
import numpy as np
from PIL import Image
import win32gui, win32process

state = os.path.join(tempfile.gettempdir(), "pcsight_zorder_state.json")
OURS = set()
log = {"activated": False, "samples": 0, "on_screen": 0}
stop = threading.Event()

def sampler():
    while not stop.is_set():
        fg = win32gui.GetForegroundWindow()
        if fg and (win32gui.GetWindowText(fg) == "pcsight-test" or win32process.GetWindowThreadProcessId(fg)[1] in OURS):
            log["activated"] = True
        mine = [h for h in core.top_windows() if win32gui.GetWindowText(h) == "pcsight-test"]
        if mine:
            log["samples"] += 1
            if not core.is_hidden(mine[0]): log["on_screen"] += 1
        time.sleep(0.005)

t = threading.Thread(target=sampler, daemon=True); t.start()
ps1 = os.path.join(HERE, "test_app.ps1")
cmd = f'powershell.exe -STA -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{ps1}" "{state}"'
r = core.open_app(cmd, creationflags=0x08000000)
OURS.add(r["pid"])
time.sleep(0.8)
stop.set(); t.join()
checks = {}
try:
    hw = r["windows"][0]["hwnd"] if r["windows"] else None
    assert hw, f"la ventana no aparecio: {r}"
    ws = core.top_windows()
    checks["no se ve en ningun monitor"] = core.is_hidden(hw)
    checks["al fondo del orden z"] = ws.index(hw) == len(ws) - 1
    checks["nunca activada"] = not log["activated"]
    flash_ms = log["on_screen"] * 5
    checks["parpadeo <= 33 ms"] = flash_ms <= 33
    print(f"parpadeo medido: ~{flash_ms} ms sobre {log['samples']} muestras")
    # aparcada pero legible: el arbol y la captura funcionan fuera de pantalla
    lk = core.look(str(hw)); checks["legible aparcada (arbol)"] = lk["mode"] == "uia" and "Pulsar" in (lk.get("text") or "")
    im = core.look(str(hw), "image"); img_ok = False
    if im.get("image"):
        img_ok = float(np.array(Image.open(im["image"])).std()) > 5
    checks["legible aparcada (captura no vacia)"] = img_ok
    # y se puede devolver a su sitio
    orig = core.PARKED[hw]
    checks["unpark la devuelve a su sitio"] = core.unpark(hw) and win32gui.GetWindowRect(hw)[:2] == orig[:2] and not core.is_hidden(hw)
    for k, v in checks.items():
        print(("OK   " if v else "FAIL ") + k)
    ok = all(checks.values())
    print("zorder verification passed" if ok else "zorder verification FAILED")
    sys.exit(0 if ok else 1)
finally:
    import subprocess
    subprocess.run(["taskkill", "/PID", str(r["pid"]), "/T", "/F"], capture_output=True, creationflags=0x08000000)
