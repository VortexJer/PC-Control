"""Reglas de cuando NO se baja una ventana nueva (dialogo de una app tuya): la usas tu, ya estaba arriba...

La ventana de prueba nace FUERA de todos los monitores (nunca se ve) en tu escritorio, para probar el orden z real.
"""
import os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pcsight import core
import win32con, win32gui, win32process

# 1) tabla de la regla pura: (is_window, is_fg, user_in_same_app, already_top, cursor_over, is_new) -> bajar?
table = [
    ((True, False, False, False, False, True), True,  "ventana nueva normal: se baja"),
    ((True, False, False, True, False, True), True,  "ventana NUEVA: nace arriba por fuerza y aun asi se baja"),
    ((False, False, False, False, False, True), False, "ya no existe"),
    ((True, True, False, False, False, True), False, "es la ventana activa"),
    ((True, False, True, False, False, True), False, "el usuario trabaja en otra ventana de esa app"),
    ((True, False, False, True, False, False), False, "existente y ya arriba: no se baja"),
    ((True, False, False, False, True, False), False, "existente con el raton del usuario encima"),
    ((True, False, False, False, True, True), True,  "NUEVA bajo el cursor: no es uso del usuario, se baja"),
]
bad = [d for args, want, d in table if core.should_tuck(*args)[0] != want]
for args, want, d in table:
    assert core.should_tuck(*args)[1], "toda decision explica su motivo"
if bad:
    print("FALLOS en la tabla:", bad); sys.exit(1)

# 2) integracion real con una ventana propia nacida fuera de pantalla
state = os.path.join(tempfile.gettempdir(), "pcsight_tuck_state.json")
cmd = ["powershell.exe", "-STA", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
       "-File", os.path.join(HERE, "test_app.ps1"), state, "-X", "-6000"]
p = subprocess.Popen(cmd, creationflags=0x08000000)
ok = False
try:
    hw = None
    for _ in range(150):
        time.sleep(0.1)
        found = []
        win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.GetWindowText(h) == "pcsight-test"
                             and win32process.GetWindowThreadProcessId(h)[1] == p.pid else None, None)
        if found: hw = found[0]; break
    assert hw, "no aparecio la ventana de prueba"
    time.sleep(0.5)
    assert core.is_hidden(hw), "la ventana de prueba debe nacer fuera de todos los monitores"
    def on_top():
        ws = core.top_windows(); above = ws[:ws.index(hw)]
        return all(win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST for h in above)
    # una ventana NUEVA nace arriba y se baja al fondo
    win32gui.SetWindowPos(hw, win32con.HWND_TOP, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
    went_down = core.tuck(hw, is_new=True); time.sleep(0.2)
    # una ventana que YA estaba arriba (siempre visible) no se baja
    for _ in range(5):
        win32gui.SetWindowPos(hw, win32con.HWND_TOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        time.sleep(0.2)
        if on_top(): break
    assert on_top(), "la prueba no logro subir la ventana"
    moved = core.tuck(hw, is_new=False); time.sleep(0.2)
    stayed = on_top()
    print(f"nueva -> bajo: {went_down} | ya arriba -> tuck devolvio {moved}, sigue arriba: {stayed}")
    ok = went_down and (not moved) and stayed
finally:
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)
print("tuck verification passed" if ok else "tuck verification FAILED")
sys.exit(0 if ok else 1)
