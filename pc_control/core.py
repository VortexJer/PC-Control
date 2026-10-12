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

_state = {}          # hwnd -> {"items": {id: Item}, "lines": set}


class Item:
    __slots__ = ("id", "kind", "name", "rect", "ctrl", "src", "clickable", "value", "aid", "parent")
    def __init__(self, kind, name, rect, ctrl=None, src="uia", clickable=False):
        self.id = 0; self.kind = kind; self.name = name; self.rect = rect
        self.ctrl = ctrl; self.src = src; self.clickable = clickable
        self.value = ""                                      # what a text field currently contains (never for password fields)
        self.aid = ""                                        # its AutomationId when it was looked at (identity check before acting)
        self.parent = None                                   # (kind, name) of the container it was in (a namesake elsewhere is not it)


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
        pat = ctrl.GetValuePattern()
        if editable_only and pat.IsReadOnly:
            return None
        return pat.Value
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
    s = min(1.0, edge / max(w, h))
    return max(1, round(w * s)), max(1, round(h * s))

def txt_cost(s):
    """Tokens of a text. Fitted on real look() outputs against a BPE tokenizer: visible characters cost ~0.45 each and every
    run of spaces or line break ~0.56 (runs of spaces merge into few tokens). len/3.6 was off by -39 % to +49 %; this, by about 10 %."""
    visible = sum(1 for ch in s if not ch.isspace())
    runs = s.count("\n") + len(re.findall(r" {2,}", s))
    return round(0.45 * visible + 0.56 * runs)


# ---------- windows ----------
def find_window(query):
    q = str(query).lower().strip()
    if not q:
        raise LookupError("no window given: use a title substring or an hwnd from windows()")
    if q.isdigit():
        if not win32gui.IsWindow(int(q)):
            raise LookupError(f"no window with hwnd {q} : it was closed, or the app replaced it (a start-up window often becomes the main one): call windows() to find it again")
        return int(q)
    hits = []
    def cb(h, _):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            if t and q in t.lower() and win32gui.GetClassName(h) != "PC-Control-Overlay":
                hits.append((h, t))
    win32gui.EnumWindows(cb, None)
    if not hits:
        raise LookupError(f"no window with '{query}' in its title: call windows()")

    hits = [(h, t) for h, t in hits if not (win32gui.GetClassName(h) == "ApplicationFrameWindow"       # a Store app's frame and its
                                            and sum(1 for _, t2 in hits if t2 == t) > 1)] or hits       # content: one app, same title
    if len(hits) == 1:
        return hits[0][0]
    exact = [h for h, t in hits if t.lower().strip() == q]
    if len(exact) == 1:
        return exact[0]
    # several windows match (two File Explorer windows, two terminals of the same app...): guessing would act on the wrong one
    raise AmbiguousWindow(f"{len(hits)} windows match '{query}': " + "; ".join(f"{h} «{t[:40]}»" for h, t in hits[:6])
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
    return img if ok and img.max() >= 8 else None


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
            kind = c.ControlTypeName; name = (c.Name or "").strip(); r = c.BoundingRectangle; off = c.IsOffscreen
        except Exception:
            continue
        if kind in ("ScrollBarControl", "ThumbControl"):         # "line up", "page down"...: noise in every app; scroll() does that job
            continue
        if not name and kind in NAMELESS_OK:                     # Chromium drops a field's name once used; icon buttons often have none: keep them
            name = fallback_name(c, kind)
        if (offscreen_ok or (not off and r.width() > 0 and r.height() > 0)) and kind in TEXTY and name:
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
        if d < 28:                                              # web pages (Chromium/Electron) nest deep; max_nodes and budget_s still bound the walk
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


def walk_all(hwnd, rect, **kw):
    """The window tree plus the entries of open menus that are another window (the Windows context menu, drop-downs...)."""
    items = walk_uia(hwnd, rect, **kw)
    for h in popup_windows(hwnd):
        try:
            items += [i for i in walk_uia(h, rect, offscreen_ok=True) if i.kind in ("menuitem", "menu")]
        except Exception:
            pass
    return items


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
        if sum(len(i.name) for i in items) >= 40:            # a minimized window has no image alternative: any tree with real text is worth sending
            assign_ids(hwnd, items)
            text = list_format(items); tok = txt_cost(text)
            _state[hwnd] = {"items": {it.id: it for it in items}, "lines": {line_key(it) for it in items},
                            "rect": (0, 0, 0, 0), "scale": 1.0, "mode": "uia", "pid": window_pid(hwnd)}
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
    if mini:
        rect = (0, 0, 0, 0)
    img = None if mini else grab(hwnd, rect)
    if img is None and not mini:
        res["note"] = "capture not available (black/protected window)"
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
        _state[hwnd] = {"items": {it.id: it for it in items}, "lines": {line_key(it) for it in items if it.src == "uia"},
                        "rect": rect, "scale": scale, "mode": kind, "pid": window_pid(hwnd)}
        return res

    def numbered(items):
        assign_ids(hwnd, items)
        return items

    if mode == "image":
        if img is None:
            res.update(mode="none", why="could not capture", tokens=0); return res
        known[:] = walk_all(hwnd, rect)            # ids for the numbered marks on the image
        return finish("image", "image requested")
    # 1) Windows tree (free and exact)
    uia = walk_all(hwnd, rect); known = uia
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
    now = {line_key(i) for i in walk_uia(hwnd, rect, offscreen_ok=bool(win32gui.IsIconic(hwnd)))}
    before = st["lines"]; st["lines"] = now
    add, rem = sorted(now - before), sorted(before - now)
    if not add and not rem:
        if st.get("mode") == "uia+ocr":              # the window's real text came from OCR: the tree not changing proves nothing
            return "no change in the UI tree (this window's text comes from OCR, so the effect cannot be measured): check with look"
        return "no changes"
    if len(add) + len(rem) > 12:                 # the view scrolled or was replaced: a list of lines would be noise
        return f"the view changed a lot (+{len(add)} / -{len(rem)} lines): look to see it"
    f = lambda L: "; ".join(x.split("|", 1)[1][:30] for x in L[:6]) + (f" (+{len(L) - 6})" if len(L) > 6 else "")
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
    return any(win32api.GetAsyncKeyState(vk) & 0x8000 for vk in (0x01, 0x02, 0x04))


def _idle_ms():
    """Milliseconds since the user's last input (mouse or keyboard)."""
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LII(); li.cbSize = ctypes.sizeof(LII)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF


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
    while _idle_ms() < quiet * 1000:
        if time.time() > end:
            return False
        time.sleep(0.03)
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
    tgt, (cx, cy) = _child_at(hwnd, sx, sy); lp = win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)
    if not right and not double and "button" in win32gui.GetClassName(tgt).lower():
        win32gui.SendMessage(tgt, 0x00F5, 0, 0)          # BM_CLICK: standard button, no focus
        return "button"
    if not wait_user_mouse_idle():
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
    k = st.get("scale", 1.0) if st else 1.0
    pts = [(int(x * k), int(y * k)) for x, y in shape_points(shape, points)]
    if len(pts) < 2:
        return "at least 2 points are needed (or 2 corners for rect/ellipse)"
    path = _densify(pts, step)
    l, t = win32gui.GetWindowRect(hwnd)[:2]
    if not wait_user_mouse_idle():
        return "did not drag: you are pressing or dragging with the mouse and a drag of mine would break your gesture; retry when you let go"
    tgt, _ = _child_at(hwnd, l + path[0][0], t + path[0][1])
    mk, d, u = (win32con.MK_RBUTTON, win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP) if right else (win32con.MK_LBUTTON, win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP)
    lp = lambda p: win32api.MAKELONG(win32gui.ScreenToClient(tgt, (l + p[0], t + p[1]))[0] & 0xFFFF, win32gui.ScreenToClient(tgt, (l + p[0], t + p[1]))[1] & 0xFFFF)
    try:
        win32gui.PostMessage(tgt, win32con.WM_MOUSEMOVE, 0, lp(path[0]))
        win32gui.PostMessage(tgt, d, mk, lp(path[0]))
        for p in path[1:]:
            win32gui.PostMessage(tgt, win32con.WM_MOUSEMOVE, mk, lp(p))
            time.sleep(delay)
    finally:
        win32gui.PostMessage(tgt, u, 0, lp(path[-1]))                 # the button is ALWAYS released, whatever happens
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
    if isinstance(target, int):
        if not st or target not in st["items"]:
            return "unknown id: call look() first"
        it = st["items"][target]
        if it.ctrl is not None:
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
    how = _post_click(hwnd, x, y, right, double)
    if how == "busy":
        return "did not press: you are pressing or dragging with the mouse and a click of mine would break your gesture; retry when you let go"
    return f"ok (BM_CLICK at {x},{y})" if how == "button" else f"ok (mouse message at {x},{y} of the window)"

def _focus_hwnd(hwnd):
    tid = win32process.GetWindowThreadProcessId(hwnd)[0]
    class GTI(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint), ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                    ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p), ("hwndMoveSize", ctypes.c_void_p),
                    ("hwndCaret", ctypes.c_void_p), ("rcCaret", ctypes.c_long * 4)]
    g = GTI(); g.cbSize = ctypes.sizeof(GTI); ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(g))
    return g.hwndFocus or hwnd

def word_caret(hwnd):
    """Where CLAUDE's insertion point in Word is (x, center_y, height) on screen, through COM. None if it is not visible."""
    from . import word
    return word.caret(hwnd)


def _read_back(ctrl, text, replace):
    """After typing into a field, read what it contains and say whether the typed text is really there (empty if it cannot be read)."""
    v = field_value(ctrl)
    if v is None:
        return ""
    norm = v.replace("\r\n", "\n").replace("\r", "").strip()
    ok = (norm == text.strip()) if replace else (text.strip() in norm)
    tail = norm[-50:] if len(norm) > 50 else norm
    return " | read back: " + ("the field now contains the text" if ok else "WARNING: the field does NOT contain the typed text") + f' ("{tail}")'


def type_text(query, text, target=None, replace=False, progress=None):
    """Order: 1) EM_REPLACESEL/WM_SETTEXT to the control  2) WM_CHAR to the control or to the app's focus. Never activates the window."""
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
    if target is not None and st and target in st["items"] and st["items"][target].ctrl is not None:
        nh, cls = _native(st["items"][target].ctrl)
        if nh and "edit" in cls:
            if replace:
                win32gui.SendMessage(nh, win32con.WM_SETTEXT, 0, text)
                return "ok (WM_SETTEXT)" + _read_back(st["items"][target].ctrl, text, True)
            win32gui.SendMessage(nh, 0x00B1, -1, -1)                     # EM_SETSEL to the end
            win32gui.SendMessage(nh, 0x00C2, 1, text)
            return "ok (EM_REPLACESEL)" + _read_back(st["items"][target].ctrl, text, False)
        ctrl = st["items"][target].ctrl
        from . import actions
        picked = actions.select_option(ctrl, text)                       # drop-down or list: pick the entry with that text
        if picked:
            return picked
        if nh and "combobox" in cls:                                     # editable classic combo: its inner text box
            inner = win32gui.FindWindowEx(nh, 0, "Edit", None)
            if inner:
                win32gui.SendMessage(inner, win32con.WM_SETTEXT, 0, text)
                return "ok (WM_SETTEXT into the drop-down's text box)" + _read_back(ctrl, text, True)
        if not nh:                                                       # no native edit window (web page, WPF, XAML): UI Automation value
            try:
                pat = ctrl.GetValuePattern()
                if pat and not pat.IsReadOnly:
                    pat.SetValue(text if replace else (pat.Value or "") + text)
                    return "ok (UI Automation SetValue)" + _read_back(ctrl, text, replace)
            except Exception:
                pass
        dest = nh or None
    dest = dest or _focus_hwnd(hwnd)
    for ch in text:
        win32gui.PostMessage(dest, win32con.WM_CHAR, ord(ch), 0)
    return f"ok ({len(text)} characters through WM_CHAR; unconfirmed)"

_VK = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
       "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdn": 0x22, "space": 0x20,
       **{f"f{i}": 0x6F + i for i in range(1, 13)}}

def key(query, name):
    """Single keys by message, no focus. Shortcuts (ctrl+s...) go through actions.shortcut: the app's own command, never real keys."""
    hwnd = find_window(query); parts = name.lower().split("+")
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
        if not wait_user_quiet():
            return "did not press the key: you are typing and it would mix with your text; retry when you stop"
        tgt = _focus_hwnd(hwnd); vk = _VK[parts[0]]
        win32gui.PostMessage(tgt, win32con.WM_KEYDOWN, vk, 0); win32gui.PostMessage(tgt, win32con.WM_KEYUP, vk, 0xC0000001)
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
def open_minimized(command, wait_s=10.0, creationflags=0):
    """Launch an app MINIMIZED and without activating it: no window on screen, no focus stolen, and its button stays in the
    taskbar so you can open it with a click. If the app ignores the request and appears open, it is minimized immediately."""
    si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW; si.wShowWindow = 7   # SW_SHOWMINNOACTIVE
    before = _all_windows(); fg0 = win32gui.GetForegroundWindow()
    p = subprocess.Popen(command, startupinfo=si, creationflags=creationflags, cwd=app_dir(command))
    found, honored, t0, last = {}, True, time.time(), 0.0
    family = {p.pid}
    via_shell = os.path.basename(_exe_of(command)).lower() in ("explorer", "explorer.exe")     # shell:AppsFolder\... (Store apps)
    next_tree = 0.0
    while time.time() - t0 < wait_s:
        if time.time() >= next_tree:              # launchers hand the window to a child (wmplayer -> setup_wm, git-gui -> wish)
            family |= _descendants(family); next_tree = time.time() + 0.1
        mine = []
        win32gui.EnumWindows(lambda h, _: mine.append(h) if win32gui.IsWindowVisible(h) and (
            win32process.GetWindowThreadProcessId(h)[1] in family
            or (via_shell and h not in before and win32gui.GetWindowText(h) and win32gui.GetClassName(h) not in SHELL_CLASSES)) else None, None)
        for h in mine:
            if h in before or h in found:
                continue
            if not win32gui.IsIconic(h) and win32gui.GetWindowText(h):
                honored = False
                # the app ignored nCmdShow: minimize it. If it already took the focus, SW_MINIMIZE (6) hands the focus back to the next
                # window (the user's); otherwise SW_SHOWMINNOACTIVE (7) leaves the focus where it is.
                ctypes.windll.user32.ShowWindow(h, 6 if h == win32gui.GetForegroundWindow() else 7)
            found[h] = win32gui.GetWindowText(h)[:50]; last = time.time()
        if any(found.values()) and time.time() - last > 0.6:
            break
        time.sleep(0.001)
    # Store apps (and some others) activate and restore themselves a moment AFTER appearing: keep them minimized for a while, and if
    # one took the focus, SW_MINIMIZE gives it back to the user's window
    end = time.time() + (3.0 if found else 0)
    while time.time() < end:
        for h in list(found):
            if win32gui.IsWindow(h) and win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h) and win32gui.GetClassName(h) != "Windows.UI.Core.CoreWindow":
                honored = False
                ctypes.windll.user32.ShowWindow(h, 6 if h == win32gui.GetForegroundWindow() else 7)
        time.sleep(0.05)
    own = {pid for pid in family if pid != p.pid or not via_shell}       # the app's OWN processes (never the shared shell ones)
    own = {pid for pid in own if _proc_name(pid) not in SHARED_PROCS}
    return {"pid": p.pid, "pids": sorted(own), "windows": [{"hwnd": h, "title": t} for h, t in found.items() if t],
            "minimizada_de_origen": honored, "foco_intacto": win32gui.GetForegroundWindow() == fg0,
            "note": "" if any(found.values()) else "no window with a title (it may be single-instance, or take longer)"}


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


def open_behind(command, exe, wait_s=15.0):
    """Browsers hide the page from accessibility while minimized, so a browser is opened NOT minimized but behind your windows and
    without activating it: its button is in the taskbar and the page can be read and used. The focus is never taken."""
    si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW; si.wShowWindow = 4   # SW_SHOWNOACTIVATE
    before = _all_windows(); fg0 = win32gui.GetForegroundWindow()
    p = subprocess.Popen(command, startupinfo=si, cwd=app_dir(command))
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
                ctypes.windll.user32.SetWindowPos(h, 1, 0, 0, 0, 0, 0x0013)        # HWND_BOTTOM | NOSIZE | NOMOVE | NOACTIVATE
                found[h] = win32gui.GetWindowText(h)[:50]; last = time.time()
        if found and time.time() - last > 1.0:
            break
        time.sleep(0.01)
    time.sleep(0.8)                                                            # the title settles once the page has loaded
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
