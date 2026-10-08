"""Adaptador de Word: Claude escribe y da formato en SU PROPIO punto del documento, sin depender de la seleccion del usuario.

Por que: con la seleccion de Word, lo que Claude escribe va "donde este el cursor", y ese cursor es del usuario. Si el
usuario hace clic en otro sitio a mitad de una tarea, el texto (y la negrita) se desvian con el. Aqui Claude tiene un marcador
oculto propio en el documento (`_PCControl`, los marcadores que empiezan por _ no salen en la lista de marcadores): se mueve
solo si el usuario edita antes de el, no se ve afectado por donde haga clic el usuario, y la seleccion del usuario no se toca.

Los botones de formato de la cinta (negrita, cursiva, alineacion...) NO se pulsan de verdad: darian formato a la seleccion del
usuario. Se anotan como formato pendiente de Claude y se aplican al texto que Claude escribe. Se reconocen por su
AutomationId (Bold, Italic, AlignCenter...), que no depende del idioma de Windows.
"""
import time

BOOKMARK, START = "_PCControl", "_PCControlStart"
FORMAT_IDS = {                                   # AutomationId del boton de la cinta -> formato
    "Bold": ("bold",), "Italic": ("italic",), "UnderlineGallery": ("underline",), "Strikethrough": ("strike",),
    "Subscript": ("sub",), "Superscript": ("super",),
    "AlignLeft": ("align", 0), "AlignCenter": ("align", 1), "AlignRight": ("align", 2), "AlignJustify": ("align", 3),
}
_ALIGN = {0: "izquierda", 1: "centrado", 2: "derecha", 3: "justificado"}
_pending = {}                                    # hwnd -> formato pendiente de Claude


def _get_app():
    import pythoncom, win32com.client
    pythoncom.CoInitialize()
    return win32com.client.GetActiveObject("Word.Application")


def window(hwnd):
    """La ventana de Word con ese hwnd (o None)."""
    for w in _get_app().Windows:
        if w.Hwnd == hwnd:
            return w
    return None


def _bm(doc, name):
    bms = doc.Bookmarks
    bms.ShowHidden = True                        # los marcadores ocultos solo se ven con esto
    return bms.Exists(name)


def anchor_pos(doc):
    """Donde escribe Claude: su marcador, o el final del documento (dentro del ultimo parrafo) la primera vez."""
    if _bm(doc, BOOKMARK):
        return doc.Bookmarks(BOOKMARK).Range.Start
    return max(0, doc.Content.End - 1)


def _set(doc, name, pos):
    doc.Bookmarks.ShowHidden = True
    doc.Bookmarks.Add(name, doc.Range(pos, pos))


def state(hwnd):
    return _pending.setdefault(hwnd, {"bold": False, "italic": False, "underline": False, "strike": False, "sub": False, "super": False, "align": None})


def describe(st):
    on = [k for k in ("bold", "italic", "underline", "strike", "sub", "super") if st[k]]
    return ", ".join(on + ([f"alineacion {_ALIGN[st['align']]}"] if st["align"] is not None else [])) or "normal"


def format_click(hwnd, automation_id):
    """Un boton de formato de la cinta: se anota para el texto de Claude (no se toca la seleccion del usuario). None si no es de formato."""
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
    return f"ok (formato de Claude: {describe(st)}; se aplica a lo que escriba Claude, no a tu seleccion)"


def _format(rng, st):
    f = rng.Font
    f.Bold, f.Italic, f.Underline, f.StrikeThrough = bool(st["bold"]), bool(st["italic"]), 1 if st["underline"] else 0, bool(st["strike"])
    f.Subscript, f.Superscript = bool(st["sub"]), bool(st["super"])      # explicito: no se hereda del texto anterior
    if st["align"] is not None:
        rng.ParagraphFormat.Alignment = st["align"]


def type_text(hwnd, text, replace=False, progress=None):
    """Escribe en el punto de Claude. replace=True sustituye lo ULTIMO que escribio Claude (nunca algo del usuario). Con `progress`,
    escribe en trocitos y avisa de donde esta el punto tras cada uno, para que se vea escribir. Devuelve el mensaje de resultado."""
    w = window(hwnd)
    if w is None:
        return None
    doc, st = w.Document, state(hwnd)
    text = text.replace("\r\n", "\r").replace("\n", "\r")
    before = doc.ComputeStatistics(0)                                          # wdStatisticWords
    pos = anchor_pos(doc)
    if replace and _bm(doc, START):
        s0 = doc.Bookmarks(START).Range.Start
        if s0 < pos:
            doc.Range(s0, pos).Delete(); pos = s0
    start = pos
    chunks = [text[i:i + 3] for i in range(0, len(text), 3)] if progress else [text]
    for chunk in chunks:
        rng = doc.Range(pos, pos)
        rng.InsertAfter(chunk)
        _format(rng, st)
        pos = rng.End
        _set(doc, BOOKMARK, pos)                                               # el marcador se mueve con cada trozo
        if progress:
            c = caret(hwnd, w)
            if c: progress(*c)
            time.sleep(0.03)
    _set(doc, START, start); _set(doc, BOOKMARK, pos)
    par = max(1, doc.Range(0, start).Paragraphs.Count) if start else 1
    ctx = doc.Range(max(0, start - 28), start).Text.replace("\r", " ¶ ")
    after = doc.ComputeStatistics(0)
    return (f"ok (Word por COM, en el punto de escritura de Claude: tu seleccion no se toca; palabras {before} -> {after}; "
            f"escrito en el parrafo {par}" + (f" tras «{ctx.strip()}»" if ctx.strip() else " al principio") + ")")


def caret(hwnd, w=None):
    """(x, centro_y, alto) en pantalla del punto de Claude, o None si no se ve (p. ej. fuera de la zona visible)."""
    try:
        w = w or window(hwnd)
        if w is None:
            return None
        p = anchor_pos(w.Document)
        x, y, _, hh = w.GetPoint(0, 0, 0, 0, w.Document.Range(p, p))
        return (x, y + hh // 2, hh) if hh and hh > 0 else None
    except Exception:
        return None
