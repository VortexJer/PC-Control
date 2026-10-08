"""El cursor naranja de Claude: solo se dibuja si alguien lo ve, deja pasar los clics, nunca toma el foco y va donde debe.

Durante unos segundos aparece de verdad un puntero naranja y luego una barra parpadeante en la pantalla.
"""
import os, sys, threading, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pcsight import cursor
import win32con, win32gui

c = {}
# 1) regla pura: (modo, minimizada, visible_en_pantalla, usuario_en_ella) -> se dibuja?
table = [
    (("user", False, True, True), True, "el usuario esta en la ventana: se ve"),
    (("user", False, True, False), False, "el usuario esta en otra ventana: no se dibuja"),
    (("user", True, True, True), False, "minimizada: no hay nada que mostrar"),
    (("user", False, False, True), False, "fuera de pantalla u oculta"),
    (("always", False, True, False), True, "modo always: siempre que la ventana sea visible"),
    (("always", True, True, True), False, "always tampoco dibuja sobre una ventana minimizada"),
    (("off", False, True, True), False, "desactivado"),
]
for args, want, d in table:
    c[f"regla: {d}"] = cursor.should_show(*args) == want
for val, want in (("", "user"), ("USER", "user"), ("always", "always"), ("off", "off"), ("zzz", "user")):
    if val: os.environ["PCSIGHT_CURSOR"] = val
    else: os.environ.pop("PCSIGHT_CURSOR", None)
    c[f"modo '{val or '(sin definir)'}' -> {want}"] = cursor.mode() == want

# 2) la ventana de verdad
activated = []
stop = threading.Event()
def sampler():
    while not stop.is_set():
        if cursor.CURSOR.hwnd and win32gui.GetForegroundWindow() == cursor.CURSOR.hwnd: activated.append(1)
        time.sleep(0.004)
threading.Thread(target=sampler, daemon=True).start()

def rect(): return win32gui.GetWindowRect(cursor.CURSOR.hwnd)
cursor.CURSOR.pointer(420, 320, click=True, ms=300)
time.sleep(0.9)
h = cursor.CURSOR.hwnd
ex = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
c["clic a traves (WS_EX_TRANSPARENT)"] = bool(ex & 0x20)
c["sin foco (WS_EX_NOACTIVATE)"] = bool(ex & 0x08000000)
c["sin boton en la barra (TOOLWINDOW)"] = bool(ex & 0x80)
c["siempre encima (TOPMOST) y transparente (LAYERED)"] = bool(ex & 0x8) and bool(ex & 0x80000)
c["el puntero llega a donde se pidio"] = abs(rect()[0] + cursor.HOT - 420) <= 2 and abs(rect()[1] + cursor.HOT - 320) <= 2
under = win32gui.WindowFromPoint((420 + 8, 320 + 8))
c["los clics pasan por debajo (no es la ventana del cursor)"] = under != h

cursor.CURSOR.caret(640, 320, h=24, hold=0.8)
time.sleep(0.5)
c["la barra aparece donde se pidio"] = abs(rect()[0] + cursor.HOT - 640) <= 2 and abs(rect()[1] + cursor.HOT - 320) <= 2
time.sleep(1.2)
c["se esconde sola (fuera de pantalla)"] = rect()[0] < -2000
stop.set()
c["nunca tomo el foco"] = not activated

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print("cursor verification passed" if ok else "cursor verification FAILED")
sys.exit(0 if ok else 1)
