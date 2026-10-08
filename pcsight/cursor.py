"""El cursor de Claude: un puntero naranja (y una barra parpadeante al escribir) que muestra lo que hace.

No es el cursor del raton: es una ventana transparente que deja pasar los clics y nunca toma el foco, asi que no
interfiere con tu raton ni tu teclado. Solo se dibuja cuando el usuario esta en la ventana que se esta manejando
(modo `user`, por defecto); `always` lo dibuja siempre que la ventana sea visible; `off` lo desactiva.
Variable de entorno: PCSIGHT_CURSOR=user|always|off
"""
import os, queue, threading, time
import tkinter as tk

ORANGE, OUTLINE, KEY = "#ff7a1a", "#ffffff", "#010203"
SIZE = 64
HOT = 6                                               # la punta del puntero esta en (HOT, HOT) dentro de la ventana
EX = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080 | 0x00000008   # LAYERED | TRANSPARENT | NOACTIVATE | TOOLWINDOW | TOPMOST
TITLE = "pcsight-cursor"


def mode():
    m = os.environ.get("PCSIGHT_CURSOR", "user").strip().lower()
    return m if m in ("user", "always", "off") else "user"


def should_show(m, is_iconic, is_visible_on_screen, user_is_on_it):
    """Regla pura: el cursor solo existe cuando alguien puede verlo y, en modo user, cuando el usuario esta en esa ventana."""
    if m == "off" or is_iconic or not is_visible_on_screen:
        return False
    return True if m == "always" else bool(user_is_on_it)


class ClaudeCursor:
    def __init__(self):
        self.q = queue.Queue()
        self.thread = None
        self.ready = threading.Event()
        self.hwnd = 0

    # -- API (se llama desde cualquier hilo) --
    def pointer(self, x, y, click=False, ms=300):
        """Mueve el puntero naranja a (x, y) en pantalla con una curva suave y, si click, marca el clic con un anillo."""
        self._ensure(); self.q.put(("pointer", int(x), int(y), click, ms))

    def caret(self, x, y, h=24, hold=1.3):
        """Muestra la barra parpadeante de escritura en (x, y) (centro vertical) durante `hold` segundos."""
        self._ensure(); self.q.put(("caret", int(x), int(y), int(h), hold))

    def hide(self):
        if self.thread and self.thread.is_alive():
            self.q.put(("hide",))

    def _ensure(self):
        if not (self.thread and self.thread.is_alive()):
            self.ready.clear()
            self.thread = threading.Thread(target=self._run, name="pcsight-cursor", daemon=True)
            self.thread.start()
        self.ready.wait(5)

    # -- hilo propio: es el unico que toca Tk --
    def _run(self):
        import ctypes
        u = ctypes.windll.user32
        root = tk.Tk(); root.withdraw(); root.title(TITLE)
        root.overrideredirect(True); root.configure(bg=KEY)
        root.attributes("-transparentcolor", KEY); root.attributes("-topmost", True)
        cv = tk.Canvas(root, width=SIZE, height=SIZE, bg=KEY, highlightthickness=0); cv.pack()
        root.geometry(f"{SIZE}x{SIZE}+-3000+-3000"); root.update_idletasks()
        h = u.GetParent(root.winfo_id()) or root.winfo_id()
        u.SetWindowLongW(h, -20, u.GetWindowLongW(h, -20) | EX)       # clic-a-traves, sin foco, sin boton en la barra
        self.hwnd = h
        root.deiconify(); root.geometry(f"{SIZE}x{SIZE}+-3000+-3000")  # nace FUERA de pantalla: nada parpadea
        self.ready.set()
        S = {"x": None, "y": None, "kind": None, "t0": 0, "dur": 0, "from": (0, 0), "to": (0, 0), "click": False,
             "until": 0.0, "caret_h": 24, "ring": 0}

        def place(x, y):
            root.geometry(f"+{int(x) - HOT}+{int(y) - HOT}")

        def draw_arrow():
            cv.delete("all")
            pts = [0, 0, 0, 19, 5, 15, 8, 22, 12, 20, 9, 14, 15, 14]
            pts = [HOT + v * 1.0 for v in pts]
            cv.create_polygon(pts, fill=ORANGE, outline=OUTLINE, width=1.5)

        def draw_ring(r):
            cv.create_oval(HOT - r, HOT - r, HOT + r, HOT + r, outline=ORANGE, width=2)

        def draw_caret(on):
            cv.delete("all")
            if on:
                hh = S["caret_h"]
                cv.create_rectangle(HOT - 1, HOT - hh // 2, HOT + 2, HOT + hh // 2, fill=ORANGE, outline=OUTLINE)
                cv.create_rectangle(HOT - 4, HOT - hh // 2, HOT + 5, HOT - hh // 2 + 2, fill=ORANGE, outline="")
                cv.create_rectangle(HOT - 4, HOT + hh // 2 - 2, HOT + 5, HOT + hh // 2, fill=ORANGE, outline="")

        def hide():
            S["kind"] = None; cv.delete("all"); root.geometry(f"{SIZE}x{SIZE}+-3000+-3000")

        def tick():
            now = time.time()
            try:
                while True:
                    cmd = self.q.get_nowait()
                    if cmd[0] == "pointer":
                        _, x, y, click, ms = cmd
                        start = (S["x"], S["y"]) if S["x"] is not None and S["kind"] else (x - 60, y + 40)
                        S.update(kind="pointer", t0=now, dur=max(ms, 1) / 1000, to=(x, y), click=click, ring=0, until=now + max(ms, 1) / 1000 + 1.3)
                        S["from"] = start; draw_arrow()
                    elif cmd[0] == "caret":
                        _, x, y, hh, hold = cmd
                        S.update(kind="caret", x=x, y=y, caret_h=hh, until=now + hold, t0=now); place(x, y)
                    elif cmd[0] == "hide":
                        hide()
            except queue.Empty:
                pass
            k = S["kind"]
            if k == "pointer":
                p = min(1.0, (now - S["t0"]) / S["dur"]); e = 1 - (1 - p) ** 3          # frena al llegar
                (x0, y0), (x1, y1) = S["from"], S["to"]
                # curva: el punto medio se desplaza en perpendicular, como una mano real
                mx, my = (x0 + x1) / 2 + (y0 - y1) * 0.12, (y0 + y1) / 2 + (x1 - x0) * 0.12
                x = (1 - e) ** 2 * x0 + 2 * (1 - e) * e * mx + e ** 2 * x1
                y = (1 - e) ** 2 * y0 + 2 * (1 - e) * e * my + e ** 2 * y1
                S["x"], S["y"] = x, y; place(x, y)
                if p >= 1.0 and S["click"] and S["ring"] < 12:
                    S["ring"] += 1; draw_arrow(); draw_ring(4 + S["ring"] * 2)
                    if S["ring"] == 12: draw_arrow()
                if now > S["until"]:
                    hide()
            elif k == "caret":
                draw_caret(int((now - S["t0"]) * 2.2) % 2 == 0 or now - S["t0"] < 0.25)   # parpadea ~2 veces por segundo
                if now > S["until"]:
                    hide()
            root.after(16, tick)

        root.after(16, tick)
        root.mainloop()


CURSOR = ClaudeCursor()
