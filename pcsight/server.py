"""pcsight MCP server: lets an AI look at and operate Windows apps without stealing focus or moving the mouse.

Where apps live
  * Apps opened with open_app() run on a hidden Windows desktop: they never appear on your screen, never
    flicker, never take focus. Apps you already have open are only observed/operated through window messages.

Permission modes (env PCSIGHT_MODE, see policy.py)
  * auto (default)  filters apps by category (password managers, terminals, system admin, remote access, banking
                    titles...) and asks for confirm=true on delicate actions (pay, delete, send, install...).
  * strict          auto + acting only on apps listed in PCSIGHT_ALLOW or ~/.pcsight/allow.txt.
  * bypass          like --dangerously-skip-permissions: no filters, no confirmations.
  * Create the file ~/.pcsight/PAUSE (or set PCSIGHT_PAUSE=1) to make every tool refuse instantly, in any mode.
  * Nothing here ever focuses a window, moves your mouse or injects keystrokes.
"""
import ctypes, ctypes.wintypes as wt, functools, os, shlex, time, traceback
import win32gui, win32process
try:                                              # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP, Image
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:                               # mcp 1.x
    from mcp.server.fastmcp import FastMCP, Image
    from mcp.server.fastmcp.exceptions import ToolError
from . import core, policy
from .hidden import DESK

HOME = os.path.join(os.path.expanduser("~"), ".pcsight")
mcp = FastMCP("pcsight")


def tool(fn):
    """Registra la herramienta y devuelve al modelo el error REAL (y lo guarda en ~/.pcsight/last_error.log)."""
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
            raise ToolError(f"{type(e).__name__}: {e}")
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
    names = {x.strip().lower() for x in os.environ.get("PCSIGHT_ALLOW", "").split(",") if x.strip()}
    f = os.path.join(HOME, "allow.txt")
    if os.path.exists(f):
        names |= {l.strip().lower() for l in open(f, encoding="utf-8") if l.strip() and not l.startswith("#")}
    return names


def _paused():
    if os.environ.get("PCSIGHT_PAUSE") or os.path.exists(os.path.join(HOME, "PAUSE")):
        raise PermissionError("pcsight en pausa (PAUSE).")


def _gate(window, act):
    _paused()
    hwnd = core.find_window(window)
    policy.check_window(policy.mode(), _proc(hwnd), win32gui.GetWindowText(hwnd), act, _allowed())
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
    return DESK.run(lambda: fn(True)) if _is_hidden(window) else fn(False)


def _act(hwnd, hidden, fn):
    """Ejecuta la accion; responde con el resultado y solo lo que cambio. En el escritorio de usuario, las ventanas
    NUEVAS que salgan se aparcan (salvo que el usuario las este usando)."""
    before = set(core.top_windows())
    result = fn()
    time.sleep(0.25)
    if not hidden:
        core.tuck_new(win32process.GetWindowThreadProcessId(hwnd)[1], before)
    return f"{result} | cambio: {core.changes(str(hwnd))}"


@tool
def windows() -> str:
    """List open windows as 'hwnd | process | title'. '(oculta)' = lives on pcsight's hidden desktop."""
    _paused()
    out = []
    for h in core.top_windows():
        p = _proc(h)
        if p not in DENY:
            out.append(f"{h} | {p} | {win32gui.GetWindowText(h)[:50]}")
    if DESK.h:
        for h, p, t in DESK.run(lambda: [(h, _proc(h), t) for h, t, _ in DESK.windows()]):
            out.append(f"{h} | {p} | {t[:50]} (oculta)")
    return "\n".join(out)


@tool
def look(window: str, mode: str = "auto"):
    """See a window WITHOUT focusing it. Picks the cheapest of: UI-tree text, OCR text, or an image with numbered marks. Elements are [id:name]; use the id with click/type. window = title substring or hwnd. mode: auto|image|uia."""
    def run(hidden):
        hwnd = _gate(window, act=False)
        r = core.look(str(hwnd), mode)
        head = f"{r['mode']} {r.get('tokens', 0)}tok | {r['why']}" + (f" | blind {r['blind']}" if "blind" in r else "")
        if r["mode"] == "image":
            return [head + f" | image {r['image_size']}: numbered marks = ids; x,y = image pixels", Image(path=r["image"])]
        return head + "\n" + (r.get("text") or "")
    return _on(window, run)


@tool
def click(window: str, target: str, right: bool = False, double: bool = False, confirm: bool = False) -> str:
    """Click an element id from look(), or 'x,y' in the pixels of the last image. Does not move the mouse or change focus. Delicate actions (pay, delete, send, install) need confirm=true after asking the user. Returns what changed."""
    def run(hidden):
        hwnd = _gate(window, act=True)
        t = tuple(int(float(v)) for v in target.split(",")) if "," in target else int(target)
        if isinstance(t, int):
            it = core._state.get(hwnd, {}).get("items", {}).get(t)
            policy.check_click(policy.mode(), it.name if it else "", confirm)
        return _act(hwnd, hidden, lambda: core.click(str(hwnd), t, right, double))
    return _on(window, run)


@tool
def type(window: str, target: str, text: str, replace: bool = False) -> str:
    """Type text into the element id (from look()). Appends, or replaces with replace=true. Id is required: no blind typing. Returns what changed."""
    def run(hidden):
        hwnd = _gate(window, act=True)
        it = core._state.get(hwnd, {}).get("items", {}).get(int(target))
        try:
            is_pw = bool(it and it.ctrl is not None and it.ctrl.IsPassword)
        except Exception:
            is_pw = False
        policy.check_type(policy.mode(), it.name if it else "", is_pw)
        return _act(hwnd, hidden, lambda: core.type_text(str(hwnd), text, int(target), replace))
    return _on(window, run)


@tool
def key(window: str, name: str) -> str:
    """Press a single key (enter, tab, esc, up, down, left, right, backspace, delete, home, end, pgup, pgdn, space). Shortcuts are unsupported."""
    def run(hidden):
        hwnd = _gate(window, act=True)
        return _act(hwnd, hidden, lambda: core.key(str(hwnd), name))
    return _on(window, run)


@tool
def open_app(command: str, visible: bool = False) -> str:
    """Launch an allowed app on a hidden desktop (never shown, no flicker, no focus). Then use windows()/look(). visible=true opens it on the user's desktop but parked off-screen instead."""
    _paused()
    exe = os.path.splitext(os.path.basename(shlex.split(command, posix=False)[0].strip('"')))[0].lower()
    if exe in DENY or exe not in _allowed():
        raise PermissionError(f"abrir '{exe}' no esta permitido (PCSIGHT_ALLOW o ~/.pcsight/allow.txt).")
    if visible:
        r = core.open_app(command)
        ws = "; ".join(f"{w['hwnd']} {w['title']}" for w in r["windows"]) or r["note"]
        return f"pid {r['pid']} | {ws} | aparcada fuera de pantalla (usa reveal para verla)"
    r = DESK.open(command)
    ws = "; ".join(f"{w['hwnd']} {w['title']}" for w in r["windows"]) or r["note"]
    return f"pid {r['pid']} | {ws} | en el escritorio oculto"


@tool
def reveal(window: str) -> str:
    """Bring a window that pcsight parked off-screen (open_app visible=true) back where it was, without focusing it. Only on the user's request."""
    if _is_hidden(window):
        return "las apps del escritorio oculto no se pueden mover a tu pantalla; usa look() para verlas, o open_app(visible=true)"
    hwnd = _gate(window, act=True)
    return "ok (vuelve a su sitio)" if core.unpark(hwnd) else "esa ventana no estaba aparcada por pcsight"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
