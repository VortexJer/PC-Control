"""input_monitor: logs, every 15 ms, everything that could block the user's input (focus changes, new windows,
the desktop receiving input, mouse clipping, captures, menus).

Usage:  python tools/input_monitor.py output.log stop.flag     (it stops when the file stop.flag is created)
Useful to check that PC-Control does not steal your focus or input: launch it, use PC-Control, and review the log.
"""
import ctypes, ctypes.wintypes as wt, json, os, sys, time
import win32api, win32con, win32gui, win32process

OUT, STOP = sys.argv[1], sys.argv[2]
u = ctypes.WinDLL("user32", use_last_error=True)
u.OpenInputDesktop.restype = wt.HANDLE
u.GetUserObjectInformationW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]

class GTI(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND),
                ("hwndMenuOwner", wt.HWND), ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT)]

def exe_of(pid):
    k = ctypes.windll.kernel32; h = k.OpenProcess(0x1000, False, pid)
    if not h: return "?"
    try:
        buf = ctypes.create_unicode_buffer(520); n = wt.DWORD(520)
        k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)); return os.path.basename(buf.value).lower()
    finally: k.CloseHandle(h)

def desc(h):
    try:
        pid = win32process.GetWindowThreadProcessId(h)[1]
        return {"hwnd": h, "cls": win32gui.GetClassName(h)[:30], "title": win32gui.GetWindowText(h)[:30], "exe": exe_of(pid)}
    except Exception: return {"hwnd": h}

log = open(OUT, "w", encoding="utf-8")
def ev(kind, **kw):
    log.write(json.dumps({"t": round(time.time() - T0, 3), "ev": kind, **kw}, ensure_ascii=False) + "\n"); log.flush()

T0 = time.time(); ev("start")
last_fg = None; known = {}; last_desk = None; last_clip = None; last_flags = None; t_slow = 0
while not os.path.exists(STOP):
    t_loop = time.time()
    fg = win32gui.GetForegroundWindow()
    if fg != last_fg:
        ev("focus", **desc(fg)); last_fg = fg
    cur = {}
    def cb(h, _):
        if win32gui.IsWindowVisible(h): cur[h] = None
    win32gui.EnumWindows(cb, None)
    for h in cur:
        if h not in known:
            d = desc(h)
            try: d["ex"] = hex(win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)); d["rect"] = win32gui.GetWindowRect(h)
            except Exception: pass
            ev("new window", **d)
    for h in list(known):
        if h not in cur: ev("window closed", **known[h])
    known = {h: known.get(h) or desc(h) for h in cur}
    # desktop that receives input
    hd = u.OpenInputDesktop(0, False, 0x0001)
    if hd:
        buf = ctypes.create_unicode_buffer(64); need = wt.DWORD()
        u.GetUserObjectInformationW(hd, 2, buf, 128, ctypes.byref(need)); desk = buf.value; u.CloseDesktop(hd)
    else: desk = "(no access)"
    if desk != last_desk: ev("input desktop", name=desk); last_desk = desk
    # cursor clipping and input states
    r = wt.RECT(); u.GetClipCursor(ctypes.byref(r)); clip = (r.left, r.top, r.right, r.bottom)
    if clip != last_clip: ev("mouse clip", rect=clip); last_clip = clip
    g = GTI(); g.cbSize = ctypes.sizeof(GTI)
    if u.GetGUIThreadInfo(0, ctypes.byref(g)):
        fl = (g.flags, bool(g.hwndCapture), bool(g.hwndMoveSize), bool(g.hwndMenuOwner))
        if fl != last_flags: ev("input state", flags=g.flags, capture=bool(g.hwndCapture), moving=bool(g.hwndMoveSize), menu=bool(g.hwndMenuOwner), **{"capturer": desc(g.hwndCapture) if g.hwndCapture else None}); last_flags = fl
    dt = time.time() - t_loop
    if dt > 0.25: ev("slow monitor", sec=round(dt, 2))      # if the system is slow, that shows too
    time.sleep(0.015)
ev("end")
