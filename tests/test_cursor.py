"""El cursor naranja de Claude (puntero + barra + cartel): siempre visibles, en reposo, aislados a su app, sin tomar el foco.

Durante unos segundos aparecen de verdad un puntero y una barra naranjas (y un cartel) en la pantalla.
"""
import os, sys, threading, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from pc_control import cursor
import win32api, win32con, win32gui, win32process

c = {}
# ---------- 1) reglas puras ----------
for args, want, d in [
    (("always", False, True, False), True, "always: se ve aunque el usuario este en otra app"),
    (("user", False, True, True), True, "user: el usuario esta en la ventana"),
    (("user", False, True, False), False, "user: el usuario esta en otra ventana"),
    (("always", True, True, True), False, "minimizada: no hay nada que mostrar"),
    (("always", False, False, True), False, "fuera de pantalla u oculta"),
    (("off", False, True, True), False, "desactivado")]:
    c[f"regla: {d}"] = cursor.should_show(*args) == want
for val, want in (("", "always"), ("always", "always"), ("USER", "user"), ("off", "off"), ("zzz", "always")):
    if val: os.environ["PC_CONTROL_CURSOR"] = val
    else: os.environ.pop("PC_CONTROL_CURSOR", None)
    c[f"modo '{val or '(sin definir)'}' -> {want}"] = cursor.mode() == want
os.environ.pop("PC_CONTROL_CURSOR", None)
for args, want, d in [
    (("always", True, False, True, False), False, "always: se queda con el usuario en otra app"),
    (("user", True, False, True, False), True, "user: el usuario salio -> se esconde"),
    (("always", True, True, True, True), True, "minimizada -> se esconde (vuelve al restaurarla)"),
    (("always", False, False, False, False), True, "cerrada -> se esconde"),
    (("always", True, False, False, True), True, "oculta -> se esconde")]:
    c[f"aislado: {d}"] = cursor.should_hide(*args) == want

# tamanos: mayores que antes, adaptados a la linea de texto y al DPI
c["puntero: mas grande que antes (0,78) y no mayor que un puntero grande de Windows"] = 0.78 < cursor.ARROW_K <= 1.1 and max(y for _, y in cursor.ARROW) * cursor.ARROW_K <= 24
c["barra: pagina enorme (Word) -> una linea normal de 22 px"] = cursor.caret_height(900, 1.0) == 22
c["barra: campo de una linea -> ~70% de el"] = cursor.caret_height(24, 1.0) == 17 and cursor.caret_height(40, 1.0) == 28
c["barra: nunca gigante ni diminuta"] = 15 <= cursor.caret_height(1, 1.0) <= 40 and cursor.caret_height(100000, 1.0) <= 40
c["barra: escala con el DPI"] = cursor.caret_height(900, 1.5) > cursor.caret_height(900, 1.0) and cursor.caret_height(900, 2.0) > cursor.caret_height(900, 1.5)
c["escala del sistema entre 0,75 y 3"] = 0.75 <= cursor.scale_for(0) <= 3.0

# color que se adapta al fondo
pal = {name: cursor.palette_for(bg) for name, bg in (("oscuro", (20, 20, 25)), ("medio", (120, 120, 130)), ("claro", (245, 245, 245)), ("naranja", (250, 130, 30)), ("desconocido", None))}
c["color: fondo oscuro -> borde claro"] = pal["oscuro"][1] == cursor.WHITE
c["color: fondo claro -> borde oscuro (se ve sobre blanco)"] = pal["claro"][1] == cursor.DARK and pal["claro"][0] != pal["oscuro"][0]
c["color: fondo naranja -> cambia de tono para no perderse"] = pal["naranja"][0] != cursor.ORANGE and pal["naranja"][0][2] > 200
c["color: sin dato -> el naranja de siempre"] = pal["desconocido"] == (cursor.ORANGE, cursor.WHITE)
c["el dibujo cambia de verdad con el color"] = cursor.render_arrow(1.0, 0, pal["claro"]) != cursor.render_arrow(1.0, 0, pal["oscuro"])
c["la barra es fija: el mismo dibujo siempre (no parpadea)"] = cursor.render_caret(22, 1.0) == cursor.render_caret(22, 1.0)

# todo cabe en su lienzo a cualquier escala del sistema (100 % a 300 %), sin recortes
for s in (0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0):
    bad_clip = []
    for name, data in (("puntero+anillo", cursor.render_arrow(s, 6)), ("puntero", cursor.render_arrow(s, 0)),
                       ("barra", cursor.render_caret(cursor.caret_height(900, s), s)), ("barra de campo", cursor.render_caret(cursor.caret_height(30, s), s))):
        a = np.frombuffer(data, dtype=np.uint8).reshape(cursor.SIZE, cursor.SIZE, 4)[..., 3]
        if a[0].any() or a[-1].any() or a[:, 0].any() or a[:, -1].any(): bad_clip.append(name)
    c[f"sin recortes a escala {int(s * 100)}%"] = not bad_clip
msg = "Si tocas esta aplicacion, Claude no podra actuar en ella. Dejala como estaba (1928x1168) y no la toques mas."
sizes = {}
for s in (1.0, 1.5, 2.0):
    data, nw, nh = cursor.render_note(msg, s); sizes[s] = (nw, nh)
    a = np.frombuffer(data, dtype=np.uint8).reshape(nh, nw, 4)[..., 3]
    c[f"cartel a escala {int(s * 100)}%: coherente y con contenido"] = len(data) == nw * nh * 4 and a.max() >= 240 and a[0, 0] <= 20
c["cartel: crece con la escala y ajusta el texto en lineas"] = sizes[1.5][1] > sizes[1.0][1] and sizes[1.0][0] <= 520 and sizes[1.0][1] >= 40

# ---------- 2) las ventanas de verdad ----------
activated = []
stop = threading.Event()
def sampler():
    while not stop.is_set():
        fg = win32gui.GetForegroundWindow()
        if fg and win32process.GetWindowThreadProcessId(fg)[1] == os.getpid(): activated.append(win32gui.GetClassName(fg))
        time.sleep(0.004)
threading.Thread(target=sampler, daemon=True).start()

wc = win32gui.WNDCLASS(); wc.lpszClassName = "PC-Control-TestOwner"; wc.hInstance = win32api.GetModuleHandle(None)
wc.lpfnWndProc = lambda hh, m, w, l: win32gui.DefWindowProc(hh, m, w, l)
try: win32gui.RegisterClass(wc)
except Exception: pass
def new_owner(): return win32gui.CreateWindowEx(0x08000080, "PC-Control-TestOwner", "pc-control-owner", 0x90000000, -4000, -4000, 200, 120, 0, 0, wc.hInstance, None)
def wait(sec):
    t_end = time.time() + sec
    while time.time() < t_end:
        win32gui.PumpWaitingMessages(); time.sleep(0.01)
H = lambda k: cursor.CURSOR.hwnds[k]
def vis(k): return bool(win32gui.IsWindowVisible(H(k)))
def rect(k): return win32gui.GetWindowRect(H(k))

owner = new_owner()
try:
    os.environ["PC_CONTROL_CURSOR"] = "always"
    cursor.CURSOR.pointer(420, 320, click=True, ms=300, scale=1.0, owner=owner)
    cursor.CURSOR.caret(640, 320, h=cursor.caret_height(900, 1.0), scale=1.0, owner=owner)
    wait(1.0)
    c["puntero y barra se ven A LA VEZ (cada uno en su capa)"] = vis("pointer") and vis("caret")
    for k in ("pointer", "caret"):
        ex = win32gui.GetWindowLong(H(k), win32con.GWL_EXSTYLE)
        c[f"{k}: clic a traves, sin foco, sin boton en la barra, transparente, SIN topmost"] = bool(ex & 0x20) and bool(ex & 0x08000000) and bool(ex & 0x80) and bool(ex & 0x80000) and not (ex & 0x8)
        c[f"{k}: es propiedad de la ventana a la que actua (aislado)"] = win32gui.GetWindow(H(k), 4) == owner       # GW_OWNER
    r = rect("pointer"); c["el puntero llega a donde se pidio"] = abs(r[0] + cursor.HOT - 420) <= 2 and abs(r[1] + cursor.HOT - 320) <= 2
    r = rect("caret"); c["la barra esta donde se pidio"] = abs(r[0] + cursor.HOT - 640) <= 2 and abs(r[1] + cursor.HOT - 320) <= 2
    c["los clics pasan por debajo (el puntero no recibe el raton)"] = win32gui.WindowFromPoint((420 + 8, 320 + 8)) != H("pointer")

    # EN REPOSO: siguen visibles mucho despues de acabar la accion (antes se iban a los ~1,3 s)
    wait(3.5)
    c["EN REPOSO: puntero y barra siguen visibles pasados 4 s sin actuar"] = vis("pointer") and vis("caret")
    r = rect("pointer"); c["en reposo el puntero se queda quieto en su ultima posicion"] = abs(r[0] + cursor.HOT - 420) <= 2
    # la barra se desliza a otra posicion y queda en reposo alli
    cursor.CURSOR.caret(700, 360, h=22, scale=1.0, owner=owner); wait(1.0)
    r = rect("caret"); c["la barra se mueve a su nueva posicion y queda alli"] = abs(r[0] + cursor.HOT - 700) <= 2 and abs(r[1] + cursor.HOT - 360) <= 2 and vis("caret")

    # ocultar/minimizar la app SOLO los esconde; al volver reaparecen donde estaban
    win32gui.ShowWindow(owner, 0); wait(0.8)
    c["app oculta/minimizada: puntero y barra se esconden"] = not vis("pointer") and not vis("caret")
    win32gui.ShowWindow(owner, 8); wait(0.8)                                       # SW_SHOWNA: vuelve sin activarse
    c["al volver la app, REAPARECEN donde estaban"] = vis("pointer") and vis("caret") and abs(rect("pointer")[0] + cursor.HOT - 420) <= 2

    # CERRAR la app los hace desaparecer del todo (y no vuelven)
    win32gui.DestroyWindow(owner); wait(1.0)
    c["app cerrada: desaparecen"] = not vis("pointer") and not vis("caret")
    wait(1.2)
    c["app cerrada: no vuelven a aparecer"] = not vis("pointer") and not vis("caret")

    # visibilidad CONTINUA en modo user: el usuario entra y sale (se simula sin activar ninguna ventana)
    owner = new_owner()
    presence = {"on": False}
    real, cursor.user_is_on = cursor.user_is_on, (lambda o: presence["on"])
    os.environ["PC_CONTROL_CURSOR"] = "user"
    try:
        cursor.CURSOR.pointer(420, 320, click=False, ms=200, owner=owner); cursor.CURSOR.caret(640, 320, h=22, owner=owner); wait(0.6)
        c["user: la accion empieza y el usuario NO esta -> no se ve"] = not vis("pointer") and not vis("caret")
        presence["on"] = True; wait(0.6)
        c["user: el usuario entra DESPUES de empezar -> aparecen"] = vis("pointer") and vis("caret")
        presence["on"] = False; wait(0.6)
        c["user: el usuario se va a otra app -> desaparecen"] = not vis("pointer") and not vis("caret")
        presence["on"] = True; wait(0.6)
        c["user: vuelve -> reaparecen"] = vis("pointer") and vis("caret")
    finally:
        cursor.user_is_on = real
    os.environ["PC_CONTROL_CURSOR"] = "always"

    # el cartel: aparece centrado, dura lo que se pide y se va; esta aislado igual
    cursor.CURSOR.note("Si tocas esta aplicacion, Claude no podra actuar en ella.", 700, 400, hold=1.6, scale=1.0, owner=owner); wait(0.7)
    r = rect("note"); c["cartel: visible, con tamano de cartel y centrado donde se pidio"] = vis("note") and (r[2] - r[0]) > 150 and abs((r[0] + r[2]) // 2 - 700) <= 3 and r[1] == 400
    c["cartel: es propiedad de la app (aislado)"] = win32gui.GetWindow(H("note"), 4) == owner
    wait(1.6)
    c["cartel: se va solo al acabar su tiempo"] = not vis("note")
    c["... pero el puntero y la barra siguen en reposo"] = vis("pointer") and vis("caret")
    cursor.CURSOR.hide(); wait(0.3)
    c["hide(): quita todo al instante"] = not vis("pointer") and not vis("caret") and not vis("note")
finally:
    os.environ.pop("PC_CONTROL_CURSOR", None)
    try: win32gui.DestroyWindow(owner)
    except Exception: pass
stop.set()
c["NUNCA se activo ninguna ventana de la prueba (ni las del cursor ni las de apoyo)"] = not activated
if activated: print("ventanas que tomaron el foco:", sorted(set(activated)))

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print("cursor verification passed" if ok else "cursor verification FAILED")
sys.exit(0 if ok else 1)
