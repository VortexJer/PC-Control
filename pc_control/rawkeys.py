"""Keys for apps that read the keyboard only through Raw Input (Blender, game engines): delivered INSIDE that app, never to the system.

Windows sends Raw Input only to the foreground window, and such apps ignore key messages, so the only real-key route would be the
user's own keyboard. Instead PC-Control posts WM_INPUT with a handle of its own; the focus guard inside the app (native/focus_guard.c)
answers GetRawInputData for that handle with Claude's key record, GetAsyncKeyState/GetKeyState/GetKeyboardState with Claude's
modifiers while it delivers them, and ToUnicodeEx with the exact character a typed key carries. The user's keyboard, focus and other
apps never see any of it.

Each record is delivered only after the app consumed the previous one (raw_done) and went back to its message loop, and a key counts
as down/up for the app only AFTER it handled that key: while it handles a press, GetKeyState must still show the key up, or the app
takes it for an auto-repeat (Blender's shortcuts ignore repeats).
"""
import ctypes, ctypes.wintypes as W, time
import win32gui, win32process

WM_INPUT, RAW_MAGIC, SLOTS = 0x00FF, 0x7EC0DE00, 64
OFF_KEYS_UNTIL, OFF_RAW_DONE, OFF_KEYS, OFF_RING = 16, 20, 24, 280        # native/focus_guard.c VPOINT
RI_KEY_BREAK, RI_KEY_E0 = 1, 2
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105

VK = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24,
      "end": 0x23, "pgup": 0x21, "pgdn": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "minus": 0xBD, "plus": 0xBB,
      "period": 0xBE, "comma": 0xBC, **{f"f{i}": 0x6F + i for i in range(1, 25)}, **{f"num{i}": 0x60 + i for i in range(10)},
      "num*": 0x6A, "num+": 0x6B, "num-": 0x6D, "num.": 0x6E, "num/": 0x6F}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x6F}
MOD_VK = {"shift": (0x10, 0xA0, 0x2A), "ctrl": (0x11, 0xA2, 0x1D), "alt": (0x12, 0xA4, 0x38), "win": (0x5B, 0x5B, 0x5B)}


class _RKB(ctypes.Structure):
    _fields_ = [("MakeCode", ctypes.c_ushort), ("Flags", ctypes.c_ushort), ("Reserved", ctypes.c_ushort), ("VKey", ctypes.c_ushort),
                ("Message", ctypes.c_uint), ("ExtraInformation", ctypes.c_ulong)]


u = ctypes.WinDLL("user32", use_last_error=True)
u.VkKeyScanExW.argtypes = [ctypes.c_wchar, W.HKL]; u.VkKeyScanExW.restype = ctypes.c_short
u.MapVirtualKeyExW.argtypes = [W.UINT, W.UINT, W.HKL]; u.MapVirtualKeyExW.restype = W.UINT
u.GetKeyboardLayout.argtypes = [W.DWORD]; u.GetKeyboardLayout.restype = W.HKL
u.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT, ctypes.POINTER(ctypes.c_size_t)]
u.SendMessageTimeoutW.restype = ctypes.c_ssize_t


def ready(hwnd):
    from .focus import vpointer_ready
    return vpointer_ready(win32process.GetWindowThreadProcessId(hwnd)[1])


def _view(pid):
    from .focus import _VP
    return _VP.get(pid, (None, None))[1]


class Session:
    """Claude's keyboard for ONE window of a raw-input app. Use as a context manager: Claude's key state is cleared at the end."""
    def __init__(self, hwnd, hold_ms=2000, point=None):
        self.hwnd = hwnd
        self.point = point                              # screen pixels Claude's pointer stays at while the keys are delivered
        tid, self.pid = win32process.GetWindowThreadProcessId(hwnd)
        self.view = _view(self.pid)
        if not self.view or not ready(hwnd):
            raise RuntimeError("this app's keyboard is not reachable (its in-app guard is not ready)")
        self.hkl = u.GetKeyboardLayout(tid)
        self.hold_ms = hold_ms
        self.seq = 0
        self.delivered = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        for i in range(256):
            ctypes.c_ubyte.from_address(self.view + OFF_KEYS + i).value = 0
        ctypes.c_uint32.from_address(self.view + OFF_KEYS_UNTIL).value = 0

    def _keys_until(self):
        ctypes.c_uint32.from_address(self.view + OFF_KEYS_UNTIL).value = (ctypes.windll.kernel32.GetTickCount() + self.hold_ms) & 0xFFFFFFFF or 1

    def _set_key(self, vk, down):
        ctypes.c_ubyte.from_address(self.view + OFF_KEYS + (vk & 0xFF)).value = 0x80 if down else 0

    def event(self, vk, down, scan=None, ext=None, alt_held=False, char=None):
        """One key going down or up, delivered to the app and waited for. False if the app did not take it (not a raw-input app).
        char: the exact character this key types (the in-app guard answers the app's ToUnicodeEx with it)."""
        scan = u.MapVirtualKeyExW(vk, 0, self.hkl) if scan is None else scan          # MAPVK_VK_TO_VSC
        ext = (vk in EXTENDED) if ext is None else ext
        extra = 0
        if char:
            units = char.encode("utf-16-le")
            extra = int.from_bytes(units[:2], "little") | ((int.from_bytes(units[2:4], "little") << 16) if len(units) > 2 else 0)
        slot = self.seq % SLOTS; self.seq += 1
        rec = _RKB(scan & 0xFFFF, (0 if down else RI_KEY_BREAK) | (RI_KEY_E0 if ext else 0), 0, vk,
                   (WM_SYSKEYDOWN if down else WM_SYSKEYUP) if alt_held else (WM_KEYDOWN if down else WM_KEYUP), extra)
        ctypes.memmove(self.view + OFF_RING + slot * 16, ctypes.byref(rec), 16)
        self._keys_until()
        if self.point is not None:                      # the app reads where the mouse is for each key: Claude's point, not the user's
            from .focus import set_vpointer
            set_vpointer(self.pid, self.point[0], self.point[1], self.hold_ms)
        done0 = ctypes.c_long.from_address(self.view + OFF_RAW_DONE).value
        win32gui.PostMessage(self.hwnd, WM_INPUT, 0, RAW_MAGIC + slot)                  # RIM_INPUT, our handle
        t0 = time.monotonic()
        while ctypes.c_long.from_address(self.view + OFF_RAW_DONE).value == done0:
            if time.monotonic() - t0 > 0.6:
                return False
            time.sleep(0.001)
        # the app has read the record; let it finish handling it (it reads the modifier state right after) before the next one
        res = ctypes.c_size_t()
        u.SendMessageTimeoutW(self.hwnd, 0, 0, 0, 0x0002, 1000, ctypes.byref(res))      # WM_NULL, SMTO_ABORTIFHUNG
        # only now is the key down/up for the app: while it handled THIS record, GetKeyState had to show the state BEFORE it
        self._set_key(vk, down)
        self.delivered += 1
        return True

    def _mods(self, mods, down):
        order = [m for m in ("ctrl", "alt", "shift", "win") if m in mods]
        for m in (order if down else order[::-1]):
            generic, left, scan = MOD_VK[m]
            if not self.event(generic, down, scan=scan, ext=(m == "win"), alt_held="alt" in mods and m != "alt"):
                return False
            self._set_key(left, down)
        return True

    def combo(self, name):
        """'ctrl+shift+s', 'x', 'esc', 'f2': modifiers down, the key down and up, modifiers up."""
        parts = [p for p in name.lower().split("+") if p]
        mods = set(parts[:-1])
        k = parts[-1]
        if k in VK:
            vk, extra = VK[k], set()
        elif len(k) == 1:
            r = u.VkKeyScanExW(k, self.hkl)
            if r == -1:
                raise ValueError(f"no key for '{k}' on this keyboard layout")
            vk, sh = r & 0xFF, (r >> 8) & 0xFF
            extra = {"shift"} if sh & 1 and not k.isalpha() else set()              # 'a' is A with no shift; '!' needs shift
        else:
            raise ValueError(f"unknown key '{k}'")
        allm = mods | extra
        ok = self._mods(allm, True) and self.event(vk, True, alt_held="alt" in allm) and self.event(vk, False, alt_held="alt" in allm)
        self._mods(allm, False)
        return ok

    def char(self, ch):
        """One character typed: its own key (or the A key when the layout has none for it), carrying the exact character, so accents,
        AltGr signs and any Unicode come out as written, with no modifier held and no dead-key state involved."""
        if ch in "\r\n":
            return self.event(0x0D, True) and self.event(0x0D, False)
        if ch == "\t":
            return self.event(0x09, True) and self.event(0x09, False)
        r = u.VkKeyScanExW(ch, self.hkl) if ord(ch) <= 0xFFFF else -1            # emoji & co. have no key: carried by A
        vk = (r & 0xFF) if r != -1 and (r & 0xFF) not in (0, 0xFF) else 0x41
        return self.event(vk, True, char=ch) and self.event(vk, False)

    def text(self, s):
        for ch in s.replace("\r\n", "\n"):
            if not self.char(ch):
                return False
        return True
