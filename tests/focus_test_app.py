"""Visible disposable adversarial app: deliberately tries to activate/focus itself.
Only run under the pre-activation guard from test_focus_guard.py.
"""
import ctypes
import json
from pathlib import Path
import sys
import time
import win32api
import win32con as C
import win32gui as G

state = Path(sys.argv[1])
dll = ctypes.WinDLL(sys.argv[2])
dll.pc_focus_blocked.argtypes = [ctypes.c_int]
dll.pc_focus_blocked.restype = ctypes.c_long
attempts = 0
label = edit = other = popup = 0


def proc(h, msg, w, l):
    global attempts
    if msg == C.WM_APP + 7:
        attempts = 0
        ctypes.windll.user32.SetTimer(h, 1, 100, None)
        return 0
    if msg == C.WM_TIMER:
        attempts += 1
        ctypes.windll.user32.SetForegroundWindow(h)
        ctypes.windll.user32.SetActiveWindow(h)
        ctypes.windll.user32.SetFocus(edit)
        value = {"hwnd": h, "edit": edit, "other": other, "popup": popup, "attempts": attempts,
                 "foreground": G.GetForegroundWindow() == h,
                 "blocked_activation": dll.pc_focus_blocked(0), "blocked_focus": dll.pc_focus_blocked(1)}
        state.write_text(json.dumps(value), encoding="utf-8")
        G.SetWindowText(label, f"Intentos de activacion: {attempts}. Bloqueados ANTES del foco: "
                            f"{value['blocked_activation']} / {value['blocked_focus']}")
        if attempts >= 24:
            ctypes.windll.user32.KillTimer(h, 1)
        return 0
    if msg == C.WM_CLOSE:
        G.DestroyWindow(h)
        return 0
    if msg == C.WM_DESTROY:
        G.PostQuitMessage(0)
        return 0
    return G.DefWindowProc(h, msg, w, l)


wc = G.WNDCLASS()
wc.hInstance = win32api.GetModuleHandle(None)
wc.lpszClassName = "PCControlFocusAdversary"
wc.lpfnWndProc = proc
wc.hbrBackground = C.COLOR_WINDOW + 1
G.RegisterClass(wc)
hidden_owner = G.CreateWindow(wc.lpszClassName, "", C.WS_POPUP,
                              0, 0, 1, 1, 0, 0, wc.hInstance, None)
h = G.CreateWindow(wc.lpszClassName, "PC-Control - prueba VISIBLE del bloqueo de foco", C.WS_OVERLAPPEDWINDOW,
                   60, 80, 740, 230, hidden_owner, 0, wc.hInstance, None)
label = G.CreateWindow("STATIC", "Preparando intentos de activacion...", C.WS_CHILD | C.WS_VISIBLE,
                       20, 25, 690, 45, h, 0, wc.hInstance, None)
edit = G.CreateWindow("EDIT", "Campo propio de prueba", C.WS_CHILD | C.WS_VISIBLE | C.WS_BORDER,
                      20, 90, 680, 30, h, 0, wc.hInstance, None)
G.ShowWindow(h, C.SW_SHOW)
other = G.CreateWindow(wc.lpszClassName, "Otra ventana del MISMO proceso", C.WS_OVERLAPPEDWINDOW,
                       820, 80, 390, 160, hidden_owner, 0, wc.hInstance, None)
G.ShowWindow(other, C.SW_SHOWNOACTIVATE)
popup = G.CreateWindow(wc.lpszClassName, "Dialogo propio de la primera ventana", C.WS_POPUP | C.WS_CAPTION,
                       820, 260, 390, 100, h, 0, wc.hInstance, None)
G.ShowWindow(popup, C.SW_SHOWNOACTIVATE)
ctypes.windll.user32.SetTimer(h, 1, 200, None)
G.PumpMessages()
