"""Excel (and saving in Word/Excel) through COM, reached from the WINDOW itself: the right instance even if several are open, also on
the hidden desktop. Like Word, Claude never uses the user's selection: cells are written by address (B3, Sheet2!A1), so the user can
keep working in the same workbook. If Excel is busy because the user is editing a cell, COM calls are refused for a while: we say so.
"""
import re
from . import word

CELL = re.compile(r"^\s*(?:(?:'([^']+)'|([^!]+))!)?\$?([A-Za-z]{1,3})\$?(\d{1,7})(?::\$?([A-Za-z]{1,3})\$?(\d{1,7}))?\s*$")


def is_excel(hwnd):
    import win32gui
    try:
        return win32gui.GetClassName(hwnd) == "XLMAIN"
    except Exception:
        return False


def is_cell(target):
    return bool(CELL.match(str(target or ""))) and not str(target).strip().isdigit()


def excel_app(hwnd):
    """The Excel Application that owns this window (through the window's native object model, not 'the first Excel running')."""
    import pythoncom, win32com.client, win32gui
    pythoncom.CoInitialize()
    desk = win32gui.FindWindowEx(hwnd, 0, "XLDESK", None)
    ch = win32gui.FindWindowEx(desk, 0, "EXCEL7", None) if desk else 0
    if not ch:
        return None
    lres = win32gui.SendMessage(ch, 0x003D, 0, 0xFFFFFFF0)                 # WM_GETOBJECT, OBJID_NATIVEOM
    if not lres:
        return None
    win = win32com.client.Dispatch(pythoncom.ObjectFromLresult(lres, pythoncom.IID_IDispatch, 0))
    return word.call(lambda: win.Application)


def _col(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26); s = chr(65 + r) + s
    return s


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _sheet(app, name):
    wb = word.call(lambda: app.ActiveWorkbook)
    return word.call(lambda: wb.Worksheets(name)) if name else word.call(lambda: app.ActiveSheet)


def summary(hwnd):
    """One line for look(): the active sheet and its used range, and how to read/write cells."""
    try:
        app = excel_app(hwnd)
        sh = word.call(lambda: app.ActiveSheet); wb = word.call(lambda: app.ActiveWorkbook)
        names = [word.call(lambda i=i: wb.Worksheets(i).Name) for i in range(1, min(word.call(lambda: wb.Worksheets.Count), 12) + 1)]
        used = word.call(lambda: sh.UsedRange.Address).replace("$", "")
        return (f"[Excel: sheet «{word.call(lambda: sh.Name)}» used {used}; sheets: {', '.join(names)}. read(window) gives the cells; "
                f"type(window, \"B3\", text) writes a cell (\"=SUM(B2:B9)\" a formula) without touching the user's selection]")
    except Exception:
        return ""


def read(hwnd, start=0, length=6000, sheet=None):
    """The used range of the active (or named) sheet as tab-separated rows, with row numbers and column letters."""
    app = excel_app(hwnd)
    if app is None:
        return None
    sh = _sheet(app, sheet)
    ur = word.call(lambda: sh.UsedRange)
    r0, c0 = word.call(lambda: ur.Row), word.call(lambda: ur.Column)
    vals = word.call(lambda: ur.Value)
    if not isinstance(vals, tuple):
        vals = ((vals,),)
    ncols = max((len(r) for r in vals), default=0)
    lines = ["\t" + "\t".join(_col(c0 + j) for j in range(ncols))]
    for i, row in enumerate(vals):
        cells = [_fmt(v) for v in row]
        if any(cells):
            lines.append(f"{r0 + i}\t" + "\t".join(cells).rstrip("\t"))
    text = "\n".join(lines)
    part = text[start:start + length]
    more = f" | more: read(start={start + len(part)})" if start + len(part) < len(text) else " | end"
    addr = word.call(lambda: ur.Address).replace("$", "")
    return f"Excel sheet «{word.call(lambda: sh.Name)}» {addr} (COM; tab-separated, first column = row number): characters {start}-{start + len(part)} of {len(text)}{more}\n{part}"


def write(hwnd, target, text):
    """Write a cell (or fill a range with the same value) by address. Numbers stay numbers, '=...' is a formula. Reads it back."""
    app = excel_app(hwnd)
    if app is None:
        return None
    m = CELL.match(target)
    sheet = m.group(1) or m.group(2)
    addr = target.split("!")[-1].strip()
    sh = _sheet(app, sheet)
    rng = word.call(lambda: sh.Range(addr))
    t = text.strip()
    if t.startswith("="):
        for prop in ("Formula", "FormulaLocal"):                            # English syntax (SUM, commas) first, then the user's language
            try:
                word.call(setattr, rng, prop, t); break
            except Exception:
                continue
        else:
            return f"did not type: Excel rejected the formula «{t[:60]}» (write it in English syntax, e.g. =SUM(B2:B9), or in the sheet's language)"
    else:
        try:
            num = float(t.replace(",", ".")) if re.fullmatch(r"-?\d+([.,]\d+)?", t) else None
        except ValueError:
            num = None
        word.call(setattr, rng, "Value", num if num is not None else text)
    first = word.call(lambda: rng.Cells(1, 1))
    shown = word.call(lambda: first.Text)
    try:
        bad = bool(word.call(lambda: app.WorksheetFunction.IsError(first)))
    except Exception:
        bad = False
    if set(str(shown)) == {"#"}:                                            # ##### = the column is too narrow, the value is fine
        shown = _fmt(word.call(lambda: first.Value))
    err = " | WARNING: the formula gives an error" if bad else ""
    return (f"ok (Excel through COM: {addr} of «{word.call(lambda: sh.Name)}» now shows «{shown}»; the user's selection is not touched){err}")


def save_combo(hwnd):
    """The save shortcut of this Office by its interface language: Spanish uses ctrl+g (ctrl+s is underline there)."""
    import win32gui
    try:
        app = excel_app(hwnd) if win32gui.GetClassName(hwnd) == "XLMAIN" else word._get_app()
        lang = word.call(lambda: app.LanguageSettings.LanguageID(2))                # msoLanguageIDUI
        return ("ctrl", "g") if lang & 0x3FF == 0x0A else ("ctrl", "s")              # primary language 0x0A = Spanish
    except Exception:
        return ("ctrl", "s")


def save(hwnd):
    """ctrl+s for Excel/Word: save the workbook/document through COM if it already has a file. None if not Office."""
    import win32gui
    cls = win32gui.GetClassName(hwnd)
    if cls == "XLMAIN":
        app = excel_app(hwnd)
        wb = word.call(lambda: app.ActiveWorkbook)
        if not word.call(lambda: wb.Path):
            return "did not save: this workbook was never saved, so it has no name yet; the user must choose where (File > Save as)"
        word.call(wb.Save)
        return f"ok (Excel workbook «{word.call(lambda: wb.Name)}» saved through COM; no keys pressed)"
    if cls == "OpusApp":
        w = word.window(hwnd)
        if w is None:
            return None
        doc = word.call(lambda: w.Document)
        if not word.call(lambda: doc.Path):
            return "did not save: this document was never saved, so it has no name yet; the user must choose where (File > Save as)"
        word.call(doc.Save)
        return f"ok (Word document «{word.call(lambda: doc.Name)}» saved through COM; no keys pressed)"
    return None


def native_window(hwnd, child_class):
    """The Office object model of a window, through its child window `child_class` (WM_GETOBJECT + OBJID_NATIVEOM). None if absent."""
    import pythoncom, win32com.client, win32gui
    pythoncom.CoInitialize()
    found = []
    win32gui.EnumChildWindows(hwnd, lambda h, _: found.append(h) if win32gui.GetClassName(h) == child_class else None, None)
    for ch in found:
        lres = win32gui.SendMessage(ch, 0x003D, 0, 0xFFFFFFF0)
        if lres:
            return win32com.client.Dispatch(pythoncom.ObjectFromLresult(lres, pythoncom.IID_IDispatch, 0))
    return None


def is_powerpoint(hwnd):
    import win32gui
    try:
        return win32gui.GetClassName(hwnd) == "PPTFrameClass"
    except Exception:
        return False


def powerpoint_text(hwnd):
    """Every slide's text (titles, boxes, tables) and its speaker notes, through COM. None if the presentation cannot be reached."""
    w = native_window(hwnd, "mdiClass")
    pres = word.call(lambda: w.Presentation) if w is not None else None
    if pres is None:
        return None
    out = []
    for n in range(1, word.call(lambda: pres.Slides.Count) + 1):
        sl = word.call(lambda n=n: pres.Slides(n)); out.append(f"--- slide {n} ---")
        for k in range(1, word.call(lambda: sl.Shapes.Count) + 1):
            sh = word.call(lambda k=k: sl.Shapes(k))
            try:
                if word.call(lambda: sh.HasTextFrame) and word.call(lambda: sh.TextFrame.HasText):
                    out.append(word.call(lambda: sh.TextFrame.TextRange.Text))
                elif word.call(lambda: sh.HasTable):
                    t = word.call(lambda: sh.Table)
                    for r in range(1, word.call(lambda: t.Rows.Count) + 1):
                        out.append("\t".join(word.call(lambda r=r, c=c: t.Cell(r, c).Shape.TextFrame.TextRange.Text)
                                             for c in range(1, word.call(lambda: t.Columns.Count) + 1)))
            except Exception:
                continue
        try:
            notes = word.call(lambda: sl.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange.Text).strip()
            if notes:
                out.append(f"(notes) {notes}")
        except Exception:
            pass
    return "\n".join(out)
