"""Remote desktops and virtual machines (Remote Desktop/mstsc, the Windows App, Citrix, VMware, VirtualBox, Hyper-V, VNC, TeamViewer,
AnyDesk...): the window is a picture of another computer. Its UI tree is empty, so it is read from its pixels (the capture works while the
window is covered) and operated with input MESSAGES aimed at the client's own input window.

Measured with mstsc (Windows 11, 2026-11): the client ignores posted mouse and key messages while it believes it is inactive. Telling it
that it is active - WM_ACTIVATE / WM_SETFOCUS MESSAGES, which change nothing in Windows' real focus or foreground - makes it forward
posted mouse messages and SCANCODE key messages to the remote session; afterwards it is told it lost the focus again. The user's mouse,
keyboard and active window are never touched. Typed text is converted to the keys of the local keyboard layout (including AltGr and
dead keys), because the client sends key positions, not characters; WM_CHAR is ignored.
"""
import ctypes, ctypes.wintypes as wt, time
import win32api, win32con, win32gui, win32process

# process name -> (kind, class of its input window or None = the child under the point)
CLIENTS = {"mstsc": ("Remote Desktop", "IHWindowClass"), "msrdc": ("Remote Desktop", "IHWindowClass"),
           "msrdcw": ("Remote Desktop", "IHWindowClass"), "windows365": ("Remote Desktop", "IHWindowClass"),
           "wfica32": ("Citrix", None), "cdviewer": ("Citrix", None), "vmware-view": ("VMware", None),
           "vmware-remotemks": ("VMware", None), "vmware": ("VMware", None), "vmplayer": ("VMware", None),
           "virtualboxvm": ("VirtualBox", None), "vmconnect": ("Hyper-V", None), "vncviewer": ("VNC", None),
           "tvnviewer": ("VNC", None), "teamviewer": ("TeamViewer", None), "anydesk": ("AnyDesk", None),
           "rustdesk": ("RustDesk", None), "parsecd": ("Parsec", None), "nomachine": ("NoMachine", None), "nxplayer": ("NoMachine", None)}
SESSION_CLASSES = {"TscShellContainerClass", "RAIL_WINDOW"}          # an RDP session window / a RemoteApp window


def _proc(hwnd):
    from .core import psutil_name
    try:
        return psutil_name(win32process.GetWindowThreadProcessId(hwnd)[1])
    except Exception:
        return ""


def kind(hwnd):
    """The remote client this window belongs to ('Remote Desktop', 'VirtualBox'...) if it SHOWS a remote screen, else None.
    The client's own dialogs (connect, certificate, credentials) are normal windows and are read as such."""
    try:
        cls = win32gui.GetClassName(hwnd)
    except Exception:
        return None
    if cls in SESSION_CLASSES:
        return "Remote Desktop"
    k = CLIENTS.get(_proc(hwnd))
    if not k or k[1] == "IHWindowClass" or cls == "#32770":
        return None                                                  # mstsc dialogs: ordinary windows
    return k[0]


def input_window(hwnd, sx=None, sy=None):
    """The window that receives the client's input: the RDP 'Input Capture Window', else the deepest child under the screen point."""
    found = []
    def cb(c, _):
        if win32gui.GetClassName(c) == "IHWindowClass" and win32gui.IsWindowVisible(c):
            found.append(c)
    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:
        pass
    if found:
        return found[0]
    if sx is None:
        l, t, r, b = win32gui.GetWindowRect(hwnd); sx, sy = (l + r) // 2, (t + b) // 2
    from .core import _child_at
    return _child_at(hwnd, sx, sy)[0]


class _Active:
    """The client is TOLD it is active (messages only) for the duration of a burst of input, then told it is not. Windows' real focus,
    foreground and z-order do not change: SendMessage of WM_ACTIVATE is a notification, not an activation."""
    def __init__(self, top, inp):
        self.top, self.inp = top, inp

    def __enter__(self):
        win32gui.SendMessage(self.top, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
        win32gui.SendMessage(self.inp, win32con.WM_SETFOCUS, 0, 0)
        return self

    def __exit__(self, *a):
        try:
            time.sleep(0.15)                                         # let the client flush what it queued before it "loses" focus
            win32gui.SendMessage(self.inp, win32con.WM_KILLFOCUS, 0, 0)
            win32gui.SendMessage(self.top, win32con.WM_ACTIVATE, win32con.WA_INACTIVE, 0)
        except Exception:
            pass


EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5}


def _post_key(inp, vk, up, ext=None):
    sc = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    if vk == 0xA5:
        sc = 0x38                                                    # right Alt (AltGr)
    ext = (vk in EXTENDED) if ext is None else ext
    lp = 1 | (sc << 16) | ((1 << 24) if ext else 0) | ((0xC0000000) if up else 0)
    msg = (win32con.WM_SYSKEYUP if up else win32con.WM_SYSKEYDOWN) if vk in (0x12, 0xA4, 0xA5) else (win32con.WM_KEYUP if up else win32con.WM_KEYDOWN)
    win32gui.PostMessage(inp, msg, vk, lp)


def _tap(inp, vk, mods=()):
    for m in mods:
        _post_key(inp, m, False)
    _post_key(inp, vk, False); time.sleep(0.012); _post_key(inp, vk, True)
    for m in reversed(mods):
        _post_key(inp, m, True)
    time.sleep(0.012)


def _mods_of(bits):
    """VkKeyScan modifier bits -> keys to hold: 1 shift, 2 ctrl, 4 alt; ctrl+alt is AltGr (left Ctrl + right Alt)."""
    if bits & 6 == 6:
        return ([0x10] if bits & 1 else []) + [0xA2, 0xA5]
    return ([0x10] if bits & 1 else []) + ([0xA2] if bits & 2 else []) + ([0xA4] if bits & 4 else [])


_DEAD = {}


def _dead_table():
    """character -> [(vk, mods) dead key, (vk, mods) base key] for the user's layout: 'á' is ´ then a on a Spanish keyboard."""
    if _DEAD:
        return _DEAD
    u = ctypes.windll.user32
    layout = u.GetKeyboardLayout(0)
    state = (ctypes.c_ubyte * 256)()
    buf = ctypes.create_unicode_buffer(8)
    def to_uni(vk, mods):
        for i in range(256):
            state[i] = 0
        for m in mods:
            state[m] = 0x80
            if m in (0xA2, 0xA3): state[0x11] = 0x80
            if m in (0xA4, 0xA5): state[0x12] = 0x80
        sc = u.MapVirtualKeyW(vk, 0)
        return u.ToUnicodeEx(vk, sc, state, buf, 8, 0, layout)
    def clear():
        for _ in range(2):
            to_uni(0x20, [])
    mod_sets = [[], [0x10], [0xA2, 0xA5]]
    bases = [(vk, m) for vk in list(range(0x30, 0x3A)) + list(range(0x41, 0x5B)) + [0x20] for m in ([], [0x10])]
    for vk in range(0x08, 0xFF):
        for mods in mod_sets:
            n = to_uni(vk, mods)
            if n < 0:                                                # a dead key: try it before every base letter
                for bvk, bmods in bases:
                    to_uni(vk, mods)
                    r = to_uni(bvk, bmods)
                    if r == 1:
                        ch = buf.value[:1]
                        if ch and ch not in _DEAD and ord(ch) > 127:
                            _DEAD[ch] = [(vk, tuple(mods)), (bvk, tuple(bmods))]
                    clear()
            elif n:
                clear()
    return _DEAD


def keys_for(text):
    """The key presses that type `text` on the local layout: list of (vk, mods) taps, and the characters that have no key."""
    u = ctypes.windll.user32
    seq, missing = [], []
    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        if ch == "\n":
            seq.append((0x0D, ())); continue
        if ch == "\t":
            seq.append((0x09, ())); continue
        r = u.VkKeyScanW(ord(ch))
        if r != -1 and r & 0xFFFF != 0xFFFF:
            seq.append((r & 0xFF, tuple(_mods_of((r >> 8) & 0xFF)))); continue
        dead = _dead_table().get(ch)
        if dead:
            seq += [(vk, tuple(m)) for vk, m in dead]; continue
        missing.append(ch)
    return seq, missing


def _client_point(inp, top, x, y):
    """(x, y) inside the TOP window -> the input window's client coordinates."""
    from .core import to_app_client
    l, t = win32gui.GetWindowRect(top)[:2]
    return to_app_client(inp, *win32gui.ScreenToClient(inp, (l + x, t + y)))


def click(top, x, y, right=False, double=False):
    from .core import wait_free_mouse
    l, t = win32gui.GetWindowRect(top)[:2]
    inp = input_window(top, l + x, t + y)
    cx, cy = _client_point(inp, top, x, y)
    if not wait_free_mouse(top):
        return "busy"
    lp = win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)
    d, up, mk = ((win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON) if right else
                 (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON))
    with _Active(top, inp):
        win32gui.PostMessage(inp, win32con.WM_MOUSEMOVE, 0, lp); time.sleep(0.03)
        for i in range(2 if double else 1):
            win32gui.PostMessage(inp, d, mk, lp); time.sleep(0.03); win32gui.PostMessage(inp, up, 0, lp)
            if double and i == 0:
                time.sleep(0.05)
    return f"ok (click sent to the remote session at {x},{y}: input messages to the client, your mouse did not move)"


def type_text(top, text):
    from .core import wait_free_keys
    seq, missing = keys_for(text)
    if missing:
        return ("did not type: these characters have no key on this keyboard layout, so the remote client cannot send them: "
                + " ".join(sorted(set(missing))))
    if not wait_free_keys(top):
        return "did not type: you are holding a modifier key (it would turn the keys into shortcuts); retry in a moment"
    inp = input_window(top)
    with _Active(top, inp):
        for vk, mods in seq:
            _tap(inp, vk, mods)
    return f"ok ({len(text)} characters typed into the remote session as key presses of your keyboard layout; your keyboard was not used)"


_NAMED = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
          "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdn": 0x22, "space": 0x20, "insert": 0x2D,
          **{f"f{i}": 0x6F + i for i in range(1, 13)}}
_MOD = {"ctrl": 0xA2, "shift": 0x10, "alt": 0xA4, "win": 0x5B}


def key(top, name):
    from .core import wait_free_keys
    parts = [p for p in name.lower().replace(" ", "").split("+") if p]
    if not parts:
        return "did not press: no key given"
    *mods, k = parts
    if any(m not in _MOD for m in mods):
        return f"did not press '{name}': modifiers are ctrl, shift, alt, win"
    vk = _NAMED.get(k) or (ctypes.windll.user32.VkKeyScanW(ord(k)) & 0xFF if len(k) == 1 else None)
    if not vk or vk == 0xFF:
        return f"did not press '{name}': unknown key"
    if not wait_free_keys(top):
        return "did not press: you are holding a modifier key (it would combine with this key); retry in a moment"
    inp = input_window(top)
    with _Active(top, inp):
        _tap(inp, vk, tuple(_MOD[m] for m in mods))
    return f"ok (key '{name}' sent to the remote session as key presses; your keyboard was not used)"


def scroll(top, direction, amount, x=None, y=None):
    from .core import wait_free_mouse
    l, t, r, b = win32gui.GetWindowRect(top)
    sx, sy = (l + x, t + y) if x is not None else ((l + r) // 2, (t + b) // 2)
    inp = input_window(top, sx, sy)
    if not wait_free_mouse(top):
        return "busy"
    delta = 120 * 3 * max(1, amount) * (1 if direction == "up" else -1)
    with _Active(top, inp):
        win32gui.PostMessage(inp, win32con.WM_MOUSEMOVE, 0, win32api.MAKELONG(*[v & 0xFFFF for v in win32gui.ScreenToClient(inp, (sx, sy))]))
        win32gui.PostMessage(inp, 0x020A, win32api.MAKELONG(0, delta & 0xFFFF), win32api.MAKELONG(sx & 0xFFFF, sy & 0xFFFF))
    return "ok (mouse-wheel sent to the remote session; your mouse did not move)"
