"""Scintilla editors (Notepad++, SciTE, Notepad2/Notepad3, Code::Blocks, FileZilla/TortoiseGit/SQL-tool editors...): UI Automation sees
nothing inside them, but Scintilla has its own message API. PC-Control uses it to read, write and scroll the document WITHOUT keyboard,
mouse or focus: text goes through SCI_APPENDTEXT/SCI_SETTEXT (the user's caret and selection are not moved), reading is SCI_GETTEXT and
scrolling is SCI_LINESCROLL. Strings cross the process boundary through a buffer allocated inside the editor's process.
Docs: https://www.scintilla.org/ScintillaDoc.html
"""
import ctypes, ctypes.wintypes as W
import win32gui, win32process

CLASSES = ("Scintilla", "ScintillaWindow")
SCI_GETLENGTH, SCI_GETTEXT, SCI_SETTEXT, SCI_APPENDTEXT = 2006, 2182, 2181, 2282
SCI_GETCODEPAGE, SCI_GETREADONLY, SCI_LINESCROLL, SCI_GETFIRSTVISIBLELINE = 2137, 2140, 2168, 2152
SCI_GETLINECOUNT, SCI_LINESONSCREEN, SCI_BEGINUNDOACTION, SCI_ENDUNDOACTION = 2154, 2370, 2078, 2079
SCI_GETEOLMODE, SCI_SETFIRSTVISIBLELINE, SCI_GETCURRENTPOS, SCI_GETANCHOR = 2030, 2613, 2008, 2009

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = W.HANDLE
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD, W.DWORD]
k32.VirtualFreeEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD]
k32.WriteProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT, ctypes.POINTER(ctypes.c_size_t)]
u32.SendMessageTimeoutW.restype = W.LPARAM


def send(h, msg, wp=0, lp=0, timeout=4000):
    """SendMessage with a timeout: a hung editor never freezes the server. None if it did not answer."""
    res = ctypes.c_size_t()
    if not u32.SendMessageTimeoutW(h, msg, wp, lp, 0x0002, timeout, ctypes.byref(res)):        # SMTO_ABORTIFHUNG
        return None
    return res.value


def editors(hwnd):
    """The VISIBLE Scintilla controls inside a window (Notepad++ keeps a hidden second view)."""
    out = []
    def cb(c, _):
        try:
            if win32gui.GetClassName(c) in CLASSES and win32gui.IsWindowVisible(c):      # a minimized window's editor has no size: keep it
                out.append(c)
        except Exception:
            pass
    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:
        pass
    return out


class _Remote:
    """A buffer inside the editor's process."""
    def __init__(self, h, size):
        pid = win32process.GetWindowThreadProcessId(h)[1]
        self.proc = k32.OpenProcess(0x0008 | 0x0010 | 0x0020, False, pid)        # VM_OPERATION | VM_READ | VM_WRITE
        if not self.proc:
            raise OSError(ctypes.get_last_error(), "cannot open the editor's process")
        self.size = max(16, size)
        self.addr = k32.VirtualAllocEx(self.proc, None, self.size, 0x3000, 0x04)
        if not self.addr:
            k32.CloseHandle(self.proc)
            raise OSError(ctypes.get_last_error(), "cannot allocate in the editor's process")

    def write(self, data):
        n = ctypes.c_size_t()
        buf = ctypes.create_string_buffer(data, len(data))
        return k32.WriteProcessMemory(self.proc, self.addr, buf, len(data), ctypes.byref(n)) and n.value == len(data)

    def read(self, size):
        buf = ctypes.create_string_buffer(size); n = ctypes.c_size_t()
        k32.ReadProcessMemory(self.proc, self.addr, buf, size, ctypes.byref(n))
        return buf.raw[:n.value]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        k32.VirtualFreeEx(self.proc, self.addr, 0, 0x8000)                         # MEM_RELEASE
        k32.CloseHandle(self.proc)


def _codec(h):
    cp = send(h, SCI_GETCODEPAGE)
    return "utf-8" if cp in (65001, None) else ("mbcs" if not cp else f"cp{cp}")


def get_text(h):
    n = send(h, SCI_GETLENGTH)
    if n is None:
        return None
    with _Remote(h, n + 1) as r:
        if send(h, SCI_GETTEXT, n + 1, r.addr) is None:
            return None
        return r.read(n).decode(_codec(h), "replace")


def _eol(h):
    return {0: "\r\n", 1: "\r", 2: "\n"}.get(send(h, SCI_GETEOLMODE), "\r\n")


def write(h, text, replace=False):
    """Append (or replace) text at Claude's own point, the end of the document; the user's caret and selection stay where they are.
    Returns 'ok (...)' after reading the document back, or a refusal."""
    if send(h, SCI_GETREADONLY):
        return "did not type: this editor is read-only"
    eol = _eol(h)
    data = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", eol).encode(_codec(h), "replace")
    before = get_text(h)
    if before is None:
        return "did not type: the editor did not answer"
    caret = (send(h, SCI_GETCURRENTPOS), send(h, SCI_GETANCHOR))
    with _Remote(h, len(data) + 1) as r:
        if not r.write(data + b"\0"):
            return "did not type: could not hand the text to the editor's process"
        send(h, SCI_BEGINUNDOACTION)
        try:
            send(h, SCI_SETTEXT, 0, r.addr) if replace else send(h, SCI_APPENDTEXT, len(data), r.addr)
        finally:
            send(h, SCI_ENDUNDOACTION)
    after = get_text(h) or ""
    want = data.decode(_codec(h), "replace")
    ok = after == want if replace else after == before + want
    moved = (send(h, SCI_GETCURRENTPOS), send(h, SCI_GETANCHOR)) != caret and not replace
    how = "SCI_SETTEXT" if replace else "SCI_APPENDTEXT at the end of the document"
    if not ok:
        return f"typed through {how}, but reading it back does not match: look again before going on"
    return (f"ok ({len(text)} characters through Scintilla's {how}; read back and confirmed"
            + ("" if replace or not moved else "; note: the editor moved its caret") + "; no keyboard, mouse or focus used)")


def scroll(h, direction, amount):
    """Scroll by lines/pages with SCI_LINESCROLL / SCI_SETFIRSTVISIBLELINE: the view moves, the caret does not. Returns (before, after)."""
    first, page = send(h, SCI_GETFIRSTVISIBLELINE), send(h, SCI_LINESONSCREEN) or 20
    if first is None:
        return None
    total = send(h, SCI_GETLINECOUNT) or 0
    if direction == "top":
        send(h, SCI_SETFIRSTVISIBLELINE, 0)
    elif direction == "bottom":
        send(h, SCI_SETFIRSTVISIBLELINE, max(0, total - page))
    elif direction in ("up", "down"):
        send(h, SCI_LINESCROLL, 0, ctypes.c_size_t((-1 if direction == "up" else 1) * page * max(1, amount)).value)
    elif direction in ("left", "right"):
        send(h, SCI_LINESCROLL, ctypes.c_size_t((-1 if direction == "left" else 1) * 20 * max(1, amount)).value, 0)
    return first, send(h, SCI_GETFIRSTVISIBLELINE)
