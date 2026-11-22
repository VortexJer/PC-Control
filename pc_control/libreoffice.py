"""LibreOffice adapter: typing through UNO (LibreOffice's own automation API, reached through its COM bridge on Windows).

Why: LibreOffice's windows (VCL "SALFRAME") take keys only while they have the system keyboard focus, and the document's
accessible value is a box that echoes back what is written into it without changing the text (a false confirmation, seen
live). UNO writes into the document itself, needs neither the focus nor the mouse, and the text can be read back from it.

The document is found by its REAL window handle (frame.ContainerWindow.getWindowHandle), never by the "current" document,
which is the one the user has active. Writer types at Claude's OWN text cursor (kept per window), so the user's caret and
selection are not touched; Calc writes the cell that is selected in that window; Math appends to (or replaces) the formula.
"""
import time

_points = {}                                     # hwnd -> (Claude's text cursor in Writer, where its last typing started)
NL, CR, PARA = chr(10), chr(13), chr(0x2029)


def _desktop():
    import pythoncom, win32com.client
    pythoncom.CoInitialize()
    sm = win32com.client.Dispatch("com.sun.star.ServiceManager")
    return sm.createInstance("com.sun.star.frame.Desktop")


def document(hwnd):
    """The UNO document shown in that LibreOffice window (or None)."""
    comps = _desktop().getComponents().createEnumeration()
    while comps.hasMoreElements():
        c = comps.nextElement()
        try:
            if c.getCurrentController().getFrame().getContainerWindow().getWindowHandle([0] * 16, 1) == hwnd:  # 1 = SYSTEM_WIN32
                return c
        except Exception:
            continue
    return None


def _lines(text):
    return text.replace(CR + NL, NL).replace(CR, NL)


def _writer(doc, hwnd, text, replace):
    body = doc.getText()
    cur, start = _points.get(hwnd, (None, None))
    if cur is None:                                       # first time: where the document's caret is now (the end if unknown)
        try:
            cur = body.createTextCursorByRange(doc.getCurrentController().getViewCursor().getStart())
        except Exception:
            cur = body.createTextCursor(); cur.gotoEnd(False)
    if replace and start is not None:                     # replace = what CLAUDE typed last, never the user's text
        cur.gotoRange(start, True); cur.setString("")
    cur.collapseToEnd()
    text = _lines(text)
    for i, part in enumerate(text.split(NL)):
        if i:
            body.insertControlCharacter(cur, 0, False)       # PARAGRAPH_BREAK
        if part:
            body.insertString(cur, part, False)
        cur.collapseToEnd()
    mark = body.createTextCursorByRange(cur.getEnd())     # the range Claude just typed: back from its point, one step per char
    mark.goLeft(len(text), True)                          # (a paragraph break is one step)
    _points[hwnd] = (cur, mark.getStart())
    typed = _lines(mark.getString()).replace(PARA, NL)
    ok = typed.strip() == text.strip()
    return ("ok (LibreOffice Writer through UNO, at Claude's own insertion point: your caret and selection are not touched) | "
            + ("read back and confirmed" if ok else f"WARNING: the document does NOT contain the typed text (\"{typed[:40]}\")"))


def _calc(doc, text, replace, cell=None):
    if cell:                                              # a cell by address (B3, Sheet2.A1 or Sheet2!A1): the user's selection stays
        sheet_name, _, addr = cell.replace("!", ".").rpartition(".")
        sheets = doc.getSheets()
        sheet = sheets.getByName(sheet_name) if sheet_name else doc.getCurrentController().getActiveSheet()
        sel = sheet.getCellRangeByName(addr.replace("$", ""))
    else:
        sel = doc.getCurrentController().getSelection()
    try:
        name = sel.AbsoluteName.split(".")[-1].replace("$", "")
    except Exception:
        name = "the selected cell"
    if not hasattr(sel, "setFormula") or ":" in name:
        return None                                       # a range or a shape: no single cell to write
    new = text if replace else sel.getString() + text
    sel.setFormula(new)
    now = sel.getFormula()
    ok = now == new or sel.getString() == new
    shown = sel.getString()
    return (f"ok (LibreOffice Calc through UNO, cell {name}; it shows «{shown[:40]}») | "
            + ("read back and confirmed" if ok else f"WARNING: the cell holds something else (\"{now[:40]}\")"))


def _math(doc, text, replace):
    new = text if replace else (doc.Formula + (" " if doc.Formula else "") + text)
    doc.Formula = new
    ok = doc.Formula == new
    return "ok (LibreOffice Math through UNO, the formula) | " + ("read back and confirmed" if ok else "WARNING: the formula did not take it")


def type_text(hwnd, text, replace=False, cell=None):
    """Type into the LibreOffice document of that window. None if it is not one (the caller tries its other ways)."""
    doc = document(hwnd)
    if doc is None:
        return None
    for i in range(30):                                   # LibreOffice busy with the user: the call is refused, retried briefly
        try:
            if cell and not doc.supportsService("com.sun.star.sheet.SpreadsheetDocument"):
                return None                               # a cell address only means something in Calc
            if doc.supportsService("com.sun.star.text.TextDocument"):
                return _writer(doc, hwnd, text, replace)
            if doc.supportsService("com.sun.star.sheet.SpreadsheetDocument"):
                return _calc(doc, text, replace, cell)
            if doc.supportsService("com.sun.star.formula.FormulaProperties"):
                return _math(doc, text, replace)
            return None
        except Exception as e:
            if "busy" in str(e).lower() or "rejected" in str(e).lower():
                time.sleep(0.1); continue
            raise
    return None


def read(hwnd):
    """(text, source) of the document in that window: Writer's whole text, Calc's used area of the active sheet (tab-separated
    rows), Math's formula. None if the window shows no document."""
    doc = document(hwnd)
    if doc is None:
        return None
    if doc.supportsService("com.sun.star.text.TextDocument"):
        return _lines(doc.getText().getString()).replace(PARA, NL), "LibreOffice Writer document (UNO: the whole text, also what is out of view)"
    if doc.supportsService("com.sun.star.sheet.SpreadsheetDocument"):
        sheet = doc.getCurrentController().getActiveSheet()
        cur = sheet.createCursor(); cur.gotoEndOfUsedArea(False)
        a = cur.getRangeAddress()
        rows = sheet.getCellRangeByPosition(0, 0, a.EndColumn, a.EndRow).getDataArray()
        return (NL.join(chr(9).join(("" if v is None else (str(int(v)) if isinstance(v, float) and v == int(v) else str(v))) for v in r)
                        for r in rows),
                f"LibreOffice Calc, sheet «{sheet.getName()}» (UNO: its used area, rows tab-separated, values as shown)")
    if doc.supportsService("com.sun.star.formula.FormulaProperties"):
        return doc.Formula, "LibreOffice Math formula (UNO)"
    return None
