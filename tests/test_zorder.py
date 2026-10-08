"""Una app abierta de cero por pcsight nace sin activarse y termina DETRAS de todas las ventanas abiertas.

Lanza la ventana de prueba propia con opacidad 1 (visible) para que el orden z sea real. Muestrea el foco y el
orden z cada ~5 ms durante todo el arranque: no puede cambiar el foco ni el raton, y nunca debe quedar por
delante de otra ventana mientras se lanza (parpadeo).
"""
import os, sys, tempfile, threading, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pcsight import core
import win32api, win32gui

state = os.path.join(tempfile.gettempdir(), "pcsight_zorder_state.json")
fg0, cur0 = win32gui.GetForegroundWindow(), win32api.GetCursorPos()
log = {"fg_changed": False, "cursor_changed": False, "above_others": 0, "samples": 0, "seen": False}
stop = threading.Event()

def sampler():
    while not stop.is_set():
        if win32gui.GetForegroundWindow() != fg0: log["fg_changed"] = True
        if win32api.GetCursorPos() != cur0: log["cursor_changed"] = True
        ws = core.top_windows()
        mine = [h for h in ws if win32gui.GetWindowText(h) == "pcsight-test"]
        if mine:
            log["seen"] = True; log["samples"] += 1
            if ws.index(mine[0]) < len(ws) - 1: log["above_others"] += 1
        time.sleep(0.005)

t = threading.Thread(target=sampler, daemon=True); t.start()
ps1 = os.path.join(HERE, "test_app.ps1")
cmd = f'powershell.exe -STA -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{ps1}" "{state}" -Opacity 1'
r = core.open_app(cmd, creationflags=0x08000000)
time.sleep(0.8)
stop.set(); t.join()
try:
    ws = core.top_windows()
    mine = [h for h in ws if win32gui.GetWindowText(h) == "pcsight-test"]
    assert mine, f"la ventana no aparecio: {r}"
    final_bottom = ws.index(mine[0]) == len(ws) - 1
    print("open_app ->", r)
    print(f"muestras con la ventana visible: {log['samples']} | de ellas por delante de otra: {log['above_others']}")
    print("foco intacto:", not log["fg_changed"], "| raton intacto:", not log["cursor_changed"], "| al fondo del todo:", final_bottom)
    ok = final_bottom and not log["fg_changed"] and not log["cursor_changed"] and log["above_others"] == 0
    print("zorder verification passed" if ok else "zorder verification FAILED")
    sys.exit(0 if ok else 1)
finally:
    import subprocess
    subprocess.run(["taskkill", "/PID", str(r["pid"]), "/T", "/F"], capture_output=True, creationflags=0x08000000)
