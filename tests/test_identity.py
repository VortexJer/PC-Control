"""What is re-verified right before acting. An element id comes from a look(), but the window is alive: between look() and click() the id
can start pointing at something else. Part 1 uses mocks (fake controls, no real windows); part 2 drives a real app through the real MCP
protocol (a button that changes its label under the same control, like a Buy / Sell toggle)."""
import asyncio, json, os, shutil, sys, tempfile, time, types
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, ROOT)
os.environ["PC_CONTROL_LANG"] = "en"
from pc_control import actions, core, policy, server

c = {}


class R:
    def __init__(s, l, t, r, b): s.left, s.top, s.right, s.bottom = l, t, r, b
    def width(s): return s.right - s.left
    def height(s): return s.bottom - s.top


class Ctl:
    """A fake UI Automation control."""
    def __init__(s, kind, name, aid="", rect=(0, 0, 10, 10), password=False, help_text="", kids=(), parent=None):
        s.ControlTypeName, s.Name, s.AutomationId, s.HelpText, s.IsPassword = kind + "Control", name, aid, help_text, password
        s.BoundingRectangle, s.IsOffscreen, s.NativeWindowHandle, s._k, s._p = R(*rect), False, 0, list(kids), parent
    def GetChildren(s): return s._k
    def GetParentControl(s): return s._p
    def GetValuePattern(s): raise RuntimeError("no value pattern")


def item(kind, name, ctrl, aid="", parent=None, rect=(0, 0, 10, 10)):
    it = core.Item(kind, name, rect, ctrl, "uia", True); it.aid, it.parent = aid, parent
    return it


# ---------- 1) identity of an element ----------
btn = Ctl("Button", "Buy now", "btnBuy")
it = item("button", "Buy now", btn, "btnBuy")
c["same element: no problem"] = core.identity_problem(it) is None
btn.Name = "Sell now"
c["a toggle that changes its label under the same control is caught"] = "was «Buy now», now «Sell now»" in (core.identity_problem(it) or "")
btn.Name = "  BUY\u200e   NOW "
c["case, spaces and invisible direction marks are not a change"] = core.identity_problem(it) is None
btn.Name = "Buy now"; btn.ControlTypeName = "CheckBoxControl"
c["the same control turned into another kind is caught"] = "now a checkbox" in (core.identity_problem(it) or "")
row = Ctl("ListItem", "message-17.eml"); r = item("listitem", "message-17.eml", row)
row.Name = "message-18.eml"
c["a recycled row of a virtual list is caught"] = core.identity_problem(r) is not None
nm = Ctl("Button", "", "btnSave"); un = item("button", "(unnamed button)", nm, "btnSave")
c["a nameless button kept by its id: no false alarm"] = core.identity_problem(un) is None
nm.AutomationId = "btnDelete"; nm.Name = "Delete"
c["... but a different id and name is caught"] = core.identity_problem(un) is not None
ed = Ctl("Edit", "hello", "txtSearch"); e = item("edit", "hello", ed, "txtSearch")
ed.Name = "hello world"                                            # a text box whose name is its value changes while typing
c["a text field changing its value is NOT a change"] = core.identity_problem(e) is None
ed.AutomationId = "txtCard"
c["a text field that now has another id is"] = "field changed" in (core.identity_problem(e) or "")
ocr = core.Item("text", "Pay", (0, 0, 5, 5), None, "ocr", True)
c["an OCR element has no control to ask: nothing to verify"] = core.identity_problem(ocr) is None

# ---------- 2) the namesake search of live_rel ----------
def fake_tree(items):
    core.walk_uia = lambda hwnd, rect, **kw: items
core_walk, core_rect, core_native = core.walk_uia, core.win32gui.GetWindowRect, core._native
core.win32gui.GetWindowRect = lambda h: (0, 0, 1000, 800)
core._native = lambda ctrl: (0, "")
stale = Ctl("Button", "OK", "btnOk"); stale.IsOffscreen = True       # its control is no good any more (hidden): look for it again
base = item("button", "OK", stale, "btnOk", ("pane", "Dialog A"), rect=(100, 100, 160, 130))
core._state[1] = {"items": {1: base}, "rect": (0, 0, 1000, 800)}
def cand(name, aid, parent, rect):
    f = core.Item("button", name, rect, Ctl("Button", name, aid), "uia", True); f.aid, f.parent = aid, parent; return f
fake_tree([cand("OK", "btnOk", ("pane", "Dialog A"), (110, 110, 170, 140)), cand("OK", "btnOk2", ("pane", "Dialog B"), (500, 500, 560, 530))])
c["two namesakes: it does not guess (None: look again)"] = core.live_rel(1, base) is None
fake_tree([cand("OK", "btnOk", ("pane", "Dialog B"), (110, 110, 170, 140))])
c["one namesake in ANOTHER container is not the element"] = core.live_rel(1, base) is None
fake_tree([cand("OK", "btnOther", ("pane", "Dialog A"), (110, 110, 170, 140))])
c["one namesake with another id is not the element"] = core.live_rel(1, base) is None
b2 = item("button", "OK", stale, "", ("pane", "Dialog A"), rect=(100, 100, 160, 130)); core._state[1]["items"][1] = b2
fake_tree([cand("OK", "", ("pane", "Dialog A"), (900, 700, 960, 730))])
c["a namesake far away and with no id to vouch for it is refused"] = core.live_rel(1, b2) is None
good = cand("OK", "btnOk", ("pane", "Dialog A"), (140, 120, 200, 150)); fake_tree([good])
c["the unique namesake, same container and id, is accepted (and the item follows it)"] = core.live_rel(1, base) == (140, 120, 200, 150) and base.ctrl is good.ctrl
core.walk_uia, core.win32gui.GetWindowRect, core._native = core_walk, core_rect, core_native
core._state.clear()

# ---------- 3) the policy judges what is there NOW ----------
mode0 = os.environ.get("PC_CONTROL_MODE"); os.environ["PC_CONTROL_MODE"] = "auto"
inno = Ctl("Button", "Accept", "btnA"); iit = item("button", "Accept", inno, "btnA")
c["innocuous element, innocuous name: allowed"] = server._guard_element(1, iit, False) is None
inno.Name = "Delete all"                                              # the id now points at a sensitive element
msg = server._guard_element(1, iit, False)
c["the label became «Delete all»: not executed (identity first)"] = msg is not None and "changed" in msg
sens = Ctl("Button", "Delete all", "btnA"); sit = item("button", "Delete all", sens, "btnA")
try:
    server._guard_element(1, sit, False); c["a sensitive live name asks the user"] = False
except policy.NeedsPermission:
    c["a sensitive live name asks the user"] = True
c["... and a yes (confirm) lets it through"] = server._guard_element(1, sit, True) is None
fld = Ctl("Edit", "Search", "txtSearch"); fit = item("edit", "Search", fld, "txtSearch")
fld.Name = "Password (again)"                                         # edits skip the name comparison: the secret check must not
try:
    policy.check_type(policy.mode(), server._live_name(fit) + " " + fit.aid, core.is_secret_field(fit)); c["a field that now says Password is not typed into"] = False
except PermissionError:
    c["a field that now says Password is not typed into"] = True
hid = Ctl("Edit", "Value", "pwdBox"); hit = item("edit", "Value", hid, "pwdBox")
c["a secret-sounding AutomationId marks the field secret"] = core.is_secret_field(hit)
pw = Ctl("Edit", "Value", "x", password=True)
c["IsPassword read live marks it secret"] = core.is_secret_field(item("edit", "Value", pw, "x"))
card = Ctl("Edit", "Card number", "x")
c["a card number field is secret"] = core.is_secret_field(item("edit", "Card number", card, "x"))
c["a secret field is never read by read()"] = actions._ctrl_text(Ctl("Edit", "API key", "k")) is None

# ---------- 4) the window changes between the gate and the action ----------
titles = {1: "Notes"}; procs = {1: "notepad"}
sv_cat, sv_proc, sv_gwt, sv_pop = server._category, server._proc, server.win32gui.GetWindowText, core.popup_windows
server._proc = lambda h: procs.get(h, "notepad"); server.win32gui.GetWindowText = lambda h: titles.get(h, "")
core.popup_windows = lambda h: []
cat0 = server._category(1)
c["a normal window has no protection category"] = cat0 is None
server._recheck_window(1, cat0)
titles[1] = "Sign in to your bank"                                   # the page navigated to a login while the cursor travelled
try:
    server._recheck_window(1, cat0); c["a title that turned into a login aborts the action"] = False
except server.ToolError as e:
    c["a title that turned into a login aborts the action"] = "changed while acting" in str(e)
titles[1] = "Notes"; titles[2] = "Verification code"; procs[2] = "notepad"
core.popup_windows = lambda h: [2]
try:
    server._recheck_window(1, cat0); c["a sign-in style dialog that popped up aborts the action"] = False
except server.ToolError as e:
    c["a sign-in style dialog that popped up aborts the action"] = "dialog appeared" in str(e)
core.popup_windows = lambda h: []
procs[1] = "keepass"; titles[1] = "KeePass"
c["a window protected from the start (the user said yes) is not aborted"] = server._recheck_window(1, server._category(1)) is None
server._category, server._proc, server.win32gui.GetWindowText, core.popup_windows = sv_cat, sv_proc, sv_gwt, sv_pop

# ---------- 5) coordinates, reused handles, keys without a name ----------
sw, sp = core.win32gui, core.window_pid
core.window_pid = lambda h: 7
sens2 = Ctl("Button", "Pay now", "btnPay"); pit = item("button", "Pay now", sens2, "btnPay", rect=(50, 50, 150, 90))
core._state[5] = {"items": {1: pit}, "rect": (0, 0, 300, 300), "scale": 1.0, "pid": 7}
try:
    server._guard_point(5, (100, 70), False); c["clicking by x,y over «Pay now» asks the user"] = False
except policy.NeedsPermission:
    c["clicking by x,y over «Pay now» asks the user"] = True
c["... but not on empty space"] = server._guard_point(5, (200, 200), False) is None
sens2.Name = "Cancel"
try:
    server._guard_point(5, (100, 70), False); c["x,y over an element that changed is refused"] = False
except server.ToolError:
    c["x,y over an element that changed is refused"] = True
core.window_pid = lambda h: 8                                          # the handle now belongs to another program
c["a reused window handle drops the old ids"] = core.stale_state(5) and 5 not in core._state
core.window_pid = sp
os.environ["PC_CONTROL_MODE"] = "auto"
server.OPENED.clear(); server.OPENED_PIDS.clear()
sv_pid = server.win32process.GetWindowThreadProcessId
server.win32process.GetWindowThreadProcessId = lambda h: (0, 99)
try:
    server._key_guard(1, "alt+f4", False); c["alt+f4 on a window Claude did not open asks"] = False
except policy.NeedsPermission:
    c["alt+f4 on a window Claude did not open asks"] = True
server.OPENED.add(1)
c["alt+f4 on a window Claude opened does not"] = server._key_guard(1, "alt+f4", False) is None
for combo in ("ctrl+c", "Ctrl+V", "shift+insert"):
    try:
        server._key_guard(1, combo, False); c[f"the clipboard key {combo} asks"] = False
    except policy.NeedsPermission:
        c[f"the clipboard key {combo} asks"] = True
sv_risk = server._dialog_default_risk
server._dialog_default_risk = lambda h: "Delete"
try:
    server._key_guard(1, "enter", False); c["enter in a dialog that offers «Delete» asks"] = False
except policy.NeedsPermission:
    c["enter in a dialog that offers «Delete» asks"] = True
server._dialog_default_risk = lambda h: None
c["enter in a harmless dialog does not"] = server._key_guard(1, "enter", False) is None
c["a letter key is not a shortcut to ask about"] = server._key_guard(1, "tab", False) is None
os.environ["PC_CONTROL_MODE"] = "bypass"
c["in bypass nothing asks"] = server._key_guard(1, "ctrl+v", False) is None
server._dialog_default_risk, server.win32process.GetWindowThreadProcessId = sv_risk, sv_pid
os.environ["PC_CONTROL_MODE"] = "auto"
for exe, args, asks in (("notepad", "", False), ("explorer", "C:/x/setup.bat", True), ("explorer", "C:/x/tool.exe", True), ("explorer", "shell:Downloads", False), ("python", "", True), ("notepad", "C:/x/run.bat", False),
                        ("curl", "", True), ("cmd", "", True), ("explorer", "C:/docs/report.pdf", False)):
    try:
        policy.check_launch("auto", exe, set(), False, args); got = False
    except policy.NeedsPermission:
        got = True
    c[f"open_app {exe} {args}: {'asks' if asks else 'does not ask'}"] = got == asks
if mode0 is None:
    os.environ.pop("PC_CONTROL_MODE", None)
else:
    os.environ["PC_CONTROL_MODE"] = mode0

# ---------- 6) a real app through the real protocol ----------
async def real():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    tmp = tempfile.mkdtemp(prefix="pc-control_ident_"); st = os.path.join(tmp, "s.json")
    host = os.path.join(tmp, "pc_control_host.exe")
    shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), host)
    S = lambda: json.load(open(st, encoding="utf-8-sig"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                                   env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": "bypass", "PYTHONUTF8": "1"})   # bypass: the host copy of powershell is a
    txt = lambda res: "\n".join(x.text for x in res.content if getattr(x, "type", "") == "text")
    try:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                call = lambda tool, **a: s.call_tool(tool, a)
                await call("open_app", command=f'"{host}" -STA -NoProfile -ExecutionPolicy Bypass -File "{os.path.join(HERE, "test_app2.ps1")}" "{st}"', hidden=True)
                for _ in range(40):
                    if "pc-control-test2" in txt(await call("windows")):
                        break
                    await asyncio.sleep(0.5)
                lk = txt(await call("look", window="pc-control-test2", find="Buy now|Make it Sell"))
                import re
                ids = {m.group(2): m.group(1) for m in re.finditer(r"\[(\d+):([^\]=]+)", lk)}
                c["real app: the toggling button and its trigger are found"] = "Buy now" in ids and "Make it Sell" in ids
                out = txt(await call("click", window="pc-control-test2", target=ids["Buy now"])); await asyncio.sleep(0.4)
                c["real app: pressing it while it still says Buy now works"] = S()["buy"] == 1
                await call("click", window="pc-control-test2", target=ids["Make it Sell"]); await asyncio.sleep(0.6)
                out = txt(await call("click", window="pc-control-test2", target=ids["Buy now"])); await asyncio.sleep(0.4)
                c["real app: after it turned into «Sell now», the old id is NOT pressed"] = (S()["sell"] == 0 and S()["buy"] == 1 and "NOT EXECUTED" in out
                                                                                            and "changed" in out)
                c["real app: the answer says what changed"] = "was «Buy now», now «Sell now»" in out
                lk = txt(await call("look", window="pc-control-test2", find="Sell now"))
                nid = re.search(r"\[(\d+):Sell now", lk).group(1)
                await call("click", window="pc-control-test2", target=nid); await asyncio.sleep(0.4)
                c["real app: after looking again, «Sell now» is pressed"] = S()["sell"] == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

try:
    asyncio.run(real())
except Exception as e:
    c[f"real app part ran ({e.__class__.__name__}: {e})"] = False

for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print(f"identity verification {'passed' if ok else 'FAILED'} ({sum(c.values())}/{len(c)} checks)")
sys.exit(0 if ok else 1)
