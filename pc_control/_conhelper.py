"""Talks to the console of ANOTHER process: write keys into its input buffer, read its screen, read its title.

Run as a short-lived process (`python -m pc_control._conhelper`, JSON in on stdin, JSON out on stdout). A process can be attached to ONE console
at a time and the server must keep its own, so each operation attaches, does its job and exits.

Why this and not keystrokes: the keys go straight into the console's input buffer, which is where the shell reads them. They never pass through
the keyboard, the mouse or the foreground window, so the user keeps working untouched. It works for classic console windows and for the
pseudo console behind Windows Terminal tabs.
"""
import ctypes, ctypes.wintypes as wt, json, sys, time

k = ctypes.WinDLL("kernel32", use_last_error=True)
GENERIC_RW, SHARE_RW, OPEN_EXISTING = 0xC0000000, 3, 3
LEFT_CTRL = 0x0008


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short), ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]


class CSBI(ctypes.Structure):
    _fields_ = [("size", COORD), ("cursor", COORD), ("attr", wt.WORD), ("win", SMALL_RECT), ("maxsize", COORD)]


class KEY(ctypes.Structure):
    _fields_ = [("down", wt.BOOL), ("repeat", wt.WORD), ("vk", wt.WORD), ("scan", wt.WORD), ("char", wt.WCHAR), ("state", wt.DWORD)]


class REC(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("key", KEY), ("pad", ctypes.c_byte * 16)]
    _anonymous_ = ("u",)
    _fields_ = [("kind", wt.WORD), ("u", _U)]


k.CreateFileW.restype = wt.HANDLE
k.GetConsoleScreenBufferInfo.argtypes = [wt.HANDLE, ctypes.POINTER(CSBI)]
k.ReadConsoleOutputCharacterW.argtypes = [wt.HANDLE, wt.LPWSTR, wt.DWORD, COORD, ctypes.POINTER(wt.DWORD)]
k.WriteConsoleInputW.argtypes = [wt.HANDLE, ctypes.POINTER(REC), wt.DWORD, ctypes.POINTER(wt.DWORD)]

# named keys: (virtual key, character, control state)
KEYS = {"enter": (0x0D, "\r", 0), "tab": (0x09, "\t", 0), "esc": (0x1B, "\x1b", 0), "backspace": (0x08, "\b", 0), "space": (0x20, " ", 0),
        "up": (0x26, "", 0), "down": (0x28, "", 0), "left": (0x25, "", 0), "right": (0x27, "", 0), "home": (0x24, "", 0), "end": (0x23, "", 0),
        "pgup": (0x21, "", 0), "pgdn": (0x22, "", 0), "delete": (0x2E, "", 0)}


def _handle(name):
    h = k.CreateFileW(name, GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, 0, None)
    if not h or h == wt.HANDLE(-1).value:
        raise OSError(f"cannot open {name} (error {ctypes.get_last_error()})")
    return h


def _records(text):
    """Key events for a text: a line break is Enter, everything else is a character (UTF-16 units, so any symbol works)."""
    out = []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for ch in text:
        if ch == "\n":
            vk, c = 0x0D, "\r"
        else:
            vk, c = 0, ch
        for unit in (c.encode("utf-16-le")[i:i + 2].decode("utf-16-le", "surrogatepass") for i in range(0, len(c.encode("utf-16-le")), 2)):
            for down in (1, 0):
                r = REC(); r.kind = 1; r.key = KEY(down, 1, vk, 0, unit, 0); out.append(r)
    return out


def _write(records):
    h = _handle("CONIN$")
    arr = (REC * len(records))(*records); n = wt.DWORD(0)
    if not k.WriteConsoleInputW(h, arr, len(records), ctypes.byref(n)):
        raise OSError(f"WriteConsoleInput failed (error {ctypes.get_last_error()})")
    return n.value


def _title():
    buf = ctypes.create_unicode_buffer(1024)
    k.GetConsoleTitleW(buf, 1024)
    return buf.value


def _screen(max_rows):
    h = _handle("CONOUT$")
    info = CSBI()
    if not k.GetConsoleScreenBufferInfo(h, ctypes.byref(info)):
        raise OSError("cannot read the console buffer")
    width, last = info.size.X, info.win.Bottom
    first = max(0, last - max_rows + 1)
    rows = []
    for y in range(first, last + 1):
        buf = ctypes.create_unicode_buffer(width + 1); got = wt.DWORD(0)
        k.ReadConsoleOutputCharacterW(h, buf, width, COORD(0, y), ctypes.byref(got))
        rows.append(buf.value.rstrip())
    while rows and not rows[-1]:
        rows.pop()
    return {"rows": rows, "width": width, "cursor": [info.cursor.X, info.cursor.Y], "first_row": first,
            "win": [info.win.Left, info.win.Top, info.win.Right, info.win.Bottom]}               # the visible part of the buffer: where the cursor is ON SCREEN


def _attach(pid):
    k.FreeConsole()
    if not k.AttachConsole(pid):
        err = ctypes.get_last_error()
        raise OSError({5: "access denied (the program runs elevated, as administrator)", 6: "invalid handle", 87: "no such process or it has no console"}.get(err, f"AttachConsole error {err}"))


def run(req):
    op, pid = req["op"], int(req.get("pid", 0))
    if op == "infos":                                        # many processes in ONE run: pid -> {console title, console window} (None if it has no console)
        out = {}
        for p in req["pids"]:
            try:
                _attach(int(p)); out[str(p)] = {"title": _title(), "window": int(k.GetConsoleWindow() or 0)}
            except OSError:
                out[str(p)] = None
            finally:
                k.FreeConsole()
        return {"infos": out}
    _attach(pid)
    try:
        if op == "type":                                         # one attach: where the cursor was, the keys, then what the console shows
            before = _screen(1)["cursor"]
            n = _write(_records(req["text"]))
            time.sleep(float(req.get("settle", 0.25)))
            return {"written": n, "before": before, **_screen(int(req.get("rows", 40)))}
        if op == "key":
            name = req["name"].lower()
            if name == "ctrl+c":                                     # a real Ctrl+C for whatever runs in that console
                k.SetConsoleCtrlHandler(None, True)              # this helper must not die from it
                ok = k.GenerateConsoleCtrlEvent(0, 0)
                time.sleep(0.3)
                return {"written": 1 if ok else 0, **_screen(int(req.get("rows", 20)))}
            if name not in KEYS:
                raise OSError(f"unknown key '{name}'")
            vk, c, st = KEYS[name]
            recs = []
            for down in (1, 0):
                r = REC(); r.kind = 1; r.key = KEY(down, 1, vk, 0, c or "\x00", st); recs.append(r)
            before = _screen(1)["cursor"]
            n = _write(recs)
            time.sleep(0.2)
            return {"written": n, "before": before, **_screen(int(req.get("rows", 20)))}
        if op == "read":
            return {**_screen(int(req.get("rows", 200))), "title": _title()}
        if op == "title":
            return {"title": _title()}
        if op == "info":                                         # which window this console is shown in (classic consoles: that window itself)
            return {"title": _title(), "window": int(k.GetConsoleWindow() or 0)}
        raise OSError(f"unknown operation '{op}'")
    finally:
        k.FreeConsole()


if __name__ == "__main__":
    try:
        sys.stdout.write(json.dumps(run(json.loads(sys.stdin.read())), ensure_ascii=False))
    except Exception as e:
        sys.stdout.write(json.dumps({"error": f"{e.__class__.__name__}: {e}"}, ensure_ascii=False))
