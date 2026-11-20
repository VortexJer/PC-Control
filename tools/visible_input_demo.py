"""Visible, nonactivating input regression demo. Only its own controls are touched."""
import ctypes
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import win32api
import win32con as C
import win32gui as G
from pc_control import core
from input_watch import InputWatch

lines = []
points = []
down = False
status = edit = user_edit = canvas = main = 0
report = Path(__file__).resolve().parents[1] / ".goldentokens" / "visible-input.txt"


def canvas_proc(h, msg, w, l):
    global down
    xy = (l & 0xFFFF, (l >> 16) & 0xFFFF)
    if msg == C.WM_LBUTTONDOWN:
        down = True
        points.append(xy)
        return 0
    if msg == C.WM_MOUSEMOVE and down:
        points.append(xy)
        G.InvalidateRect(h, None, True)
        return 0
    if msg == C.WM_LBUTTONUP:
        down = False
        return 0
    if msg == C.WM_PAINT:
        dc, paint = G.BeginPaint(h)
        G.FillRect(dc, G.GetClientRect(h), G.GetStockObject(C.WHITE_BRUSH))
        if points:
            G.MoveToEx(dc, *points[0])
            for p in points[1:]:
                G.LineTo(dc, *p)
        G.EndPaint(h, paint)
        return 0
    return G.DefWindowProc(h, msg, w, l)


def proc(h, msg, w, l):
    if msg == C.WM_CLOSE:
        G.DestroyWindow(h)
        return 0
    if msg == C.WM_DESTROY:
        G.PostQuitMessage(0)
        return 0
    return G.DefWindowProc(h, msg, w, l)


def note(text):
    lines.append(text)
    G.SetWindowText(status, text)
    report.parent.mkdir(exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_checks():
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        try:
            note("VISIBLE: prueba preparada. Tu teclado y raton siguen en tu app.")
            time.sleep(4)
            fg, mouse, tick = G.GetForegroundWindow(), win32api.GetCursorPos(), core._input_tick()
            G.SendMessage(user_edit, 0x00B1, 2, 6)
            before_user = G.GetWindowText(user_edit), core._edit_selection(user_edit)
            ctrl = auto.ControlFromHandle(edit)
            it = core.Item("edit", "Campo de la IA", (0, 0, 500, 30), ctrl=ctrl)
            core._state[main] = {"items": {1: it}, "rect": G.GetWindowRect(main), "scale": 1.0}
            G.SendMessage(edit, 0x00B1, 1, 3)
            result = core.type_text(str(main), " + texto escrito sin foco", target=1)
            assert G.GetWindowText(edit) == "IA: campo propio + texto escrito sin foco", result
            assert core._edit_selection(edit) == (1, 3), f"selection changed: {core._edit_selection(edit)}"
            assert (G.GetWindowText(user_edit), core._edit_selection(user_edit)) == before_user, "user field changed"
            note("OK: escritura directa; seleccion de usuario y de IA conservadas.")
            time.sleep(4)
            left, top = G.GetWindowRect(main)[:2]
            x, y = G.ClientToScreen(canvas, (30, 35))
            result = core.drag(str(main), [(x-left, y-top), (x-left+220, y-top+70)], "rect", delay=0.01)
            time.sleep(0.6)
            assert len(points) > 20 and not down, f"canvas points={len(points)}, held={down}; {result}"
            image = core.grab(main, G.GetWindowRect(main))
            assert image is not None
            cx, cy = G.ClientToScreen(canvas, (0, 0))
            ml, mt = G.GetWindowRect(main)[:2]
            crop = image[cy-mt+15:cy-mt+125, cx-ml+15:cx-ml+280]
            assert (crop.max(axis=2) < 80).sum() > 200, "no painted pixels in canvas"
            image_path = report.parent / "visible-input.png"
            from PIL import Image
            Image.fromarray(image[:, :, ::-1]).save(image_path)
            assert not any(hwnd in (main, edit, user_edit, canvas) for _, hwnd, _ in watch.events), "test controls received keyboard focus"
            lines.append("WinEvent observer: no foreground/focus event targeted the demo controls")
            note("OK: rectangulo dibujado por mensajes; foco del usuario conservado.")
            if core._input_tick() == tick:
                assert G.GetForegroundWindow() == fg, "foreground changed without user input"
                assert win32api.GetCursorPos() == mouse
                lines.append("mouse unchanged; real input timestamp unchanged")
            else:
                lines.append("user input changed during visible demo; initial/final foreground/cursor comparison is inconclusive")
            lines.append("Visible input verification passed")
            report.write_text("\n".join(lines) + "\n", encoding="utf-8")
            time.sleep(14)
        except Exception as e:
            note(f"FAILED: {e}")
            time.sleep(10)
        finally:
            if G.IsWindow(main):
                G.PostMessage(main, C.WM_CLOSE, 0, 0)


if __name__ == "__main__":
    watch = InputWatch().__enter__()
    instance = win32api.GetModuleHandle(None)
    for name, fn in (("PCControlVisibleDemo", proc), ("PCControlVisibleCanvas", canvas_proc)):
        wc = G.WNDCLASS()
        wc.hInstance, wc.lpszClassName, wc.lpfnWndProc = instance, name, fn
        wc.hbrBackground = C.COLOR_WINDOW + 1
        G.RegisterClass(wc)
    sw = win32api.GetSystemMetrics(0)
    main = G.CreateWindowEx(C.WS_EX_NOACTIVATE, "PCControlVisibleDemo",
        "PC-Control - PRUEBA VISIBLE (sin foco)", C.WS_OVERLAPPED | C.WS_CAPTION | C.WS_SYSMENU,
        max(10, sw-730), 70, 700, 410, 0, 0, instance, None)
    def child(cls, text, x, y, width, height, style=0):
        return G.CreateWindow(cls, text, C.WS_CHILD | C.WS_VISIBLE | style, x, y, width, height, main, 0, instance, None)
    status = child("STATIC", "Preparando prueba visible...", 20, 15, 660, 40)
    child("STATIC", "Campo simulado del usuario (no se modifica):", 20, 60, 640, 20)
    user_edit = child("EDIT", "Tu texto y tu seleccion se conservan", 20, 85, 640, 28, C.WS_BORDER)
    child("STATIC", "Campo de la IA (escritura sin activar la ventana):", 20, 125, 640, 20)
    edit = child("EDIT", "IA: campo propio", 20, 150, 640, 28, C.WS_BORDER)
    canvas = child("PCControlVisibleCanvas", "", 20, 200, 640, 140, C.WS_BORDER)
    G.ShowWindow(main, C.SW_SHOWNOACTIVATE)
    G.SetWindowPos(main, C.HWND_TOP, 0, 0, 0, 0, C.SWP_NOMOVE | C.SWP_NOSIZE | C.SWP_NOACTIVATE)
    threading.Thread(target=run_checks, daemon=True).start()
    G.PumpMessages()
    watch.__exit__()
    print("\n".join(lines))
    sys.exit(0 if "Visible input verification passed" in lines else 1)
