"""El usuario puede mover o cambiar el tamano de la ventana a mitad de una accion y todo sigue funcionando.

La ventana de prueba vive fuera de pantalla (nunca se ve). Tiene un boton anclado abajo a la derecha: al redimensionar, se recoloca.
"""
import json, os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pcsight import core
import uiautomation as auto
import win32con, win32gui, win32process

state = os.path.join(tempfile.gettempdir(), "pcsight_resize_state.json")
S = lambda: json.load(open(state, encoding="utf-8-sig"))
cmd = ["powershell.exe", "-STA", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
       "-File", os.path.join(HERE, "test_app.ps1"), state, "-X", "-6000"]
p = subprocess.Popen(cmd, creationflags=0x08000000)
c = {}
def move_resize(hw, x=None, y=None, w=None, h=None):
    l, t, r, b = win32gui.GetWindowRect(hw)
    win32gui.SetWindowPos(hw, 0, l if x is None else x, t if y is None else y, (r - l) if w is None else w, (b - t) if h is None else h,
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
    time.sleep(0.5)
try:
    hw = None
    for _ in range(150):
        time.sleep(0.1)
        f = []
        win32gui.EnumWindows(lambda h, _: f.append(h) if win32gui.GetWindowText(h) == "pcsight-test" and win32process.GetWindowThreadProcessId(h)[1] == p.pid else None, None)
        if f: hw = f[0]; break
    assert hw, "no aparecio la ventana de prueba"
    time.sleep(0.6)
    with auto.UIAutomationInitializerInThread():
        core.look(str(hw)); items = core._state[hw]["items"]
        anc = next(i for i, x in items.items() if x.name == "Anclado")
        it = items[anc]
        rel0 = core.live_rel(hw, it); stale_cx, stale_cy = (rel0[0] + rel0[2]) // 2, (rel0[1] + rel0[3]) // 2
        size0 = core._size(win32gui.GetWindowRect(hw))

        # 1) mover la ventana: la posicion relativa sigue valiendo; la de pantalla se desplaza con ella
        l0, t0 = win32gui.GetWindowRect(hw)[:2]
        move_resize(hw, x=-5200, y=300)
        rel1 = core.live_rel(hw, it)
        sx0 = it.ctrl.BoundingRectangle.left
        c["mover: la posicion relativa se mantiene"] = rel1 == rel0
        c["mover: la de pantalla se desplaza con la ventana"] = sx0 == win32gui.GetWindowRect(hw)[0] + rel0[0]
        n = S()["anchored"]; core._post_click(hw, stale_cx, stale_cy); time.sleep(0.4)
        c["mover: un clic por coordenadas sigue acertando"] = S()["anchored"] == n + 1

        # 2) cambiar el tamano: los controles se recolocan; la posicion guardada ya no vale, la de en vivo si
        move_resize(hw, w=size0[0] + 300, h=size0[1] + 200)
        rel2 = core.live_rel(hw, it)
        c["redimensionar: el boton anclado se movio de verdad"] = rel2 is not None and rel2 != rel0 and rel2[0] > rel0[0] + 200 and rel2[1] > rel0[1] + 120
        n = S()["anchored"]; core._post_click(hw, stale_cx, stale_cy); time.sleep(0.4)
        c["redimensionar: la posicion VIEJA ya no acierta (control: justifica la correccion)"] = S()["anchored"] == n
        n = S()["anchored"]; core._post_click(hw, (rel2[0] + rel2[2]) // 2, (rel2[1] + rel2[3]) // 2); time.sleep(0.4)
        c["redimensionar: la posicion EN VIVO acierta"] = S()["anchored"] == n + 1

        # 3) un clic por coordenadas de una imagen anterior se rechaza con un mensaje claro en vez de caer en otro sitio
        n = S()["anchored"]
        msg = core.click(str(hw), (stale_cx, stale_cy)); time.sleep(0.3)
        c["redimensionar: coordenadas de una imagen vieja se rechazan"] = "cambio de tamano" in msg and S()["anchored"] == n
        # 4) un elemento de OCR (sin control) solo vale si la ventana mide lo mismo que al mirar
        ocr_item = core.Item("ocr", "Anclado", rel0, None, "ocr", True)
        c["redimensionar: elemento de OCR obsoleto -> None"] = core.live_rel(hw, ocr_item) is None
        core.look(str(hw))                                                       # mirar de nuevo refresca todo
        items2 = core._state[hw]["items"]; anc2 = next(i for i, x in items2.items() if x.name == "Anclado")
        c["tras mirar de nuevo, el elemento de OCR vuelve a valer"] = core.live_rel(hw, core.Item("ocr", "x", rel2, None, "ocr", True)) == rel2
        # 5) click por id sigue funcionando tras cambiar el tamano (por el control, no por coordenadas)
        n = S()["anchored"]; core.click(str(hw), anc2); time.sleep(0.4)
        c["click por id tras redimensionar"] = S()["anchored"] == n + 1
        # 6) hacerla pequena: los controles que no caben dejan de ser accesibles -> mensaje claro, no un clic perdido
        move_resize(hw, w=size0[0] - 220, h=size0[1] - 120)
        rel3 = core.live_rel(hw, items2[anc2])
        c["ventana pequena: el elemento se resuelve en vivo o se avisa (nunca coordenadas viejas)"] = rel3 is None or rel3 != rel0
finally:
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values()) and len(c) >= 11
print("resize verification passed" if ok else "resize verification FAILED")
sys.exit(0 if ok else 1)
