"""Word adapter: Claude types and formats at ITS OWN point in the document, without depending on the user's selection,
and the user can keep working in the same document at the same time.

Why: with Word's selection, what Claude types goes "wherever the caret is", and that caret belongs to the user. If the
user clicks somewhere else in the middle of a task, the text (and the bold) drift along with it. Here Claude has its own
hidden bookmark in the document (`_PCControl`; bookmarks starting with _ do not show in the bookmark list): it moves
only if the user edits before it, is not affected by where the user clicks, and the user's selection is never touched.

The ribbon formatting buttons (bold, italic, alignment...) are NOT really pressed: they would format the user's
selection. They are recorded as Claude's pending format and applied to the text Claude types. They are recognized by their
AutomationId (Bold, Italic, AlignCenter...), which does not depend on the Windows language.

If Word is busy serving the user (typing, opening a menu), it rejects COM calls with RPC_E_CALL_REJECTED:
each call is retried for a few seconds instead of failing.
"""
import time

BOOKMARK, START = "_PCControl", "_PCControlStart"
FORMAT_IDS = {                                   # ribbon button AutomationId -> format
    "Bold": ("bold",), "Italic": ("italic",), "UnderlineGallery": ("underline",), "Strikethrough": ("strike",),
    "Subscript": ("sub",), "Superscript": ("super",),
    "AlignLeft": ("align", 0), "AlignCenter": ("align", 1), "AlignRight": ("align", 2), "AlignJustify": ("align", 3),
}
_ALIGN = {0: "left", 1: "centered", 2: "right", 3: "justified"}
_pending = {}                                    # hwnd -> Claude's pending format
_BUSY = {-2147418111, -2147417846}               # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER: Word is busy with the user
RETRY = {"tries": 60, "wait": 0.1}               # up to ~6 s per call


def call(fn, *args):
    """One atomic COM call, retried if Word rejects it because it is busy (a rejected call was not executed: retrying is safe)."""
    for i in range(RETRY["tries"]):
        try:
            return fn(*args)
        except Exception as e:
            code = getattr(e, "hresult", None)
            if code is None and getattr(e, "args", None) and isinstance(e.args[0], int):
                code = e.args[0]
            if code in _BUSY and i < RETRY["tries"] - 1:
                time.sleep(RETRY["wait"]); continue
            raise


def _get_app():
    import pythoncom, win32com.client
    pythoncom.CoInitialize()
    return win32com.client.GetActiveObject("Word.Application")


def window(hwnd):
    """The Word window with that hwnd (or None)."""
    for w in call(lambda: list(_get_app().Windows)):
        if call(lambda: w.Hwnd) == hwnd:
            return w
    return None


def _bm(doc, name):
    bms = doc.Bookmarks
    call(setattr, bms, "ShowHidden", True)        # hidden bookmarks are only visible with this
    return call(bms.Exists, name)


def anchor_pos(doc):
    """Where Claude types: its bookmark, or the end of the document (inside the last paragraph) the first time."""
    if _bm(doc, BOOKMARK):
        return call(lambda: doc.Bookmarks(BOOKMARK).Range.Start)
    return max(0, call(lambda: doc.Content.End) - 1)


def _set(doc, name, pos):
    call(setattr, doc.Bookmarks, "ShowHidden", True)
    call(doc.Bookmarks.Add, name, call(doc.Range, pos, pos))


def state(hwnd):
    return _pending.setdefault(hwnd, {"bold": False, "italic": False, "underline": False, "strike": False, "sub": False, "super": False, "align": None})


def describe(st):
    on = [k for k in ("bold", "italic", "underline", "strike", "sub", "super") if st[k]]
    return ", ".join(on + ([f"alignment {_ALIGN[st['align']]}"] if st["align"] is not None else [])) or "normal"


def format_click(hwnd, automation_id):
    """A ribbon formatting button: it is recorded for Claude's text (the user's selection is not touched). None if it is not a formatting button."""
    spec = FORMAT_IDS.get(automation_id)
    if not spec:
        return None
    st = state(hwnd)
    if spec[0] == "align":
        st["align"] = spec[1]
    else:
        st[spec[0]] = not st[spec[0]]
        if spec[0] == "sub" and st["sub"]: st["super"] = False
        if spec[0] == "super" and st["super"]: st["sub"] = False
    return f"ok (Claude format: {describe(st)}; applied to what Claude types, not to your selection)"


def _format(rng, st):
    f = call(lambda: rng.Font)
    for name, val in (("Bold", bool(st["bold"])), ("Italic", bool(st["italic"])), ("Underline", 1 if st["underline"] else 0),
                      ("StrikeThrough", bool(st["strike"])), ("Subscript", bool(st["sub"])), ("Superscript", bool(st["super"]))):
        call(setattr, f, name, val)               # explicit: what Claude types does not inherit the format of the previous text


def _align(doc, positions, value):
    """Align the paragraphs containing those positions. Only called with NEW Claude paragraphs (or an empty one where it types):
    a range crossing a paragraph mark would also align the previous paragraph, which may belong to the user."""
    for p in positions:
        call(setattr, call(lambda: call(doc.Range, p, p).ParagraphFormat), "Alignment", value)


def type_text(hwnd, text, replace=False, progress=None):
    """Type at Claude's point. replace=True replaces what Claude typed LAST (never anything of the user's). With `progress`,
    it types in small chunks and reports where the point is after each one, so the typing is visible. Returns the result message."""
    w = window(hwnd)
    if w is None:
        return None
    doc, st = call(lambda: w.Document), state(hwnd)
    text = text.replace("\r\n", "\r").replace("\n", "\r")
    before = call(doc.ComputeStatistics, 0)                                    # wdStatisticWords
    pos = anchor_pos(doc)
    if replace and _bm(doc, START):
        s0 = call(lambda: doc.Bookmarks(START).Range.Start)
        if s0 < pos:
            call(call(doc.Range, s0, pos).Delete); pos = s0
    start = pos
    before_ch = call(lambda: doc.Range(max(0, pos - 1), pos).Text) if pos else "\r"
    after_ch = call(lambda: doc.Range(pos, pos + 1).Text)
    was_empty = before_ch == "\r" and after_ch == "\r"                          # typing into an empty paragraph
    chunks = [text[i:i + 3] for i in range(0, len(text), 3)] if progress else [text]
    for n, chunk in enumerate(chunks):
        rng = call(doc.Range, pos, pos)
        call(rng.InsertAfter, chunk)
        _format(rng, st)
        if st["align"] is not None:
            news = [pos + i + 1 for i, ch in enumerate(chunk) if ch == "\r"]       # paragraphs that START inside the typed text
            _align(doc, ([pos] if (n == 0 and was_empty) else []) + news, st["align"])
        pos = call(lambda: rng.End)
        _set(doc, BOOKMARK, pos)                                               # the bookmark moves with each chunk
        if progress:
            c = caret(hwnd, w)
            if c: progress(*c)
            time.sleep(0.03)
    _set(doc, START, start); _set(doc, BOOKMARK, pos)
    lead = len(text) - len(text.lstrip("\r"))                                   # line breaks at the start: the text lands in a later paragraph
    par = (call(lambda: doc.Range(0, start).Text).count("\r") + 1 if start else 1) + lead
    raw = call(lambda: doc.Range(max(0, start - 28), start).Text)
    if start > 28 and " " in raw and raw[0] not in " \r":
        raw = raw[raw.index(" ") + 1:]                                          # the excerpt starts at a whole word, not mid-word
    ctx = raw.replace("\r", " ¶ ")
    after = call(doc.ComputeStatistics, 0)
    return (f"ok (Word through COM, at Claude's insertion point: your selection is not touched; words {before} -> {after}; "
            f"typed in paragraph {par}" + (f" after «{ctx.strip()}»" if ctx.strip() else " at the beginning") + ")")


def backspace(hwnd):
    """Delete the last character CLAUDE typed (never the user's text). None if it is not Word."""
    w = window(hwnd)
    if w is None:
        return None
    doc = call(lambda: w.Document); pos = anchor_pos(doc)
    s0 = call(lambda: doc.Bookmarks(START).Range.Start) if _bm(doc, START) else pos
    if pos <= s0:
        return "did not delete: in Word I only delete what Claude typed, and there is nothing of Claude's before its point"
    call(call(doc.Range, pos - 1, pos).Delete); _set(doc, BOOKMARK, pos - 1)
    return "ok (Word through COM: deleted Claude's last character; your selection is not touched; words " + str(call(doc.ComputeStatistics, 0)) + ")"


def caret(hwnd, w=None):
    """(x, center_y, height) on screen of Claude's point, or None if it is not visible (e.g. outside the visible area)."""
    try:
        w = w or window(hwnd)
        if w is None:
            return None
        doc = call(lambda: w.Document)
        p = anchor_pos(doc)
        x, y, _, hh = call(w.GetPoint, 0, 0, 0, 0, call(doc.Range, p, p))
        return (x, y + hh // 2, hh) if hh and hh > 0 else None
    except Exception:
        return None
