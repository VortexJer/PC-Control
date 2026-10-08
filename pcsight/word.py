"""Adaptador de Word: Claude escribe y da formato en SU PROPIO punto del documento, sin depender de la seleccion del usuario,
y el usuario puede seguir trabajando en el mismo documento a la vez.

Por que: con la seleccion de Word, lo que Claude escribe va "donde este el cursor", y ese cursor es del usuario. Si el
usuario hace clic en otro sitio a mitad de una tarea, el texto (y la negrita) se desvian con el. Aqui Claude tiene un marcador
oculto propio en el documento (`_PCControl`, los marcadores que empiezan por _ no salen en la lista de marcadores): se mueve
solo si el usuario edita antes de el, no se ve afectado por donde haga clic el usuario, y la seleccion del usuario no se toca.

Los botones de formato de la cinta (negrita, cursiva, alineacion...) NO se pulsan de verdad: darian formato a la seleccion del
usuario. Se anotan como formato pendiente de Claude y se aplican al texto que Claude escribe. Se reconocen por su
AutomationId (Bold, Italic, AlignCenter...), que no depende del idioma de Windows.

Si Word esta ocupado atendiendo al usuario (esta escribiendo, abriendo un menu), rechaza las llamadas COM con RPC_E_CALL_REJECTED:
cada llamada se reintenta unos segundos en vez de fallar.
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
_BUSY = {-2147418111, -2147417846}               # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER: Word esta ocupado con el usuario
RETRY = {"tries": 60, "wait": 0.1}               # hasta ~6 s por llamada


def call(fn, *args):
    """Una llamada COM atomica con reintentos si Word la rechaza por estar ocupado (una llamada rechazada no se ejecuto: reintentar es seguro)."""
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
    """La ventana de Word con ese hwnd (o None)."""
    for w in call(lambda: list(_get_app().Windows)):
        if call(lambda: w.Hwnd) == hwnd:
            return w
    return None


def _bm(doc, name):
    bms = doc.Bookmarks
    call(setattr, bms, "ShowHidden", True)        # los marcadores ocultos solo se ven con esto
    return call(bms.Exists, name)


def anchor_pos(doc):
    """Donde escribe Claude: su marcador, o el final del documento (dentro del ultimo parrafo) la primera vez."""
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
    f = call(lambda: rng.Font)
    for name, val in (("Bold", bool(st["bold"])), ("Italic", bool(st["italic"])), ("Underline", 1 if st["underline"] else 0),
                      ("StrikeThrough", bool(st["strike"])), ("Subscript", bool(st["sub"])), ("Superscript", bool(st["super"]))):
        call(setattr, f, name, val)               # explicito: lo que Claude escribe no hereda el formato del texto anterior
    if st["align"] is not None:
        call(setattr, call(lambda: rng.ParagraphFormat), "Alignment", st["align"])


def type_text(hwnd, text, replace=False, progress=None):
    """Escribe en el punto de Claude. replace=True sustituye lo ULTIMO que escribio Claude (nunca algo del usuario). Con `progress`,
    escribe en trocitos y avisa de donde esta el punto tras cada uno, para que se vea escribir. Devuelve el mensaje de resultado."""
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
    chunks = [text[i:i + 3] for i in range(0, len(text), 3)] if progress else [text]
    for chunk in chunks:
        rng = call(doc.Range, pos, pos)
        call(rng.InsertAfter, chunk)
        _format(rng, st)
        pos = call(lambda: rng.End)
        _set(doc, BOOKMARK, pos)                                               # el marcador se mueve con cada trozo
        if progress:
            c = caret(hwnd, w)
            if c: progress(*c)
            time.sleep(0.03)
    _set(doc, START, start); _set(doc, BOOKMARK, pos)
    par = call(lambda: doc.Range(0, start).Text).count("\r") + 1 if start else 1
    ctx = call(lambda: doc.Range(max(0, start - 28), start).Text).replace("\r", " ¶ ")
    after = call(doc.ComputeStatistics, 0)
    return (f"ok (Word por COM, en el punto de escritura de Claude: tu seleccion no se toca; palabras {before} -> {after}; "
            f"escrito en el parrafo {par}" + (f" tras «{ctx.strip()}»" if ctx.strip() else " al principio") + ")")


def caret(hwnd, w=None):
    """(x, centro_y, alto) en pantalla del punto de Claude, o None si no se ve (p. ej. fuera de la zona visible)."""
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
