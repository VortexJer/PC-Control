"""More actions, with the same rule as core.py: no focus, no mouse, no keystrokes injected into the user's keyboard.

scroll(query, target, direction, amount)  -> UI Automation ScrollPattern, else scroll-bar / wheel MESSAGES to the control
read_text(query, target, start, length)   -> the WHOLE text of a document, page or field (also what is scrolled out of view)
shortcut(query, combo)                    -> ctrl+s and friends WITHOUT pressing keys: the app's own menu command, the element
                                             that declares that shortcut (UI Automation), or the edit-control message (copy, paste...)
select_option(ctrl, text)                 -> pick an entry of a drop-down / list by its text
find(query, terms, wait)                  -> only the elements whose text matches (also scrolled out of view), optionally waiting
"""
import ctypes, re, time
import uiautomation as auto
import win32api, win32con, win32gui
from . import core

SB = {"up": 0, "down": 1, "pageup": 2, "pagedown": 3, "top": 6, "bottom": 7, "end": 8}      # SB_LINEUP.. SB_TOP/SB_BOTTOM, SB_ENDSCROLL
_CONTAINERS = {"PaneControl", "DocumentControl", "ListControl", "TreeControl", "DataGridControl", "TableControl", "WindowControl",
               "CustomControl", "GroupControl", "EditControl", "ComboBoxControl", "TabControl"}


# ---------- scroll ----------
def _scroll_pat(c):
    try:
        p = c.GetScrollPattern()
        if p and (p.VerticallyScrollable or p.HorizontallyScrollable):
            return p
    except Exception:
        pass
    return None


def _scrollable_from(ctrl, vertical):
    """The element itself or its nearest ancestor that can scroll in that axis."""
    c = ctrl
    for _ in range(25):
        if c is None:
            return None, None
        p = _scroll_pat(c)
        if p and (p.VerticallyScrollable if vertical else p.HorizontallyScrollable):
            return c, p
        try:
            c = c.GetParentControl()
        except Exception:
            return None, None
    return None, None


def _biggest_scrollable(hwnd, vertical, budget_s=3.0):
    """With no target: the biggest visible area of the window that can scroll (the page, the document, the list)."""
    root = auto.ControlFromHandle(hwnd); best = None; t0 = time.time(); stack = [(root, 0)]; n = 0
    while stack and time.time() - t0 < budget_s and n < 2500:
        c, d = stack.pop(); n += 1
        try:
            kind = c.ControlTypeName
        except Exception:
            continue
        if kind in _CONTAINERS:
            p = _scroll_pat(c)
            if p and (p.VerticallyScrollable if vertical else p.HorizontallyScrollable):
                try:
                    r = c.BoundingRectangle; a = r.width() * r.height()
                except Exception:
                    a = 0
                if best is None or a > best[0]:
                    best = (a, c, p)
        if d < 28:
            try:
                stack.extend((ch, d + 1) for ch in c.GetChildren())
            except Exception:
                pass
    return (best[1], best[2]) if best else (None, None)


def _pct(p, vertical):
    try:
        v = p.VerticalScrollPercent if vertical else p.HorizontalScrollPercent
        return None if v is None or v < 0 else round(v)
    except Exception:
        return None


def _native_scroll(nh, direction, amount):
    """Scroll-bar messages to a control with its own window (lists, text boxes, classic apps). Returns (before, after) positions or None."""
    vertical = direction in ("up", "down", "top", "bottom")
    bar = 1 if vertical else 0                                             # SB_VERT / SB_HORZ
    msg = win32con.WM_VSCROLL if vertical else win32con.WM_HSCROLL
    try:
        before = win32gui.GetScrollInfo(nh, bar)[4]
    except Exception:
        return None
    code = {"up": 2, "down": 3, "left": 2, "right": 3, "top": 6, "bottom": 7}[direction]       # page up/down, or top/bottom
    for _ in range(1 if direction in ("top", "bottom") else amount):
        win32gui.SendMessage(nh, msg, code, 0)
    win32gui.SendMessage(nh, msg, SB["end"], 0)
    try:
        after = win32gui.GetScrollInfo(nh, bar)[4]
    except Exception:
        after = before
    return before, after


def _wheel(nh, x, y, direction, amount):
    """Mouse-wheel MESSAGES posted to the window under the point (screen x, y): the user's mouse does not move."""
    delta = 120 * 3 * amount * (1 if direction in ("up", "left") else -1)
    msg = 0x020A if direction in ("up", "down") else 0x020E                 # WM_MOUSEWHEEL / WM_MOUSEHWHEEL (positive = right)
    if msg == 0x020E:
        delta = -delta
    wp = win32api.MAKELONG(0, delta & 0xFFFF); lp = win32api.MAKELONG(x & 0xFFFF, y & 0xFFFF)
    win32gui.PostMessage(nh, msg, wp, lp)


def scroll(query, target=None, direction="down", amount=1):
    """Scroll a window or the element `target` (an id from look) by `amount` pages, or to the top/bottom. No focus, no mouse."""
    hwnd = core.find_window(query); st = core._state.get(hwnd)
    direction = (direction or "down").lower().strip()
    if direction not in ("up", "down", "left", "right", "top", "bottom"):
        return "did not scroll: direction must be up, down, left, right, top or bottom"
    amount = max(1, min(int(amount or 1), 50))
    vertical = direction in ("up", "down", "top", "bottom")
    it = None; cx = cy = None
    if target not in (None, ""):
        it = st["items"].get(int(target)) if st else None
        if it is None:
            return "unknown id: call look() on this window first"
    ctrl, pat = _scrollable_from(it.ctrl, vertical) if (it and it.ctrl is not None) else _biggest_scrollable(hwnd, vertical)
    if pat is not None:                                                     # 1) UI Automation: what a screen reader does
        b = _pct(pat, vertical)
        try:
            if direction in ("top", "bottom"):
                v = 0 if direction == "top" else 100
                pat.SetScrollPercent(-1 if vertical else v, v if vertical else -1, waitTime=0)
            else:
                inc = auto.ScrollAmount.LargeIncrement if direction in ("down", "right") else auto.ScrollAmount.LargeDecrement
                for _ in range(amount):
                    if vertical:
                        pat.Scroll(auto.ScrollAmount.NoAmount, inc, waitTime=0)
                    else:
                        pat.Scroll(inc, auto.ScrollAmount.NoAmount, waitTime=0)
            for _ in range(8):                                         # web pages update the position asynchronously
                time.sleep(0.12)
                a = _pct(pat, vertical)
                if a != b:
                    break
            name = (ctrl.Name or ctrl.ControlTypeName[:-7].lower())[:40]
            if b is not None and a is not None:
                edge = " (already at the edge: nothing more to scroll that way)" if a == b else ""
                return f"ok (UI Automation scroll of «{name}»: {b}% -> {a}%{edge}; ids from the last look still work, look again to see the new content)"
            return f"ok (UI Automation scroll of «{name}»; look again to see the new content)"
        except Exception:
            pass
    nh = 0                                                                  # 2) classic controls: scroll-bar messages
    if it and it.ctrl is not None:
        nh = core._native(it.ctrl)[0]
    if not nh and not win32gui.IsIconic(hwnd):
        l, t, r, bt = win32gui.GetWindowRect(hwnd)
        rel = core.live_rel(hwnd, it) if it else None
        cx, cy = ((l + (rel[0] + rel[2]) // 2, t + (rel[1] + rel[3]) // 2) if rel else ((l + r) // 2, (t + bt) // 2))
        nh = core._child_at(hwnd, cx, cy)[0]
    if nh:
        res = _native_scroll(nh, direction, amount)
        if res and res[0] != res[1]:
            return f"ok (scroll-bar messages to the control: position {res[0]} -> {res[1]}; look again to see the new content)"
        if res and res[0] == res[1] and res[0] > 0 and direction in ("down", "bottom", "right"):
            return "ok (already at the edge: nothing more to scroll that way)"
        if direction in ("top", "bottom"):
            return "did not scroll: this window offers no scroll bar or scroll pattern to jump to the top/bottom; use up/down with an amount"
        if cx is None:
            l, t, r, bt = win32gui.GetWindowRect(hwnd); cx, cy = (l + r) // 2, (t + bt) // 2
        if not core.wait_user_mouse_idle():
            return "did not scroll: you are pressing or dragging with the mouse; retry when you let go"
        _wheel(nh, cx, cy, direction, amount)                             # 3) wheel messages (web pages, custom-drawn apps)
        return "ok (mouse-wheel messages to the window, the mouse does not move; unconfirmed: the changes below say whether it scrolled)"
    return "did not scroll: no scrollable area found in this window (it may be minimized: open it or use open_app(hidden=true))"


# ---------- full text ----------
def _clean(s):
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").replace("\ufffc", "")     # no-break space; embedded object (a field)
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _wm_text(nh):
    n = win32gui.SendMessage(nh, win32con.WM_GETTEXTLENGTH, 0, 0)
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    ctypes.windll.user32.SendMessageW(nh, win32con.WM_GETTEXT, n + 1, buf)
    return buf.value


def _ctrl_text(c):
    """Text of one element: its text pattern (documents, pages), its value (fields) or its own window text (classic edits)."""
    try:
        if c.IsPassword:
            return None
    except Exception:
        pass
    try:
        tp = c.GetTextPattern()
        if tp:
            s = tp.DocumentRange.GetText(-1)
            if s and s.strip():
                return s
    except Exception:
        pass
    v = core.field_value(c)
    if v and v.strip():
        return v
    nh, cls = core._native(c)
    if nh and ("edit" in cls or "rich" in cls):
        return _wm_text(nh)
    return None


def _biggest_text(hwnd, budget_s=3.0):
    """The element holding the most text: the document, the page, the editor."""
    root = auto.ControlFromHandle(hwnd); best = None; t0 = time.time(); stack = [(root, 0)]; n = 0
    while stack and time.time() - t0 < budget_s and n < 2500:
        c, d = stack.pop(); n += 1
        try:
            kind = c.ControlTypeName
        except Exception:
            continue
        if kind in ("DocumentControl", "EditControl"):
            s = _ctrl_text(c)
            if s and (best is None or len(s) > len(best[1])):
                best = (c, s)
            if kind == "DocumentControl" and s:
                continue                                                    # a document's children are already in its text
        if d < 28:
            try:
                stack.extend((ch, d + 1) for ch in c.GetChildren())
            except Exception:
                pass
    return best


def read_text(query, target=None, start=0, length=6000):
    """The whole text of the window's document/page/field (or of the element `target`), in pieces of `length` characters."""
    hwnd = core.find_window(query); st = core._state.get(hwnd); src = None; text = None
    if target not in (None, ""):
        it = st["items"].get(int(target)) if st else None
        if it is None:
            return "unknown id: call look() on this window first"
        if it.ctrl is not None:
            text = _ctrl_text(it.ctrl); src = f"element «{it.name[:40]}»"
        if text is None:
            text = it.name; src = "element name only (it exposes no text)"
    elif win32gui.GetClassName(hwnd) == "OpusApp":                          # Word: the document through COM
        try:
            from . import word
            w = word.window(hwnd)
            if w is not None:
                text = word.call(lambda: w.Document.Content.Text); src = "Word document (COM)"
        except Exception:
            text = None
    if text is None:
        best = _biggest_text(hwnd)
        if best:
            text = best[1]; src = f"«{(best[0].Name or best[0].ControlTypeName[:-7].lower())[:40]}»"
    if text is None:                                                        # no document: every text of the tree, also scrolled out of view
        items = core.walk_uia(hwnd, win32gui.GetWindowRect(hwnd), offscreen_ok=True)
        seen, lines = set(), []
        for i in items:
            s = i.name + (f" = {i.value}" if i.value else "")
            if s not in seen:
                seen.add(s); lines.append(s)
        text = "\n".join(lines); src = "all texts of the window (no document found)"
    text = _clean(text or "")
    if not text:
        return "no text found in this window (an image-only app: use look mode=image)"
    start = max(0, int(start or 0)); length = max(200, min(int(length or 6000), 40000))
    part = text[start:start + length]
    more = f" | more: read(start={start + len(part)})" if start + len(part) < len(text) else " | end of text"
    return f"{src}: characters {start}-{start + len(part)} of {len(text)}{more}\n{part}"


# ---------- shortcuts without pressing keys ----------
_ALIAS = {"control": "ctrl", "strg": "ctrl", "ctl": "ctrl", "mayús": "shift", "mayus": "shift", "may": "shift", "umschalt": "shift",
          "shift": "shift", "alt": "alt", "ctrl": "ctrl", "win": "win", "escape": "esc", "supr": "delete", "del": "delete",
          "entrar": "enter", "intro": "enter", "return": "enter", "inicio": "home", "fin": "end", "re pág": "pgup", "av pág": "pgdn"}


def norm_combo(s):
    """'Ctrl+Mayús+S' -> ('ctrl', 'shift', 's'): modifiers sorted, then the key. None if it does not look like a shortcut."""
    s = (s or "").strip().lower().replace(" ", "")
    if not s:
        return None
    plus = s.endswith("++") or s == "+"
    parts = [p for p in s.split("+") if p] + (["+"] if plus else [])
    if not parts:
        return None
    parts = [_ALIAS.get(p.rstrip(".") if len(p) > 2 else p, p.rstrip(".") if len(p) > 2 else p) for p in parts]      # "Mayús." (Spanish Windows)
    mods = sorted(p for p in parts[:-1])
    return tuple(mods + [parts[-1]]) if parts else None


def _menu_items(hmenu, depth=0):
    """(text, command id, enabled) of every entry of a Windows menu and its submenus."""
    u = ctypes.windll.user32; out = []
    if not hmenu or depth > 4:
        return out
    for i in range(max(0, u.GetMenuItemCount(hmenu))):
        buf = ctypes.create_unicode_buffer(256)
        u.GetMenuStringW(hmenu, i, buf, 256, 0x400)                           # MF_BYPOSITION
        sub = u.GetSubMenu(hmenu, i)
        if sub:
            out += _menu_items(sub, depth + 1)
        else:
            state = u.GetMenuState(hmenu, i, 0x400)
            out.append((buf.value, u.GetMenuItemID(hmenu, i), not (state & 0x3)))   # MF_GRAYED | MF_DISABLED
    return out


def _edit_command(hwnd, combo):
    """Clipboard / select-all / undo on the app's focused CLASSIC text box, by message (no keys)."""
    tgt = core._focus_hwnd(hwnd)
    if not tgt or "edit" not in win32gui.GetClassName(tgt).lower():
        return None
    k = combo
    if k == ("ctrl", "a"):
        win32gui.SendMessage(tgt, 0x00B1, 0, -1); return "ok (EM_SETSEL: all the text of the focused box selected)"
    if k == ("ctrl", "c"):
        win32gui.SendMessage(tgt, win32con.WM_COPY, 0, 0); return "ok (WM_COPY: the selection is now on the clipboard)"
    if k == ("ctrl", "x"):
        win32gui.SendMessage(tgt, win32con.WM_CUT, 0, 0); return "ok (WM_CUT)"
    if k == ("ctrl", "v"):
        win32gui.SendMessage(tgt, win32con.WM_PASTE, 0, 0); return "ok (WM_PASTE: clipboard pasted at the box's caret)"
    if k == ("ctrl", "z"):
        win32gui.SendMessage(tgt, 0x00C7, 0, 0); return "ok (EM_UNDO)"
    return None


def _uia_accelerator(hwnd, combo, budget_s=3.0):
    """An element that declares this shortcut (AcceleratorKey), pressed through its pattern."""
    root = auto.ControlFromHandle(hwnd); t0 = time.time(); stack = [(root, 0)]; n = 0
    while stack and time.time() - t0 < budget_s and n < 3000:
        c, d = stack.pop(); n += 1
        try:
            acc = c.AcceleratorKey
        except Exception:
            acc = ""
        if acc and norm_combo(acc) == combo:
            name = (c.Name or "")[:40]
            for getter, call in (("GetInvokePattern", "Invoke"), ("GetTogglePattern", "Toggle"), ("GetSelectionItemPattern", "Select"),
                                 ("GetExpandCollapsePattern", "Expand"), ("GetLegacyIAccessiblePattern", "DoDefaultAction")):
                try:
                    p = getattr(c, getter)()
                    if p:
                        getattr(p, call)()
                        return f"ok (pressed «{name}», the element whose shortcut is {acc}, through UI Automation; no keys pressed)"
                except Exception:
                    continue
        if d < 28:
            try:
                stack.extend((ch, d + 1) for ch in c.GetChildren())
            except Exception:
                pass
    return None


SEEN = {}                                                                   # entry -> shortcut seen while searching (to suggest the right one)
_MENU_CACHE = {}                                                            # hwnd -> (time, SEEN) of a full menu search that found nothing


def _menu_bar_accelerator(hwnd, combo, budget_s=8.0):
    """Modern menu bars (WinUI, WPF, WinForms MenuStrip) only create their entries when a menu is open: open each top menu through
    UI Automation, look for the entry that shows this shortcut, press it, and close the menus that did not have it."""
    root = auto.ControlFromHandle(hwnd); bars = []; stack = [(root, 0)]; t0 = time.time()
    while stack and len(bars) < 3:
        c, d = stack.pop()
        try:
            if c.ControlTypeName == "MenuBarControl":
                if c.AutomationId != "SystemMenuBar" and c.GetParentControl().ControlTypeName != "TitleBarControl":   # never the window's system menu
                    bars.append(c)
                continue
            if d < 12:
                stack.extend((ch, d + 1) for ch in c.GetChildren())
        except Exception:
            continue
    for bar in bars:
        try:
            tops = [m for m in bar.GetChildren() if m.ControlTypeName == "MenuItemControl"][:12]
        except Exception:
            continue
        for top in tops:
            if time.time() - t0 > budget_s:
                return None
            try:
                exp = top.GetExpandCollapsePattern()
                if not exp:
                    continue
                exp.Expand(waitTime=0); time.sleep(0.35)
            except Exception:
                continue
            hit = None
            for scope in [auto.ControlFromHandle(h) for h in core.popup_windows(hwnd)] + [top]:      # the open menu: a pop-up window, or inside the item
                st = [(scope, 0)]
                while st and hit is None:
                    c, d = st.pop()
                    try:
                        if c.ControlTypeName == "MenuItemControl" and c.AcceleratorKey:
                            SEEN[(c.Name or "")[:30]] = c.AcceleratorKey
                            if norm_combo(c.AcceleratorKey) == combo:
                                hit = c; break
                        if d < 10:
                            st.extend((ch, d + 1) for ch in c.GetChildren())
                    except Exception:
                        continue
                if hit is not None:
                    break
            if hit is not None:
                name = (hit.Name or "")[:40]
                try:
                    if not hit.IsEnabled:
                        exp.Collapse(waitTime=0)
                        return f"did not press: the menu entry «{name}» is disabled (greyed out) right now"
                except Exception:
                    pass
                for getter, call in (("GetInvokePattern", "Invoke"), ("GetLegacyIAccessiblePattern", "DoDefaultAction")):
                    try:
                        p = getattr(hit, getter)()
                        if p:
                            getattr(p, call)()
                            return f"ok (menu entry «{name}» ({hit.AcceleratorKey}) pressed through UI Automation; no keys pressed)"
                    except Exception:
                        continue
            try:
                exp.Collapse(waitTime=0)
            except Exception:
                pass
    return None


def shortcut(query, combo_text):
    """Run a keyboard shortcut WITHOUT pressing keys. Routes, in order: the classic text box command (copy, paste, select all, undo),
    the app's menu entry showing that shortcut (WM_COMMAND), the element declaring it (UI Automation), alt+f4 = close request."""
    hwnd = core.find_window(query); combo = norm_combo(combo_text)
    if not combo:
        return "did not press: write the shortcut like ctrl+s, ctrl+shift+n or alt+f4"
    if combo == ("alt", "f4"):
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return "ok (close request sent to the window, like alt+f4; if there are unsaved changes the app asks)"
    done = _edit_command(hwnd, combo)
    if done:
        return done
    SEEN.clear()
    for text, cid, enabled in _menu_items(ctypes.windll.user32.GetMenu(hwnd)):
        if "\t" in text:
            SEEN[text.split("\t", 1)[0].replace("&", "")[:30]] = text.split("\t", 1)[1]
        if "\t" in text and norm_combo(text.split("\t", 1)[1]) == combo:
            entry = text.split("\t", 1)[0].replace("&", "")
            if not enabled:
                return f"did not press: the menu entry «{entry}» ({combo_text}) is disabled (greyed out) right now"
            win32gui.PostMessage(hwnd, win32con.WM_COMMAND, cid, 0)
            return f"ok (menu command «{entry}» sent to the app, the same one {combo_text} triggers; no keys pressed)"
    done = _uia_accelerator(hwnd, combo)
    if done:
        return done
    from .hidden import on_hidden_thread
    hidden = on_hidden_thread()
    cached = _MENU_CACHE.get(hwnd)
    if not hidden:
        pass        # opening a modern app's menus on the user's desktop shows pop-ups that can take the focus: only on the hidden desktop
    elif cached and time.time() - cached[0] < 300 and combo not in {norm_combo(v) for v in cached[1].values()}:
        SEEN.update(cached[1])                                               # menus already searched a moment ago: no need to open them again
    else:
        done = _menu_bar_accelerator(hwnd, combo)
        if done:
            return done
        _MENU_CACHE[hwnd] = (time.time(), dict(SEEN))
    known = "; ".join(f"{k} = {v}" for k, v in list(SEEN.items())[:25])
    if not hidden and not known:
        return (f"did not press '{combo_text}': this app keeps its shortcuts inside menus that only exist while open, and opening them on the "
                f"user's desktop could take the focus. Use the menu instead: look, click the menu (e.g. File), look, click the entry. "
                f"(In apps opened with open_app(hidden=true) shortcuts like this work.)")
    return (f"did not press '{combo_text}': no menu entry or element of this app has that shortcut (pressing real keys would need to focus "
            f"the window and type on the user's keyboard)." + (f" Shortcuts this app does have (they depend on its language): {known}" if known
            else " Do it through the menu instead: look, then click the entry."))


# ---------- drop-downs and lists ----------
def select_option(ctrl, text):
    """Pick the entry whose text is `text` (exact, else starting with it, else containing it) in a drop-down or list. None if not one."""
    try:
        kind = ctrl.ControlTypeName
    except Exception:
        return None
    if kind not in ("ComboBoxControl", "ListControl"):
        return None
    want = text.strip().lower()
    nh, cls = core._native(ctrl)
    if nh and "combobox" in cls:                                            # classic combo box: its own messages, then tell its parent
        idx = win32gui.SendMessage(nh, 0x0158, -1, text)                     # CB_FINDSTRINGEXACT
        if idx < 0:
            idx = win32gui.SendMessage(nh, 0x014C, -1, text)                 # CB_FINDSTRING (prefix)
        if idx >= 0:
            win32gui.SendMessage(nh, 0x014E, idx, 0)                         # CB_SETCURSEL
            parent = win32gui.GetParent(nh); cid = win32gui.GetDlgCtrlID(nh)
            win32gui.SendMessage(parent, win32con.WM_COMMAND, win32api.MAKELONG(cid & 0xFFFF, 1), nh)     # CBN_SELCHANGE
            return f"ok (option {idx} selected in the drop-down by message, no focus)" + _readback(ctrl, text)
    if nh and "listbox" in cls:                                             # classic list: its own messages, then tell its parent
        idx = win32gui.SendMessage(nh, 0x01A2, -1, text)                     # LB_FINDSTRINGEXACT
        if idx < 0:
            idx = win32gui.SendMessage(nh, 0x018F, -1, text)                 # LB_FINDSTRING (prefix)
        if idx >= 0:
            win32gui.SendMessage(nh, 0x0186, idx, 0)                         # LB_SETCURSEL
            win32gui.SendMessage(win32gui.GetParent(nh), win32con.WM_COMMAND, win32api.MAKELONG(win32gui.GetDlgCtrlID(nh) & 0xFFFF, 1), nh)  # LBN_SELCHANGE
            return f"ok (entry {idx} selected in the list by message, no focus)" + _readback(ctrl, text)
    if nh and ("combobox" in cls or "listbox" in cls):
        names = _native_options(nh, "combobox" in cls)
        if names and not any(want in n.lower() for n in names):
            return f"did not select: no option «{text}» in this list (options: {', '.join(n[:25] for n in names[:12])}{'...' if len(names) > 12 else ''})"
    exp = None
    try:
        exp = ctrl.GetExpandCollapsePattern()
        if exp and exp.ExpandCollapseState == 0:
            exp.Expand(waitTime=0); time.sleep(0.3)
        else:
            exp = None
    except Exception:
        exp = None
    try:
        opts = []
        stack = [(ctrl, 0)]
        while stack:
            c, d = stack.pop()
            for ch in c.GetChildren():
                if ch.ControlTypeName in ("ListItemControl", "MenuItemControl", "DataItemControl"):
                    opts.append(ch)
                elif d < 4:
                    stack.append((ch, d + 1))
        pick = (next((o for o in opts if (o.Name or "").strip().lower() == want), None)
                or next((o for o in opts if (o.Name or "").strip().lower().startswith(want)), None)
                or next((o for o in opts if want in (o.Name or "").strip().lower()), None))
        if pick is None:
            names = ", ".join((o.Name or "?")[:25] for o in opts[:12])
            return f"did not select: no option «{text}» in this list" + (f" (options: {names}{'...' if len(opts) > 12 else ''})" if opts else "")
        for getter, call in (("GetSelectionItemPattern", "Select"), ("GetInvokePattern", "Invoke"), ("GetLegacyIAccessiblePattern", "DoDefaultAction")):
            try:
                p = getattr(pick, getter)()
                if p:
                    getattr(p, call)()
                    return f"ok (option «{(pick.Name or '')[:40]}» selected through UI Automation, no focus)" + _readback(ctrl, pick.Name or text)
            except Exception:
                continue
        return None
    finally:
        if exp is not None:
            try:
                exp.Collapse(waitTime=0)
            except Exception:
                pass


def _native_options(nh, combo):
    """Texts of the entries of a classic combo box or list box (read by message)."""
    count, getlen, get = (0x0146, 0x0149, 0x0148) if combo else (0x018B, 0x018A, 0x0189)     # CB_/LB_ GETCOUNT, GETTEXTLEN, GETTEXT
    out = []
    for i in range(min(max(0, win32gui.SendMessage(nh, count, 0, 0)), 200)):
        n = win32gui.SendMessage(nh, getlen, i, 0)
        if n < 0 or n > 1000:
            continue
        buf = ctypes.create_unicode_buffer(n + 1)
        ctypes.windll.user32.SendMessageW(nh, get, i, buf)
        out.append(buf.value)
    return out


def _readback(ctrl, text):
    time.sleep(0.1)
    v = core.field_value(ctrl)
    if v is None:
        try:
            sel = ctrl.GetSelectionPattern().GetSelection()
            v = ", ".join(s.Name for s in sel)
        except Exception:
            return ""
    ok = text.strip().lower() in (v or "").lower()
    return f" | read back: " + ("the drop-down now shows it" if ok else "WARNING: it shows something else") + f' ("{(v or "")[:40]}")'


# ---------- find (and wait) ----------
def find(query, terms, wait=0.0):
    """Only the elements whose text contains one of `terms` ('|' separated), visible or scrolled out of view, with ids for click/type.
    A term starting with '!' waits until that text is GONE (a spinner, 'Loading...'). With wait > 0 it polls until satisfied."""
    hwnd = core.find_window(query)
    want = [t.strip() for t in str(terms).split("|") if t.strip()]
    gone = [t[1:].lower() for t in want if t.startswith("!")]
    present = [t.lower() for t in want if not t.startswith("!")]
    t0 = time.time(); deadline = t0 + max(0.0, min(float(wait or 0), 120.0))
    while True:
        items = core.walk_uia(hwnd, win32gui.GetWindowRect(hwnd), offscreen_ok=True)
        text_of = lambda i: (i.name + " " + (i.value or "")).lower()
        hits = [i for i in items if any(p in text_of(i) for p in present)]
        still = [g for g in gone if any(g in text_of(i) for i in items)]
        ok = (not present or hits) and not still
        if ok or time.time() >= deadline:
            break
        time.sleep(0.4)
    waited = round(time.time() - t0, 1)
    rect = win32gui.GetWindowRect(hwnd)
    for n, it in enumerate(hits, 1):
        it.id = n
    old = core._state.get(hwnd, {})
    visible = {core.line_key(i) for i in items if not _off(i)}
    core._state[hwnd] = {"items": {it.id: it for it in hits}, "lines": old.get("lines") or visible,
                         "rect": rect, "scale": 1.0, "mode": "uia"}
    if old.get("rect") and old.get("scale"):
        core._state[hwnd]["scale"] = old["scale"]
    lines = []
    for it in hits[:40]:
        off = _off(it)
        lab = f"[{it.id}:{it.name[:70]}" + (f'="{it.value[:50]}"' if it.value else "") + "]" if it.clickable else f"{it.id}: {it.name[:90]}"
        lines.append(f"{lab} ({it.kind}{', out of view: click scrolls to it' if off else ''})")
    head = ("found" if ok else "NOT found") + (f" after {waited} s" if wait else "")
    if still:
        head += f" | still present: {', '.join(still)}"
    more = f" (+{len(hits) - 40} more)" if len(hits) > 40 else ""
    return f"{head} | {len(hits)} match(es){more}; ids valid for click/type until the next look\n" + "\n".join(lines)


def _off(it):
    try:
        return bool(it.ctrl is not None and it.ctrl.IsOffscreen)
    except Exception:
        return False


def scroll_into_view(ctrl):
    """If an element is scrolled out of view, bring it into view through UI Automation (no mouse). True if it moved."""
    try:
        if not ctrl.IsOffscreen:
            return False
        p = ctrl.GetScrollItemPattern()
        if p:
            p.ScrollIntoView(waitTime=0); time.sleep(0.2)
            return True
    except Exception:
        pass
    return False


def press_out_of_view(it):
    """Press an element that is scrolled out of view and could not be brought into view: an entry of a list is selected through its
    list (by message or UI Automation); anything else through its pattern. None if no route works."""
    c = it.ctrl
    try:
        parent = c.GetParentControl()
        if it.kind in ("listitem", "dataitem", "treeitem") and parent is not None and parent.ControlTypeName in ("ListControl", "ComboBoxControl"):
            out = select_option(parent, it.name)
            if out and out.startswith("ok"):
                return out + " (it was out of view: selected through its list)"
    except Exception:
        pass
    for getter, call in (("GetSelectionItemPattern", "Select"), ("GetInvokePattern", "Invoke"), ("GetTogglePattern", "Toggle"),
                         ("GetLegacyIAccessiblePattern", "DoDefaultAction")):
        try:
            p = getattr(c, getter)()
            if p:
                getattr(p, call)()
                return f"ok (UI Automation {call}: the element is out of view, so it was pressed without scrolling to it)"
        except Exception:
            continue
    return None
