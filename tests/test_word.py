"""Word: Claude escribe en SU PROPIO punto del documento, sin depender de la seleccion del usuario, y el usuario puede trabajar a la vez.

Se prueba contra un Word simulado (nunca contra el Word real del usuario: lo aprendido con el Bloc de notas). El simulado reproduce
lo que importa: marcadores que se desplazan cuando se edita antes, formato por rangos, seleccion del usuario y llamadas rechazadas.
"""
import os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pywintypes
from pcsight import word

FMT = ("bold", "italic", "underline", "strike", "sub", "super")


class Doc:
    def __init__(self, text):
        self.text = text + "\r"                                      # un documento de Word siempre acaba en marca de parrafo
        self.fmt = [dict.fromkeys(FMT, 0) for _ in self.text]
        self.align = [0] * len(self.text)
        self.marks = {}                                              # marcadores colapsados: nombre -> posicion
        self.sel = [0, 0]                                            # la seleccion DEL USUARIO
        self.busy = 0
        self.Bookmarks = Bookmarks(self)
        self.Content = type("C", (), {"End": property(lambda s: len(self.text))})()

    def insert(self, p, s):
        self.text = self.text[:p] + s + self.text[p:]
        self.fmt[p:p] = [dict(self.fmt[max(0, p - 1)]) for _ in s]   # hereda el formato del caracter anterior, como Word
        self.align[p:p] = [self.align[max(0, p - 1)]] * len(s)
        for k, v in self.marks.items():
            if v > p: self.marks[k] = v + len(s)
        for i in (0, 1):
            if self.sel[i] > p: self.sel[i] += len(s)

    def delete(self, a, b):
        n = b - a
        self.text = self.text[:a] + self.text[b:]; del self.fmt[a:b]; del self.align[a:b]
        for k, v in self.marks.items(): self.marks[k] = a if a < v <= b else (v - n if v > b else v)

    def Range(self, a, b): return R(self, a, b)
    def ComputeStatistics(self, kind): return len(self.text.split())


class Bookmarks:
    def __init__(self, doc): self.doc, self.ShowHidden = doc, False
    def Exists(self, name):
        if name.startswith("_") and not self.ShowHidden: return False      # los marcadores ocultos solo se ven con ShowHidden
        return name in self.doc.marks
    def __call__(self, name):                                        # como en Word: un Bookmark, que tiene .Range
        p = self.doc.marks[name]
        return type("Bookmark", (), {"Range": R(self.doc, p, p)})()
    def Add(self, name, rng): self.doc.marks[name] = rng.Start


class FontProxy:
    def __init__(self, r): object.__setattr__(self, "r", r)
    def __setattr__(self, name, val):
        key = {"Bold": "bold", "Italic": "italic", "Underline": "underline", "StrikeThrough": "strike", "Subscript": "sub", "Superscript": "super"}[name]
        for i in range(self.r.start, self.r.end): self.r.doc.fmt[i][key] = int(val)


class PF:
    def __init__(self, r): object.__setattr__(self, "r", r)
    def __setattr__(self, name, val):
        d, t = self.r.doc, self.r.doc.text
        a = t.rfind("\r", 0, self.r.start) + 1                       # el parrafo entero de cada extremo
        b = t.find("\r", max(self.r.end - 1, self.r.start)) + 1
        for i in range(a, b): d.align[i] = val


class R:
    def __init__(self, doc, a, b): self.doc, self.start, self.end = doc, a, b
    Start = property(lambda s: s.start); End = property(lambda s: s.end)
    Text = property(lambda s: s.doc.text[s.start:s.end])
    Font = property(lambda s: FontProxy(s)); ParagraphFormat = property(lambda s: PF(s))
    def InsertAfter(self, s):
        if self.doc.busy > 0:                                        # Word esta ocupado atendiendo al usuario
            self.doc.busy -= 1
            raise pywintypes.com_error(-2147418111, "La llamada fue rechazada por el destinatario", None, None)
        self.doc.insert(self.end, s); self.end += len(s)
    def Delete(self): self.doc.delete(self.start, self.end); self.end = self.start


class Win:
    def __init__(self, doc): self.Hwnd, self.Document = 4242, doc
    def GetPoint(self, a, b, c, d, rng): return (100 + rng.Start, 300, 0, 20)


class App:
    def __init__(self, doc): self.Windows = [Win(doc)]


def fresh(text="Hola mundo.\rSegundo parrafo."):
    d = Doc(text); word._get_app = lambda: App(d); word._pending.clear(); word.RETRY.update(tries=20, wait=0.01)
    return d

def para(d, i):
    ps = d.text.split("\r"); return ps[i]

c = {}
H = 4242
# 1) primer uso: va al final del documento (dentro del ultimo parrafo) y avisa de donde
d = fresh()
m = word.type_text(H, " Uno.")
c["primer uso: al final del documento"] = para(d, 1) == "Segundo parrafo. Uno." and "parrafo 2" in m and "Segundo parrafo." in m
c["la respuesta dice el parrafo, tras que palabras y que la seleccion no se toca"] = "tu seleccion no se toca" in m and "palabras" in m

# 2) EL USUARIO HACE CLIC EN OTRO SITIO: el texto de Claude sigue yendo a su punto
d.sel[:] = [3, 3]
word.type_text(H, " Dos.")
c["el usuario hace clic en otro sitio: el texto va igual a su punto"] = para(d, 1) == "Segundo parrafo. Uno. Dos." and para(d, 0) == "Hola mundo."
c["la seleccion del usuario no se mueve"] = d.sel == [3, 3]

# 3) el usuario edita ANTES del punto de Claude: el marcador se desplaza y sigue en su sitio
d.insert(0, "AAAA")
word.type_text(H, " Tres.")
c["el usuario escribe antes: el punto de Claude se desplaza con el texto"] = para(d, 1) == "Segundo parrafo. Uno. Dos. Tres." and para(d, 0) == "AAAAHola mundo."

# 4) formato: los botones anotan formato para el texto de Claude, NO tocan la seleccion del usuario
d = fresh(); d.sel[:] = [0, 4]
msg = word.format_click(H, "Bold")
c["Bold: mensaje claro y no es 'None'"] = bool(msg) and "bold" in msg and "no a tu seleccion" in msg
word.type_text(H, " NEGRITA")
n = len(" NEGRITA"); end = d.text.index(" NEGRITA") + n
c["lo que escribe Claude sale en negrita"] = all(d.fmt[i]["bold"] == 1 for i in range(end - n, end))
c["el texto del usuario (y su seleccion) NO se pone en negrita"] = all(d.fmt[i]["bold"] == 0 for i in range(0, end - n))
word.format_click(H, "Bold")                                          # otra vez: se quita
word.type_text(H, " normal")
s = d.text.index(" normal")
c["pulsar de nuevo quita la negrita aunque el texto anterior sea negrita (no se hereda)"] = all(d.fmt[i]["bold"] == 0 for i in range(s, s + 7))
word.format_click(H, "Italic"); word.format_click(H, "UnderlineGallery"); word.type_text(H, " IU")
s = d.text.index(" IU")
c["cursiva y subrayado tambien"] = all(d.fmt[i]["italic"] == 1 and d.fmt[i]["underline"] == 1 for i in range(s, s + 3))
c["un boton que no es de formato devuelve None (sigue el clic normal)"] = word.format_click(H, "InsertTab") is None and word.format_click(H, "") is None

# 5) alineacion y saltos de parrafo
d = fresh("Linea1")
word.format_click(H, "AlignCenter"); word.type_text(H, "\nTitulo")
p2 = d.text.index("Titulo")
c["un salto de linea se convierte en un parrafo nuevo (marca de parrafo)"] = d.text.split("\r")[:2] == ["Linea1", "Titulo"]
c["alineacion centrada solo en el parrafo de Claude"] = d.align[p2] == 1 and d.align[0] == 0
word.format_click(H, "AlignLeft"); word.type_text(H, "\rCuerpo")
c["alineacion a la izquierda para el siguiente parrafo"] = d.align[d.text.index("Cuerpo")] == 0 and d.align[p2] == 1

# 6) replace: sustituye lo ultimo que escribio Claude, nunca lo del usuario
d = fresh("Texto del usuario.")
word.type_text(H, " BORRADOR")
word.type_text(H, " DEFINITIVO", replace=True)
c["replace sustituye solo lo ultimo de Claude"] = para(d, 0) == "Texto del usuario. DEFINITIVO"

# 7) el usuario tiene a Word ocupado: las llamadas rechazadas se reintentan
d = fresh(); d.busy = 3
t0 = time.time(); m = word.type_text(H, " ocupado")
c["Word ocupado (llamada rechazada 3 veces): se reintenta y se escribe"] = para(d, 1).endswith(" ocupado") and d.busy == 0 and "ok (" in m
d = fresh(); d.busy = 10**6; word.RETRY.update(tries=3, wait=0.01)
try: word.type_text(H, " x"); c["Word ocupado sin fin: acaba fallando con el error real (no se cuelga)"] = False
except pywintypes.com_error: c["Word ocupado sin fin: acaba fallando con el error real (no se cuelga)"] = True
word.RETRY.update(tries=20, wait=0.01)

# 8) escribir en trocitos para que se vea, con el punto de escritura de Claude en pantalla
d = fresh(); seen = []
word.type_text(H, " abcdefgh", progress=lambda x, y, h: seen.append((x, y, h)))
c["con progreso: escribe en trocitos y avisa de donde esta el punto cada vez"] = len(seen) >= 3 and seen == sorted(seen) and para(d, 1).endswith(" abcdefgh")
c["caret(): la posicion del punto de Claude, no la del cursor del usuario"] = word.caret(H) is not None and word.caret(H)[2] == 20

# 9) un documento nuevo: el marcador es por documento, el primer uso vuelve al final
d = fresh("Otro doc")
c["documento nuevo: primer uso al final"] = word.anchor_pos(d) == len(d.text) - 1

bad = [k for k, v in c.items() if not v]
for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
print(f"word verification passed ({len(c)} comprobaciones)" if not bad else f"word verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
