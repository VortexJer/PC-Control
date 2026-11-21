"""A plain Win32 window that logs every input message it receives, with the key state its own thread sees at that moment.

Used by the independence test: one copy plays the user's window (foreground, receives the user's real input), another is
Claude's target (background, receives PC-Control's messages). Each line of the log is JSON:
{"m": "char"|"keydown"|"lbuttondown"|"lbuttonup"|"mousemove"|"wheel", "w": wParam, "x":, "y":, "shift": bool, "ctrl": bool, "lbutton": bool}
Usage: python input_probe_app.py <title> <logfile> [x y w h]
"""
import ctypes, ctypes.wintypes as W, json, sys

title, log = sys.argv[1], sys.argv[2]
x, y, w, h = (int(v) for v in sys.argv[3:7]) if len(sys.argv) >= 7 else (100, 100, 420, 300)
u, k = ctypes.windll.user32, ctypes.windll.kernel32
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]; u.DefWindowProcW.restype = ctypes.c_ssize_t
out = open(log, "a", encoding="utf-8", buffering=1)
NAMES = {0x0102: "char", 0x0100: "keydown", 0x0101: "keyup", 0x0201: "lbuttondown", 0x0202: "lbuttonup", 0x0200: "mousemove",
         0x020A: "wheel", 0x0204: "rbuttondown", 0x0007: "setfocus", 0x0006: "activate"}


def proc(hwnd, msg, wp, lp):
    name = NAMES.get(msg)
    if name:
        rec = {"m": name, "w": int(wp), "x": ctypes.c_short(lp & 0xFFFF).value, "y": ctypes.c_short((lp >> 16) & 0xFFFF).value,
               "shift": bool(u.GetKeyState(0x10) & 0x8000), "ctrl": bool(u.GetKeyState(0x11) & 0x8000),
               "lbutton": bool(u.GetKeyState(0x01) & 0x8000)}
        out.write(json.dumps(rec) + "\n")
    if msg == 0x0002:
        u.PostQuitMessage(0)
    return u.DefWindowProcW(hwnd, msg, wp, lp)


cb = WNDPROC(proc)


class WC(ctypes.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", W.HINSTANCE), ("hIcon", W.HICON), ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]


wc = WC(0, cb, 0, 0, k.GetModuleHandleW(None), None, u.LoadCursorW(None, 32512), ctypes.c_void_p(6), None, "PccInputProbe")
u.RegisterClassW(ctypes.byref(wc))
u.CreateWindowExW.restype = W.HWND
u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
hwnd = u.CreateWindowExW(0, "PccInputProbe", title, 0x00CF0000, x, y, w, h, None, None, wc.hInstance, None)
u.ShowWindow(hwnd, 4)                       # SW_SHOWNOACTIVATE: appearing never takes the user's keyboard
out.write(json.dumps({"m": "ready", "hwnd": int(hwnd)}) + "\n")
msg = W.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
