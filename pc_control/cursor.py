"""El cursor de Claude: un puntero naranja (y una barra parpadeante al escribir) que muestra lo que hace.

No es el cursor del raton: es una ventana transparente que deja pasar los clics y nunca toma el foco, asi que no
interfiere con tu raton ni tu teclado. Nace ya con el estilo "sin activar" y solo se muestra con SW_SHOWNOACTIVATE
(Tk, por ejemplo, activa su ventana al crearla y te quitaria el foco: por eso es Win32 puro).

Visibilidad CONTINUA: cada accion crea una "escena" (puntero que viaja, barra que parpadea, cartel) con su duracion, y en
cada fotograma se comprueba si alguien puede verla: si el usuario entra en la app despues de empezar la accion, aparece; si
se va, se esconde; si vuelve, reaparece. Solo se dibuja cuando el usuario esta en la ventana que se esta manejando (modo
`user`, por defecto); `always` lo dibuja siempre que la ventana sea visible; `off` lo desactiva.
Variable de entorno: PCSIGHT_CURSOR=user|always|off

Los tamanos se adaptan a la pantalla: el puntero escala con el DPI de la ventana (como el cursor de Windows) y la barra
con la altura de la linea de texto, nunca un tamano fijo.
"""
import ctypes, ctypes.wintypes as wt, os, queue, threading, time
import numpy as np
from PIL import Image, ImageDraw

ORANGE, WHITE = (255, 122, 26, 255), (255, 255, 255, 255)
SIZE, HOT = 144, 72                                   # lienzo holgado: cabe el puntero y su anillo hasta ~2,5x de escala
EX = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080   # LAYERED | TRANSPARENT | NOACTIVATE | TOOLWINDOW (NO topmost: sigue a su ventana)
TITLE = "pcsight-cursor"
SS = 4                                                # supermuestreo para suavizar los bordes
ARROW = [(0, 0), (0, 19), (5, 15), (8, 22), (12, 20), (9, 14), (15, 14)]   # puntero clasico, en pixeles a 96 dpi
ARROW_K = 0.78                                        # el puntero de Claude es algo mas discreto que el de Windows

_u = ctypes.WinDLL("user32", use_last_error=True)
_u.GetAncestor.argtypes = [wt.HWND, wt.UINT]; _u.GetAncestor.restype = wt.HWND
_u.GetForegroundWindow.restype = wt.HWND


def mode():
    m = os.environ.get("PCSIGHT_CURSOR", "user").strip().lower()
    return m if m in ("user", "always", "off") else "user"


def should_show(m, is_iconic, is_visible_on_screen, user_is_on_it):
    """Regla pura: el cursor solo existe cuando alguien puede verlo y, en modo user, cuando el usuario esta en esa ventana."""
    if m == "off" or is_iconic or not is_visible_on_screen:
        return False
    return True if m == "always" else bool(user_is_on_it)


def should_hide(m, owner_alive, owner_iconic, owner_visible, user_is_on_it):
    """Regla pura, comprobada en cada fotograma: el cursor esta aislado a la app donde actua. Se esconde si esa ventana se
    cierra, se minimiza o se oculta, y, en modo user, mientras el usuario no este en ella. Es una condicion del momento, no
    una decision tomada al empezar: cuando se cumple de nuevo, el cursor vuelve a dibujarse."""
    if m == "off" or not owner_alive or owner_iconic or not owner_visible:
        return True
    return m == "user" and not user_is_on_it


def user_is_on(owner):
    """True si la ventana en primer plano es (o pertenece a) esa app. Se consulta en cada fotograma. (Los tests la sustituyen
    para simular que el usuario entra y sale sin tener que activar ninguna ventana.)"""
    try:
        fg = _u.GetForegroundWindow()
        return bool(fg) and _u.GetAncestor(fg, 2) == owner
    except Exception:
        return False


def scale_for(hwnd=0):
    """Escala de la pantalla de esa ventana (1.0 = 96 dpi, 1.5 = 144 dpi...). Es lo que adapta el tamano del cursor."""
    try:
        dpi = _u.GetDpiForWindow(hwnd) if hwnd else _u.GetDpiForSystem()
        return max(0.75, min(3.0, (dpi or 96) / 96.0))
    except Exception:
        return 1.0


def caret_height(element_h, s):
    """Altura de la barra de escribir: la de una linea de texto, NO la del elemento (una pagina entera de Word no es una linea).
    Si el elemento es de una linea (un campo, una celda) la barra mide ~70% de el; si es grande, una linea normal."""
    one_line = 17 * s
    h = 0.7 * element_h if 0 < element_h <= 48 * s else one_line
    return int(round(max(12 * s, min(32 * s, h))))


# ---------- dibujo ----------
def _bgra(im):
    """Imagen RGBA -> bytes BGRA con alfa premultiplicado (lo que pide UpdateLayeredWindow)."""
    a = np.asarray(im.resize((SIZE, SIZE), Image.LANCZOS)).astype(np.uint16)
    alpha = a[..., 3:4]
    rgb = (a[..., :3] * alpha // 255)[..., ::-1]
    return np.concatenate([rgb, alpha], axis=2).astype(np.uint8).tobytes()


def _canvas():
    im = Image.new("RGBA", (SIZE * SS, SIZE * SS), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def render_arrow(s=1.0, ring=0):
    im, d = _canvas()
    k = ARROW_K * s
    if ring:                                          # anillo del clic centrado en la punta
        r = (3 + ring * 1.6) * s * SS
        d.ellipse([HOT * SS - r, HOT * SS - r, HOT * SS + r, HOT * SS + r], outline=ORANGE, width=max(2, round(2.2 * s * SS)))
    d.polygon([((HOT + x * k) * SS, (HOT + y * k) * SS) for x, y in ARROW], fill=ORANGE, outline=WHITE, width=max(2, round(1.4 * s * SS)))
    return _bgra(im)


def render_caret(h, s=1.0):
    """Barra en I: fina, de la altura de una linea, con remates pequenos."""
    im, d = _canvas()
    x, top, bot = HOT * SS, (HOT - h / 2) * SS, (HOT + h / 2) * SS
    half = max(1.0, 0.9 * s) * SS                     # medio grosor (la barra mide ~2 px a 96 dpi)
    serif = max(2.5, 3.2 * s) * SS
    d.rounded_rectangle([x - half, top, x + half, bot], radius=half, fill=ORANGE)
    for y in (top, bot):
        d.rounded_rectangle([x - serif, y - half, x + serif, y + half], radius=half, fill=ORANGE)
    return _bgra(im)


def render_note(text, s=1.0, max_w=470):
    """Cartel de aviso: texto blanco sobre fondo oscuro con borde naranja, ajustado en lineas. Devuelve (bytes BGRA premultiplicado, ancho, alto).
    El tamano de letra y del cartel escalan con la pantalla (s)."""
    from PIL import ImageFont
    K = 2                                             # supermuestreo del texto
    px = max(12, round(14 * s))
    try:
        font = ImageFont.truetype("segoeui.ttf", px * K)
    except Exception:
        font = ImageFont.load_default(size=px * K)
    meas = ImageDraw.Draw(Image.new("L", (1, 1)))
    lines, cur = [], ""
    for wd in text.split():
        trial = (cur + " " + wd).strip()
        if meas.textlength(trial, font=font) <= max_w * s * K or not cur:
            cur = trial
        else:
            lines.append(cur); cur = wd
    lines.append(cur)
    lh = font.getbbox("Ag")[3] + 6 * K * s
    pad, bar = 12 * K * s, 5 * K * s
    w = int(max(meas.textlength(l, font=font) for l in lines) + 2 * pad + bar)
    h = int(len(lines) * lh + 2 * pad)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=10 * K * s, fill=(28, 28, 32, 240), outline=ORANGE, width=max(2, int(2 * K * s)))
    d.rounded_rectangle([0, 0, bar + 6 * K * s, h - 1], radius=10 * K * s, fill=ORANGE)
    d.rectangle([bar, 2 * K, bar + 6 * K * s, h - 2 * K], fill=(28, 28, 32, 240))
    for i, line in enumerate(lines):
        d.text((pad + bar, pad + i * lh), line, fill=WHITE, font=font)
    im = im.resize((max(1, w // K), max(1, h // K)), Image.LANCZOS)
    a = np.asarray(im).astype(np.uint16); alpha = a[..., 3:4]
    return np.concatenate([(a[..., :3] * alpha // 255)[..., ::-1], alpha], axis=2).astype(np.uint8).tobytes(), im.width, im.height


# ---------- Win32 ----------
class _BMI(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

class _BLEND(ctypes.Structure):
    _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte), ("fmt", ctypes.c_ubyte)]


class ClaudeCursor:
    def __init__(self):
        self.q = queue.Queue()
        self.thread = None
        self.ready = threading.Event()
        self.hwnd = 0

    # -- API (se llama desde cualquier hilo) --
    def pointer(self, x, y, click=False, ms=300, scale=1.0, owner=0):
        """Mueve el puntero naranja a (x, y) en pantalla con una curva suave y, si click, marca el clic con un anillo.
        owner = ventana a la que actua: el cursor queda aislado a ella (su orden de ventanas, minimizar, cerrar, que el usuario este en ella...)."""
        self._ensure(); self.q.put(("pointer", int(x), int(y), click, ms, float(scale), int(owner)))

    def caret(self, x, y, h=17, hold=1.3, scale=1.0, owner=0):
        """Muestra la barra parpadeante de escritura en (x, y) (centro vertical, h px de alto) durante `hold` segundos."""
        self._ensure(); self.q.put(("caret", int(x), int(y), int(h), hold, float(scale), int(owner)))

    def note(self, text, x, y, hold=10.0, scale=1.0, owner=0):
        """Cartel de aviso para el usuario, centrado en x y con su borde superior en y. Dura `hold` segundos."""
        self._ensure(); self.q.put(("note", str(text), int(x), int(y), float(hold), float(scale), int(owner)))

    def hide(self):
        if self.thread and self.thread.is_alive():
            self.q.put(("hide",))

    def _ensure(self):
        if not (self.thread and self.thread.is_alive()):
            self.ready.clear()
            self.thread = threading.Thread(target=self._run, name="pcsight-cursor", daemon=True)
            self.thread.start()
        self.ready.wait(5)

    # -- hilo propio: crea la ventana, la anima y bombea sus mensajes --
    def _run(self):
        import win32api, win32gui
        u, g = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
        u.GetDC.restype = wt.HDC; g.CreateCompatibleDC.restype = wt.HDC; g.CreateCompatibleDC.argtypes = [wt.HDC]
        g.CreateDIBSection.restype = wt.HBITMAP
        g.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(_BMI), wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
        g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]; g.SelectObject.restype = wt.HGDIOBJ
        g.DeleteObject.argtypes = [wt.HGDIOBJ]
        u.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                          ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(_BLEND), wt.DWORD]
        u.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]; u.SetWindowLongPtrW.restype = ctypes.c_void_p

        wc = win32gui.WNDCLASS(); wc.lpszClassName = "PcsightCursor"; wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpfnWndProc = lambda h, m, w, l: win32gui.DefWindowProc(h, m, w, l)
        try: win32gui.RegisterClass(wc)
        except Exception: pass
        # nace con el estilo sin-activar y sin mostrarse: nada parpadea y nunca toma el foco
        h = win32gui.CreateWindowEx(EX, "PcsightCursor", TITLE, 0x80000000, -3000, -3000, SIZE, SIZE, 0, 0, wc.hInstance, None)
        self.hwnd = h
        hdc_screen = u.GetDC(None); memdc = g.CreateCompatibleDC(hdc_screen)
        bmi = _BMI(ctypes.sizeof(_BMI), SIZE, -SIZE, 1, 32, 0, SIZE * SIZE * 4, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        hbm = g.CreateDIBSection(memdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        g.SelectObject(memdc, hbm)
        blank = bytes(SIZE * SIZE * 4)
        cache = {}

        def frame(kind, s, extra=0):
            key = (kind, round(s * 20), extra)
            if key not in cache:
                cache[key] = render_arrow(s, extra) if kind == "arrow" else render_caret(extra, s)
            return cache[key]

        S = {"owner": 0, "shown": False, "img": None, "kind": None, "pos": (None, None), "t0": 0, "dur": 0.3, "from": (0, 0),
             "to": (0, 0), "click": False, "until": 0.0, "ring": 0, "s": 1.0, "caret_h": 17, "last_blink": None, "note": None}

        def set_owner(o):
            if S["owner"] != o:                                          # GWLP_HWNDPARENT en una ventana de nivel superior = dueno
                u.SetWindowLongPtrW(h, -8, o or None); S["owner"] = o

        def present(x, y, data=None):
            if data is not None and data is not S["img"]:
                ctypes.memmove(bits, data, SIZE * SIZE * 4); S["img"] = data
            pt, sz, src = wt.POINT(int(x) - HOT, int(y) - HOT), wt.SIZE(SIZE, SIZE), wt.POINT(0, 0)
            u.UpdateLayeredWindow(h, hdc_screen, ctypes.byref(pt), ctypes.byref(sz), memdc, ctypes.byref(src), 0,
                                  ctypes.byref(_BLEND(0, 0, 255, 1)), 2)             # ULW_ALPHA
            if not S["shown"]:
                win32gui.ShowWindow(h, 4); S["shown"] = True                           # SW_SHOWNOACTIVATE: no activa

        def show_note(data, w, hh, x, y):
            bmi2 = _BMI(ctypes.sizeof(_BMI), w, -hh, 1, 32, 0, w * hh * 4, 0, 0, 0, 0); bits2 = ctypes.c_void_p()
            hbm2 = g.CreateDIBSection(memdc, ctypes.byref(bmi2), 0, ctypes.byref(bits2), None, 0)
            g.SelectObject(memdc, hbm2); ctypes.memmove(bits2, data, w * hh * 4)
            pt, sz, src = wt.POINT(int(x), int(y)), wt.SIZE(w, hh), wt.POINT(0, 0)
            u.UpdateLayeredWindow(h, hdc_screen, ctypes.byref(pt), ctypes.byref(sz), memdc, ctypes.byref(src), 0,
                                  ctypes.byref(_BLEND(0, 0, 255, 1)), 2)
            g.SelectObject(memdc, hbm); g.DeleteObject(hbm2)               # se vuelve al lienzo del puntero
            S["img"] = None
            if not S["shown"]:
                win32gui.ShowWindow(h, 4); S["shown"] = True

        def hide_window():
            """Esconde la ventana SIN descartar la escena: si el usuario vuelve a la app, reaparece."""
            if S["shown"]:
                win32gui.ShowWindow(h, 0); S["shown"] = False
            S["img"] = None

        def end_scene():
            S["kind"] = None; S["note"] = None
            hide_window()

        self.ready.set()
        while True:
            win32gui.PumpWaitingMessages()
            now = time.time()
            try:
                while True:
                    cmd = self.q.get_nowait()
                    if cmd[0] == "pointer":
                        _, x, y, click, ms, s, own = cmd
                        set_owner(own)
                        cur = S["pos"] if S["kind"] == "pointer" and S["pos"][0] is not None else (x - 60 * s, y + 40 * s)
                        S.update(kind="pointer", t0=now, dur=max(ms, 1) / 1000.0, to=(x, y), click=click, ring=0, s=s,
                                 until=now + max(ms, 1) / 1000.0 + 1.3, note=None)
                        S["from"] = cur; S["img"] = None
                    elif cmd[0] == "caret":
                        _, x, y, hh, hold, s, own = cmd
                        set_owner(own)
                        moved = S["kind"] == "caret"
                        S.update(kind="caret", t0=S["t0"] if moved else now, until=now + hold, pos=(x, y), caret_h=hh, s=s, note=None)
                        if not moved: S["last_blink"] = None
                        S["img"] = None
                    elif cmd[0] == "note":
                        _, text, x, y, hold, s, own = cmd
                        set_owner(own)
                        data, nw, nh = render_note(text, s)
                        S.update(kind="note", until=now + hold, t0=now, note=(data, nw, nh, x - nw // 2, y))
                        hide_window()                                    # se redibuja (con su tamano) si procede
                    elif cmd[0] == "hide":
                        end_scene()
            except queue.Empty:
                pass

            k, o = S["kind"], S["owner"]
            if k:
                if now > S["until"]:
                    end_scene()
                else:
                    alive = bool(win32gui.IsWindow(o)) if o else True
                    if o and not alive:                                  # la app se cerro: la escena no tiene sentido
                        end_scene(); set_owner(0); k = None
                    else:
                        iconic = bool(o and win32gui.IsIconic(o)); vis = bool(not o or win32gui.IsWindowVisible(o))
                        on = user_is_on(o) if o else True
                        # visibilidad CONTINUA: se evalua en cada fotograma; si no se cumple solo se esconde, y vuelve cuando se cumpla
                        if should_hide(mode(), alive, iconic, vis, on):
                            hide_window()
                        elif k == "pointer":
                            p = min(1.0, (now - S["t0"]) / S["dur"]); e = 1 - (1 - p) ** 3            # frena al llegar
                            (x0, y0), (x1, y1) = S["from"], S["to"]
                            mx, my = (x0 + x1) / 2 + (y0 - y1) * 0.12, (y0 + y1) / 2 + (x1 - x0) * 0.12   # curva como una mano real
                            x = (1 - e) ** 2 * x0 + 2 * (1 - e) * e * mx + e ** 2 * x1
                            y = (1 - e) ** 2 * y0 + 2 * (1 - e) * e * my + e ** 2 * y1
                            S["pos"] = (x, y)
                            if p >= 1.0 and S["click"] and S["ring"] < 12:
                                S["ring"] += 1; present(x, y, frame("arrow", S["s"], S["ring"]))
                            else:
                                present(x, y, frame("arrow", S["s"], 0))
                        elif k == "caret":
                            on_blink = int((now - S["t0"]) * 2.2) % 2 == 0 or now - S["t0"] < 0.25     # parpadea ~2 veces por segundo
                            if on_blink != S["last_blink"] or S["img"] is None:
                                S["last_blink"] = on_blink
                                present(*S["pos"], frame("caret", S["s"], S["caret_h"]) if on_blink else blank)
                            else:
                                present(*S["pos"])
                        elif k == "note" and not S["shown"]:
                            show_note(*S["note"])
            time.sleep(0.012)


CURSOR = ClaudeCursor()
