"""PC-Control MCP server: lets an AI look at and operate Windows apps without stealing focus or moving the mouse.

Where apps live
  * Apps opened with open_app() start minimized (button in the taskbar, nothing on screen, no focus), or on a
    hidden desktop with hidden=true. Apps you already have open are only observed/operated through window messages.

Permission modes (env PC_CONTROL_MODE, see policy.py)
  * auto (default)  protected categories (password managers, terminals, system admin, remote access, banking titles...)
                    are listed but need the user's permission: ask them, then repeat with confirm=true. Delicate actions
                    (pay, delete, send, install...) also need confirm=true.
  * strict          auto + acting only on apps listed in PC_CONTROL_ALLOW or ~/.pc-control/allow.txt.
  * bypass          like --dangerously-skip-permissions: no filters, no confirmations.
  * Create the file ~/.pc-control/PAUSE (or set PC_CONTROL_PAUSE=1) to make every tool refuse instantly, in any mode.
  * Nothing here ever focuses a window, moves your mouse or injects keystrokes.
  * Dialogs that an action opens in YOUR apps are only sent behind other windows, and never if you are using them.
"""
import ctypes, ctypes.wintypes as wt, functools, os, shlex, time, traceback
from concurrent.futures import ThreadPoolExecutor
import uiautomation as auto
import win32gui, win32process
try:                                              # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP, Image
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:                               # mcp 1.x
    from mcp.server.fastmcp import FastMCP, Image
    from mcp.server.fastmcp.exceptions import ToolError
from . import core, cursor, envvars, policy
from .hidden import DESK

NOTICE_SECONDS = 10.0                 # lo que dura el cartel de aviso y lo que se espera a que el usuario deje la ventana como estaba
HOME = envvars.get("HOME") or os.path.join(os.path.expanduser("~"), ".pc-control")
mcp = FastMCP("PC-Control")           # asi aparece en el chat: mcp__PC-Control__look ...

# UI Automation (COM) no admite usar un objeto desde otro hilo: todo lo que toque UIA en tu escritorio va a UN hilo dedicado.
def _init_main():
    global _uia_main
    _uia_main = auto.UIAutomationInitializerInThread(); _uia_main.__enter__()
MAIN = ThreadPoolExecutor(1, thread_name_prefix="pc-control-main", initializer=_init_main)


def tool(fn):
    """Registra la herramienta y devuelve al modelo el error REAL (y lo guarda en ~/.pc-control/last_error.log)."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except ToolError:
            raise
        except Exception as e:
            os.makedirs(HOME, exist_ok=True)
            with open(os.path.join(HOME, "last_error.log"), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%F %T')} {fn.__name__}\n{traceback.format_exc()}\n")
            raise ToolError(f"{e.__class__.__name__}: {e}")      # ojo: la herramienta `type` tapa el builtin type()
    return mcp.tool()(wrapper)


def _proc(hwnd):
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520); n = wt.DWORD(520)
        k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return os.path.splitext(os.path.basename(buf.value))[0].lower()
    finally:
        k.CloseHandle(h)


def _allowed():
    names = {x.strip().lower() for x in envvars.get("ALLOW", "").split(",") if x.strip()}
    f = os.path.join(HOME, "allow.txt")
    if os.path.exists(f):
        names |= {l.strip().lower() for l in open(f, encoding="utf-8") if l.strip() and not l.startswith("#")}
    return names


def _paused():
    if envvars.get("PAUSE") or os.path.exists(os.path.join(HOME, "PAUSE")):
        raise PermissionError("PC-Control en pausa (PAUSE).")


def _gate(window, act, confirm=False):
    _paused()
    hwnd = core.find_window(window)
    policy.check_window(policy.mode(), _proc(hwnd), win32gui.GetWindowText(hwnd), act, _allowed(), confirm)
    return hwnd


def _is_hidden(window):
    """Si la ventana vive en el escritorio oculto, todo se ejecuta en su hilo."""
    if not DESK.h:
        return False
    try:
        core.find_window(window); main_has = True
    except LookupError:
        main_has = False
    return DESK.owns(window, main_has)


def _on(window, fn):
    if _is_hidden(window):
        return DESK.run(lambda: fn(True))
    return MAIN.submit(lambda: fn(False)).result(timeout=120)


def _cursor_wanted(hwnd, hidden):
    """Se lanza la escena del cursor naranja si la ventana puede verse (no oculta, no minimizada, en pantalla). Despues, el propio
    cursor decide en cada fotograma si se ve (ver cursor.py)."""
    if hidden or cursor.mode() == "off":
        return False
    try:
        return not win32gui.IsIconic(hwnd) and bool(win32gui.IsWindowVisible(hwnd)) and not core.is_hidden(hwnd)
    except Exception:
        return False


def _screen_point(hwnd, it, target=None):
    """(x, y, x_izquierda, alto) en pantalla del elemento, calculado EN VIVO: si el usuario movio la ventana o le cambio el
    tamano, el cursor apunta a donde esta ahora el elemento, no a donde estaba cuando se miro."""
    l, tp = win32gui.GetWindowRect(hwnd)[:2]
    if it:
        rel = core.live_rel(hwnd, it) or it.rect
        x0, y0, x1, y1 = rel
        return l + (x0 + x1) // 2, tp + (y0 + y1) // 2, l + x0, y1 - y0
    k = core._state.get(hwnd, {}).get("scale", 1.0)
    return l + int(target[0] * k), tp + int(target[1] * k), l + int(target[0] * k), 0


def _layout_ok(hwnd, it, st):
    """True si lo que se va a tocar sigue ahi: el elemento (por su control en vivo) o, sin elemento, el mismo tamano de ventana."""
    if win32gui.IsIconic(hwnd):
        return True
    if it is not None:
        return core.live_rel(hwnd, it) is not None
    return not st or core._size(win32gui.GetWindowRect(hwnd)) == core._size(st["rect"])


def notice_text(ow, oh):
    """Lo que ve el usuario en el cartel de aviso."""
    return (f"Si tocas esta aplicacion, Claude no podra actuar en ella. Dejala como estaba ({ow}x{oh}) y no la toques mas: "
            f"cuando lo hagas, Claude sigue solo.")


def _ensure_layout(hwnd, hidden, it=None, timeout=None):
    """Si el usuario cambio la ventana de forma que ya no se encuentra lo que se iba a tocar (p. ej. al encogerla se colapso la
    cinta), se le AVISA con un cartel en la propia ventana y se espera (10 s) a que la deje como estaba; en cuanto lo hace, se
    sigue solo. Si no, se devuelve un mensaje claro. None = todo en orden."""
    st = core._state.get(hwnd)
    if hidden or _layout_ok(hwnd, it, st):
        return None
    timeout = NOTICE_SECONDS if timeout is None else timeout
    ow, oh = core._size(st["rect"]) if st else (0, 0)
    cw, ch = core._size(win32gui.GetWindowRect(hwnd))
    what = f"'{it.name}'" if it else "lo que iba a pulsar"
    if cursor.mode() != "off":                           # el cartel dura lo que se espera (10 s) y esta aislado a la app
        l, tp, r, _ = win32gui.GetWindowRect(hwnd)
        cursor.CURSOR.note(notice_text(ow, oh), (l + r) // 2, tp + 70, hold=timeout, scale=cursor.scale_for(hwnd), owner=hwnd)
    t0 = time.time(); end = t0 + timeout
    while time.time() < end:
        time.sleep(0.3)
        if _layout_ok(hwnd, it, st):
            cursor.CURSOR.hide_note()
            _LAST_NOTICE[hwnd] = round(time.time() - t0, 1)
            return None
    cursor.CURSOR.hide_note()
    return (f"La ventana cambio de tamano (antes {ow}x{oh}, ahora {cw}x{ch}) y ya no encuentro {what}. Dile al usuario que no toque "
            f"mas la aplicacion, que la ponga como estaba ({ow}x{oh}) y que la deje asi; despues repite la accion.")


_LAST_NOTICE = {}                                        # hwnd -> segundos que tardo el usuario en dejar la ventana como estaba


def verdict(result, delta):
    """Una frase que dice si la accion cumplio su funcion, comprobada contra lo que de verdad cambio en la ventana."""
    if result.startswith(("no he pulsado", "ventana minimizada", "el elemento ya no", "id desconocido", "la ventana cambio")):
        return "NO SE EJECUTO"
    if "formato de Claude" in result:
        return "formato anotado para lo que escriba Claude"
    if any(k in result for k in ("palabras", "EM_REPLACESEL", "WM_SETTEXT")):
        return "escritura confirmada por la propia aplicacion"
    if delta == "sin cambios":
        return "AVISO: SIN EFECTO VISIBLE (puede que no haya funcionado); comprueba con look"
    if delta.startswith(("cambio no medible", "sin lectura")):
        return "efecto no medible en este modo; comprueba con look"
    return "efecto observado"


def _act(hwnd, hidden, fn, note=""):
    """Ejecuta la accion; responde con el resultado, solo lo que cambio y si cumplio su funcion. En tu escritorio, las ventanas
    NUEVAS que salgan se mandan detras de las demas (nunca si las estas usando); en el oculto no hace falta."""
    before = set(core.top_windows())
    result = fn()
    time.sleep(0.25)
    if not hidden:
        core.tuck_new(win32process.GetWindowThreadProcessId(hwnd)[1], before)
    delta = core.changes(str(hwnd))
    extra = []
    if note: extra.append(note)
    if hwnd in _LAST_NOTICE:
        extra.append(f"el usuario tardo {_LAST_NOTICE.pop(hwnd)} s en dejar la ventana como estaba tras el aviso y segui solo")
    return f"{result} | cambio: {delta} | {verdict(result, delta)}" + "".join(f" | {e}" for e in extra)


@tool
def windows() -> str:
    """List open windows as 'hwnd | process | title', most recently used first. [EN USO] = the window the user is working in right now; [ULTIMA USADA] = the one they had in front before this chat (use it when they ask about "this app"). [PROTEGIDA: category] = needs the user's permission (ask them, then repeat the tool with confirm=true). (oculta) = on PC-Control's hidden desktop, (minimizada) = minimized."""
    _paused()
    m = policy.mode()
    fg = win32gui.GetForegroundWindow()
    fg_root = ctypes.windll.user32.GetAncestor(fg, 2) if fg else 0
    out, first = [], True
    for h in core.top_windows():                       # el orden z es el orden de uso: la primera es la mas reciente
        p, t = _proc(h), win32gui.GetWindowText(h)
        cat = None if m == "bypass" else policy.blocked_category(p, t)
        using = h == fg or h == fg_root
        tag = " [EN USO]" if using else (" [ULTIMA USADA]" if first else "")
        first = first and using                          # la ultima usada es la primera que NO es la que esta en uso
        out.append(f"{h} | {p} | {t[:50]}" + (" (minimizada)" if win32gui.IsIconic(h) else "") + tag
                   + (f" [PROTEGIDA: {cat}; pide permiso al usuario antes de usarla]" if cat else ""))
    if DESK.h:
        for h, p, t in DESK.run(lambda: [(h, _proc(h), t) for h, t, _ in DESK._enum()]):
            cat = None if m == "bypass" else policy.blocked_category(p, t)
            out.append(f"{h} | {p} | {t[:50]} (oculta)" + (f" [PROTEGIDA: {cat}]" if cat else ""))
    return "\n".join(out)


@tool
def look(window: str, mode: str = "auto", confirm: bool = False):
    """See a window WITHOUT focusing it. Picks the cheapest of: UI-tree text, OCR text, or an image with numbered marks. Elements are [id:name]; use the id with click/type. window = title substring or hwnd. mode: auto|image|uia. Protected windows (terminal, password manager...) need the user's permission: ask them first, then repeat with confirm=true."""
    def run(hidden):
        hwnd = _gate(window, False, confirm)
        r = core.look(str(hwnd), mode)
        head = f"{r['mode']} {r.get('tokens', 0)}tok | {r['why']}" + (f" | blind {r['blind']}" if "blind" in r else "")
        if r["mode"] == "image":
            return [head + f" | image {r['image_size']}: numbered marks = ids; x,y = image pixels", Image(path=r["image"])]
        return head + "\n" + (r.get("text") or "")
    return _on(window, run)


@tool
def click(window: str, target: str, right: bool = False, double: bool = False, confirm: bool = False) -> str:
    """Click an element id from look(), or 'x,y' in the pixels of the last image. In Word, the formatting buttons (bold, italic, alignment...) apply to the text Claude writes, never to the user's selection. Does not move the mouse or change focus. If the user moved or resized the window, the click is re-aimed on its own and the answer says so; it also says whether the click had a visible effect. Delicate actions (pay, delete, send, install) and protected windows need confirm=true after asking the user."""
    def run(hidden):
        hwnd = _gate(window, True, confirm)
        t = tuple(int(float(v)) for v in target.split(",")) if "," in target else int(target)
        it = core._state.get(hwnd, {}).get("items", {}).get(t) if isinstance(t, int) else None
        if isinstance(t, int):
            policy.check_click(policy.mode(), it.name if it else "", confirm)
        old = tuple(it.rect) if it else None
        warn = _ensure_layout(hwnd, hidden, it if isinstance(t, int) else None)
        if warn:
            raise ToolError(warn)
        if _cursor_wanted(hwnd, hidden):                   # el cursor naranja viaja hasta el elemento y luego se pulsa
            x, y, _, _ = _screen_point(hwnd, it, None if isinstance(t, int) else t)
            cursor.CURSOR.pointer(x, y, click=True, ms=320, scale=cursor.scale_for(hwnd), owner=hwnd); time.sleep(0.36)
            x2, y2, _, _ = _screen_point(hwnd, it, None if isinstance(t, int) else t)     # el usuario pudo mover/redimensionar mientras viajaba
            if abs(x2 - x) + abs(y2 - y) > 6:
                cursor.CURSOR.pointer(x2, y2, click=True, ms=140, scale=cursor.scale_for(hwnd), owner=hwnd); time.sleep(0.18)
        note = core.moved_note(old, core.live_rel(hwnd, it)) if it and not hidden else ""
        return _act(hwnd, hidden, lambda: core.click(str(hwnd), t, right, double), note)
    return _on(window, run)


@tool
def type(window: str, target: str, text: str, replace: bool = False, confirm: bool = False) -> str:
    """Type text into the element id (from look()). Appends, or replaces with replace=true. Id is required: no blind typing. Returns what changed. In Word, Claude writes at its OWN insertion point (not the user's caret) and the answer says in which paragraph and after which words, so the user clicking elsewhere cannot divert it. Protected windows need confirm=true after asking the user."""
    def run(hidden):
        hwnd = _gate(window, True, confirm)
        it = core._state.get(hwnd, {}).get("items", {}).get(int(target))
        try:
            is_pw = bool(it and it.ctrl is not None and it.ctrl.IsPassword)
        except Exception:
            is_pw = False
        policy.check_type(policy.mode(), it.name if it else "", is_pw)
        if it and win32gui.GetClassName(hwnd) != "OpusApp" and (it.ctrl is None or not core._native(it.ctrl)[0]):
            warn = _ensure_layout(hwnd, hidden, it)
            if warn:
                raise ToolError(warn)
        progress = None
        if it and _cursor_wanted(hwnd, hidden):            # barra naranja de escritura, del tamano de una linea
            s = cursor.scale_for(hwnd)
            wc = core.word_caret(hwnd) if win32gui.GetClassName(hwnd) == "OpusApp" else None        # Word: punto de Claude, posicion y altura reales
            if wc:
                cursor.CURSOR.caret(wc[0], wc[1], h=cursor.caret_height(wc[2], s), scale=s, owner=hwnd)
                progress = lambda x, y, hh: cursor.CURSOR.caret(x, y, h=cursor.caret_height(hh, s), scale=s, owner=hwnd)
            else:
                _, y, x, eh = _screen_point(hwnd, it)
                cursor.CURSOR.caret(x + 8, y, h=cursor.caret_height(eh, s), scale=s, owner=hwnd)
            time.sleep(0.25)
        return _act(hwnd, hidden, lambda: core.type_text(str(hwnd), text, int(target), replace, progress))
    return _on(window, run)


@tool
def key(window: str, name: str, confirm: bool = False) -> str:
    """Press a single key (enter, tab, esc, up, down, left, right, backspace, delete, home, end, pgup, pgdn, space). Shortcuts are unsupported. Protected windows need confirm=true after asking the user."""
    def run(hidden):
        hwnd = _gate(window, True, confirm)
        return _act(hwnd, hidden, lambda: core.key(str(hwnd), name))
    return _on(window, run)


@tool
def open_app(command: str, hidden: bool = False, confirm: bool = False) -> str:
    """Launch an app MINIMIZED and un-focused: nothing on screen, its button in the taskbar so the user can open it with a click. hidden=true runs it on a hidden desktop instead (no taskbar button). Then use windows()/look()/click()/type(). Protected apps (terminal, password manager...) need confirm=true after asking the user."""
    _paused()
    exe = os.path.splitext(os.path.basename(shlex.split(command, posix=False)[0].strip('"')))[0].lower()
    policy.check_launch(policy.mode(), exe, _allowed(), confirm)
    if hidden:
        r = DESK.open(command); where = "escritorio oculto (sin boton en la barra)"
    else:
        r = core.open_minimized(command); where = "minimizada, con su boton en la barra de tareas"
    ws = "; ".join(f"{w['hwnd']} {w['title']}" for w in r["windows"]) or r["note"]
    return f"pid {r['pid']} | {ws} | {where}"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
