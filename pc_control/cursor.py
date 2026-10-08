"""El cursor de Claude: un puntero naranja y una barra de escritura que muestran lo que hace, siempre a la vista.

No es el cursor del raton: son ventanas transparentes que dejan pasar los clics y nunca toman el foco, asi que no
interfieren con tu raton ni tu teclado. Nacen ya con el estilo "sin activar" y solo se muestran con SW_SHOWNOACTIVATE
(Tk, por ejemplo, activa su ventana al crearla y te quitaria el foco: por eso es Win32 puro).

Tres capas independientes, para que se vean a la vez: el PUNTERO (donde pulso), la BARRA (donde escribo) y el CARTEL de aviso.
  * Siempre visibles: tras actuar quedan en REPOSO, quietos en su ultima posicion (la barra no parpadea).
  * Entran y salen con una animacion suave (aparecen en fundido; el puntero viaja desde un lado y se va en fundido).
  * Cerrar la app donde actuan los hace desaparecer. Minimizarla solo los esconde: al volver, reaparecen donde estaban.
  * Aislados a la app: son ventanas "propiedad" de ella, asi que otra ventana encima los tapa a la vez que a ella.
  * El color se adapta al fondo (naranja con borde claro sobre fondo oscuro, naranja intenso con borde oscuro sobre fondo claro,
    azul si el fondo ya es naranja) y se vuelve a elegir si el fondo cambia.
  * Tamano: algo mayor que el cursor de Windows; escalan con el DPI de la ventana (nunca un tamano fijo).
La visibilidad se decide en CADA fotograma, no una vez al empezar: PC_CONTROL_CURSOR=always (por defecto: visibles mientras la app
se vea), user (solo si el usuario esta en la app: aparece si entra, desaparece si sale), off.
"""
import ctypes, ctypes.wintypes as wt, math, os, queue, threading, time
import numpy as np
from PIL import Image, ImageDraw
from . import envvars

SIZE, HOT = 176, 88                                   # lienzo holgado: cabe el puntero y su anillo hasta ~3x de escala
EX = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080   # LAYERED | TRANSPARENT | NOACTIVATE | TOOLWINDOW (NO topmost: sigue a su ventana)
SS = 4                                                # supermuestreo para suavizar los bordes
ARROW = [(0, 0), (0, 19), (5, 15), (8, 22), (12, 20), (9, 14), (15, 14)]   # puntero clasico, en pixeles a 96 dpi
ARROW_K = 1.0                                         # algo mayor que antes (0,78) y que el de Windows no pasa de 22 px
RING_S, FADE_IN, FADE_OUT = 0.45, 0.18, 0.22          # duracion del anillo del clic y de los fundidos (s)
ORANGE, WHITE, DARK = (255, 122, 26), (255, 255, 255), (24, 24, 28)
TITLES = {"pointer": "pc-control-cursor", "caret": "pc-control-caret", "note": "pc-control-note"}

_u = ctypes.WinDLL("user32", use_last_error=True)
_u.GetAncestor.argtypes = [wt.HWND, wt.UINT]; _u.GetAncestor.restype = wt.HWND
_u.GetForegroundWindow.restype = wt.HWND


def mode():
    m = envvars.get("CURSOR", "always").strip().lower()
    return m if m in ("user", "always", "off") else "always"


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
    one_line = 22 * s
    h = 0.7 * element_h if 0 < element_h <= 56 * s else one_line
    return int(round(max(15 * s, min(40 * s, h))))


def palette_for(bg):
    """(relleno, borde) que se ve bien sobre un fondo medio (r, g, b) o None si no se sabe. Se adapta al fondo."""
    if not bg:
        return ORANGE, WHITE
    r, g, b = bg
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if r > 190 and 60 < g < 190 and b < 110:          # el fondo ya es naranja: se cambia el tono para que no se pierda
        return (0, 132, 255), WHITE
    if lum < 100:
        return (255, 140, 44), WHITE                   # fondo oscuro: naranja vivo con borde claro
    if lum < 185:
        return (255, 106, 0), WHITE
    return (238, 90, 0), DARK                          # fondo claro: naranja intenso con borde oscuro


# ---------- dibujo ----------
def _bgra(im, alpha=255):
    """Imagen RGBA -> bytes BGRA con alfa premultiplicado (lo que pide UpdateLayeredWindow)."""
    a = np.asarray(im.resize((SIZE, SIZE), Image.LANCZOS)).astype(np.uint16)
    al = a[..., 3:4]
    rgb = (a[..., :3] * al // 255)[..., ::-1]
    return np.concatenate([rgb, al], axis=2).astype(np.uint8).tobytes()


def _canvas():
    im = Image.new("RGBA", (SIZE * SS, SIZE * SS), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def render_arrow(s=1.0, ring=0, pal=None):
    """Puntero naranja. ring = 0 (sin anillo) o 1..12: el anillo del clic, que se expande y se desvanece."""
    fill, edge = pal or (ORANGE, WHITE)
    im, d = _canvas()
    k = ARROW_K * s
    if ring:
        p = ring / 12.0
        r = (3 + p * 24) * s * SS
        d.ellipse([HOT * SS - r, HOT * SS - r, HOT * SS + r, HOT * SS + r],
                  outline=fill + (int(255 * (1 - p) ** 1.3),), width=max(2, round(2.4 * s * SS)))
    d.polygon([((HOT + x * k) * SS, (HOT + y * k) * SS) for x, y in ARROW], fill=fill + (255,), outline=edge + (255,), width=max(2, round(1.6 * s * SS)))
    return _bgra(im)


def render_caret(h, s=1.0, pal=None):
    """Barra en I: de la altura de una linea, con remates, FIJA (no parpadea)."""
    fill, edge = pal or (ORANGE, WHITE)
    im, d = _canvas()
    x, top, bot = HOT * SS, (HOT - h / 2) * SS, (HOT + h / 2) * SS
    half = max(1.4, 1.5 * s) * SS                     # ~3 px de grosor a 96 dpi
    serif = max(3.5, 4.6 * s) * SS
    for grow, col in ((0.9 * SS, edge + (255,)), (0, fill + (255,))):    # borde claro/oscuro por fuera, relleno por dentro
        d.rounded_rectangle([x - half - grow, top - grow, x + half + grow, bot + grow], radius=half, fill=col)
        for y in (top, bot):
            d.rounded_rectangle([x - serif - grow, y - half - grow, x + serif + grow, y + half + grow], radius=half, fill=col)
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
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=10 * K * s, fill=(28, 28, 32, 240), outline=ORANGE + (255,), width=max(2, int(2 * K * s)))
    d.rounded_rectangle([0, 0, bar + 6 * K * s, h - 1], radius=10 * K * s, fill=ORANGE + (255,))
    d.rectangle([bar, 2 * K, bar + 6 * K * s, h - 2 * K], fill=(28, 28, 32, 240))
    for i, line in enumerate(lines):
        d.text((pad + bar, pad + i * lh), line, fill=WHITE + (255,), font=font)
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
        self.hwnds = {}                               # capa -> hwnd de su ventana ("pointer", "caret", "note")

    @property
    def hwnd(self):
        return self.hwnds.get("pointer", 0)

    # -- API (se llama desde cualquier hilo) --
    def pointer(self, x, y, click=False, ms=320, scale=1.0, owner=0):
        """Lleva el puntero naranja a (x, y) en pantalla con una curva suave y, si click, marca el clic con un anillo. Despues queda
        en reposo, visible. owner = ventana a la que actua: el cursor queda aislado a ella."""
        self._ensure(); self.q.put(("pointer", int(x), int(y), click, ms, float(scale), int(owner)))

    def caret(self, x, y, h=22, hold=None, scale=1.0, owner=0):
        """Pone la barra de escritura en (x, y) (centro vertical, h px de alto). Se desliza hasta alli y queda en reposo, visible."""
        self._ensure(); self.q.put(("caret", int(x), int(y), int(h), float(scale), int(owner)))

    def note(self, text, x, y, hold=10.0, scale=1.0, owner=0):
        """Cartel de aviso para el usuario, centrado en x y con su borde superior en y. Dura `hold` segundos."""
        self._ensure(); self.q.put(("note", str(text), int(x), int(y), float(hold), float(scale), int(owner)))

    def hide_note(self):
        if self.thread and self.thread.is_alive():
            self.q.put(("hide_note",))

    def hide(self):
        """Quita las tres capas al instante y olvida su estado."""
        if self.thread and self.thread.is_alive():
            self.q.put(("hide",))

    def _ensure(self):
        if not (self.thread and self.thread.is_alive()):
            self.ready.clear()
            self.thread = threading.Thread(target=self._run, name="pc-control-cursor", daemon=True)
            self.thread.start()
        self.ready.wait(5)

    # -- hilo propio: crea las ventanas, las anima y bombea sus mensajes --
    def _run(self):
        import win32api, win32gui
        u, g = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)
        u.GetDC.restype = wt.HDC; g.CreateCompatibleDC.restype = wt.HDC; g.CreateCompatibleDC.argtypes = [wt.HDC]
        g.CreateDIBSection.restype = wt.HBITMAP
        g.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(_BMI), wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
        g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]; g.SelectObject.restype = wt.HGDIOBJ
        g.DeleteObject.argtypes = [wt.HGDIOBJ]
        g.GetPixel.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]; g.GetPixel.restype = wt.COLORREF
        u.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                          ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(_BLEND), wt.DWORD]
        u.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]; u.SetWindowLongPtrW.restype = ctypes.c_void_p
        hdc_screen = u.GetDC(None)

        wc = win32gui.WNDCLASS(); wc.lpszClassName = "PC-Control-Overlay"; wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpfnWndProc = lambda h, m, w, l: win32gui.DefWindowProc(h, m, w, l)
        try: win32gui.RegisterClass(wc)
        except Exception: pass

        class Win:
            """Una ventana superpuesta transparente (una capa). Nace sin activar y fuera de pantalla."""
            def __init__(self, title):
                self.h = win32gui.CreateWindowEx(EX, "PC-Control-Overlay", title, 0x80000000, -3000, -3000, 8, 8, 0, 0, wc.hInstance, None)
                self.memdc = g.CreateCompatibleDC(hdc_screen); self.dib = None; self.size = (0, 0)
                self.shown = False; self.owner = 0; self.last = None

            def set_owner(self, o):
                if self.owner != o:                                      # GWLP_HWNDPARENT en una ventana de nivel superior = dueno
                    u.SetWindowLongPtrW(self.h, -8, o or None); self.owner = o

            def present(self, x, y, data, w, h, alpha):
                if self.size != (w, h):                                  # cada capa tiene su propio lienzo, del tamano de lo que dibuja
                    bmi = _BMI(ctypes.sizeof(_BMI), w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0); bits = ctypes.c_void_p()
                    hbm = g.CreateDIBSection(self.memdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
                    old = g.SelectObject(self.memdc, hbm)
                    if self.dib: g.DeleteObject(self.dib[0])
                    self.dib, self.size, self.last = (hbm, bits), (w, h), None
                if self.last is not data:
                    ctypes.memmove(self.dib[1], data, w * h * 4); self.last = data
                pt, sz, src = wt.POINT(int(x), int(y)), wt.SIZE(w, h), wt.POINT(0, 0)
                u.UpdateLayeredWindow(self.h, hdc_screen, ctypes.byref(pt), ctypes.byref(sz), self.memdc, ctypes.byref(src), 0,
                                      ctypes.byref(_BLEND(0, 0, max(0, min(255, int(alpha))), 1)), 2)             # ULW_ALPHA
                if not self.shown:
                    win32gui.ShowWindow(self.h, 4); self.shown = True                                               # SW_SHOWNOACTIVATE: no activa

            def hide(self):
                if self.shown:
                    win32gui.ShowWindow(self.h, 0); self.shown = False

        wins = {k: Win(t) for k, t in TITLES.items()}
        for k, w in wins.items(): self.hwnds[k] = w.h

        def new_layer(): return {"on": False, "owner": 0, "a": 0.0, "closing": False, "dirty": True, "bg": None, "bg_t": 0.0, "bg_at": None, "pal": None}
        P, C, N = new_layer(), new_layer(), new_layer()
        P.update(pos=None, frm=(0, 0), to=(0, 0), t0=0.0, dur=0.3, travel=False, click=False, ring_t0=0.0, s=1.0)
        C.update(pos=None, to=(0, 0), h=22, s=1.0)
        N.update(until=0.0, data=None, s=1.0)
        cache = {}

        def sample_bg(x, y, pts):
            """Color medio del fondo alrededor (puntos FUERA del dibujo, para no medirse a si mismo)."""
            rs = gs = bs = n = 0
            for dx, dy in pts:
                c = g.GetPixel(hdc_screen, int(x + dx), int(y + dy))
                if c in (0xFFFFFFFF, -1): continue
                rs += c & 0xFF; gs += (c >> 8) & 0xFF; bs += (c >> 16) & 0xFF; n += 1
            return (rs / n, gs / n, bs / n) if n else None

        def refresh_palette(L, x, y, pts, now):
            moved = L["bg_at"] is None or abs(x - L["bg_at"][0]) + abs(y - L["bg_at"][1]) > 40
            if moved or now - L["bg_t"] > 0.4:
                bg = sample_bg(x, y, pts); L["bg_t"], L["bg_at"] = now, (x, y)
                pal = palette_for(bg)
                if pal != L["pal"]:
                    L["pal"] = pal; L["dirty"] = True

        def reset_all():
            for L in (P, C, N):
                L["on"] = False; L["a"] = 0.0
            for w in wins.values(): w.hide()

        self.ready.set()
        last = time.time()
        while True:
            win32gui.PumpWaitingMessages()
            now = time.time(); dt = max(0.001, min(0.1, now - last)); last = now
            # ---- comandos ----
            try:
                while True:
                    cmd = self.q.get_nowait()
                    if cmd[0] == "pointer":
                        _, x, y, click, ms, s, own = cmd
                        fresh = (not P["on"]) or P["owner"] != own or P["pos"] is None
                        P.update(on=True, owner=own, s=s, to=(x, y), click=click, dur=max(ms, 1) / 1000.0, t0=now, closing=False, travel=True, dirty=True)
                        if fresh:
                            P["frm"] = (x - 60 * s, y + 40 * s); P["a"] = 0.0           # entrada: llega desde un lado, en fundido
                        else:
                            P["frm"] = P["pos"]
                        wins["pointer"].set_owner(own)
                    elif cmd[0] == "caret":
                        _, x, y, hh, s, own = cmd
                        fresh = (not C["on"]) or C["owner"] != own or C["pos"] is None
                        C.update(on=True, owner=own, s=s, h=hh, to=(x, y), closing=False, dirty=True)
                        if fresh: C["pos"] = (x, y); C["a"] = 0.0                       # entrada: aparece en fundido
                        wins["caret"].set_owner(own)
                    elif cmd[0] == "note":
                        _, text, x, y, hold, s, own = cmd
                        data, nw, nh = render_note(text, s)
                        N.update(on=True, owner=own, until=now + hold, data=(data, nw, nh, x - nw // 2, y), closing=False, dirty=True, a=0.0)
                        wins["note"].set_owner(own)
                    elif cmd[0] == "hide_note":
                        N["closing"] = True
                    elif cmd[0] == "hide":
                        reset_all()
            except queue.Empty:
                pass

            # ---- cada capa: visibilidad continua, fundido y dibujo ----
            for name, L in (("pointer", P), ("caret", C), ("note", N)):
                if not L["on"]:
                    continue
                w, o = wins[name], L["owner"]
                if name == "note" and now > N["until"]:
                    N["closing"] = True
                alive = bool(win32gui.IsWindow(o)) if o else True
                iconic = bool(o and alive and win32gui.IsIconic(o)); vis = bool(not o or (alive and win32gui.IsWindowVisible(o)))
                on_user = user_is_on(o) if o else True
                hide_now = should_hide(mode(), alive, iconic, vis, on_user)
                want = 0.0 if (hide_now or L["closing"]) else 255.0
                step = 255.0 * dt / (FADE_IN if want > L["a"] else FADE_OUT)
                a2 = min(want, L["a"] + step) if want > L["a"] else max(want, L["a"] - step)
                if a2 != L["a"]:
                    L["a"] = a2; L["dirty"] = True
                if L["a"] <= 0.0:
                    w.hide(); L["last_shown"] = False
                    if (o and not alive) or L["closing"]:                  # la app se cerro (o se acabo el aviso): la capa desaparece del todo
                        L["on"] = False; L["closing"] = False
                        if o and not alive: w.set_owner(0)
                    continue
                if name == "pointer":
                    if P["travel"]:
                        p = min(1.0, (now - P["t0"]) / P["dur"]); e = 1 - (1 - p) ** 3                      # frena al llegar
                        (x0, y0), (x1, y1) = P["frm"], P["to"]
                        mx, my = (x0 + x1) / 2 + (y0 - y1) * 0.12, (y0 + y1) / 2 + (x1 - x0) * 0.12           # curva como una mano real
                        P["pos"] = ((1 - e) ** 2 * x0 + 2 * (1 - e) * e * mx + e ** 2 * x1, (1 - e) ** 2 * y0 + 2 * (1 - e) * e * my + e ** 2 * y1)
                        P["dirty"] = True
                        if p >= 1.0:
                            P["travel"] = False
                            if P["click"]: P["ring_t0"] = now
                    ring = 0
                    if P["ring_t0"] and now - P["ring_t0"] < RING_S:
                        ring = 1 + int((now - P["ring_t0"]) / RING_S * 11); P["dirty"] = True
                    elif P["ring_t0"]:
                        P["ring_t0"] = 0.0; P["dirty"] = True
                    x, y = P["pos"]; s = P["s"]
                    refresh_palette(P, x, y, [(dx * s, dy * s) for dx, dy in ((-34, -34), (0, -38), (34, -34), (-38, 0), (38, 0), (-34, 34), (0, 38), (34, 34))], now)
                    if not P["dirty"]: continue
                    k = ("a", round(s * 20), ring, P["pal"])
                    if k not in cache: cache[k] = render_arrow(s, ring, P["pal"])
                    off = (1 - L["a"] / 255.0) * 14 * s if want == 0.0 else 0.0                          # salida: se va en fundido, derivando un poco
                    w.present(x - HOT + off, y - HOT + off * 0.7, cache[k], SIZE, SIZE, L["a"]); P["dirty"] = False
                elif name == "caret":
                    (cx, cy), (tx, ty) = C["pos"], C["to"]
                    if abs(tx - cx) + abs(ty - cy) > 0.4:                                                  # se desliza hasta su sitio (sin saltar)
                        f = 1 - (0.62 ** (dt / 0.012)); C["pos"] = (cx + (tx - cx) * f, cy + (ty - cy) * f); C["dirty"] = True
                    elif C["pos"] != C["to"]:
                        C["pos"] = C["to"]; C["dirty"] = True
                    x, y = C["pos"]; s, hh = C["s"], C["h"]
                    refresh_palette(C, x, y, [(-20 * s, -hh / 2), (-20 * s, 0), (-20 * s, hh / 2), (20 * s, -hh / 2), (20 * s, 0), (20 * s, hh / 2)], now)
                    if not C["dirty"]: continue
                    k = ("c", round(s * 20), hh, C["pal"])
                    if k not in cache: cache[k] = render_caret(hh, s, C["pal"])
                    w.present(x - HOT, y - HOT, cache[k], SIZE, SIZE, L["a"]); C["dirty"] = False
                else:
                    if not N["dirty"]: continue
                    data, nw, nh, nx, ny = N["data"]
                    w.present(nx, ny, data, nw, nh, L["a"]); N["dirty"] = False
            time.sleep(0.012 if (P["on"] or C["on"] or N["on"]) else 0.05)


CURSOR = ClaudeCursor()
