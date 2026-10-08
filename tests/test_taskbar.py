"""Modo por defecto de open_app: la app nace MINIMIZADA (nada en pantalla, sin foco) y deja su boton en la barra de tareas.

Mientras esta minimizada se lee por su arbol y se maneja por mensajes; para capturarla se restaura FUERA de pantalla un
instante y vuelve minimizada con su posicion original (asi, al pulsar tu el boton, se abre donde estaba).
"""
import json, os, subprocess, sys, tempfile, threading, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pcsight import core
import numpy as np
from PIL import Image
import uiautomation as auto
import win32gui, win32process

state = os.path.join(tempfile.gettempdir(), "pcsight_taskbar_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
OURS = set()
log = {"samples": 0, "on_screen": 0, "activated": False}
stop = threading.Event()

def sampler():
    while not stop.is_set():
        fg = win32gui.GetForegroundWindow()
        if fg and (win32gui.GetWindowText(fg) == "pcsight-test" or win32process.GetWindowThreadProcessId(fg)[1] in OURS):
            log["activated"] = True
        for h in core.top_windows():
            if win32gui.GetWindowText(h) == "pcsight-test":
                log["samples"] += 1
                if not win32gui.IsIconic(h) and not core.is_hidden(h): log["on_screen"] += 1
        time.sleep(0.003)

def taskbar_has(title):
    for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        h = win32gui.FindWindow(cls, None)
        if not h: continue
        stack = [(auto.ControlFromHandle(h), 0)]
        while stack:
            c, d = stack.pop()
            try:
                if title in (c.Name or ""): return True
                if d < 10: stack.extend((ch, d + 1) for ch in c.GetChildren())
            except Exception: pass
    return False

th = threading.Thread(target=sampler, daemon=True); th.start()
cmd = (f'powershell.exe -STA -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{os.path.join(HERE, "test_app.ps1")}" "{state}" -Taskbar')
checks = {}; r = None
try:
    r = core.open_minimized(cmd, creationflags=0x08000000); OURS.add(r["pid"])
    hw = r["windows"][0]["hwnd"] if r["windows"] else None
    assert hw, f"no aparecio la ventana: {r}"
    time.sleep(0.8)
    rc0 = win32gui.GetWindowPlacement(hw)[4]
    checks["nace minimizada (sin ventana en pantalla)"] = r["minimizada_de_origen"] and win32gui.IsIconic(hw)
    checks["su boton esta en la barra de tareas"] = taskbar_has("pcsight-test")

    lk = core.look(str(hw)); items = core._state[hw]["items"]
    checks["se lee minimizada por el arbol (sin captura)"] = lk["mode"] == "uia" and lk.get("minimized") and "Pulsar" in lk["text"]
    print(f"look minimizada: {lk['mode']} {lk['tokens']} tok | {lk['why']}")
    by = lambda s: next(i for i, x in items.items() if s in x.name)
    btn, edt = by("Pulsar"), by("Campo")
    core.click(str(hw), btn); time.sleep(0.4)
    checks["clic minimizada"] = S()["clicks"] == 1
    core.type_text(str(hw), "hola", target=edt); time.sleep(0.4)
    checks["escritura minimizada"] = S()["text"] == "hola"
    ch = core.changes(str(hw)); print("cambio:", ch)
    checks["solo se envia el cambio"] = "Clicks: 1" in ch

    im = core.look(str(hw), "image")
    img_ok = bool(im.get("image")) and float(np.array(Image.open(im["image"])).std()) > 5
    checks["captura minimizada (restaurada fuera de pantalla)"] = img_ok
    checks["vuelve minimizada"] = bool(win32gui.IsIconic(hw))
    checks["conserva su posicion original (el boton la abre donde estaba)"] = tuple(win32gui.GetWindowPlacement(hw)[4]) == tuple(rc0)
finally:
    stop.set(); th.join()
    if r: subprocess.run(["taskkill", "/PID", str(r["pid"]), "/T", "/F"], capture_output=True, creationflags=0x08000000)
checks["nunca se vio en pantalla"] = log["on_screen"] == 0
checks["nunca activo el foco"] = not log["activated"]
print(f"muestras: {log['samples']} | veces visible en pantalla: {log['on_screen']}")
for k, v in checks.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(checks.values())
print("taskbar verification passed" if ok else "taskbar verification FAILED")
sys.exit(0 if ok else 1)
