"""PC-Control core: see and operate a window WITHOUT bringing it to the front, sending the cheapest thing that works.

look(query)            -> the program picks UIA text / OCR text / image according to cost and quality
click(query, target)   -> through UIA (without moving or focusing) or PostMessage; target = id or (x, y)
type_text(query, text, target=None, replace=False)
key(query, name, focus=False)
FIXED RULE: never SetForegroundWindow, keybd_event/SendInput or SetCursorPos. The user keeps working without noticing anything.
"""
import asyncio, contextlib, ctypes, ctypes.wintypes, io, math, os, re, subprocess, tempfile, time, unicodedata
ctypes.windll.shcore.SetProcessDpiAwareness(2)
import cv2, numpy as np, uiautomation as auto
import win32api, win32con, win32gui, win32process
from PIL import Image

CELL = 28
IMG_EDGE = 1024
OUT_DIR = os.path.join(tempfile.gettempdir(), "pc-control")
os.makedirs(OUT_DIR, exist_ok=True)

CLICKABLE = {"ButtonControl", "EditControl", "MenuItemControl", "TabItemControl", "ListItemControl",
             "CheckBoxControl", "ComboBoxControl", "HyperlinkControl", "RadioButtonControl",
             "TreeItemControl", "SplitButtonControl", "DataItemControl"}
NAMELESS_OK = {"EditControl", "ComboBoxControl", "CheckBoxControl", "RadioButtonControl", "ButtonControl", "SplitButtonControl"}
LISTS = {"ListControl", "TreeControl", "DataGridControl", "TableControl"}       # named lists get an id too: scroll() and type() (select) aim at them
TEXTY = CLICKABLE | LISTS | {"TextControl", "DocumentControl"}
SKIP_KINDS = {"ScrollBarControl", "ThumbControl"}
LEAF_KINDS = {"TextControl", "ImageControl", "SeparatorControl", "ProgressBarControl"}

_state = {}          # hwnd -> {"items": {id: Item}, "lines": set}


class Item:
    __slots__ = ("id", "kind", "name", "rect", "ctrl", "src", "clickable", "value", "aid", "parent", "hwnd")
    def __init__(self, kind, name, rect, ctrl=None, src="uia", clickable=False):
        self.id = 0; self.kind = kind; self.name = name; self.rect = rect
        self.ctrl = ctrl; self.src = src; self.clickable = clickable
        self.value = ""                                      # what a text field currently contains (never for password fields)
        self.aid = ""                                        # its AutomationId when it was looked at (identity check before acting)
        self.parent = None                                   # (kind, name) of the container it was in (a namesake elsewhere is not it)
        self.hwnd = 0                                        # native control behind an element that UI Automation does not see (Scintilla)


def line_key(it):
    """What identifies an element in the 'what changed' comparison: its kind, name and, for text fields, what they contain."""
    return f"{it.kind}|{it.name}" + (f"={it.value[:30]}" if it.value else "")


def secret_name(*names):
    """True if a field's name, AutomationId or help text says it holds a secret (password, PIN, API key, card number...): such a field is
    neither read nor typed into, whatever the app calls its control type."""
    from .policy import PASSWORD_NAME
    return any(n and PASSWORD_NAME.search(n) for n in names)


def is_secret_field(it):
    """Read NOW: is the control behind this item a password / secret field? Fails closed on what it can read (IsPassword true, a native
    ES_PASSWORD edit, a secret-sounding name / id / help text)."""
    c = it.ctrl
    if c is None:
        return secret_name(it.name)
    try:
        if c.IsPassword:
            return True
    except Exception:
        pass
    nh, cls = _native(c)
    try:
        if nh and "edit" in cls and win32gui.GetWindowLong(nh, win32con.GWL_STYLE) & 0x20:        # ES_PASSWORD
            return True
    except Exception:
        pass
    try:
        return secret_name(c.Name, c.AutomationId, c.HelpText, it.name)
    except Exception:
        return secret_name(it.name)


def field_value(ctrl, editable_only=False):
    """The text a field holds, read through UI Automation (None if it cannot be read or it is a password field).
    editable_only: also None for read-only fields (table cells exposed as text boxes repeat what is already listed: it only costs tokens)."""
    try:
        if ctrl.IsPassword:
            return None
        for getter in ("GetValuePattern", "GetLegacyIAccessiblePattern"):
            try:
                pat = getattr(ctrl, getter)()
                if not pat:
                    continue
                if editable_only and (getattr(pat, "IsReadOnly", False) or getattr(pat, "State", 0) & 0x40):
                    continue
                value = pat.Value
                if value is not None:
                    return value
            except Exception:
                continue
        return None
    except Exception:
        return None


# ---------- cost utilities ----------
def img_cost(w, h):
    return math.ceil(w / CELL) * math.ceil(h / CELL)

def auto_edge(screen_long):
    """Long side of the capture that is sent, adapted to the screen: ~55% of its long side, in multiples of 64, between 768 and 1344.
    A 1920 screen gives 1024; a 4K one, 1344; a small laptop one, 768. A smaller window is never enlarged."""
    return int(max(768, min(1344, round(0.55 * screen_long / 64) * 64)))


def screen_long_for(hwnd):
    """Long side of the MONITOR the window is on (not of the whole virtual desktop: with two monitors it would add both)."""
    try:
        mon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        l, t, r, b = win32api.GetMonitorInfo(mon)["Monitor"]
        return max(r - l, b - t)
    except Exception:
        return max(virtual_screen()[2:4])


def mark_font(img_w):
    """Size of the numbered labels on the image: proportional to the image itself (11 px at 1024 wide)."""
    return max(9, round(11 * img_w / 1024))


def fit(w, h, edge):
    s = min(1.0, edge / max(1, w, h))                       # a window with no size yet (just created, or minimized to nothing) must not divide by zero
    return max(1, round(w * s)), max(1, round(h * s))

def txt_cost(s):
    """Tokens of a text. Fitted on real look() outputs against a BPE tokenizer: visible characters cost ~0.45 each and every
    run of spaces or line break ~0.56 (runs of spaces merge into few tokens). len/3.6 was off by -39 % to +49 %; this, by about 10 %."""
    visible = sum(1 for ch in s if not ch.isspace())
    runs = s.count("\n") + len(re.findall(r" {2,}", s))
    return round(0.45 * visible + 0.56 * runs)


# ---------- windows ----------
TERMINAL_PROCS = {"windowsterminal", "cmd", "powershell", "pwsh", "conhost", "openconsole", "wezterm-gui", "alacritty", "mintty",
                  "wt", "tabby", "hyper", "kitty", "conemu64", "cmder"}
STATUS_ICONS = "✳◑◐◒◓●•⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏*· "
_KNOWN = {}          # hwnd -> (pid, process, title): what a handle was, to find the window that REPLACED it after it closed


def _clean_title(t):
    return t.lstrip(STATUS_ICONS).strip()


def _score(q, title, proc, cls):
    """How well a window matches what the model wrote. The app's own process beats a window that only MENTIONS the app in its title
    (a terminal tab called "Word con PC control" is not Word); a console window of an app is not the app."""
    t = _clean_title(title).casefold()
    score = 0
    if t == q:
        score = 100
    elif proc and (proc == q or proc == q.removesuffix(".exe")):
        score = 80 + (10 if q in t else 0)
    elif q in t:
        score = 40 + (30 if proc and q in proc else 0)
    elif proc and q in proc:
        score = 30
    if not score:
        return 0
    if proc in TERMINAL_PROCS and q not in proc and t != q:
        score -= 35                                                  # a terminal whose title mentions the app (Claude's own session title)
    if cls == "ConsoleWindowClass" and t != q:
        score -= 30                                                  # an app's console (Blender's) is not the app
    return max(score, 1)


def _replacement(old):
    """The window that took the place of a closed handle: same program, the only window of its process with a title (a start-up window
    that became the main one, a document reopened). None if it cannot be told for sure."""
    known = _KNOWN.get(old)
    if not known:
        st = _state.get(old)
        known = (st.get("pid"), "", st.get("title", "")) if st else None
    if not known:
        return None
    pid, proc, title = known
    same_pid, same_proc = [], []
    for h in top_windows():
        try:
            if not win32gui.GetWindowText(h) or win32gui.GetClassName(h) == "PC-Control-Overlay":
                continue
            p = window_pid(h)
        except Exception:
            continue
        if pid and p == pid:
            same_pid.append(h)
        elif proc and psutil_name(p) == proc:
            same_proc.append(h)
    for group in (same_pid, same_proc):
        exact = [h for h in group if title and win32gui.GetWindowText(h) == title]
        if len(exact) == 1:
            return exact[0]
        if len(group) == 1:
            return group[0]
    return None


def _remember(h):
    try:
        pid = window_pid(h)
        _KNOWN[h] = (pid, psutil_name(pid), win32gui.GetWindowText(h))
        if len(_KNOWN) > 400:
            _KNOWN.pop(next(iter(_KNOWN)))
    except Exception:
        pass


class WindowReplaced(LookupError):
    """The hwnd is gone but its program shows ONE window that took its place: callers that only read may follow it."""
    def __init__(self, old, new):
        self.old, self.new = old, new
        super().__init__(f"no window with hwnd {old}: it closed and its program now shows {new} «{win32gui.GetWindowText(new)[:40]}» "
                         f"(the same app): use {new}")


def find_window(query, follow=False):
    """The window the model means: an hwnd, an exact title, the app's process name (winword, mspaint) or a title substring.
    follow=True: a closed hwnd whose app replaced it with ONE new window resolves to that window (for reading)."""
    q = str(query).casefold().strip()
    if not q:
        raise LookupError("no window given: use a title substring, a program name or an hwnd from windows()")
    if q.isdigit():
        h = int(q)
        if not win32gui.IsWindow(h):
            new = _replacement(h)
            if new:
                if follow:
                    return new
                raise WindowReplaced(h, new)
            raise LookupError(f"no window with hwnd {q}: it was closed: call windows() to find it again")
        _remember(h)
        return h
    q = _clean_title(q).casefold() or q
    hits = []
    def cb(h, _):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            if not t:
                return
            cls = win32gui.GetClassName(h)
            if cls == "PC-Control-Overlay":
                return
            proc = psutil_name(window_pid(h))
            sc = _score(q, t, proc, cls)
            if sc:
                hits.append((sc, h, t, cls))
    win32gui.EnumWindows(cb, None)
    if not hits:
        raise LookupError(f"no window with '{query}' in its title or program name: call windows()")
    hits = [x for x in hits if not (x[3] == "ApplicationFrameWindow"                                    # a Store app's frame and its
                                    and sum(1 for y in hits if y[2] == x[2]) > 1)] or hits            # content: one app, same title
    hits.sort(key=lambda x: -x[0])
    best = hits[0][0]
    top = [x for x in hits if x[0] >= best - 14]
    if len(top) == 1:
        _remember(top[0][1])
        return top[0][1]
    # several windows of the same quality (two documents of the same app...): guessing would act on the wrong one
    raise AmbiguousWindow(f"{len(top)} windows match '{query}': " + "; ".join(f"{h} «{_clean_title(t)[:40]}»" for _, h, t, _ in top[:6])
                          + " - use the hwnd (the number) of the one you mean")


class AmbiguousWindow(Exception):
    """A title that matches several windows. Not a LookupError: callers that treat 'not found' as 'try elsewhere' must not do so."""

def list_windows():
    out = []
    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) and win32gui.GetClassName(h) != "PC-Control-Overlay":
            r = win32gui.GetWindowRect(h)
            if r[2] - r[0] > 100 and r[0] > -10000:
                out.append((h, win32gui.GetWindowText(h)[:60]))
    win32gui.EnumWindows(cb, None)
    return out


# ---------- capture without focusing ----------
def dpi_factor(hwnd):
    """How much Windows scales this window up on its monitor: 1.0 for apps that handle DPI themselves; 1.5 for a DPI-UNAWARE app (old
    Win32, WinForms, VB6, Java 8 / Oracle Forms...) on a 150 % monitor. Such an app lives in smaller "logical" pixels: its capture comes
    out at the logical size and the coordinates in its mouse messages are logical too."""
    try:
        u = ctypes.windll.user32
        wdpi = u.GetDpiForWindow(hwnd)
        mon = u.MonitorFromWindow(hwnd, 2)
        x, y = ctypes.c_uint(), ctypes.c_uint()
        if wdpi and ctypes.windll.shcore.GetDpiForMonitor(mon, 0, ctypes.byref(x), ctypes.byref(y)) == 0 and x.value and abs(x.value - wdpi) > 1:
            return x.value / wdpi
    except Exception:
        pass
    return 1.0


def to_app_client(hwnd, cx, cy):
    """Client coordinates in OUR (real) pixels -> the ones the app reads in its mouse messages (its logical pixels if Windows scales it)."""
    f = dpi_factor(hwnd)
    return (round(cx / f), round(cy / f)) if f > 1.01 else (cx, cy)


def to_app_screen(hwnd, sx, sy):
    """A screen point in real pixels -> the app's own screen coordinates (WM_MOUSEWHEEL carries screen coordinates)."""
    if dpi_factor(hwnd) <= 1.01:
        return sx, sy
    pt = ctypes.wintypes.POINT(sx, sy)
    try:
        if ctypes.windll.user32.PhysicalToLogicalPointForPerMonitorDPI(hwnd, ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    return sx, sy


def grab(hwnd, rect):
    import win32ui
    l, t, r, b = rect; w, h = r - l, b - t
    if w < 50 or h < 50:
        return None
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc); mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap(); bmp.CreateCompatibleBitmap(src, w, h); mem.SelectObject(bmp)
    ok = ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
    img = np.frombuffer(bmp.GetBitmapBits(True), dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    win32gui.DeleteObject(bmp.GetHandle()); mem.DeleteDC(); src.DeleteDC(); win32gui.ReleaseDC(hwnd, hdc)
    f = dpi_factor(hwnd)
    if ok and f > 1.01:                       # a DPI-unaware app is printed at its logical size in the top-left corner: scale it to the
        lw, lh = max(1, round(w / f)), max(1, round(h / f))       # window's real pixels, so pixels, OCR and the UI tree share coordinates
        img = cv2.resize(img[:lh, :lw], (w, h), interpolation=cv2.INTER_LINEAR)
    return img if ok and img.max() >= 8 else None


def snap(hwnd):
    """A grey picture of the window, to tell afterwards whether an action changed anything on screen (None if it cannot be captured)."""
    try:
        if win32gui.IsIconic(hwnd):
            return None
        rect = win32gui.GetWindowRect(hwnd)
        img = grab(hwnd, rect)
        return None if img is None else (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), rect)
    except Exception:
        return None


def pixel_change(before, after, roi=None):
    """None if it cannot be measured; '' if nothing meaningful changed on screen; else a short phrase saying where it changed.
    A blinking text caret (a thin vertical line) and a few stray pixels are not a change. roi: window pixels of what was acted on."""
    if before is None or after is None:
        return None
    (a, _), (b, _) = before, after
    if a.shape != b.shape:
        return "the window changed size"
    d = cv2.absdiff(a, b) > 24
    n = int(d.sum())
    if n < 25:
        return ""
    ys, xs = np.nonzero(d)
    if xs.max() - xs.min() <= 3 and ys.max() - ys.min() <= 60:
        return ""                                                    # the text caret blinking
    where = "in the window"
    if roi:
        x0, y0, x1, y1 = [int(v) for v in roi]
        inside = int(d[max(0, y0 - 16):max(0, y1 + 16), max(0, x0 - 16):max(0, x1 + 16)].sum())
        where = "at the element and around it" if inside >= 25 else "elsewhere in the window (not where it acted)"
    return f"pixels changed {where} ({100 * n / d.size:.1f}% of the window)"


# ---------- text extraction ----------
def fallback_name(c, kind):
    """Label for a control that exposes no Name: its AutomationId, its help text, or the kind (so look() still offers an id to aim at)."""
    for attr in ("HelpText", "AutomationId"):
        try:
            v = (getattr(c, attr) or "").strip()
            if v and not v.startswith("PART_") and not v.isdigit():        # PART_xxx / numbers: internal parts of a control, not a label
                return v[:40]
        except Exception:
            pass
    if kind in ("ButtonControl", "SplitButtonControl"):
        return ""                                                          # an icon button with no label at all: nothing to tell the model
    return "(unnamed " + {"EditControl": "text field", "ComboBoxControl": "dropdown", "CheckBoxControl": "checkbox",
                          "RadioButtonControl": "radio button"}.get(kind, "field") + ")"


def state_value(c, kind):
    """The current choice of a drop-down, or on/off of a checkbox, or 'selected' for the chosen radio button ('' if unknown)."""
    try:
        if kind == "ComboBoxControl":
            v = field_value(c)
            if not v:
                sel = c.GetSelectionPattern().GetSelection()
                v = sel[0].Name if sel else ""
            return (v or "").strip()[:60]
        if kind == "CheckBoxControl":
            return {0: "off", 1: "on", 2: "mixed"}.get(c.GetTogglePattern().ToggleState, "")
        if kind == "RadioButtonControl":
            return "selected" if c.GetSelectionItemPattern().IsSelected else ""
    except Exception:
        pass
    return ""


ROW_CELLS = {"EditControl", "TextControl", "ImageControl"}


def row_cells(c, name):
    """A list row whose children are only cells (File Explorer: name, date, type, size): their VALUES joined, so the row reads as one
    line instead of repeating the column headers. None if the row holds anything else (buttons, toggles): then it is walked as usual."""
    try:
        kids = c.GetChildren()
    except Exception:
        return None
    if not kids or len(kids) > 12 or any(k.ControlTypeName not in ROW_CELLS for k in kids):
        return None
    parts = []
    for k in kids:
        try:
            v = field_value(k)
            v = "".join(ch for ch in (v if v is not None else (k.Name or "")) if unicodedata.category(ch) != "Cf").strip()   # LTR/RTL marks
        except Exception:
            continue
        if v and v != name and v not in parts:
            parts.append(v)
    return " · ".join(parts)


def walk_uia(hwnd, rect, budget_s=6.0, max_nodes=2500, offscreen_ok=False):
    root = auto.ControlFromHandle(hwnd)
    l, t = rect[0], rect[1]
    t0 = time.time(); n = 0; items = []; stack = [(root, 0, None)]
    while stack:
        c, d, ps = stack.pop(); n += 1
        if n > max_nodes or time.time() - t0 > budget_s:
            break
        try:
            kind = c.ControlTypeName
            if kind in SKIP_KINDS:                               # "line up", "page down"...: noise in every app; scroll() does that job
                continue
            name = (c.Name or "").strip()
            if kind in TEXTY:                                    # each property is a call to the other process: only for what is kept
                r = c.BoundingRectangle; off = c.IsOffscreen
            else:
                r = off = None
        except Exception:
            continue
        leaf = kind in LEAF_KINDS                                # a label or an image has nothing worth listing below it
        if not name and kind in NAMELESS_OK:                     # Chromium drops a field's name once used; icon buttons often have none: keep them
            name = fallback_name(c, kind)
        if kind in TEXTY and name and (offscreen_ok or (not off and r.width() > 0 and r.height() > 0)):
            it = Item(kind[:-7].lower(), name, (r.left - l, r.top - t, r.right - l, r.bottom - t), c, "uia", kind in CLICKABLE or kind in LISTS)
            it.parent = ps
            try:
                it.aid = c.AutomationId or ""
            except Exception:
                pass
            if kind == "EditControl":
                v = None if secret_name(name, it.aid) else field_value(c, editable_only=True)      # a field called "API key" is not read either
                it.value = (v or "").replace("\r", " ").replace("\n", " ").strip()[:80]
            elif kind in ("ComboBoxControl", "CheckBoxControl", "RadioButtonControl"):
                it.value = state_value(c, kind)                 # what is chosen / ticked: visible in look and in "what changed"
            items.append(it)
            if kind in ("ListItemControl", "DataItemControl"):
                cells = row_cells(c, name)
                if cells is not None:                                  # one line per row: its cells' values, not its headers again
                    it.value = cells[:80]
                    continue
        if d < 28 and not leaf:                                 # web pages (Chromium/Electron) nest deep; max_nodes and budget_s still bound the walk
            try:
                stack.extend((ch, d + 1, (kind[:-7].lower(), name)) for ch in reversed(c.GetChildren()))
            except Exception:
                pass
    return items

def popup_windows(hwnd):
    """Open menus that are a SEPARATE window from the app: same process, visible, and pop-ups (class #32768, owned windows or title-less drop-downs)."""
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]; out = []
    def cb(h, _):
        try:
            if h == hwnd or not win32gui.IsWindowVisible(h) or win32process.GetWindowThreadProcessId(h)[1] != pid:
                return
            st, ex = win32gui.GetWindowLong(h, win32con.GWL_STYLE), win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
            if (win32gui.GetClassName(h) == "#32768" or win32gui.GetWindow(h, 4) == hwnd                # Windows menu, or owned window (GW_OWNER)
                    or (st & win32con.WS_POPUP and ex & (win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW) and win32gui.GetClassName(h) != "SysShadow")):   # drop-down (only the menu entries are kept)
                out.append(h)
        except Exception:
            pass
    win32gui.EnumWindows(cb, None)
    return out


_SURF = {}           # hwnd -> (pid, surface, time)


def surface(hwnd):
    """Which reader understands this window: 'java' (Java Access Bridge on), 'java-off' (a Java window whose app did not load the
    bridge: only pixels), 'remote' (a remote desktop / virtual machine screen: only pixels) or 'uia' (UI Automation)."""
    pid = window_pid(hwnd)
    hit = _SURF.get(hwnd)
    if hit and hit[0] == pid and (hit[1] != "java-off" or time.time() - hit[2] < 10):
        return hit[1]
    from . import java, remote
    s = "uia"
    if remote.kind(hwnd):
        s = "remote"
    elif java.is_java_window(hwnd):
        try:
            s = {"on": "java", "off": "java-off", "missing": "java-off"}[java.bridge_state(hwnd)]
        except Exception:
            s = "java-off"
    _SURF[hwnd] = (pid, s, time.time())
    return s


def tree(hwnd, rect, offscreen_ok=False, budget_s=6.0, popups=True):
    """The elements of a window from the reader that understands it (see surface)."""
    s = surface(hwnd)
    if s == "java":
        from . import java
        try:
            return java.walk(hwnd, rect, budget_s=budget_s, offscreen_ok=offscreen_ok)
        except Exception:
            _SURF.pop(hwnd, None)
            return []
    if s == "remote":
        return []                                                    # the frame of a remote screen has nothing to read: pixels only
    if popups:
        return walk_all(hwnd, rect, offscreen_ok=offscreen_ok, budget_s=budget_s)
    items = walk_uia(hwnd, rect, budget_s=budget_s, offscreen_ok=offscreen_ok)
    return items + native_editors(hwnd, rect, items)


def walk_all(hwnd, rect, **kw):
    """The window tree plus the entries of open menus that are another window (the Windows context menu, drop-downs...)."""
    items = walk_uia(hwnd, rect, **kw)
    items += native_editors(hwnd, rect, items)
    for h in popup_windows(hwnd):
        try:
            items += [i for i in walk_uia(h, rect, offscreen_ok=True) if i.kind in ("menuitem", "menu")]
        except Exception:
            pass
    return items


RICHEDIT_CLASSES = ("RichEditD2DPT", "RICHEDIT50W", "RichEdit20W", "RichEdit20A")


def native_editors(hwnd, rect, known=()):
    """Editor controls that UI Automation does not list (Scintilla, always; a RichEdit document such as the modern Notepad's while the
    window is minimized) as document elements; they are written through their own APIs, never through keys."""
    from . import scintilla
    out = []
    seen = set()
    for it in known:
        try:
            seen.add(it.ctrl.NativeWindowHandle if it.ctrl is not None else 0)
        except Exception:
            pass
    rich = []
    win32gui.EnumChildWindows(hwnd, lambda c, _: rich.append(c) if win32gui.GetClassName(c) in RICHEDIT_CLASSES
                              and win32gui.IsWindowVisible(c) and c not in seen else None, None)
    for h in rich[:1] if len(rich) == 1 else []:                     # one document: unambiguous; several: leave them to the tree
        try:
            l, t, r, b = win32gui.GetWindowRect(h)
            out.append(Item("document", win32gui.GetWindowText(h) or "document", (l - rect[0], t - rect[1], r - rect[0], b - rect[1]),
                            auto.ControlFromHandle(h), "uia", True))
        except Exception:
            pass
    for h in scintilla.editors(hwnd):
        try:
            l, t, r, b = win32gui.GetWindowRect(h)
            lines = scintilla.send(h, scintilla.SCI_GETLINECOUNT, timeout=800)
            it = Item("document", f"editor ({lines} lines)" if lines else "editor", (l - rect[0], t - rect[1], r - rect[0], b - rect[1]),
                      None, "sci", True)
            it.hwnd = h
            out.append(it)
        except Exception:
            pass
    return out


async def _ocr(png):
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
    eng = OcrEngine.try_create_from_user_profile_languages()
    if eng is None:                                    # the user's language has no OCR engine installed
        from winrt.windows.globalization import Language
        for tag in ("en-US", "es-ES", "en-GB"):
            eng = OcrEngine.try_create_from_language(Language(tag))
            if eng: break
    if eng is None:
        return []                                      # no OCR on this machine: the decider falls back to the image
    st = InMemoryRandomAccessStream(); w = DataWriter(st); w.write_bytes(png); await w.store_async(); await w.flush_async(); st.seek(0)
    bmp = await (await BitmapDecoder.create_async(st)).get_software_bitmap_async()
    out = []
    for ln in (await eng.recognize_async(bmp)).lines:
        ws = list(ln.words)
        if ws:
            x0 = min(w.bounding_rect.x for w in ws); y0 = min(w.bounding_rect.y for w in ws)
            x1 = max(w.bounding_rect.x + w.bounding_rect.width for w in ws); y1 = max(w.bounding_rect.y + w.bounding_rect.height for w in ws)
            out.append(Item("ocr", ln.text.strip(), (int(x0), int(y0), int(x1), int(y1)), None, "ocr", True))
    return out

def ocr_items(img):
    b = io.BytesIO(); Image.fromarray(img[:, :, ::-1]).save(b, "PNG")
    return asyncio.run(_ocr(b.getvalue()))

def grid(items, w, h):
    rows_n = max(20, min(70, h // 18)); cols = 120
    rows = {}
    for it in sorted(items, key=lambda i: (i.rect[1] // max(1, h // rows_n), i.rect[0])):
        r = min(rows_n - 1, it.rect[1] * rows_n // max(1, h)); c = it.rect[0] * cols // max(1, w)
        tag = (f"[{it.id}:{it.name[:26]}" + (f'="{it.value[:56]}"' if it.value else "") + "]") if it.clickable else it.name[:44]
        row = rows.setdefault(r, "")
        pad = max(1, c - len(row)) if row else c
        rows[r] = row + " " * pad + tag
    return "\n".join(rows[r] for r in sorted(rows))

def blind_cells(img, items):
    h, w = img.shape[:2]
    edges = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 80, 160)
    cov = np.zeros((h, w), np.uint8)
    for it in items:
        x0, y0, x1, y1 = it.rect
        if (x1 - x0) * (y1 - y0) > 0.15 * w * h:      # large container: not real text
            continue
        cov[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = 1
    blind = content = 0; zones = []
    for gy in range(9):
        for gx in range(16):
            ys, ye, xs, xe = gy * h // 9, (gy + 1) * h // 9, gx * w // 16, (gx + 1) * w // 16
            if edges[ys:ye, xs:xe].mean() / 255 > 0.02:
                content += 1
                if cov[ys:ye, xs:xe].mean() < 0.08:
                    blind += 1; zones.append((xs, ys, xe, ye))
    return blind, content, zones


def draw_marks(im, items, k, area):
    """Number the actionable elements on the image (at most 40, no large containers). The label size
    is proportional to the image: not a fixed size."""
    from PIL import ImageDraw, ImageFont
    fs = mark_font(im.width)
    try:
        font = ImageFont.load_default(size=fs)
    except Exception:
        font = None
    d = ImageDraw.Draw(im); n = 0
    for it in items:
        if not it.clickable:
            continue
        x0, y0, x1, y1 = [v * k for v in it.rect]
        if (it.rect[2] - it.rect[0]) * (it.rect[3] - it.rect[1]) > 0.15 * area:
            continue
        d.rectangle([x0, y0, x1, y1], outline=(255, 90, 40), width=max(1, fs // 10))
        lab = str(it.id); tw = int(fs * 0.62 * len(lab)) + 4
        d.rectangle([x0, y0, x0 + tw, y0 + fs + 2], fill=(255, 90, 40)); d.text((x0 + 2, y0 - 1), lab, fill=(0, 0, 0), font=font)
        n += 1
        if n >= 40:
            break


def dedupe(items):
    """Drop a plain text whose words are already shown by the clickable element that contains it (the label inside a button, a cell
    inside its row, a placeholder inside its field): the model sees the same words once, with the id. Nothing else is dropped."""
    clickable = [c for c in items if c.clickable]
    def shown_by(t):
        cx, cy = (t.rect[0] + t.rect[2]) // 2, (t.rect[1] + t.rect[3]) // 2
        n = t.name.strip().lower()
        return any(c.rect[0] <= cx <= c.rect[2] and c.rect[1] <= cy <= c.rect[3]
                   and (n == c.name.strip().lower() or n == (c.value or "").strip().lower() or n in c.name.lower()) for c in clickable)
    return [it for it in items if it.clickable or it.src != "uia" or not shown_by(it)]


def assign_ids(hwnd, items):
    """Stable ids: an element that was in the last reading keeps its id (same kind and name, about the same place); only new
    elements get new numbers. A model can keep using an id it saw earlier instead of re-reading everything."""
    prev = _state.get(hwnd, {}).get("items", {})
    used, nxt = set(), max(prev, default=0) + 1
    by_key = {}
    for i, o in prev.items():
        by_key.setdefault((o.kind, o.name), []).append((i, o))
    counts = {}
    for it in items:
        counts[(it.kind, it.name)] = counts.get((it.kind, it.name), 0) + 1
    for it in items:
        cands = by_key.get((it.kind, it.name), ())
        if len(cands) == 1 and counts[(it.kind, it.name)] == 1 and cands[0][0] not in used:
            same = cands[0][0]                                       # the only one with that name: the same element, even if it moved
        else:
            same = next((i for i, o in cands if i not in used
                         and abs(o.rect[0] - it.rect[0]) <= 6 and abs(o.rect[1] - it.rect[1]) <= 6), None)
        if same is None:
            same = nxt; nxt += 1
        it.id = same; used.add(same)
    return items


def list_format(items):
    """Text without positions (minimized windows: their rectangles mean nothing)."""
    return "\n".join((f"[{it.id}:{it.name[:60]}" + (f'="{it.value[:60]}"' if it.value else "") + "]" if it.clickable else it.name[:80]) for it in items)


def virtual_screen():
    u = ctypes.windll.user32
    return u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)   # x, y, width, height


def _look_minimized(hwnd, mode, edge):
    edge = edge or auto_edge(screen_long_for(hwnd))
    rc = win32gui.GetWindowPlacement(hwnd)[4]; w, h = rc[2] - rc[0], rc[3] - rc[1]
    if mode != "image":
        items = walk_uia(hwnd, (0, 0, 0, 0), offscreen_ok=True)
        items += native_editors(hwnd, win32gui.GetWindowRect(hwnd), items)
        if sum(len(i.name) for i in items) >= 40:            # a minimized window has no image alternative: any tree with real text is worth sending
            assign_ids(hwnd, items)
            text = list_format(items); tok = txt_cost(text)
            _state[hwnd] = {"items": {it.id: it for it in items}, "lines": {line_key(it) for it in items},
                            "rect": (0, 0, 0, 0), "scale": 1.0, "mode": "uia", "pid": window_pid(hwnd), "title": win32gui.GetWindowText(hwnd)}
            return {"window": win32gui.GetWindowText(hwnd)[:50], "hwnd": hwnd, "size": f"{w}x{h}", "image_cost": img_cost(*fit(w, h, edge)),
                    "mode": "uia", "why": f"minimized: tree {tok} tok, no capture", "tokens": tok, "text": text, "minimized": True}
    # Capturing a minimized window would force it to be shown (Windows does not allow restoring it off-screen): flicker.
    return {"window": win32gui.GetWindowText(hwnd)[:50], "hwnd": hwnd, "size": f"{w}x{h}", "image_cost": img_cost(*fit(w, h, edge)),
            "mode": "none", "tokens": 0, "minimized": True,
            "why": "minimized and no usable tree (or an image was requested): capturing it would force it to be shown. "
                   "ask the user to open it from the taskbar"}


# ---------- the decider ----------
def choose(text_tok, img_tok, text_chars, blind, content):
    """Pure cost/quality rule: ('text'|'image', reason). Never sends text that is empty, expensive or does not cover the window."""
    if text_chars < 40:
        return "image", f"empty text ({text_chars} characters): image"
    if text_tok > img_tok:
        return "image", f"text {text_tok} tok > image {img_tok} tok"
    if content and blind / content > 0.6:
        return "image", f"{blind}/{content} zones with content but no text: image"
    return "text", f"text {text_tok} tok <= image {img_tok} tok"


def look(query, mode="auto", edge=None):
    """Returns a dict: mode, why, tokens, text|image. Picks the cheapest thing that works."""
    hwnd = find_window(query)
    return _look_minimized(hwnd, mode, edge) if win32gui.IsIconic(hwnd) else _look(hwnd, mode, edge)


def _look(hwnd, mode, edge):
    edge = edge or auto_edge(screen_long_for(hwnd))
    rect = win32gui.GetWindowRect(hwnd); w, h = rect[2] - rect[0], rect[3] - rect[1]
    mini = bool(win32gui.IsIconic(hwnd))
    res = {"window": win32gui.GetWindowText(hwnd)[:50], "hwnd": hwnd, "size": f"{w}x{h}"}
    if w <= 0 or h <= 0:                                             # a window with no size yet (being created or destroyed)
        res.update(mode="none", why="the window has no size right now (it is opening or closing): look again in a moment", tokens=0)
        return res
    surf = surface(hwnd)
    if surf == "java-off":
        from . import java
        res["note"] = java.GUIDE_OFF
    elif surf == "remote":
        from . import remote
        res["note"] = (f"{remote.kind(hwnd)} screen: read from its pixels; click/type/key/scroll go INTO the remote session as input "
                       "messages to the client (your mouse and keyboard are not used)")
    if mini:
        rect = (0, 0, 0, 0)
    img = None if mini else grab(hwnd, rect)
    if img is None and not mini:
        res["note"] = (res["note"] + " | " if res.get("note") else "") + "capture not available (black/protected window)"
    iw, ih = fit(w, h, edge); img_tok = img_cost(iw, ih)
    res["image_cost"] = img_tok

    known = []

    def finish(kind, why, items=None, text=None, tokens=None):
        items = items if items is not None else known
        assign_ids(hwnd, items)
        res.update(mode=kind, why=why)
        scale = 1.0
        if kind == "image":
            im = Image.fromarray(img[:, :, ::-1]).resize((iw, ih), Image.LANCZOS).convert("RGB")
            scale = w / iw
            draw_marks(im, items, 1 / scale, w * h)
            path = os.path.join(OUT_DIR, f"win{hwnd}.jpg"); im.save(path, quality=88)
            res.update(image=path, image_size=f"{iw}x{ih}", tokens=img_tok)
        else:
            res.update(text=text, tokens=tokens)
        _state[hwnd] = {"items": {it.id: it for it in items}, "lines": {line_key(it) for it in items if it.src in ("uia", "jab")},
                        "rect": rect, "scale": scale, "mode": kind, "pid": window_pid(hwnd), "title": win32gui.GetWindowText(hwnd)}
        return res

    def numbered(items):
        assign_ids(hwnd, items)
        return items

    if mode == "image":
        if img is None:
            res.update(mode="none", why="could not capture", tokens=0); return res
        known[:] = tree(hwnd, rect)                # ids for the numbered marks on the image
        return finish("image", "image requested")
    # 1) Windows tree (free and exact)
    uia = tree(hwnd, rect); known = uia
    adequate = sum(1 for i in uia if i.clickable) >= 4 and sum(len(i.name) for i in uia) >= 40
    items, kind = (uia if adequate else []), "uia"
    blind = content = 0; zones = []
    if img is not None and items:
        blind, content, zones = blind_cells(img, items)
    # 2) if the tree is no good or leaves a lot uncovered: native OCR (0.2-0.8 s), merged in
    poor = (not adequate) or (content and blind / content > 0.6)
    if img is not None and poor and mode != "uia":
        t0 = time.time(); oc = ocr_items(img); res["ocr_s"] = round(time.time() - t0, 2)
        def covered(o):
            cx, cy = (o.rect[0] + o.rect[2]) // 2, (o.rect[1] + o.rect[3]) // 2
            return any(u.rect[0] <= cx <= u.rect[2] and u.rect[1] <= cy <= u.rect[3]
                       and (u.rect[2] - u.rect[0]) * (u.rect[3] - u.rect[1]) <= 0.15 * w * h for u in items)
        merged = items + [o for o in oc if not covered(o)]
        known = merged
        if sum(len(i.name) for i in merged) >= 40:
            items, kind = merged, ("uia+ocr" if adequate else "ocr")
            blind, content, zones = blind_cells(img, items)
        else:
            return finish("image", f"empty text (uia {len(uia)} elem, ocr {len(oc)} lines): image")
    if not items:
        why = ("minimized and no usable tree: nothing to send" if mini else
                "empty tree and no capture (if this window runs as administrator, Windows does not let a normal process read it)")
        res.update(mode="none", why=why, tokens=0); return res
    items = dedupe(items)
    numbered(items)
    text = grid(items, w, h); tok = txt_cost(text)
    if content: res["blind"] = f"{blind}/{content}"
    # 3) cost/quality rules on the chosen text
    if mode == "auto" and img is not None:
        pick, why = choose(tok, img_tok, sum(len(i.name) for i in items), blind, content)
        if pick == "image":
            return finish("image", why)
        res["blind_zones"] = zones[:6]
    return finish(kind, f"an image would cost {img_tok}", items, text, tok)


def changes(query):
    """What changed in the text tree since the last reading (it is the only thing sent after acting).
    Chained: each call leaves the state updated. In image/OCR mode it cannot be measured and says so."""
    try:
        hwnd = find_window(query)
    except LookupError:
        return "the window is gone (it was closed)"
    if not win32gui.IsWindow(hwnd):
        _state.pop(hwnd, None)
        return "the window is gone (it was closed)"
    st = _state.get(hwnd)
    if not st:
        return "no previous reading: call look()"
    if st.get("mode") not in ("uia", "uia+ocr") and not st.get("lines"):
        return "change not measurable in this mode (the window exposes no text): call look()"
    rect = win32gui.GetWindowRect(hwnd)
    if surface(hwnd) == "java":
        from . import java
        try:
            now = {line_key(i) for i in java.walk(hwnd, rect, keep=False)}
        except Exception:
            return "change not measurable right now (the Java app did not answer): call look()"
    else:
        now = {line_key(i) for i in walk_uia(hwnd, rect, offscreen_ok=bool(win32gui.IsIconic(hwnd)))}
    before = st["lines"]; st["lines"] = now
    add, rem = sorted(now - before), sorted(before - now)
    if not add and not rem:
        if st.get("mode") == "uia+ocr":              # the window's real text came from OCR: the tree not changing proves nothing
            return "no change in the UI tree (this window's text comes from OCR, so the effect cannot be measured): check with look"
        return "no changes"
    if len(add) + len(rem) > 12:                 # the view scrolled or was replaced: a list of lines would be noise
        return f"the view changed a lot (+{len(add)} / -{len(rem)} lines): look to see it"
    f = lambda L: "; ".join(x.split("|", 1)[1][:26] for x in L[:4]) + (f" (+{len(L) - 4})" if len(L) > 4 else "")
    return (f"+ {f(add)}" if add else "") + (" | " if add and rem else "") + (f"- {f(rem)}" if rem else "")


# ---------- live positions: the window can move or be resized in the middle of an action ----------
def _size(rect):
    return (rect[2] - rect[0], rect[3] - rect[1])


def moved_note(old, new):
    """If an element moved (the user moved/resized the window and it was laid out again), a sentence saying so; otherwise ''."""
    if not old or not new or old[0] < -20000 or abs(new[0]) > 20000 or abs(new[1]) > 20000:             # read while the window was minimized: its rectangles were off screen, not "moved"
        return ""
    ox, oy = (old[0] + old[2]) // 2, (old[1] + old[3]) // 2
    nx, ny = (new[0] + new[2]) // 2, (new[1] + new[3]) // 2
    if abs(nx - ox) + abs(ny - oy) <= 6:
        return ""
    return f"the button had moved (from {ox},{oy} to {nx},{ny} inside the window): coordinates corrected automatically"


def live_rel(hwnd, it):
    """Where the element is NOW, relative to its window (None if it is gone). If the user only moved the window, the
    relative position is still valid. If it was resized, the controls are laid out again or disappear (a ribbon that
    collapses) and UI Automation may keep returning the LAST position of a control that is already hidden: so after a
    resize the stored control is not trusted and the current tree is searched again by name and type (closest to the old one).
    An OCR element has no control: it is only valid if the window is the same size as when it was looked at."""
    wl, wtop = win32gui.GetWindowRect(hwnd)[:2]
    st = _state.get(hwnd)
    resized = bool(st) and _size(win32gui.GetWindowRect(hwnd)) != _size(st["rect"])
    if it.ctrl is None:
        return None if resized else it.rect
    if getattr(it, "src", "") == "jab":                              # Java tells where its element is NOW, also after a resize
        try:
            info = it.ctrl.info()
            if "showing" not in info.states_en_US:
                return None
            r = it.ctrl.screen_rect(info)
            return (r.left - wl, r.top - wtop, r.right - wl, r.bottom - wtop) if r.width() > 0 and r.height() > 0 else None
        except Exception:
            return None
    nh, _ = _native(it.ctrl)
    if nh and not win32gui.IsWindowVisible(nh):                      # control with its own window that is already hidden
        return None
    if not resized:
        try:
            r = it.ctrl.BoundingRectangle
            if r.width() > 0 and r.height() > 0 and not it.ctrl.IsOffscreen:
                return (r.left - wl, r.top - wtop, r.right - wl, r.bottom - wtop)
        except Exception:
            pass
    # The stored control is no good. Looking for a namesake is only safe when there is exactly ONE, in the same container and with the
    # same id: a second "OK" / "Delete" in another panel or dialog is NOT the element that was looked at. Otherwise: look again.
    ox, oy = (it.rect[0] + it.rect[2]) // 2, (it.rect[1] + it.rect[3]) // 2
    cands = [f for f in walk_uia(hwnd, win32gui.GetWindowRect(hwnd)) if f.name == it.name and f.kind == it.kind]       # the tree as it is NOW
    if len(cands) != 1:
        return None
    f = cands[0]
    if it.aid and f.aid and it.aid != f.aid:
        return None
    if it.parent and f.parent and it.parent != f.parent:
        return None
    rc = win32gui.GetWindowRect(hwnd); diag = math.hypot(rc[2] - rc[0], rc[3] - rc[1])
    dist = math.hypot((f.rect[0] + f.rect[2]) // 2 - ox, (f.rect[1] + f.rect[3]) // 2 - oy)
    if not (it.aid and f.aid) and dist > 0.35 * diag:                # no id to vouch for it: it cannot have gone far
        return None
    it.ctrl, it.rect, it.aid, it.parent = f.ctrl, f.rect, f.aid or it.aid, f.parent or it.parent
    return f.rect


def window_pid(hwnd):
    try:
        return win32process.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return 0


def stale_state(hwnd):
    """True (and the snapshot is dropped) if this window handle now belongs to ANOTHER program than the one that was looked at: handles
    are reused after a window closes, and its old ids would point into somebody else's window."""
    st = _state.get(hwnd)
    if st and st.get("pid") and window_pid(hwnd) != st["pid"]:
        _state.pop(hwnd, None)
        return True
    return False


# ---------- is it still the same element? ----------
NAME_KINDS = {"button", "menuitem", "listitem", "hyperlink", "checkbox", "radiobutton", "tabitem", "treeitem", "splitbutton", "dataitem"}


def norm_name(s):
    """A name for comparing: no LTR/RTL marks (Cf), one space between words, case-insensitive."""
    s = "".join(ch for ch in (s or "") if unicodedata.category(ch) != "Cf")
    return " ".join(s.split()).casefold()


def live_identity(it):
    """(kind, name, AutomationId) read NOW from the control behind an item; None if it has no control (OCR) or cannot be read."""
    c = it.ctrl
    if c is None:
        return None
    try:
        return c.ControlTypeName[:-7].lower(), c.Name or "", c.AutomationId or ""
    except Exception:
        return None


def identity_problem(it, live=None):
    """None if the element behind `it` is still the one that was looked at; otherwise a sentence saying what changed. A UI Automation
    reference stays valid while the app reuses the control for something else (a Buy/Sell toggle, recycled rows of a virtual list): the
    live type and name must match. Fields whose name is their value (edits) only compare type and id, so typing does not trip it."""
    live = live or live_identity(it)
    if live is None:
        return None                                                  # cannot read it: live_rel() decides whether it is gone
    kind, name, aid = live
    if kind != it.kind:
        return f"the element changed (was a {it.kind} «{it.name[:30]}», now a {kind} «{name[:30]}»)"
    if it.kind in NAME_KINDS:
        was, now = norm_name(it.name), norm_name(name)
        if was != now:
            if it.aid and aid == it.aid and (not now or was.startswith("(unnamed") or norm_name(it.aid) == was):
                return None                                          # its name came from the id (the app dropped the real one): same id
            return f"the element changed (was «{it.name[:30]}», now «{name[:30]}»)"
    elif it.aid and aid and it.aid != aid:
        return f"the field changed (id was «{it.aid[:30]}», now «{aid[:30]}»)"
    return None


def item_at(hwnd, x, y):
    """The smallest element of the last look that contains the point (x, y) of that look (window pixels), or None."""
    st = _state.get(hwnd)
    if not st:
        return None
    best = None
    for it in st["items"].values():
        r = it.rect
        if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
            a = (r[2] - r[0]) * (r[3] - r[1])
            if best is None or a < best[0]:
                best = (a, it)
    return best[1] if best else None


# ---------- actions without focusing ----------
# ---------- coexistence: the user can work in the same app at the same time ----------
MOUSE_WAIT, KEY_QUIET_WAIT = 2.0, 3.0          # how long to wait for the user to release the mouse / stop typing


def _buttons_down():
    """True if the user is holding a mouse button down (dragging, selecting...). It is only read, nothing is touched."""
    return any(win32api.GetAsyncKeyState(vk) & 0x8000 for vk in (0x01, 0x02, 0x04, 0x05, 0x06))


def _keys_down():
    """A held key/modifier is input too, even before key repeat updates GetLastInputInfo."""
    return any(win32api.GetAsyncKeyState(vk) & 0x8000 for vk in range(0x08, 0xFF))


def _input_tick():
    """Timestamp of real input; None if Windows could not report it."""
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LII(); li.cbSize = ctypes.sizeof(LII)
    return li.dwTime if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)) else None


def _idle_ms():
    """Milliseconds since the user's last input; a failed query is treated as busy."""
    tick = _input_tick()
    return (ctypes.windll.kernel32.GetTickCount() - tick) & 0xFFFFFFFF if tick is not None else 0


def window_busy(hwnd):
    """Mutating controls in the user's active window must yield to current input."""
    return win32gui.GetForegroundWindow() == hwnd and (_idle_ms() < 250 or _buttons_down() or _keys_down())


def wait_user_mouse_idle(timeout=None):
    """Wait for the user to release the mouse before sending a synthetic click (otherwise a drag of theirs would break). False if they do not release it."""
    end = time.time() + (MOUSE_WAIT if timeout is None else timeout)
    while _buttons_down():
        if time.time() > end:
            return False
        time.sleep(0.02)
    return True


def wait_user_quiet(quiet=0.25, timeout=None):
    """Wait until the user has stopped typing/moving the mouse for `quiet` seconds before sending a key (so it does not land in the middle of their word)."""
    end = time.time() + (KEY_QUIET_WAIT if timeout is None else timeout)
    while _idle_ms() < quiet * 1000 or _buttons_down() or _keys_down():
        if time.time() > end:
            return False
        time.sleep(0.03)
    return True


# ---------- independence: Claude works while the user works ELSEWHERE ----------
# Measured (tests/input_probe_app.py, 2026-11-21): a message posted to a BACKGROUND app is not mixed with the user's typing - their keys go
# to their own foreground thread - but the key STATE is shared: while the user holds Shift/Ctrl/Alt/Win (or a mouse button), the target's
# GetKeyState sees it held, so a posted Home becomes Shift+Home and a click becomes Shift+click. So for any other window Claude waits only
# for a held modifier to be released (milliseconds), never for the user to stop typing; only the window the user is in keeps the quiet rule.
MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)
HELD_WAIT = 3.0


def _mods_down():
    return any(win32api.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIERS)


def user_in(hwnd):
    """True if the user's keyboard is in this window or in its input thread (same app window, its dialogs): there input can mix."""
    try:
        fg = win32gui.GetForegroundWindow()
        if not fg or not hwnd:
            return False
        root = lambda h: ctypes.windll.user32.GetAncestor(h, 3) or h      # GA_ROOTOWNER
        return root(fg) == root(hwnd) or win32process.GetWindowThreadProcessId(fg)[0] == win32process.GetWindowThreadProcessId(hwnd)[0]
    except Exception:
        return True                                                     # unknown: be careful


def _mouse_in(hwnd):
    """The user's pointer is over this window, or this window's thread has captured their mouse (a drag of theirs inside it)."""
    try:
        x, y = win32api.GetCursorPos()
        under = win32gui.WindowFromPoint((x, y))
        root = lambda h: ctypes.windll.user32.GetAncestor(h, 2) or h      # GA_ROOT
        if under and root(under) == root(hwnd):
            return True
        tid = win32process.GetWindowThreadProcessId(hwnd)[0]
        class GTI(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint), ("act", ctypes.c_void_p), ("foc", ctypes.c_void_p),
                        ("cap", ctypes.c_void_p), ("menu", ctypes.c_void_p), ("move", ctypes.c_void_p), ("caret", ctypes.c_void_p),
                        ("rc", ctypes.c_long * 4)]
        g = GTI(); g.cbSize = ctypes.sizeof(GTI)
        return bool(ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(g)) and g.cap)
    except Exception:
        return False


def wait_free_keys(hwnd, timeout=None):
    """Before keys/characters to `hwnd`: in the user's own window, wait for them to stop typing (their word is not split);
    anywhere else, only wait while a modifier is held (it would turn Claude's key into a shortcut). False = still busy."""
    if user_in(hwnd):
        return wait_user_quiet(timeout=timeout)
    end = time.time() + (HELD_WAIT if timeout is None else timeout)
    while _mods_down():
        if time.time() > end:
            return False
        time.sleep(0.01)
    return True


def wait_free_mouse(hwnd, timeout=None):
    """Before a synthetic click/drag/wheel on `hwnd`: a held modifier would make it a Shift/Ctrl-click, so wait for it; a held mouse
    button only matters when the user's gesture is IN this window (their drag would break). A drag elsewhere does not wait."""
    end = time.time() + (MOUSE_WAIT if timeout is None else timeout)
    while _mods_down() or (_buttons_down() and (user_in(hwnd) or _mouse_in(hwnd))):
        if time.time() > end:
            return False
        time.sleep(0.01)
    return True


RIGHT_CLICK_QUIET = 3.0          # a right click opens a REAL menu: only if the user has not touched anything for this long


def menu_open():
    """True if a Windows pop-up menu is open on screen (class #32768): the user's or one of ours."""
    found = []
    win32gui.EnumWindows(lambda h, _: found.append(h) if win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == "#32768" else None, None)
    return bool(found)


def right_click_blocked():
    """Reason why a context menu is NOT opened right now (or None). A context menu is modal and closes on any click outside it:
    if the user is working in the app, Claude yields and does not open it."""
    if menu_open():
        return "a menu is already open on screen (it may be yours): I will not open another on top"
    if not wait_user_quiet(RIGHT_CLICK_QUIET, timeout=5.0):
        return "you are using the mouse or keyboard: a context menu closes on any click of yours, so I do not open it while you work"
    return None


def _child_at(hwnd, sx, sy):
    cur = hwnd
    while True:
        cx, cy = win32gui.ScreenToClient(cur, (sx, sy))
        ch = ctypes.windll.user32.ChildWindowFromPointEx(cur, ctypes.wintypes.POINT(cx, cy), 0x0001)   # CWP_SKIPINVISIBLE
        if not ch or ch == cur:
            return cur, win32gui.ScreenToClient(cur, (sx, sy))
        cur = ch

def _post_click(hwnd, x, y, right=False, double=False):
    rect = win32gui.GetWindowRect(hwnd); sx, sy = rect[0] + x, rect[1] + y
    tgt, (cx, cy) = _child_at(hwnd, sx, sy); cx, cy = to_app_client(tgt, cx, cy); lp = win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)
    if not right and not double and "button" in win32gui.GetClassName(tgt).lower():
        win32gui.SendMessage(tgt, 0x00F5, 0, 0)          # BM_CLICK: standard button, no focus
        return "button"
    if not wait_free_mouse(hwnd):
        return "busy"                                    # the user is dragging or pressing: a synthetic click would break it
    d, u, mk = (win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON) if right else (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON)
    win32gui.PostMessage(tgt, win32con.WM_MOUSEMOVE, 0, lp)
    for _ in range(2 if double else 1):
        win32gui.PostMessage(tgt, d, mk, lp); win32gui.PostMessage(tgt, u, 0, lp)
    return "mouse"

def shape_points(shape, pts):
    """Points along the path of a shape. line: 2 points; rect: 2 opposite corners; ellipse: 2 corners of its box; path: the ones given (freehand)."""
    import math
    shape = (shape or "path").lower()
    if shape == "line" and len(pts) >= 2:
        return [pts[0], pts[-1]]
    if shape in ("rect", "rectangle", "rectangulo") and len(pts) >= 2:
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    if shape in ("ellipse", "circle", "elipse", "circulo") and len(pts) >= 2:
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0) / 2, abs(y1 - y0) / 2
        return [(round(cx + rx * math.cos(2 * math.pi * i / 48)), round(cy + ry * math.sin(2 * math.pi * i / 48))) for i in range(49)]
    return list(pts)


def _densify(points, step=6):
    """Interpolate points so the drag is smooth (one mouse move every `step` pixels at most)."""
    import math
    out = [points[0]]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) // step))
        out += [(round(x0 + (x1 - x0) * i / n), round(y0 + (y1 - y0) * i / n)) for i in range(1, n + 1)]
    return out


def drag(query, points, shape="path", right=False, step=6, delay=0.006):
    """Drag with the button held through a list of points (pixels of the last image), using mouse MESSAGES to the window: it does not move your
    mouse or change focus. Works for drawing (Paint), selecting, moving. If the user is dragging or pressing, it waits/yields."""
    hwnd = find_window(query); st = _state.get(hwnd)
    if win32gui.IsIconic(hwnd):
        return "window minimized: there are no coordinates; use an element id, or ask the user to open it from the taskbar"
    if st and _size(win32gui.GetWindowRect(hwnd)) != _size(st["rect"]):
        return "the window changed size since the last image: those coordinates are no longer valid; look again with look"
    if win32gui.GetClassName(hwnd) == "MSPaintApp":
        modern = []
        win32gui.EnumChildWindows(hwnd, lambda h, _: modern.append(h) if win32gui.GetClassName(h) == "Microsoft.UI.Content.DesktopChildSiteBridge" else None, None)
        if modern:
            return ("did not drag: this modern Paint canvas ignores background mouse messages and exposes no drawing pattern. "
                    "Real pointer injection would interfere with your input, so it was not used")
    k = st.get("scale", 1.0) if st else 1.0
    pts = [(int(x * k), int(y * k)) for x, y in shape_points(shape, points)]
    if len(pts) < 2:
        return "at least 2 points are needed (or 2 corners for rect/ellipse)"
    width, height = _size(win32gui.GetWindowRect(hwnd))
    if any(x < 0 or y < 0 or x >= width or y >= height for x, y in pts):
        return "did not drag: a point is outside the window; look again and use points inside the image"
    path = _densify(pts, step)
    l, t = win32gui.GetWindowRect(hwnd)[:2]
    if not wait_free_mouse(hwnd):
        return "did not drag: you are pressing or dragging with the mouse and a drag of mine would break your gesture; retry when you let go"
    tgt, _ = _child_at(hwnd, l + path[0][0], t + path[0][1])
    mk, d, u = (win32con.MK_RBUTTON, win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP) if right else (win32con.MK_LBUTTON, win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP)
    def lp(p):
        cx, cy = to_app_client(tgt, *win32gui.ScreenToClient(tgt, (l + p[0], t + p[1])))
        return win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)
    down, last = False, path[0]
    try:
        win32gui.PostMessage(tgt, win32con.WM_MOUSEMOVE, 0, lp(path[0]))
        win32gui.PostMessage(tgt, d, mk, lp(path[0]))
        down = True
        for p in path[1:]:
            win32gui.PostMessage(tgt, win32con.WM_MOUSEMOVE, mk, lp(p))
            last = p
            time.sleep(delay)
    finally:
        if down:
            win32gui.PostMessage(tgt, u, 0, lp(last))       # release only our drag, where it actually reached
    return f"ok (drag of {len(path)} moves, from {pts[0][0]},{pts[0][1]} to {pts[-1][0]},{pts[-1][1]}, through mouse messages to the window)"


def _native(ctrl):
    """The control's own hwnd and its class (lowercase), or (0, '')."""
    try:
        nh = ctrl.NativeWindowHandle
        return (nh, win32gui.GetClassName(nh).lower()) if nh else (0, "")
    except Exception:
        return 0, ""

def _click_menu_item(ctrl, name):
    """Entry of a menu (context menu, drop-down): the menu is ANOTHER window, so a message click sent to the app window is lost.
    Its accessibility pattern is used: it does not depend on where it is drawn and does not move the mouse. None if no route works."""
    try:
        if not ctrl.IsEnabled:
            return f"the entry «{name[:40]}» is disabled (greyed out) in this menu: it cannot be pressed. This is normal if something is missing (e.g. a selected word)"
    except Exception:
        pass
    for getter, call, label in (("GetInvokePattern", "Invoke", "invoked"), ("GetExpandCollapsePattern", "Expand", "submenu expanded"),
                                ("GetSelectionItemPattern", "Select", "selected"), ("GetLegacyIAccessiblePattern", "DoDefaultAction", "default action")):
        try:
            p = getattr(ctrl, getter)()
            if p:
                getattr(p, call)()
                return f"ok (menu entry «{name[:40]}» {label} through accessibility; if it opens a submenu, use look to see its entries)"
        except Exception:
            continue
    return None


def in_title_bar(ctrl):
    """True if the control is a title-bar button (minimize / maximize / close): those are not part of the client area, so mouse
    messages sent to the window never reach them."""
    try:
        return ctrl.GetParentControl().ControlTypeName == "TitleBarControl"
    except Exception:
        return False


def invoke(query, target, why="this app ignores mouse messages", double=False):
    """Press an element through its UI Automation pattern (Invoke, Toggle, Select, Expand). Apps whose controls are drawn by the app itself
    (modern Store/XAML apps such as Calculator or Paint) ignore the mouse messages, but a screen reader's Invoke works on them: it neither
    moves the mouse nor needs focus. Returns a result text, or None if the element has no such pattern."""
    hwnd = find_window(query); st = _state.get(hwnd)
    if window_busy(hwnd):
        return "did not press: you are using this window; your current input was left untouched"
    it = st["items"].get(target) if st and isinstance(target, int) else None
    if it is None or it.ctrl is None:
        return None
    order = [("GetInvokePattern", "Invoke", "Invoke"), ("GetTogglePattern", "Toggle", "Toggle"),
             ("GetSelectionItemPattern", "Select", "Select"), ("GetExpandCollapsePattern", "Expand", "Expand")]
    if double:                                                   # a double click means "open / expand": try those first
        order = [order[3], order[0]] + [order[1], order[2]]
    for getter, call, label in order:
        try:
            p = getattr(it.ctrl, getter)()
            if p:
                getattr(p, call)()
                return f"ok (UI Automation {label}: {why})"
        except Exception:
            continue
    return None


def click(query, target, right=False, double=False, allow_focus=False, rel=None):
    """Order: 1) direct message to the control (no focus)  2) mouse message  (UIA only with allow_focus=True: it may activate the window)."""
    hwnd = find_window(query); st = _state.get(hwnd)
    if window_busy(hwnd):
        return "did not press: you are using this window; your current input was left untouched"
    if isinstance(target, int):
        if not st or target not in st["items"]:
            return "unknown id: call look() first"
        it = st["items"][target]
        if getattr(it, "src", "") == "jab" and it.ctrl is not None and not right:
            from . import java
            try:
                if it.kind == "dataitem" and isinstance(it.ctrl, java.JCell) and java.select_cell(it.ctrl):
                    return f"ok (Java table cell «{it.name[:40]}» selected through the accessibility bridge)"
                if it.kind in ("tabitem", "listitem") and java.select_in_parent(it.ctrl):
                    return f"ok (Java selection of «{it.name[:40]}» through the accessibility bridge)"
                done = java.do_action(it.ctrl)
                if done:
                    return done
            except Exception:
                pass                                                 # no action: pressed by position below (mouse message)
        if it.ctrl is not None and getattr(it, "src", "") != "jab":
            from . import actions
            if actions.scroll_into_view(it.ctrl):                         # out of view (found with look(find=...)): bring it in first
                rel = None
            elif not right and actions._off(it):
                done = actions.press_out_of_view(it)
                if done:
                    return done
            if not right and not double and it.kind == "listitem":         # a list entry: selected through its list (confirmed, the app is told)
                done = actions.select_in_parent(it)
                if done:
                    return done
        if it.ctrl is not None and it.kind == "button" and not right and not double and in_title_bar(it.ctrl):
            done = invoke(query, target, "title-bar buttons are not reachable with client mouse messages")
            if done:
                return done
        if it.ctrl is not None and it.kind == "menuitem" and not right and not double:
            done = _click_menu_item(it.ctrl, it.name)
            if done:
                return done
        if it.ctrl is not None and not right and not double and win32gui.GetClassName(hwnd) == "OpusApp":
            try:                                                  # formatting buttons: AutomationId (does not depend on the language)
                from . import word
                done = word.format_click(hwnd, it.ctrl.AutomationId, it.ctrl)
                if done:
                    return done
            except Exception:
                pass
        if it.ctrl is not None and not right and not double:
            nh, cls = _native(it.ctrl)
            if nh and "button" in cls:
                win32gui.SendMessage(nh, 0x00F5, 0, 0); return "ok (BM_CLICK)"
            for getter, call in (() if not allow_focus else (("GetInvokePattern", "Invoke"), ("GetTogglePattern", "Toggle"),
                                 ("GetSelectionItemPattern", "Select"), ("GetExpandCollapsePattern", "Expand"),
                                 ("GetLegacyIAccessiblePattern", "DoDefaultAction"))):
                try:
                    p = getattr(it.ctrl, getter)()
                    if p:
                        getattr(p, call)()
                        return f"ok (UIA {call}; may activate the window)"
                except Exception:
                    continue
        rel = rel if rel is not None else live_rel(hwnd, it)             # `rel`: already resolved by the caller (only once per click)
        for _ in range(10):                                      # the user may still be resizing: give it ~1.5 s
            if rel is not None:
                break
            time.sleep(0.15); rel = live_rel(hwnd, it)
        if rel is None:
            return "the element is no longer where it was (the window changed size or the control disappeared): look again with look"
        x, y = (rel[0] + rel[2]) // 2, (rel[1] + rel[3]) // 2
        if it.ctrl is not None and not in_client(hwnd, x, y):
            # In the window's FRAME (menu bar, title bar, borders): a posted client mouse message lands at a negative client
            # coordinate and the app ignores it (seen live: Notepad++'s menu-bar "+" and "x" answered "ok" and did nothing).
            # Pressed through its own UI Automation pattern instead; the focus guard vetoes any activation that may cause.
            if right or double:
                return "did not press: this element is in the window's frame (menu or title bar), where background right/double clicks do not reach"
            for getter, call in (("GetInvokePattern", "Invoke"), ("GetTogglePattern", "Toggle"), ("GetSelectionItemPattern", "Select"),
                                 ("GetExpandCollapsePattern", "Expand"), ("GetLegacyIAccessiblePattern", "DoDefaultAction")):
                try:
                    p = getattr(it.ctrl, getter)()
                    if p:
                        getattr(p, call)()
                        return f"ok (UI Automation {call}: the element is in the window's frame, where mouse messages do not reach)"
                except Exception:
                    continue
            done = _menubar_command(hwnd, it)
            if done:
                return done
            return ("did not press: this element is in the window's frame (menu or title bar), where background mouse messages do not "
                    "reach, and it exposes no pattern to press it")
    else:
        if st and _size(win32gui.GetWindowRect(hwnd)) != _size(st["rect"]) and not win32gui.IsIconic(hwnd):
            return "the window changed size since the last image: those coordinates are no longer valid; look again with look"
        k = st.get("scale", 1.0) if st else 1.0
        x, y = int(target[0] * k), int(target[1] * k)
        cw, ch = _size(win32gui.GetWindowRect(hwnd))
        if not win32gui.IsIconic(hwnd) and (x >= cw or y >= ch):
            return f"did not press: {target[0]},{target[1]} is outside the window (the last image is {int(cw / k)}x{int(ch / k)})"
    if win32gui.IsIconic(hwnd):
        return "window minimized: there are no coordinates; use an id from look()"
    if right:
        why = right_click_blocked()
        if why:
            return f"did not open the context menu: {why}. Retry when the user stops working in that app."
    if surface(hwnd) == "remote":
        from . import remote
        how = remote.click(hwnd, x, y, right, double)
        if how == "busy":
            return "did not press: you are pressing or dragging with the mouse and a click of mine would break your gesture; retry when you let go"
        return how
    how = _post_click(hwnd, x, y, right, double)
    if how == "busy":
        return "did not press: you are pressing or dragging with the mouse and a click of mine would break your gesture; retry when you let go"
    return f"ok (BM_CLICK at {x},{y})" if how == "button" else f"ok (mouse message at {x},{y} of the window)"

def _menubar_command(hwnd, it):
    """A command entry of the window's classic menu BAR (Notepad++'s '+' / 'x' at its right end): run by its WM_COMMAND, what clicking
    it does, without opening anything. Matched by text, or - for owner-drawn entries without text - by its position among the bar's
    entries as UI Automation lists them. Submenus (File, Edit...) are not commands: None."""
    u = ctypes.windll.user32
    menu = u.GetMenu(hwnd)
    n = u.GetMenuItemCount(menu) if menu else 0
    if n <= 0:
        return None
    clean = lambda s: s.split("\t")[0].replace("&", "").strip().lower()
    texts = []
    for i in range(n):
        buf = ctypes.create_unicode_buffer(256)
        u.GetMenuStringW(menu, i, buf, 256, 0x400)                           # MF_BYPOSITION
        texts.append(clean(buf.value))
    pos = texts.index(clean(it.name)) if clean(it.name) in texts else None
    if pos is None:
        try:                                                                 # owner-drawn: same position among the bar's entries
            bar = it.ctrl.GetParentControl()
            sibs = [c.Name for c in bar.GetChildren()] if bar else []
            if len(sibs) == n and it.name in sibs and sibs.count(it.name) == 1:
                pos = sibs.index(it.name)
        except Exception:
            pos = None
    if pos is None or u.GetSubMenu(menu, pos):
        return None
    if u.GetMenuState(menu, pos, 0x400) & 0x3:                               # MF_GRAYED | MF_DISABLED
        return f"did not press: «{it.name}» is disabled (greyed out) right now"
    cid = u.GetMenuItemID(menu, pos)
    if cid in (0, 0xFFFFFFFF, -1):
        return None
    win32gui.PostMessage(hwnd, win32con.WM_COMMAND, cid, 0)
    return f"ok (menu-bar command «{it.name}» run by the app: WM_COMMAND {cid})"


def in_client(hwnd, x, y):
    """True if (x, y) - pixels from the window's top-left - falls inside its CLIENT area (not the title bar, menu bar or borders)."""
    try:
        l, t = win32gui.GetWindowRect(hwnd)[:2]
        cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
        _, _, cw, ch = win32gui.GetClientRect(hwnd)
        sx, sy = l + x, t + y
        return cx <= sx < cx + cw and cy <= sy < cy + ch
    except Exception:
        return True


def _thread_focus(hwnd):
    tid = win32process.GetWindowThreadProcessId(hwnd)[0]
    class GTI(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint), ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                    ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p), ("hwndMoveSize", ctypes.c_void_p),
                    ("hwndCaret", ctypes.c_void_p), ("rcCaret", ctypes.c_long * 4)]
    g = GTI(); g.cbSize = ctypes.sizeof(GTI)
    return g.hwndFocus if ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(g)) else 0


def _root_window(hwnd):
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]
    u.GetAncestor.restype = ctypes.wintypes.HWND
    return u.GetAncestor(hwnd, 2)


def _focus_hwnd(hwnd):
    """A thread can host multiple top-level windows: its focus is NOT necessarily ours."""
    focused = _thread_focus(hwnd)
    root = _root_window(focused) if focused else 0
    seen = set()
    while root and root not in seen:
        if root == hwnd:
            return focused
        seen.add(root)
        root = win32gui.GetWindow(root, win32con.GW_OWNER)
    return chromium_input(hwnd)  # scoped to this window; never another window's editor


def chromium_input(hwnd):
    """The window of a Chromium / Electron app (Chrome, Edge, VS Code, Spotify, Discord, Slack...) that RECEIVES the keys: the child that draws the page.
    Messages posted to the top-level window are ignored by these apps, and a window that is not active has no focus window to ask for."""
    try:
        if not win32gui.GetClassName(hwnd).startswith("Chrome_WidgetWin"):
            return 0
        kids = []
        win32gui.EnumChildWindows(hwnd, lambda k, _: kids.append(k) if win32gui.GetClassName(k) == "Chrome_RenderWidgetHostHWND" and win32gui.IsWindowVisible(k) else None, None)
        return kids[0] if len(kids) == 1 else 0
    except Exception:
        return 0


def word_caret(hwnd):
    """Where CLAUDE's insertion point in Word is (x, center_y, height) on screen, through COM. None if it is not visible."""
    from . import word
    return word.caret(hwnd)


def notify_combo_edit(edit):
    """The text box of an editable combo (the File name box of Open/Save dialogs, address bars, search boxes): its owner only learns
    about a change through the combo's CBN_EDITCHANGE / CBN_EDITUPDATE, which Windows sends for TYPED text only. Without them the
    real Save As dialog of Notepad++ saved under its OLD name although the box showed the new one (a false 'ok', seen live). The
    notification the user's typing would have produced is sent, so the dialog takes the new text."""
    try:
        combo = win32gui.GetParent(edit)
        if not combo or win32gui.GetClassName(combo).lower() not in ("combobox", "comboboxex32"):
            return False
        owner = win32gui.GetParent(combo)
        cid = win32gui.GetDlgCtrlID(combo)
        for code in (5, 6):                                              # CBN_EDITCHANGE, CBN_EDITUPDATE
            win32gui.SendMessage(owner, win32con.WM_COMMAND, win32api.MAKELONG(cid & 0xFFFF, code), combo)
        return True
    except Exception:
        return False


def _read_back(ctrl, text, replace):
    """After typing into a field, read what it contains and say whether the typed text is really there (empty if it cannot be read)."""
    v = field_value(ctrl)
    if v is None:
        return ""
    norm = v.replace("\r\n", "\n").replace("\r", "").strip()
    flat = lambda s: s.replace("\r", "").replace("\n", "")                  # many apps report a multi-line field with its line breaks dropped
    ok = (norm == text.strip() or flat(norm) == flat(text.strip())) if replace else (text.strip() in norm or flat(text.strip()) in flat(norm))
    tail = norm[-50:] if len(norm) > 50 else norm
    return " | read back: " + ("the field now contains the text" if ok else "WARNING: the field does NOT contain the typed text") + f' ("{tail}")'


def _edit_selection(hwnd):
    """EM_GETSEL with full 32-bit offsets (the return value packs only 16 bits each)."""
    u = ctypes.WinDLL("user32", use_last_error=True)
    send = u.SendMessageW
    send.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
    send.restype = ctypes.wintypes.LPARAM
    start, end = ctypes.wintypes.DWORD(), ctypes.wintypes.DWORD()
    send(hwnd, 0x00B0, ctypes.addressof(start), ctypes.addressof(end))
    return start.value, end.value


def _typing_busy(hwnd, ctrl=None, native=0):
    """Yield when the user is editing this very field; independent background fields remain usable."""
    if win32gui.GetForegroundWindow() != hwnd:
        return False
    focused = native and _focus_hwnd(hwnd) == native
    if ctrl is not None:
        try:
            focused = focused or ctrl.HasKeyboardFocus
        except Exception:
            pass
    return bool(focused and (_idle_ms() < 250 or _buttons_down() or _keys_down()))


def _set_accessible_text(ctrl, text, replace):
    """Try only value setters; never focus a field to route keyboard messages to it."""
    for getter in ("GetValuePattern", "GetLegacyIAccessiblePattern"):
        try:
            pat = getattr(ctrl, getter)()
            if not pat or getattr(pat, "IsReadOnly", False) or getattr(pat, "State", 0) & (0x40 | 0x20000000):
                continue
            old = pat.Value
            if old is None and not replace:
                continue                # unknown old content must not be erased by an append
            value = text if replace else str(old) + text
            if pat.SetValue(value) is False:
                continue
            return "ok (UI Automation " + ("LegacyIAccessible.SetValue" if "Legacy" in getter else "SetValue") + ")" + _read_back(ctrl, text, replace)
        except Exception:
            continue
    return None


def type_text(query, text, target=None, replace=False, progress=None):
    """Explicit targets use RichEdit ranges, native edits or value patterns. Unsupported targets never focus or receive blind keys."""
    hwnd = find_window(query); st = _state.get(hwnd)
    if win32gui.GetClassName(hwnd) == "OpusApp":                 # Word ignores WM_CHAR: typing is done through COM, at Claude's OWN point
        from . import word
        try:
            out = word.type_text(hwnd, text, replace, progress)
        except Exception as e:
            out = f"Word COM error: {e.__class__.__name__}: {e}"
        if out:
            return out
    dest = None
    if surface(hwnd) == "remote":
        from . import remote
        where = ""
        if target is not None:                                       # first click the field (an id from look, or x,y of the last image)
            if isinstance(target, tuple):
                k = st.get("scale", 1.0) if st else 1.0
                x, y = int(target[0] * k), int(target[1] * k)
            elif st and target in st["items"]:
                r = st["items"][target].rect; x, y = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
            else:
                return "did not type: unknown id: look at the remote screen again"
            if remote.click(hwnd, x, y) == "busy":
                return "did not type: you are pressing the mouse; retry when you let go"
            time.sleep(0.25); where = f" after clicking at {x},{y}"
        if replace:
            remote.key(hwnd, "ctrl+a"); time.sleep(0.05)
        out = remote.type_text(hwnd, text)
        return out.replace("ok (", f"ok (clicked the field{where}; " if where else "ok (", 1) if out.startswith("ok") else out
    if target is not None and st and target in st["items"] and getattr(st["items"][target], "src", "") == "sci":
        from . import scintilla
        return scintilla.write(st["items"][target].hwnd, text, replace)
    if target is not None and st and target in st["items"] and getattr(st["items"][target], "src", "") == "jab":
        it = st["items"][target]
        from . import java
        if _typing_busy(hwnd, it.ctrl):
            return "did not type: you are editing this field; your keyboard and selection were left untouched"
        if it.kind in ("combobox", "list"):
            return java.select_option(it.ctrl, text) or "did not select: this Java list exposes no entries through the accessibility bridge"
        out = java.set_text(it.ctrl, text, replace)
        if out:
            LAST_TYPED[hwnd] = it
            return out
        return ("did not type: this Java element is not an editable text through the accessibility bridge; "
                "look again and use the id of the text field")
    if target is not None and st and target in st["items"] and st["items"][target].ctrl is not None:
        ctrl = st["items"][target].ctrl
        nh, cls = _native(ctrl)
        if _typing_busy(hwnd, ctrl, nh):
            return "did not type: you are editing this field; your keyboard and selection were left untouched"
        if nh and "richedit" in cls:
            expected_title = st.get("title")
            if expected_title is not None and expected_title.lstrip("*").strip() != win32gui.GetWindowText(hwnd).lstrip("*").strip():
                return "did not type: the document/tab title changed since look(); look again and confirm the intended document"
            from . import richedit
            done = richedit.type_text(nh, text, replace)
            if done is not None:
                return done
        if nh and "edit" in cls:
            crlf = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")      # a classic edit box breaks a line only with CR LF
            selection, tick = _edit_selection(nh), _input_tick()
            if tick is None:
                return "did not type: Windows could not report user input state; your selection was left untouched"
            try:
                if replace:
                    win32gui.SendMessage(nh, win32con.WM_SETTEXT, 0, crlf)
                else:
                    end = win32gui.SendMessage(nh, win32con.WM_GETTEXTLENGTH, 0, 0)
                    win32gui.SendMessage(nh, 0x00B1, end, end)          # -1 removes the selection; it does NOT mean end of document
                    win32gui.SendMessage(nh, 0x00C2, 1, crlf)
            finally:
                if tick is not None and _input_tick() == tick:
                    win32gui.SendMessage(nh, 0x00B1, *selection)        # preserve their caret; do not undo a new selection they made meanwhile
            notify_combo_edit(nh)
            return ("ok (WM_SETTEXT)" if replace else "ok (EM_REPLACESEL)") + _read_back(ctrl, text, replace)
        from . import actions
        picked = actions.select_option(ctrl, text)                       # drop-down or list: pick the entry with that text
        if picked:
            return picked
        if nh and "combobox" in cls:                                     # editable classic combo: its inner text box
            inner = win32gui.FindWindowEx(nh, 0, "Edit", None)
            if inner:
                win32gui.SendMessage(inner, win32con.WM_SETTEXT, 0, text)
                notify_combo_edit(inner)
                return "ok (WM_SETTEXT into the drop-down's text box)" + _read_back(ctrl, text, True)
        done = _set_accessible_text(ctrl, text, replace)
        if done:
            return done
        return "did not type: this control exposes no writable value interface; focusing it or sending keys to another field would interfere with your input"
    dest = dest or _focus_hwnd(hwnd)
    if not dest:
        return "did not type: no unambiguous input control belongs to this window; another window's caret was not used"
    page = dest == chromium_input(hwnd) and dest != 0
    if target is not None:
        return "did not type: the target has no live control; look again instead of typing at the user's caret"
    if not wait_free_keys(dest):
        return "did not type: you are holding a modifier key or typing in this same window; your input was left untouched"
    for ch in text.replace("\r\n", "\n").replace("\n", "\r"):                  # a line break is Enter (CR), not LF
        win32gui.PostMessage(dest, win32con.WM_CHAR, ord(ch), 0)
    where = "to the page of this Chromium/Electron app" if page else "to the window"
    return f"ok ({len(text)} characters through WM_CHAR {where}; unconfirmed)"

_VK = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
       "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdn": 0x22, "space": 0x20,
       **{f"f{i}": 0x6F + i for i in range(1, 13)}}

LAST_TYPED = {}       # hwnd -> the Java field Claude typed into last (where enter/backspace apply)
JAVA_KEYS = {"enter": ("notify-field-accept", "insert-break"), "backspace": ("delete-previous",), "delete": ("delete-next",),
             "home": ("caret-begin-line", "caret-begin"), "end": ("caret-end-line", "caret-end"), "ctrl+a": ("select-all",),
             "tab": ("insert-tab",)}


def _java_key(hwnd, name):
    """A key on a Java text field = the field's own editor action (what the key would trigger), through the bridge. None if not one."""
    acts = JAVA_KEYS.get(name.lower().replace(" ", ""))
    if not acts:
        return None
    from . import java
    field = LAST_TYPED.get(hwnd)
    if field is None:
        focused = [it for it in _state.get(hwnd, {}).get("items", {}).values() if getattr(it, "src", "") == "jab" and it.kind == "edit"
                   and it.ctrl is not None and it.ctrl.HasKeyboardFocus]
        field = focused[0] if len(focused) == 1 else None
    if field is None:
        return None
    try:
        return java.do_named(field.ctrl, acts, f"key '{name}' on «{field.name[:30]}»")
    except Exception:
        return None


def key_lparam(vk, up=False):
    """The lParam a real key press carries: repeat count 1, its scancode and the extended bit (arrows, home/end, insert/delete...).
    Apps that read the scancode (games, Java, Chromium, remote clients) ignore a key whose lParam is 0."""
    sc = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ext = vk in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E)
    return 1 | (sc << 16) | ((1 << 24) if ext else 0) | (0xC0000000 if up else 0)


def key(query, name):
    """Single keys by message, no focus. Shortcuts (ctrl+s...) go through actions.shortcut: the app's own command, never real keys."""
    hwnd = find_window(query); parts = name.lower().split("+")
    surf = surface(hwnd)
    if surf == "remote":
        from . import remote
        return remote.key(hwnd, name)
    if surf == "java":
        done = _java_key(hwnd, name)
        if done:
            return done
    if len(parts) == 1 and parts[0] in ("enter", "space", "tab"):              # Scintilla: the key's character at Claude's point
        from . import scintilla
        eds = scintilla.editors(hwnd)
        if len(eds) == 1:
            return scintilla.write(eds[0], {"enter": "\n", "space": " ", "tab": "\t"}[parts[0]])
    if len(parts) == 1 and parts[0] in _VK and win32gui.GetClassName(hwnd) == "OpusApp":
        from . import word                                       # Word: the key would go to the USER's caret; it is done at Claude's point instead
        if parts[0] == "enter":
            out = word.type_text(hwnd, "\r")
        elif parts[0] == "backspace":
            out = word.backspace(hwnd)
        elif parts[0] == "space":
            out = word.type_text(hwnd, " ")
        else:
            return (f"did not press '{parts[0]}': in Word, movement or delete keys would move or delete at YOUR caret. "
                    f"Available in Word: enter, space, backspace (only at Claude's point)")
        if out:
            return out
    if len(parts) == 1 and parts[0] not in _VK and len(parts[0]) > 1:
        return f"did not press '{name}': unknown key. Keys: {', '.join(_VK)}; shortcuts like ctrl+s"
    if len(parts) == 1 and parts[0] in _VK:
        documents = [it for it in _state.get(hwnd, {}).get("items", {}).values()
                     if getattr(it, "kind", "") == "document" and it.ctrl is not None
                     and "richedit" in _native(it.ctrl)[1] and not is_secret_field(it)]
        if len(documents) == 1:
            if popup_windows(hwnd):
                return "did not press: a dialog/menu is open; target it explicitly instead of editing the document behind it"
            if parts[0] in ("enter", "space"):
                return type_text(query, "\r" if parts[0] == "enter" else " ", documents[0].id)
            return f"did not press '{parts[0]}': RichEdit movement/deletion keys would affect the user's caret; use type by id, or enter/space at the document end"
        if not wait_free_keys(hwnd):
            return "did not press the key: you are holding a modifier key, or typing in this same window, and it would mix with your input; retry in a moment"
        tgt = _focus_hwnd(hwnd); vk = _VK[parts[0]]
        if not tgt and not _thread_focus(hwnd) and win32gui.IsWindow(hwnd):
            tgt = hwnd                                           # nothing in the app has the focus: the window itself takes the key (F5...)
        if not tgt:
            return "did not press: no unambiguous input control belongs to this window; another window's caret was not used"
        if "richedit" in win32gui.GetClassName(tgt).lower():
            return "did not press: look at this RichEdit document first; raw keys would affect the user's caret"
        win32gui.PostMessage(tgt, win32con.WM_KEYDOWN, vk, key_lparam(vk)); win32gui.PostMessage(tgt, win32con.WM_KEYUP, vk, key_lparam(vk, True))
        return "ok (key sent by message to the app, at its focus point)"
    from . import actions
    return actions.shortcut(query, name)


# ---------- windows always at the back ----------
def top_windows():
    """Visible windows with a title, from top (first) to bottom (last) in z-order."""
    out = []
    win32gui.EnumWindows(lambda h, _: out.append(h) if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h)
                         and win32gui.GetClassName(h) not in ("Progman", "WorkerW") else None, None)
    return out

LAST_TUCK = {}          # hwnd -> reason for the last decision (diagnostics)

def should_tuck(is_window, is_fg, user_in_same_app, already_top, cursor_over, is_new=False):
    """Pure rule: (send_down: bool, reason). A window the user is using or that is already on top is never sent down."""
    if not is_window:
        return False, "the window no longer exists"
    if is_fg:
        return False, "it is the user's active window"
    if user_in_same_app:
        return False, "the user is working in another window of that app"
    if already_top and not is_new:                 # a newly created window is always born on top: that does not count
        return False, "it was already on top"
    if cursor_over and not is_new:                 # a new window appearing under the cursor is not the user "using" it
        return False, "the user's mouse is over it"
    return True, "send down"

def _facts(hwnd):
    fg = win32gui.GetForegroundWindow()
    ws = top_windows()
    above = ws[:ws.index(hwnd)] if hwnd in ws else ws
    already_top = all(win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST for h in above)
    under = win32gui.WindowFromPoint(win32api.GetCursorPos())
    cursor_over = bool(under) and ctypes.windll.user32.GetAncestor(under, 2) == hwnd       # GA_ROOT
    same_app = bool(fg) and fg != hwnd and win32process.GetWindowThreadProcessId(fg)[1] == win32process.GetWindowThreadProcessId(hwnd)[1]
    return win32gui.IsWindow(hwnd), fg == hwnd, same_app, already_top, cursor_over

def tuck(hwnd, is_new=False):
    """Send ONE window to the back without activating or moving it, unless the user is using it or it was already on top.
    is_new=True: pc_control just opened it (it is born on top by force, so that criterion does not apply)."""
    ok, why = should_tuck(*_facts(hwnd), is_new=is_new)
    LAST_TUCK[hwnd] = why
    if not ok:
        return False
    if win32gui.IsIconic(hwnd):
        return False                 # minimized = already out of the way; it is NEVER restored (it would show on screen)
    win32gui.SetWindowPos(hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
    return True

def tuck_new(pid, before):
    """Send down only the windows of that process that did NOT exist before the action (new dialogs)."""
    return sum(1 for h in top_windows() if h not in before and win32process.GetWindowThreadProcessId(h)[1] == pid and tuck(h, True))

def is_hidden(hwnd):
    """True if the window is entirely outside all monitors."""
    l, t, r, b = win32gui.GetWindowRect(hwnd); vx, vy, vw, vh = virtual_screen()
    return r <= vx or l >= vx + vw or b <= vy or t >= vy + vh


# ---------- apps opened by PC-Control on your desktop: minimized, with their button in the taskbar ----------
def open_minimized(command, wait_s=10.0, creationflags=0, env=None):
    from .focus import FocusGuard
    exe = os.path.basename(_exe_of(command)).lower().removesuffix(".exe")
    focus_guard = FocusGuard(exe).start()
    try:
        return _open_minimized(command, wait_s, creationflags, focus_guard, env)
    finally:
        # Some apps take the foreground LATE (Settings brought its existing window up ~3.6 s after the launch, when open_app had
        # already answered): the guard keeps watching for a while after the answer, off the caller's time, then closes.
        import threading
        t = threading.Timer(BACK_GRACE_S, focus_guard.close); t.daemon = True; t.start()


SOFT_MINIMIZE_S = 0.6
_SOFT = {}                     # hwnd -> when it was sent behind all windows (minimized SOFT_MINIMIZE_S later)


def visible_above(h):
    """The first VISIBLE window right above h: its place in the stacking order. Invisible helpers (an app's IME window) are glued
    above their owner and Windows ignores them as a place to insert after (seen in tests: the window stayed on top)."""
    u = ctypes.windll.user32
    prev = u.GetWindow(h, 3)                                             # GW_HWNDPREV
    while prev and not win32gui.IsWindowVisible(prev):
        prev = u.GetWindow(prev, 3)
    return prev


def soft_minimize(h, user_window, above=None):
    """An app that ignored "start minimized" is NOT minimized at once (a window flashing away is the flicker the user hates): it is
    sent behind ALL windows (a new window) or back to its own place in the stacking order (`above`: the window that was right above
    it, for one that already existed) without activating it, and minimized a moment later, out of sight. Never if the user has
    brought it to the front meanwhile."""
    now = time.time()
    if now - _SOFT.get(h, 0) < SOFT_MINIMIZE_S + 1.0:
        return                                                       # already on its way
    _SOFT[h] = now
    if h != user_window:
        after = above if (above and above != h and win32gui.IsWindow(above)) else 1             # its old place, or HWND_BOTTOM
        try:
            ctypes.windll.user32.SetWindowPos(h, after, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # NOSIZE | NOMOVE | NOACTIVATE
        except Exception:
            pass
    def later():
        try:
            if win32gui.IsWindow(h) and not win32gui.IsIconic(h) and win32gui.GetForegroundWindow() != h:
                ctypes.windll.user32.ShowWindow(h, 7)                  # SW_SHOWMINNOACTIVE
        except Exception:
            pass
    import threading
    threading.Timer(SOFT_MINIMIZE_S, later).start()


def _open_minimized(command, wait_s, creationflags, focus_guard, env=None):
    """Launch an app MINIMIZED and without activating it: no window on screen, no focus stolen, and its button stays in the
    taskbar so you can open it with a click. If the app ignores the request and appears open, it is minimized immediately."""
    si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW; si.wShowWindow = 7   # SW_SHOWMINNOACTIVE
    before = _all_windows(); fg0 = win32gui.GetForegroundWindow()
    p = focus_guard.launch(command, startupinfo=si, creationflags=creationflags, cwd=app_dir(command), env=env)
    found, honored, t0, last = {}, True, time.time(), 0.0
    family = {p.pid}
    via_shell = os.path.basename(_exe_of(command)).lower() in ("explorer", "explorer.exe")     # shell:AppsFolder\... (Store apps)
    exe_name = os.path.splitext(os.path.basename(_exe_of(command)))[0].lower()
    # A single-instance app (the modern Notepad) hands the request to its EXISTING process, which may restore its minimized window:
    # those windows are watched, and put back minimized if they come up without the user asking.
    reuse = {h: win32gui.GetWindowText(h) for h in before if win32gui.IsWindowVisible(h) and win32gui.IsIconic(h)
             and _proc_name(win32process.GetWindowThreadProcessId(h)[1]) == exe_name}
    above0 = {h: visible_above(h) for h in reuse}                                     # its place in the stacking order
    reused = {}
    next_tree = 0.0
    while time.time() - t0 < wait_s:
        if time.time() >= next_tree:              # launchers hand the window to a child (wmplayer -> setup_wm, git-gui -> wish)
            family |= _descendants(family); next_tree = time.time() + 0.1
        mine = []
        # A launcher stub that hands off and exits (calc.exe -> the Store Calculator's frame) leaves a NEW frame window: it is the app's,
        # unless the user clicked or switched meanwhile (then a new window may be one they opened)
        handoff = (via_shell or p.poll() is not None) and not focus_guard.user_recent()
        win32gui.EnumWindows(lambda h, _: mine.append(h) if win32gui.IsWindowVisible(h) and (
            win32process.GetWindowThreadProcessId(h)[1] in family
            or (handoff and h not in before and win32gui.GetWindowText(h) and win32gui.GetClassName(h) not in SHELL_CLASSES
                and (via_shell or win32gui.GetClassName(h) == "ApplicationFrameWindow") and app_window(h))) else None, None)
        for h in mine:
            if h in before or h in found:
                continue
            try:
                try:
                    focus_guard.verify_window(h, timeout=3.0)
                except RuntimeError:
                    pass                  # a frame that only loads Microsoft-signed DLLs (Store apps): the give-back covers it; the app IS open
                if not win32gui.IsIconic(h) and win32gui.GetWindowText(h) and not focus_guard.user_selected(h):
                    honored = False
                    # The native guard prevents activation where supported; minimizing
                    # must not itself activate anything or undo the user's choice.
                    soft_minimize(h, fg0)
                found[h] = win32gui.GetWindowText(h)[:50]; last = time.time()
            except win32gui.error:
                continue                  # a passing window that closed meanwhile: not the app's
        for h, title in reuse.items():
            if not win32gui.IsWindow(h) or focus_guard.user_recent():
                continue
            if not win32gui.IsIconic(h):
                soft_minimize(h, fg0, above0.get(h)); reused[h] = win32gui.GetWindowText(h)[:50]; last = time.time()
            elif win32gui.GetWindowText(h) != title:
                reused[h] = win32gui.GetWindowText(h)[:50]; last = time.time()
        if (any(found.values()) or (reused and p.poll() is not None)) and time.time() - last > 0.6:
            break
        time.sleep(0.001)
    # Catch late restores without fighting a deliberate user activation.
    end = time.time() + (3.0 if found else 0)
    while time.time() < end:
        for h in list(found) + list(reused):
            if win32gui.IsWindow(h) and win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h) and win32gui.GetClassName(h) != "Windows.UI.Core.CoreWindow" and not focus_guard.user_selected(h) and not focus_guard.user_recent():
                honored = False
                soft_minimize(h, fg0, above0.get(h))
        time.sleep(0.05)
    own = {pid for pid in family if pid != p.pid or not via_shell}       # the app's OWN processes (never the shared shell ones)
    own = {pid for pid in own if _proc_name(pid) not in SHARED_PROCS}
    for h in getattr(focus_guard, "reclaimed", []):
        if h not in found and h not in reused and win32gui.IsWindow(h):
            reused[h] = win32gui.GetWindowText(h)[:50]
    note = "" if any(found.values()) else (
        "single-instance app: it opened in its EXISTING window " + "; ".join(f"{h} «{t}»" for h, t in reused.items())
        + " (left as it was; if it came to the front, the foreground was given back to the user)" if reused
        else "no window with a title (it may be single-instance, or take longer)")
    return {"pid": p.pid, "pids": sorted(own), "windows": [{"hwnd": h, "title": t} for h, t in found.items() if t],
            "minimizada_de_origen": honored, "foco_intacto": win32gui.GetForegroundWindow() == fg0, "reused": list(reused), "note": note}


def app_window(h):
    """A real app window (it has a frame or a taskbar button), not a helper: XAML 'PopupHost', tool windows, tooltips, drop shadows.
    Seen live: opening Settings produced a new 'PopupHost' window that was taken for the app."""
    try:
        style, ex = win32gui.GetWindowLong(h, win32con.GWL_STYLE), win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
    except Exception:
        return False
    if ex & win32con.WS_EX_TOOLWINDOW or win32gui.GetWindowText(h) in ("PopupHost", "DesktopWindowXamlSource"):
        return False
    return win32gui.GetClassName(h) == "ApplicationFrameWindow" or bool(ex & win32con.WS_EX_APPWINDOW) or (style & win32con.WS_CAPTION) == win32con.WS_CAPTION


SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "PC-Control-Overlay"}     # the desktop and taskbar


SHARED_PROCS = {"explorer", "applicationframehost", "svchost", "dllhost", "runtimebroker", "conhost", "openconsole",
                "windowsterminal", "sihost", "shellexperiencehost", "startmenuexperiencehost", "searchhost", "textinputhost"}


def _proc_name(pid):
    try:
        return psutil_name(pid)
    except Exception:
        return ""


def _all_windows():
    """Every top-level window that exists right now (visible or not, with or without title): anything not in here appeared later."""
    out = set()
    win32gui.EnumWindows(lambda h, _: out.add(h), None)
    return out


def _exe_of(command):
    import shlex
    try:
        return shlex.split(command, posix=False)[0].strip('"')
    except (ValueError, IndexError):
        return ""


def _descendants(pids):
    """Child processes (any depth) of these pids: the window of a launcher often belongs to a process it started."""
    from .perm import _procs
    procs = _procs(); out = set(pids); grew = True
    while grew:
        new = {pid for pid, (ppid, _) in procs.items() if ppid in out and pid not in out}
        out |= new; grew = bool(new)
    return out


def app_dir(command):
    """The folder of the program being launched, used as its working folder (what Windows shortcuts do). Some apps (OBS...) only
    find their own files from there. None if the program is not given with a path."""
    import shlex
    try:
        exe = shlex.split(command, posix=False)[0].strip('"')
    except (ValueError, IndexError):
        return None
    d = os.path.dirname(os.path.expandvars(exe))
    return d if d and os.path.isdir(d) else None


CHROMIUM = {"msedge", "chrome", "brave", "opera", "vivaldi", "chromium", "arc", "thorium"}


_EVENT_PROC = ctypes.WINFUNCTYPE(None, ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.wintypes.HWND, ctypes.wintypes.LONG,
                                 ctypes.wintypes.LONG, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD)


class _BackOnCreate:
    """While active, every NEW top-level window of the given processes (or of programs with that name) is sent to the very back the instant
    Windows creates it, BEFORE the app shows it, and again if the app activates itself. A window that is never shown above the others is
    never seen above them: it does not flash over what the user is doing and, sitting at the back, most apps do not take the focus either.
    Polling for the window and sending it back afterwards (the old way) left it drawn on top for 20-45 ms; this leaves 0 ms for most apps.
    Only windows that did not exist before are touched: the user's own windows of the same program are never sunk."""
    def __init__(self, pids, exe, before, focus_guard=None):
        self.pids, self.exe, self.before, self.sent = pids, exe, set(before), []
        self.focus_guard = focus_guard
        threading = __import__("threading")
        self.stop = threading.Event(); self.ready = threading.Event(); self.thread = None

    def _callback(self, hook, event, hwnd, id_object, id_child, thread, ms):
        if id_object != 0 or id_child != 0 or hwnd in self.before:           # OBJID_WINDOW only, and never a window that was already there
            return
        try:
            u = self.u
            if u.GetAncestor(hwnd, 2) != hwnd:                                # top-level windows only
                return
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            if pid in self.pids or psutil_name(pid) == self.exe:
                if self.focus_guard and self.focus_guard.user_selected(hwnd):
                    return  # a deliberate click/shell switch belongs to the user
                # Z-order changes must never activate the window themselves.
                # The native CBT guard rejects automatic activation BEFORE this event.
                u.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0013)
                self.sent.append(hwnd)
        except Exception:
            pass

    def _pump(self):
        u = self.u = ctypes.WinDLL("user32", use_last_error=True)           # our own copy: the prototypes below must not leak to other code
        u.SetWinEventHook.restype = ctypes.wintypes.HANDLE
        u.SetWinEventHook.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.HMODULE, _EVENT_PROC,
                                      ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
        u.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]; u.GetAncestor.restype = ctypes.wintypes.HWND
        proc = _EVENT_PROC(self._callback)                                    # kept alive here for as long as the hooks live
        hooks = [u.SetWinEventHook(0x8000, 0x8002, None, proc, 0, 0, 0),     # EVENT_OBJECT_CREATE .. EVENT_OBJECT_SHOW
                 u.SetWinEventHook(0x0003, 0x0003, None, proc, 0, 0, 0)]     # EVENT_SYSTEM_FOREGROUND: the app activated itself
        self.ready.set()
        msg = ctypes.wintypes.MSG()
        while not self.stop.is_set():
            if u.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):                  # events reach this thread as messages
                u.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.0003)
        for h in hooks:
            if h:
                u.UnhookWinEvent(h)

    def start(self):
        self.thread = __import__("threading").Thread(target=self._pump, name="pc-control-back-on-create", daemon=True)
        self.thread.start(); self.ready.wait(2)
        return self

    def close(self):
        self.stop.set()


BACK_GRACE_S = 8.0           # the hook outlives open_behind by this long: apps open a second window (splash, then the main one) a bit later


def open_behind(command, exe, wait_s=15.0, env=None):
    """Open an app NOT minimized but behind all the user's windows, without activating it and without drawing it above anything: its button
    is in the taskbar and its window can be read and used (browsers and some apps hide their content while minimized). The focus is never
    taken by us; the window is sent to the back the instant it is created (see _BackOnCreate). Returns as soon as the window is up."""
    before = _all_windows(); fg0 = win32gui.GetForegroundWindow()
    si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW; si.wShowWindow = 4   # SW_SHOWNOACTIVATE
    pids = set()
    from .focus import FocusGuard
    focus_guard = FocusGuard(exe).start()
    hook = _BackOnCreate(pids, exe, before, focus_guard).start()
    try:
        p = focus_guard.launch(command, startupinfo=si, cwd=app_dir(command), env=env); pids.add(p.pid)
        found, t0, last = {}, time.time(), 0.0
        while time.time() - t0 < wait_s:
            for h in top_windows():
                if h in before or h in found:
                    continue
                try:
                    pid = win32process.GetWindowThreadProcessId(h)[1]
                except Exception:
                    continue
                if pid == p.pid or psutil_name(pid) == exe:          # also a window handed to an already-running browser
                    try:
                        try:
                            focus_guard.verify_window(h, timeout=3.0)
                        except RuntimeError:
                            pass          # cannot be verified (Microsoft-signed-only frame): the give-back covers it; the app IS open
                        if not focus_guard.user_selected(h):
                            ctypes.windll.user32.SetWindowPos(h, 1, 0, 0, 0, 0, 0x0013)
                        found[h] = win32gui.GetWindowText(h)[:50]; last = time.time()
                    except win32gui.error:
                        continue          # a passing window that closed meanwhile
            if any(found.values()) and time.time() - last > 0.3:
                break
            time.sleep(0.005)
        if exe in CHROMIUM:
            time.sleep(0.8)                                                    # a browser's title settles once the page has loaded
    finally:
        threading = __import__("threading")
        t = threading.Timer(BACK_GRACE_S, hook.close); t.daemon = True; t.start()      # keep catching late windows, off the caller's time
        f = threading.Timer(BACK_GRACE_S, focus_guard.close); f.daemon = True; f.start()
    wins = [{"hwnd": h, "title": win32gui.GetWindowText(h)[:50]} for h in found if win32gui.IsWindow(h)]
    return {"pid": p.pid, "windows": [w for w in wins if w["title"]], "foco_intacto": win32gui.GetForegroundWindow() == fg0,
            "note": "" if wins else "no window with a title (it may have joined a browser that was already open)"}

def psutil_name(pid):
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520); n = ctypes.wintypes.DWORD(520)
        k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return os.path.splitext(os.path.basename(buf.value))[0].lower()
    finally:
        k.CloseHandle(h)
