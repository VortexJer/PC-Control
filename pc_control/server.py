"""PC-Control MCP server: lets an AI look at and operate Windows apps without stealing focus or moving the mouse.

Where apps live
  * Apps opened with open_app() start minimized (button in the taskbar, nothing on screen, no focus), or on a
    hidden desktop with hidden=true. Apps you already have open are only observed/operated through window messages.

Permission modes (env PC_CONTROL_MODE, see policy.py)
  * auto (default)  protected categories (password managers, terminals, system admin, remote access, banking titles...)
                    are listed but PC-Control itself asks the user for permission (the model cannot answer). Delicate actions
                    (pay, delete, send, install...) are asked the same way.
  * strict          auto + acting only on apps listed in PC_CONTROL_ALLOW or ~/.pc-control/allow.txt.
  * bypass          like --dangerously-skip-permissions: no filters, no confirmations.
  * Create the file ~/.pc-control/PAUSE (or set PC_CONTROL_PAUSE=1) to make every tool refuse instantly, in any mode.
  * Nothing here ever focuses a window, moves your mouse or injects keystrokes.
  * Dialogs that an action opens in YOUR apps are only sent behind other windows, and never if you are using them.
"""
import asyncio, ctypes, ctypes.wintypes as wt, functools, inspect, os, re, shlex, time, traceback
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel, Field
import uiautomation as auto
import win32gui, win32process
try:                                              # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP, Image, Context
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:                               # mcp 1.x
    from mcp.server.fastmcp import FastMCP, Image, Context
    from mcp.server.fastmcp.exceptions import ToolError
from . import actions, core, cursor, envvars, office, perm, policy
from .hidden import DESK

NOTICE_SECONDS = 10.0                 # how long the notice banner lasts, and how long to wait for the user to put the window back as it was
HOME = envvars.get("HOME") or os.path.join(os.path.expanduser("~"), ".pc-control")
mcp = FastMCP("PC-Control")           # this is how it appears in the chat: mcp__PC-Control__look ...

# UI Automation (COM) does not allow using an object from another thread: everything that touches UIA on your desktop goes to ONE dedicated thread.
def _init_main():
    global _uia_main
    _uia_main = auto.UIAutomationInitializerInThread(); _uia_main.__enter__()
MAIN = ThreadPoolExecutor(1, thread_name_prefix="pc-control-main", initializer=_init_main)


class _Permission(BaseModel):
    allow: bool = Field(False, description="Whether you allow Claude to do it")


async def _ask_user(ctx, e, tool_name, args):
    """Ask the USER for permission, never the model. First through the MCP protocol (elicitation). If the client does not support it (Claude Code),
    it relies on the PC-Control hook: Claude Code shows its permission dialog to the user (see hook.py and perm.py)."""
    if e.key and perm.approved(e.key):
        return True
    try:
        r = await ctx.elicit(e.question, _Permission)
    except Exception:
        s = perm.sig(tool_name, args)
        if perm.take("asked", s):                       # the hook asked the user and they said yes: valid for THIS time only
            if perm.flag("pr_seen") and not perm.take("shown", s):
                # This Claude Code announces every time it is about to show a dialog, and for this call it did NOT: some rule ("don't ask
                # again") skipped it and the user never saw the question. It is not executed.
                raise ToolError(f"{e.question} The question was NOT shown to the user (a Claude Code permission rule skips it), so it is not "
                                f"executed. Tell the user to remove that rule with /permissions (the PC-Control one) and repeat the call.")
            return True                                 # a plain yes is for THIS call only, in every mode ("don't ask again" remembers the window)
        perm.mark("pending", s, e.detail or e.question)
        raise ToolError(f"{e.question} It needs the USER's permission and they cannot be asked from here. Repeat EXACTLY the same "
                        f"call: Claude Code will show the user a permission question; if they say no, it is not executed and you must not insist. "
                        f"(If no question appears for them, the hook is missing: they should run `pc-control install`.)")
    return r.action == "accept" and bool(getattr(r.data, "allow", False))        # a yes is valid for this time only; remembering the window is done by the hook (convert_rules)


RAW = {}                                                        # tool name -> the plain function (no MCP wrapper)
SETTLE_S = 0.12                                                 # wait after an action before reading what changed (it was 0.25: the next call also reports what came late)
_FAST = {"skip": False}                                         # True while a middle step of steps() runs (actions all run on the MAIN thread, one at a time)


def tool(fn):
    """Register the tool. Returns the REAL error to the model (and saves ONLY the last one in ~/.pc-control/last_error.log; it is overwritten and deleted after a day).
    Permissions are NOT a parameter the model can set: if the user's permission is needed, the server asks them."""
    @functools.wraps(fn)
    def inner(*a, **k):
        try:
            return fn(*a, **k)
        except (ToolError, policy.NeedsPermission):
            raise
        except Exception as e:
            os.makedirs(HOME, exist_ok=True)
            with open(os.path.join(HOME, "last_error.log"), "w", encoding="utf-8") as f:        # only the LAST error: it is overwritten, never grows
                f.write(f"{time.strftime('%F %T')} {fn.__name__}\n{traceback.format_exc()}\n")
            raise ToolError(f"{e.__class__.__name__}: {e}")      # note: the `type` tool shadows the builtin type()
        finally:
            _FAST["skip"] = False                                   # never leaks from steps() into the next call

    RAW[fn.__name__] = inner                                   # the `steps` tool runs several of these in one call

    @functools.wraps(fn)
    async def wrapper(*a, ctx: Context, **k):
        try:
            return await asyncio.to_thread(inner, *a, **k)
        except policy.NeedsPermission as e:
            if not await _ask_user(ctx, e, fn.__name__, k):
                raise ToolError("The user did NOT give permission: it is not executed. Do not insist or try to work around it.")
            return await asyncio.to_thread(inner, *a, **k, confirm=True)
    sig = inspect.signature(fn)
    ps = [p for p in sig.parameters.values() if p.name != "confirm"]
    ps.append(inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context))
    wrapper.__signature__ = sig.replace(parameters=ps)
    wrapper.__annotations__ = {k: v for k, v in fn.__annotations__.items() if k != "confirm"} | {"ctx": Context}
    return mcp.tool()(wrapper)


def _target(target, allow_point=True, hwnd=None):
    """An element id (int), an 'x,y' point (tuple) or an element NAME (resolved to its id) from what the model wrote."""
    t = str(target).strip()
    if allow_point and re.fullmatch(r"[-\d.\s]*,[-\d.\s]*", t):
        try:
            x, y = (int(float(v)) for v in t.split(","))
        except ValueError:
            raise ToolError(f"bad point '{target}': write x,y in pixels of the last image, e.g. 120,45")
        if x < 0 or y < 0:
            raise ToolError(f"bad point '{target}': coordinates start at 0,0 (top-left of the last image)")
        return x, y
    if t.isdigit():
        return int(t)
    if not t or hwnd is None or re.fullmatch(r"[-\d.\s]+", t):
        raise ToolError(f"bad target '{target}': use an element id from look(), or the element's name" + (", or x,y" if allow_point else ""))
    return _by_name(hwnd, t)


def _by_name(hwnd, name):
    """The id of the element called `name`: from the last look, else searched in the window (also out of view). Several with that
    name -> their ids are listed instead of guessing."""
    def matches():
        items = core._state.get(hwnd, {}).get("items", {})
        exact = [(i, it) for i, it in items.items() if it.name.strip().lower() == name.lower()]
        if len(exact) > 1:
            exact = [(i, it) for i, it in exact if it.clickable] or exact
        return exact or [(i, it) for i, it in items.items() if it.clickable and name.lower() in it.name.lower()]
    hits = matches()
    if not hits:
        actions.find(str(hwnd), name)
        hits = matches()
    if len(hits) == 1:
        return hits[0][0]
    if not hits:
        raise ToolError(f"no element named '{name}' in this window: use look() (or look with find=\"...\") and an id")
    raise ToolError(f"{len(hits)} elements match '{name}': " + " ".join(f"[{i}:{it.name[:30]}] ({it.kind})" for i, it in hits[:8]) + " - use the id")


OPENED_PIDS = set()                                       # the app's OWN processes (never shared ones): their later windows count too
OPENED = set()                                            # WINDOWS (hwnds) Claude opened in this session: never whole processes, which
                                                          # can be shared (explorer.exe = desktop + folders; ApplicationFrameHost = every Store app)


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
        raise PermissionError("PC-Control is paused (PAUSE).")


def _gate(window, act, confirm=False):
    _paused()
    hwnd = core.find_window(window)
    policy.check_window(policy.mode(), _proc(hwnd), win32gui.GetWindowText(hwnd), act, _allowed(), confirm, hwnd)
    return hwnd


# ---- re-verified right before acting: between look() / _gate() and the real action the window is alive ----
def _category(hwnd):
    """The protection category the window falls under NOW (protected app, sensitive title, ask mode) or None."""
    return policy.needs_ask(policy.mode(), _proc(hwnd), win32gui.GetWindowText(hwnd))


def _recheck_window(hwnd, cat0):
    """The window (and any dialog it opened) must still be what the user was asked about: a title that turned into a login or a payment, a
    protected category that was not there, a sign-in dialog that popped up. Raises ToolError (not executed) if not."""
    cat = _category(hwnd)
    if cat and cat != cat0:
        raise ToolError(f"the window changed while acting (now: {cat}): not executed. Repeat the call so the user is asked about it")
    if policy.mode() == "bypass":
        return
    for h in core.popup_windows(hwnd):
        try:
            pc = policy.needs_ask(policy.mode(), _proc(h), win32gui.GetWindowText(h))
        except Exception:
            continue
        if pc and pc not in (cat0, policy.ASK_ALL):
            raise ToolError(f"a dialog appeared ({pc}): not executed. Repeat the call so the user is asked about it")


def _live_name(it):
    """The name of what is behind the item RIGHT NOW (the policy must judge what will really be pressed), else the one looked at."""
    live = core.live_identity(it)
    return (live[1] if live else it.name) or it.name


def _guard_element(hwnd, it, confirm, verb="press"):
    """Right before acting on an element id: it must be the same element that was looked at (a reused control, a recycled row, a toggle
    that changed its label) and the sensitive-action policy runs on its LIVE name. Returns a sentence if it must NOT be executed."""
    if core.stale_state(hwnd):
        return "unknown id: this window is not the app that was looked at (its handle was reused): call look() again"
    bad = core.identity_problem(it)
    if bad:
        return f"{bad}: look again"
    policy.check_click(policy.mode(), _live_name(it), confirm)
    return None


CLOSE_NAMES = {"close", "cerrar", "fermer", "schliessen", "schließen", "chiudi", "fechar"}
CLIPBOARD_KEYS = {("ctrl", "c"), ("ctrl", "x"), ("ctrl", "v"), ("ctrl", "insert"), ("shift", "insert"), ("shift", "delete")}


def _guard_point(hwnd, t, confirm):
    """x,y carries no identity. With a look() behind it, the element of that look under the point is judged: it must still be that element
    and the sensitive-action policy runs on its live name. Without a look nothing is known about what is there (see README)."""
    if core.stale_state(hwnd):
        raise ToolError("this window is not the app that was looked at (its handle was reused): call look() again")
    st = core._state.get(hwnd)
    if not st:
        return
    k = st.get("scale", 1.0)
    it = core.item_at(hwnd, int(t[0] * k), int(t[1] * k))
    if it is None:
        return
    bad = core.identity_problem(it)
    if bad:
        raise ToolError(f"{bad}: look again before clicking by coordinates")
    policy.check_click(policy.mode(), _live_name(it), confirm)


def _mine(hwnd):
    return hwnd in OPENED or win32process.GetWindowThreadProcessId(hwnd)[1] in OPENED_PIDS


def _closing_guard(hwnd, confirm):
    """Closing a window Claude did not open can lose the user's work: it is a delicate action (asked every time)."""
    if policy.mode() != "bypass" and not confirm and not _mine(hwnd):
        raise policy.NeedsPermission("Claude wants to close a window it did not open. Do you allow it?", detail=T_("close_other"))


def _key_guard(hwnd, name, confirm):
    """Keys that act without naming what they act on: alt+f4 closes the window, the clipboard keys read or change the user's clipboard
    (which may hold a copied password), enter / space press the default button of a dialog (maybe «Delete»)."""
    if policy.mode() == "bypass" or confirm:
        return
    from .actions import norm_combo
    combo = norm_combo(name) or ()
    if combo == ("alt", "f4"):
        _closing_guard(hwnd, confirm)
    elif combo in CLIPBOARD_KEYS:
        raise policy.NeedsPermission("Claude wants to use the clipboard (copy, cut or paste). Do you allow it?", detail=T_("clipboard"))
    elif combo in (("enter",), ("space",)):
        bad = _dialog_default_risk(hwnd)
        if bad:
            raise policy.NeedsPermission(f"Claude wants to press {combo[0]} in a dialog that offers '{bad}'. Do you allow it?", detail=T_("press_in_dialog", name=bad))


def _dialog_default_risk(hwnd):
    """The name of a sensitive button (Delete, Send, Pay...) offered by a dialog of this app, or None. Enter or space would press it unseen."""
    for h in core.popup_windows(hwnd) + ([hwnd] if win32gui.GetClassName(hwnd) == "#32770" else []):
        try:
            for it in core.walk_uia(h, win32gui.GetWindowRect(h), offscreen_ok=True, budget_s=1.5):
                if it.kind in ("button", "splitbutton") and policy.SENSITIVE_ACTION.search(it.name or ""):
                    return it.name
        except Exception:
            continue
    return None


def T_(key, **kw):
    from .ui_text import T
    return T(key, **kw)


def _is_hidden(window):
    """If the window lives on the hidden desktop, everything runs on its thread."""
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
    """The orange cursor scene is launched if the window can be seen (not hidden, not minimized, on screen). After that, the cursor
    itself decides on every frame whether it is visible (see cursor.py)."""
    if hidden or cursor.mode() == "off":
        return False
    try:
        return not win32gui.IsIconic(hwnd) and bool(win32gui.IsWindowVisible(hwnd)) and not core.is_hidden(hwnd)
    except Exception:
        return False


def _screen_point(hwnd, it, target=None):
    """(x, y, left_x, height) of the element on screen, computed LIVE: if the user moved or resized the window,
    the cursor points to where the element is now, not to where it was when it was looked at."""
    l, tp = win32gui.GetWindowRect(hwnd)[:2]
    if it:
        rel = core.live_rel(hwnd, it) or it.rect
        x0, y0, x1, y1 = rel
        return l + (x0 + x1) // 2, tp + (y0 + y1) // 2, l + x0, y1 - y0
    k = core._state.get(hwnd, {}).get("scale", 1.0)
    return l + int(target[0] * k), tp + int(target[1] * k), l + int(target[0] * k), 0


def _layout_ok(hwnd, it, st):
    """True if what is about to be touched is still there: the element (through its live control) or, with no element, the same window size."""
    if win32gui.IsIconic(hwnd):
        return True
    if not st or core._size(st["rect"]) == (0, 0) or core._size(win32gui.GetWindowRect(hwnd)) == core._size(st["rect"]):
        return True                     # same size: the user did not resize it (an element out of view is click's job, not the user's)
    if it is not None:
        return core.live_rel(hwnd, it) is not None
    return not st or core._size(win32gui.GetWindowRect(hwnd)) == core._size(st["rect"])


def notice_text(ow, oh):
    """What the user sees on the notice banner."""
    from .ui_text import T
    return T("notice", w=ow, h=oh)                       # in the language of the user's Windows


def _ensure_layout(hwnd, hidden, it=None, timeout=None):
    """If the user changed the window so that what was about to be touched can no longer be found (e.g. shrinking it collapsed the
    ribbon), they are WARNED with a banner on the window itself and we wait (10 s) for them to put it back as it was; as soon as they
    do, we carry on by ourselves. Otherwise a clear message is returned. None = all fine."""
    st = core._state.get(hwnd)
    if hidden or _layout_ok(hwnd, it, st):
        return None
    timeout = NOTICE_SECONDS if timeout is None else timeout
    ow, oh = core._size(st["rect"]) if st else (0, 0)
    cw, ch = core._size(win32gui.GetWindowRect(hwnd))
    what = f"'{it.name}'" if it else "what I was about to press"
    if cursor.mode() != "off":                           # the banner lasts as long as the wait (10 s) and is isolated to the app
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
    return (f"The window changed size (was {ow}x{oh}, now {cw}x{ch}) and I can no longer find {what}. Tell the user not to touch "
            f"the application any more, to put it back as it was ({ow}x{oh}) and leave it that way; then repeat the action.")


_LAST_NOTICE = {}                                        # hwnd -> seconds the user took to put the window back as it was


def verdict(result, delta):
    """One sentence saying whether the action did its job, checked against what actually changed in the window."""
    if result.startswith(("did not press", "did not open", "did not drag", "did not type", "did not select", "did not scroll", "did not save", "at least 2 points are needed", "window minimized", "the element is no longer", "unknown id", "the window changed")):
        return "NOT EXECUTED"
    if "drag of" in result:
        return "drag sent: to see whether it drew, check with look (image mode)"
    if "is disabled" in result:
        return "NOT EXECUTED"
    if "saved through COM" in result or "menu command «" in result or ("through UI Automation)" in result and ("menu entry «" in result or "pressed «" in result)):
        return "command run by the app itself"
    if "menu entry" in result:
        return "menu entry pressed through accessibility (the menu closes or opens the submenu: check with look)"
    if "Claude format" in result:
        return "format noted for what Claude types"
    if "WARNING: the field does NOT contain" in result:
        return "WARNING: the typed text is not in the field; check with look"
    if "the field now contains the text" in result:
        return "typing confirmed by reading the field back"
    if "WARNING: it shows something else" in result:
        return "WARNING: the control shows another entry; check with look"
    if "the control now shows it" in result:
        return "selection confirmed by reading it back"
    if "the app is asking" in result:
        return "NOT CLOSED YET: the app is asking something (above)"
    m = re.search(r"(\d+)%? -> (\d+)%?", result)
    if result.startswith("ok") and "scroll" in result and m:                # a scroll reports its own position: that IS the measure
        return "scrolled" if m.group(1) != m.group(2) else "did not move: already at the edge"
    if "Excel through COM" in result and "now shows" in result:
        return "cell written, confirmed by reading it back"
    if any(k in result for k in ("words", "EM_REPLACESEL", "WM_SETTEXT")):
        return "typing confirmed by the application itself"
    if delta.startswith("the window is gone"):
        return "the window closed"
    if delta == "no changes" and "BM_CLICK" in result:
        return "clicked; nothing changed on screen"
    if delta == "no changes":
        return "WARNING: NO VISIBLE EFFECT; check with look"
    if delta.startswith(("change not measurable", "no previous reading", "no change in the UI tree")):
        return "effect not measurable; check with look"
    return "effect observed"


_TR = []


def _tr(label):
    """PC_CONTROL_TRACE=1: where the time of an action goes (appended to its answer). Off by default; costs nothing then."""
    if envvars.get("TRACE"):
        _TR.append((label, time.perf_counter()))


def _tr_report():
    if not _TR:
        return ""
    t0, out = _TR[0][1], []
    for (a, ta), (b, tb) in zip(_TR, _TR[1:]):
        out.append(f"{b} {int((tb - ta) * 1000)}ms")
    _TR.clear()
    return " | trace: " + ", ".join(out)


def _act(hwnd, hidden, fn, note=""):
    """Run the action; reply with the result, only what changed and whether it did its job. On your desktop, any NEW
    windows that appear are sent behind the others (never if you are using them); on the hidden desktop this is not needed."""
    _tr("start")
    before = set(core.top_windows())
    result = fn(); _tr("action")
    time.sleep(0.05 if _FAST["skip"] else SETTLE_S); _tr("settle")
    if not hidden:
        core.tuck_new(win32process.GetWindowThreadProcessId(hwnd)[1], before)
    if _FAST["skip"]:                       # middle step of steps(): re-reading the whole tree costs ~1 s; the last step reports the sum
        _tr("changes")
        return f"{result} | changes: counted at the end of the batch" + _tr_report()
    if "through COM" in result and ("now shows" in result or "saved" in result):
        delta = "read back by the app itself"             # the grid/file is not in the UI tree: comparing it would say nothing
    else:
        delta = core.changes(str(hwnd))
    _tr("changes")
    extra = []
    if note: extra.append(note)
    if hwnd in _LAST_NOTICE:
        extra.append(f"the user took {_LAST_NOTICE.pop(hwnd)} s to put the window back as it was after the notice, and I carried on by myself")
    return f"{result} | changes: {delta} | {verdict(result, delta)}" + "".join(f" | {e}" for e in extra) + _tr_report()


@tool
def windows(filter: str = "") -> str:
    """Open windows ('hwnd | process | title'), most recent first; filter = text in the title or process (cheaper). [IN USE] = where the user works now; [LAST USED] = what they had in front before this chat. [mine] = opened by you. [PROTECTED] / [ASKS] = PC-Control asks the user itself before you act there. (hidden), (minimized)."""
    _paused()
    flt = filter.strip().lower()
    m = policy.mode()
    fg = win32gui.GetForegroundWindow()
    fg_root = ctypes.windll.user32.GetAncestor(fg, 2) if fg else 0
    out, first = [], True
    tops = core.top_windows()
    # a Store app shows twice (its frame, owned by the shared ApplicationFrameHost, and its own content window): list it ONCE, as
    # its content window (the app's real process), with the frame's state (minimized, opened by Claude, in use)
    cls = {h: win32gui.GetClassName(h) for h in tops}
    content = {win32gui.GetWindowText(h): h for h in tops if cls[h] == "Windows.UI.Core.CoreWindow"}
    frame_of = {content[win32gui.GetWindowText(h)]: h for h in tops if cls[h] == "ApplicationFrameWindow" and win32gui.GetWindowText(h) in content}
    framed = set(frame_of.values())
    for h in tops:                                     # z-order is usage order: the first one is the most recent
        if h in framed:
            continue
        fr = frame_of.get(h, h)
        p, t = _proc(h), win32gui.GetWindowText(h)
        if flt and flt not in t.lower() and flt not in p:
            first = first and not (fg in (h, fr) or fg_root in (h, fr))           # the [LAST USED] mark must not move because of the filter
            continue
        t = t.lstrip("✳◑◐◒◓●• ")                # status icons that terminals put in their titles
        cat = policy.needs_ask(m, p, t)
        using = fg in (h, fr) or fg_root in (h, fr)
        tag = " [IN USE]" if using else (" [LAST USED]" if first else "")
        first = first and using                          # the last used is the first one that is NOT the one in use
        mine = h in OPENED or fr in OPENED or win32process.GetWindowThreadProcessId(h)[1] in OPENED_PIDS
        out.append(f"{h} | {p} | {t[:44]}" + (" (minimized)" if win32gui.IsIconic(fr) else "") + (" [mine]" if mine else "") + tag
                   + (" [ASKS]" if cat == policy.ASK_ALL else f" [PROTECTED: {cat}]" if cat else ""))
    if DESK.h:
        for h, p, t in DESK.run(lambda: [(h, _proc(h), t) for h, t, _ in DESK._enum()]):      # everything there was opened by Claude
            if flt and flt not in t.lower() and flt not in p:
                continue
            cat = policy.needs_ask(m, p, t)
            out.append(f"{h} | {p} | {t[:44]} (hidden) [mine]" + (" [ASKS]" if cat == policy.ASK_ALL else f" [PROTECTED: {cat}]" if cat else ""))
    return "\n".join(out) or f"no window matches '{filter}'"


@tool
def look(window: str, mode: str = "auto", find: str = "", wait: float = 0, confirm: bool = False):
    """See a window without focusing it: UI-tree text, OCR or an image with numbered marks, whichever is cheapest (keep mode=auto). Elements are [id:name]; ids stay valid (find never renumbers). 'blind n/m' = zones with content but no text (mode=image shows them). window = title substring or hwnd. find="Save|Cancel": only the matching elements, also out of view (cheapest way to locate something); "!Loading" = until that text is gone; wait=N seconds polls."""
    def run(hidden):
        hwnd = _gate(window, False, confirm)
        if find:
            return actions.find(str(hwnd), find, wait)
        r = core.look(str(hwnd), mode)
        head = f"{r['mode']} {r.get('tokens', 0)}tok | {r['why']}" + (f" | blind {r['blind']}" if "blind" in r else "")
        if office.is_excel(hwnd):                          # the grid is not in the UI tree: say how to reach the cells
            head += "\n" + office.summary(hwnd)
        if r["mode"] == "image":
            return [head + f" | image {r['image_size']}: numbered marks = ids; x,y = image pixels", Image(path=r["image"])]
        return head + "\n" + (r.get("text") or "")
    return _on(window, run)


@tool
def click(window: str, target: str, right: bool = False, double: bool = False, confirm: bool = False) -> str:
    """Click an element (its id from look(), or its exact name), or 'x,y' in pixels of the last image (right/double click too). Re-aims by itself if the window moved; says whether it had a visible effect. Word formatting buttons apply only to the text Claude writes. Delicate actions (pay, delete, send, install) ask the user first."""
    def run(hidden):
        _TR.clear(); _tr("t0")
        hwnd = _gate(window, True, confirm)
        cat0 = _category(hwnd)                              # what the user was (or was not) asked about: re-checked right before acting
        t = _target(target, True, hwnd)
        it = core._state.get(hwnd, {}).get("items", {}).get(t) if isinstance(t, int) else None
        if isinstance(t, int):
            if it is not None:
                bad = _guard_element(hwnd, it, confirm)
                if bad:
                    return f"did not press: {bad} | NOT EXECUTED"
                if it.ctrl is not None and core.in_title_bar(it.ctrl) and core.norm_name(_live_name(it)) in CLOSE_NAMES:
                    _closing_guard(hwnd, confirm)           # the title-bar X of a window that is not Claude's
            else:
                policy.check_click(policy.mode(), "", confirm)
        else:
            _guard_point(hwnd, t, confirm)                   # coordinates carry no identity: judge what is under the point
        old = tuple(it.rect) if it else None
        _tr("guards")
        warn = _ensure_layout(hwnd, hidden, it if isinstance(t, int) else None)
        if warn:
            raise ToolError(warn)
        rel = core.live_rel(hwnd, it) if (it and not hidden) else None        # a SINGLE resolution: pointer, warning and click all use the same one
        _tr("layout+rel")
        if it is not None and not hidden and rel is None and it.ctrl is not None:
            return "did not press: the element is no longer where it was (or there are several like it now): look again | NOT EXECUTED"
        if _cursor_wanted(hwnd, hidden) and not (isinstance(t, int) and it is None):   # an id that was never seen: nothing to travel to, core answers it
            # the orange cursor travels to the element and then the click happens
            x, y, _, _ = _screen_point(hwnd, it, None if isinstance(t, int) else t)
            cursor.CURSOR.pointer(x, y, click=True, ms=150, scale=cursor.scale_for(hwnd), owner=hwnd); time.sleep(0.17)
            x2, y2, _, _ = _screen_point(hwnd, it, None if isinstance(t, int) else t)     # the user may have moved/resized while it was travelling
            if abs(x2 - x) + abs(y2 - y) > 6:
                cursor.CURSOR.pointer(x2, y2, click=True, ms=100, scale=cursor.scale_for(hwnd), owner=hwnd); time.sleep(0.12)
                rel = core.live_rel(hwnd, it) if (it and not hidden) else rel
        _tr("cursor")
        # the 0.4 s of the cursor's trip are the window of opportunity: verify AGAIN right before dispatching
        _recheck_window(hwnd, cat0)
        if it is not None:
            bad = _guard_element(hwnd, it, confirm)
            if bad:
                return f"did not press: {bad} | NOT EXECUTED"
            if not hidden and it.ctrl is not None and rel is None:
                return "did not press: the element is no longer where it was: look again | NOT EXECUTED"
        note = core.moved_note(old, rel) if rel else ""
        res = _act(hwnd, hidden, lambda: core.click(str(hwnd), t, right, double, rel=rel), note)
        # Apps that draw their own controls (modern Store/XAML apps) ignore mouse messages: if the plain click had NO visible effect, press
        # the element through its UI Automation pattern instead (what a screen reader does: no mouse, no focus) and say so.
        # Not if the click already changed what the element is (a toggle): pressing it again would do the opposite.
        if "NO VISIBLE EFFECT" in res and "mouse message" in res and it is not None and not right and not core.identity_problem(it):
            r2 = core.invoke(str(hwnd), t, double=double)
            if r2:
                time.sleep(0.25)
                delta = core.changes(str(hwnd))
                res = f"{r2} | changes: {delta} | {verdict(r2, delta)} | the plain mouse click had no effect, so it was sent through UI Automation"
        return res
    return _on(window, run)


@tool
def type(window: str, target: str, text: str, replace: bool = False, confirm: bool = False) -> str:
    """Type into an element (id from look(), or its name; appends, replace=true replaces). No blind typing. On a drop-down or list it selects the entry with that text. Excel: target can be a cell (B3, Sheet2!A1; '=...' = formula). Word: writes at Claude's OWN insertion point, not the user's caret, and says where. Answers with what changed."""
    def run(hidden):
        hwnd = _gate(window, True, confirm)
        if office.is_excel(hwnd) and office.is_cell(target):  # Excel: a cell by address (B3, Sheet2!A1), never the user's selection
            policy.check_type(policy.mode(), str(target), False)
            return _act(hwnd, hidden, lambda: office.write(hwnd, str(target), text) or "did not type: this Excel window exposes no workbook")
        try:
            it = core._state.get(hwnd, {}).get("items", {}).get(_target(target, False, hwnd))
        except ToolError as e:
            if "elements match" in str(e):
                raise
            it = None
        if it is None:                                     # no blind typing: the id must come from a look() of this window
            raise ToolError("unknown id: call look() on this window first and use an id from its answer (typing without a valid id is not allowed)")
        cat0 = _category(hwnd)
        if core.stale_state(hwnd):
            raise ToolError("unknown id: this window is not the app that was looked at (its handle was reused): call look() again")
        bad = core.identity_problem(it)
        if bad:
            return f"did not type: {bad}: look again | NOT EXECUTED"
        policy.check_type(policy.mode(), _live_name(it), core.is_secret_field(it), it.aid)      # judged on what the field is NOW
        if it and win32gui.GetClassName(hwnd) != "OpusApp" and (it.ctrl is None or not core._native(it.ctrl)[0]):
            warn = _ensure_layout(hwnd, hidden, it)
            if warn:
                raise ToolError(warn)
        progress = None
        if it and _cursor_wanted(hwnd, hidden):            # orange typing bar, the size of one line
            s = cursor.scale_for(hwnd)
            wc = core.word_caret(hwnd) if win32gui.GetClassName(hwnd) == "OpusApp" else None        # Word: Claude's point, real position and height
            if wc:
                cursor.CURSOR.caret(wc[0], wc[1], h=cursor.caret_height(wc[2], s), scale=s, owner=hwnd)
                progress = lambda x, y, hh: cursor.CURSOR.caret(x, y, h=cursor.caret_height(hh, s), scale=s, owner=hwnd)
            else:
                _, y, x, eh = _screen_point(hwnd, it)
                cursor.CURSOR.caret(x + 8, y, h=cursor.caret_height(eh, s), scale=s, owner=hwnd)
            time.sleep(0.15)
        _recheck_window(hwnd, cat0)                        # after the typing bar's trip: same window, same field, still not a secret
        bad = core.identity_problem(it)
        if bad:
            return f"did not type: {bad}: look again | NOT EXECUTED"
        policy.check_type(policy.mode(), _live_name(it), core.is_secret_field(it), it.aid)
        return _act(hwnd, hidden, lambda: core.type_text(str(hwnd), text, it.id, replace, progress))
    return _on(window, run)


def _drag_point(hwnd, token):
    """One point of a drag: 'x,y' in pixels of the last image, or the id / name of an element (its centre, in the same pixels)."""
    t = token.strip()
    if "," in t and re.fullmatch(r"[-\d.\s]+,[-\d.\s]+", t):
        return tuple(int(float(v)) for v in t.replace(" ", "").split(","))
    tid = _target(t, False, hwnd)
    st = core._state.get(hwnd, {})
    it = st.get("items", {}).get(tid)
    if it is None:
        raise ToolError(f"unknown element '{t}': call look() first or use x,y")
    rel = core.live_rel(hwnd, it) or it.rect
    k = st.get("scale", 1.0) or 1.0
    return int((rel[0] + rel[2]) / 2 / k), int((rel[1] + rel[3]) / 2 / k)


@tool
def drag(window: str, points: str, shape: str = "path", right: bool = False, confirm: bool = False) -> str:
    """Drag (draw, select, move) through points "x1,y1;x2,y2" in pixels of the last image; a point can be an element id or name (its centre): "12;15" selects from one to the other. shape: path, line, rect, ellipse."""
    def run(hidden):
        hwnd = _gate(window, True, confirm)
        cat0 = _category(hwnd)
        try:
            pts = [_drag_point(hwnd, p) for p in points.split(";") if p.strip()]
        except ValueError:
            raise ToolError(f"bad points '{points[:40]}': write x1,y1;x2,y2;... in pixels of the last image")
        if any(len(p) != 2 for p in pts):
            raise ToolError("bad points: each point is x,y and points are separated by ';'")
        if shape.lower() not in ("path", "line", "rect", "rectangle", "ellipse", "circle"):
            raise ToolError(f"unknown shape '{shape}': path, line, rect or ellipse")
        shape_pts = core.shape_points(shape, pts)
        if _cursor_wanted(hwnd, hidden) and len(shape_pts) >= 2:      # the orange pointer goes to the start and follows the stroke
            s = cursor.scale_for(hwnd)
            x0, y0, _, _ = _screen_point(hwnd, None, shape_pts[0])
            cursor.CURSOR.pointer(x0, y0, click=True, ms=300, scale=s, owner=hwnd); time.sleep(0.34)
            x1, y1, _, _ = _screen_point(hwnd, None, shape_pts[-1])
            cursor.CURSOR.pointer(x1, y1, click=False, ms=int(min(2500, 120 + 6 * len(core._densify(shape_pts)))), scale=s, owner=hwnd)
        _recheck_window(hwnd, cat0)
        return _act(hwnd, hidden, lambda: core.drag(str(hwnd), pts, shape, right))
    return _on(window, run)


@tool
def key(window: str, name: str, confirm: bool = False) -> str:
    """A key (enter, tab, esc, arrows, backspace, delete, home, end, pgup, pgdn, space, f1-f12) or a shortcut (ctrl+s, alt+f4). Shortcuts run the app's own command, never real keys; if missing, the answer lists the app's shortcuts (language-dependent). Word: only enter, space, backspace, at Claude's insertion point."""
    def run(hidden):
        hwnd = _gate(window, True, confirm)
        cat0 = _category(hwnd)
        _key_guard(hwnd, name, confirm)
        _recheck_window(hwnd, cat0)
        return _act(hwnd, hidden, lambda: core.key(str(hwnd), name))
    return _on(window, run)


@tool
def scroll(window: str, direction: str = "down", target: str = "", amount: int = 1, confirm: bool = False) -> str:
    """Scroll the window or an element id by `amount` pages: up|down|left|right|top|bottom. Then look again (old ids still work)."""
    def run(hidden):
        hwnd = _gate(window, False, confirm)
        return _act(hwnd, hidden, lambda: actions.scroll(str(hwnd), _target(target, False, hwnd) if str(target).strip() else None, direction, amount))
    return _on(window, run)


@tool
def read(window: str, target: str = "", start: int = 0, length: int = 6000, find: str = "", confirm: bool = False) -> str:
    """The WHOLE text of the window's document, page or editor (also out of view), or of an element, in pieces (start/length, 6000 characters by default). find=\"a|b\": only the matching lines, numbered. Excel: the cells; PowerPoint: slides and notes. Cheaper than scrolling and looking. Never reads password fields."""
    def run(hidden):
        hwnd = _gate(window, False, confirm)
        tgt = _target(target, False, hwnd) if str(target).strip() else None
        if not find.strip():
            return actions.read_text(str(hwnd), tgt, start, length)
        out = actions.read_text(str(hwnd), tgt, 0, 10 ** 7)          # the whole text, then only the lines that matter
        head, _, body = out.partition("\n")
        terms = [t.strip().lower() for t in find.split("|") if t.strip()]
        lines = body.split("\n")
        hits = [f"{n}: {ln[:300]}" for n, ln in enumerate(lines, 1) if any(t in ln.lower() for t in terms)]
        src = head.split(": characters")[0]
        return (f"{src}: {len(hits)} of {len(lines)} lines match" + (" (first 40 shown)" if len(hits) > 40 else "") + "\n" + "\n".join(hits[:40]))
    return _on(window, run)


def _do_show(hidden, window, target, label, seconds, confirm):
    """Draw the orange circle for the user. Returns (message for the model, token of the circle)."""
    hwnd = _gate(window, False, confirm)
    if hidden:
        raise ToolError("that window is on the hidden desktop: the user cannot see it")
    if win32gui.IsIconic(hwnd):
        raise ToolError("the window is minimized: ask the user to open it from the taskbar, then call show again")
    t = _target(target, True, hwnd)
    l, tp = win32gui.GetWindowRect(hwnd)[:2]
    if isinstance(t, int):
        it = core._state.get(hwnd, {}).get("items", {}).get(t)
        if it is None:
            raise ToolError("unknown id: call look() on this window first")
        if it.ctrl is not None:
            actions.scroll_into_view(it.ctrl)
        rel = core.live_rel(hwnd, it) or it.rect
        name = it.name[:40]
    else:
        k = core._state.get(hwnd, {}).get("scale", 1.0)
        x, y = int(t[0] * k), int(t[1] * k); rel = (x - 14, y - 14, x + 14, y + 14); name = f"{t[0]},{t[1]}"
    rect = (l + rel[0], tp + rel[1], l + rel[2], tp + rel[3])
    secs = max(2, min(int(seconds or 10), 120))
    shown = (f"{label[:30]} «{name[:24]}»" if label.strip() and core.norm_name(label) != core.norm_name(name) else label[:40])
    token = cursor.CURSOR.mark(rect, shown, secs, cursor.scale_for(hwnd), owner=hwnd)          # the label never hides what the element really is
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    front = ctypes.windll.user32.GetAncestor(win32gui.WindowFromPoint((cx, cy)), 2) == hwnd
    disabled = False
    if isinstance(t, int) and it.ctrl is not None:
        try:
            disabled = not it.ctrl.IsEnabled
        except Exception:
            pass
    return (f"shown to the user: an orange circle around «{name}» for {secs} s (nothing was clicked)"
            + (". It is DISABLED (greyed out) right now: tell the user what enables it first" if disabled else "")
            + ("" if front else ". The window is behind other windows there: tell the user to bring it to the front to see it")), token


@tool
def show(window: str, target: str, label: str = "", seconds: int = 10, confirm: bool = False) -> str:
    """Show the USER where something is (for "where do I click to...?"): an orange circle around the element `target` (its id, name or x,y), optional label. Nothing is clicked; it goes when they click it or it changes."""
    return _on(window, lambda hidden: _do_show(hidden, window, target, label, seconds, confirm)[0])


_STEP_ARGS = {"click": ("target", "right", "double"), "type": ("target", "text", "replace"), "key": ("name",),
              "scroll": ("direction", "target", "amount"), "read": ("target", "start", "length", "find"),
              "look": ("mode", "find", "wait"), "drag": ("points", "shape", "right")}
_STEP_BAD = ("NOT EXECUTED", "NO VISIBLE EFFECT")


@tool
def steps(window: str, todo: list[dict]) -> str:
    """Several actions on ONE window in one call, in order (faster). todo=[{"do":"click","target":"Save"},{"do":"type","target":"Name","text":"x"},{"do":"key","name":"enter"},{"do":"wait","seconds":1}]; do = click|type|key|scroll|read|look|drag|wait, same arguments. Use names, not ids, for what appears later. Stops at the first failure, no-effect or permission need (repeat that step alone)."""
    if not 0 < len(todo) <= 12:
        raise ToolError("give between 1 and 12 actions")
    out = []
    for n, a in enumerate(todo, 1):
        verb = str(a.get("do", "")).strip().lower()
        if verb == "wait":
            time.sleep(max(0.0, min(float(a.get("seconds", 1)), 10.0)))
            out.append(f"{n}. wait ok")
            continue
        fn = RAW.get(verb)
        if verb not in _STEP_ARGS or fn is None:
            out.append(f"{n}. unknown action '{verb}' | STOPPED")
            break
        kw = {k: a[k] for k in _STEP_ARGS[verb] if k in a}
        _FAST["skip"] = n < len(todo) and not a.get("check")      # middle steps skip the (slow) diff of the tree; "check":true keeps it
        try:
            r = fn(window, **kw)
        except policy.NeedsPermission:
            out.append(f"{n}. {verb} needs the user's permission | STOPPED: repeat this step alone (a normal {verb} call) and the user will be asked")
            break
        except ToolError as e:
            out.append(f"{n}. {verb} failed: {e} | STOPPED")
            break
        text = r if isinstance(r, str) else " ".join(x for x in r if isinstance(x, str))         # a look() may return text + image
        last = n == len(todo)
        bad = any(b in text for b in _STEP_BAD)
        out.append(f"{n}. {verb}: " + (text if (last or bad or verb in ("read", "look")) else text.split(" | changes:")[0][:160]) + (" | STOPPED" if bad else ""))
        if bad:
            break
    return "\n".join(out)


@tool
def wait_for(window: str, target: str, label: str = "", timeout: int = 60, confirm: bool = False) -> str:
    """Like show() but WAITS until the user clicks the circled `target` (or it changes): returns clicked | changed | timeout (max 120 s), then give the next step. For guiding several steps in turn."""
    def run(hidden):
        msg, token = _do_show(hidden, window, target, label, timeout, confirm)
        if "DISABLED" in msg:
            return msg + " | not waiting: it cannot be clicked yet"
        end = time.time() + max(2, min(int(timeout or 60), 120)) + 1.5
        while time.time() < end:
            r = cursor.CURSOR.mark_result
            if r and r[0] == token:
                why = r[1]
                return (f"{why}: the user " + {"clicked": "clicked inside the circle", "changed": "changed what is inside it (they did the step)",
                                               "timeout": f"did not do it in {timeout} s"}[why] + ". Look at the window to see the result, then give the next step.")
            time.sleep(0.1)
        return "timeout: the circle was not resolved in time"
    return _on(window, run)


@tool
def open_app(command: str, hidden: bool = False, confirm: bool = False) -> str:
    """Launch an app minimized and unfocused, with its taskbar button: the user watches what you do by opening it. hidden=true (invisible, hidden desktop) ONLY if the user asks for it."""
    _paused()
    try:
        exe = os.path.splitext(os.path.basename(shlex.split(command, posix=False)[0].strip('"')))[0].lower()
    except (ValueError, IndexError):
        raise ToolError("bad command: give the program (quoted if it has spaces) and its arguments, e.g. notepad.exe \"my notes.txt\"")
    policy.check_launch(policy.mode(), exe, _allowed(), confirm, " ".join(shlex.split(command, posix=False)[1:]))
    if hidden:
        r = DESK.open(command); where = "hidden desktop (no taskbar button)"
    else:
        if exe in core.CHROMIUM:                     # browsers hide the page while minimized: behind the user's windows instead
            tok = shlex.split(command, posix=False)[0]
            extra = [f for f in ("--force-renderer-accessibility", "--disable-backgrounding-occluded-windows") if f not in command]
            r = core.open_behind(command.replace(tok, tok + " " + " ".join(extra), 1) if extra else command, exe)
            where = "behind the user's windows, not minimized (a minimized browser hides the page), with its button in the taskbar"
        else:
            r = core.open_minimized(command); where = "minimized, with its button in the taskbar"
    ws = "; ".join(f"{w['hwnd']} {w['title']}" for w in r["windows"]) or r["note"]
    OPENED.update(w["hwnd"] for w in r["windows"]); OPENED_PIDS.update(r.get("pids", []))
    return f"pid {r['pid']} | {ws} | {where}"


def _clean_old_log():
    """The last-error log is not kept long term: if it is more than a day old, it is deleted at startup."""
    f = os.path.join(HOME, "last_error.log")
    try:
        if time.time() - os.path.getmtime(f) > 24 * 3600:
            os.remove(f)
    except OSError:
        pass


def _shutdown():
    """The client (Claude Code) is gone: cursors are removed from the screen and the process exits, leaving nothing of its own alive."""
    try:
        cursor.CURSOR.hide()
        time.sleep(0.15)                                  # let the cursor thread hide and destroy its windows
    except Exception:
        pass
    os._exit(0)


def _watch_parent():
    """If the process that launched us (Claude Code) dies or closes abruptly, without closing the connection cleanly, we exit too."""
    import threading
    ppid = os.getppid()
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x00100000, False, ppid)          # SYNCHRONIZE
    if not h:
        return
    def wait():
        k.WaitForSingleObject(ctypes.c_void_p(h), 0xFFFFFFFF)
        _shutdown()
    threading.Thread(target=wait, name="pc-control-parent", daemon=True).start()


def _slim_schemas():
    """Tool definitions are sent on EVERY request: drop the "title" that pydantic adds to each parameter (it just repeats the name)."""
    def strip(node):
        if isinstance(node, dict):
            node.pop("title", None)
            for k, v in node.items():
                if k == "properties" and isinstance(v, dict):
                    for sub in v.values():
                        strip(sub)
                else:
                    strip(v)
        elif isinstance(node, list):
            for v in node:
                strip(v)
    for t in mcp._tool_manager._tools.values():
        strip(t.parameters)


_slim_schemas()


def main():
    _clean_old_log()
    _watch_parent()
    try:
        mcp.run()
    finally:                                               # the connection closed (EOF) or something failed: leave no cursors or process behind
        _shutdown()


if __name__ == "__main__":
    main()
