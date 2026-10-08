"""Reglas de cuando NO se baja una ventana al fondo: la usa el usuario, ya esta arriba, etc."""
import os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pcsight import core
import win32con, win32gui

# 1) tabla de la regla pura: (is_window, is_fg, user_in_same_app, already_top, cursor_over) -> bajar?
table = [
    ((True, False, False, False, False), True,  "ventana nueva normal: se baja"),
    ((False, False, False, False, False), False, "ya no existe"),
    ((True, True, False, False, False), False, "es la ventana activa"),
    ((True, False, True, False, False), False, "el usuario trabaja en otra ventana de esa app"),
    ((True, False, False, True, False), False, "ya esta arriba"),
    ((True, False, False, False, True), False, "el raton del usuario esta encima"),
]
bad = [d for args, want, d in table if core.should_tuck(*args)[0] != want]
for args, want, d in table:
    assert core.should_tuck(*args)[1], "toda decision explica su motivo"
if bad:
    print("FALLOS en la tabla:", bad); sys.exit(1)

# 2) integracion real con una ventana propia
state = os.path.join(tempfile.gettempdir(), "pcsight_tuck_state.json")
cmd = f'powershell.exe -STA -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{os.path.join(HERE, "test_app.ps1")}" "{state}"'
r = core.open_app(cmd, creationflags=0x08000000)
try:
    hw = r["windows"][0]["hwnd"]; time.sleep(0.4)
    def on_top():
        ws = core.top_windows(); above = ws[:ws.index(hw)]
        return all(win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST for h in above)
    assert not on_top(), "tras open_app debe estar al fondo (control positivo)"
    # se sube SIN activarla (solo la ventana propia de la prueba)
    win32gui.SetWindowPos(hw, win32con.HWND_TOP, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
    time.sleep(0.2)
    assert on_top(), "la prueba no logro subir la ventana"
    moved = core.tuck(hw); time.sleep(0.2)
    stayed = on_top()
    print(f"ya estaba arriba -> tuck devolvio {moved}, sigue arriba: {stayed}")
    ok = (not moved) and stayed
finally:
    subprocess.run(["taskkill", "/PID", str(r["pid"]), "/T", "/F"], capture_output=True, creationflags=0x08000000)
print("tuck verification passed" if ok else "tuck verification FAILED")
sys.exit(0 if ok else 1)
