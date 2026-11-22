"""Every failure class mined from REAL sessions (1250 PC-Control calls in the user's transcripts) has its fix checked here.

Simulated: window lists, element tables and Windows errors are replaced, no app is touched.
  * wrong window: "Word" matched Claude's own terminal tab «◐ Word con PC control»; "Spotify" matched a terminal; "winword" found nothing
  * ambiguous names that are ONE control (a list entry and the button inside it); real ambiguity explained with context
  * a window replaced by its app (start-up window -> main one): the model got a bare "no window"
  * wrong parameter names (keys=, id=, app=) and drag points as JSON
  * programs that are installed but not on PATH (winword, excel.exe /e, chrome ...): FileNotFoundError
  * raw Windows errors (1400 invalid handle, 1447 no scroll bars, 740 elevation) and a ZeroDivisionError in look
  * keys posted with lParam 0 (apps that read the scancode ignore them)
"""
import os, sys, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pc_control import core, server, apps
from pc_control.core import Item

c = {}

# ---- 1) which window: the app's own process beats a window that only mentions it ----
term = core._score("word", "\u25d0 Word con PC control", "windowsterminal", "CASCADIA_HOSTING_WINDOW_CLASS")
doc = core._score("word", "Documento1 - Word", "winword", "OpusApp")
c["'Word' prefers Word over Claude's terminal tab titled 'Word con PC control'"] = doc - term > 14
c["'Spotify' prefers Spotify over a terminal tab mentioning it"] = (core._score("spotify", "Spotify Premium", "spotify", "Chrome_WidgetWin_1")
                                                                  - core._score("spotify", "\u25d0 Playlist de favoritos en Spotify", "windowsterminal", "X")) > 14
c["'winword' finds Word by its process name"] = core._score("winword", "Documento1 - Word", "winword", "OpusApp") >= 80
c["an app's console window loses against the app"] = (core._score("blender", "Ford_GT [C:\\x.blend]", "blender", "GHOST_WindowClass")
                                                     - core._score("blender", "C:\\Program Files\\Blender Foundation\\blender.exe", "blender", "ConsoleWindowClass")) > 14
c["an exact title wins over everything"] = core._score("calculadora", "Calculadora", "applicationframehost", "X") == 100

# find_window over a fake desktop
FAKE = {101: ("\u25d0 Word con PC control", "windowsterminal", "CASCADIA_HOSTING_WINDOW_CLASS", 11),
        102: ("Documento1 - Word", "winword", "OpusApp", 12), 103: ("Documento2 - Word", "winword", "OpusApp", 12),
        104: ("Spotify Premium", "spotify", "Chrome_WidgetWin_1", 13), 105: ("Bloc de notas", "notepad", "Notepad", 14)}
g = core.win32gui
saved = (g.EnumWindows, g.IsWindowVisible, g.GetWindowText, g.GetClassName, g.IsWindow, core.psutil_name, core.window_pid, core.top_windows)
alive = set(FAKE)
g.EnumWindows = lambda cb, x: [cb(h, x) for h in list(alive)] and None
g.IsWindowVisible = lambda h: h in alive
g.GetWindowText = lambda h: FAKE[h][0] if h in FAKE else ""
g.GetClassName = lambda h: FAKE[h][2] if h in FAKE else ""
g.IsWindow = lambda h: h in alive
core.psutil_name = lambda pid: next((v[1] for v in FAKE.values() if v[3] == pid), "")
core.window_pid = lambda h: FAKE[h][3] if h in FAKE else 0
core.top_windows = lambda: [h for h in alive]
try:
    core.find_window("Word"); c["two Word documents are a REAL ambiguity"] = False
except core.AmbiguousWindow as e:
    c["two Word documents are a REAL ambiguity, and the terminal is not listed"] = "102" in str(e) and "103" in str(e) and "101" not in str(e)
c["'spotify' resolves to Spotify, not to a terminal"] = core.find_window("spotify") == 104
c["'notepad' resolves by process name"] = core.find_window("notepad") == 105
# a window replaced by its app: the start-up window 105 closes and 106 takes its place (same process)
core._remember(105)
FAKE[106] = ("Sin titulo - Bloc de notas", "notepad", "Notepad", 14); alive.discard(105); alive.add(106)
try:
    core.find_window("105"); c["a replaced window names its replacement"] = False
except core.WindowReplaced as e:
    c["a replaced window names its replacement (one cheap retry, not a windows() call)"] = e.new == 106 and "use 106" in str(e)
c["reading follows the replacement by itself"] = core.find_window("105", follow=True) == 106
alive.discard(106)
try:
    core.find_window("106"); c["a window closed for good is plainly gone"] = False
except core.WindowReplaced:
    c["a window closed for good is plainly gone"] = False
except LookupError as e:
    c["a window closed for good is plainly gone"] = "was closed" in str(e)
(g.EnumWindows, g.IsWindowVisible, g.GetWindowText, g.GetClassName, g.IsWindow, core.psutil_name, core.window_pid, core.top_windows) = saved

# ---- 2) element names: one control seen twice vs. really different controls ----
def item(i, kind, name, rect, parent=None):
    it = Item(kind, name, rect); it.id = i; it.clickable = True; it.parent = parent
    return it
li, bt = item(55, "listitem", "Rect\u00e1ngulo redondeado", (10, 10, 60, 60)), item(56, "button", "Rect\u00e1ngulo redondeado", (12, 12, 58, 58))
c["a list entry and the button inside it are ONE control: the button is pressed"] = server._same_thing([(55, li), (56, bt)]) == 56
a1, a2 = item(9, "button", "Abrir", (10, 10, 50, 30), ("pane", "Recientes")), item(15, "button", "Abrir", (300, 10, 340, 30), ("pane", "Plantillas"))
c["two different 'Abrir' buttons are NOT merged"] = server._same_thing([(9, a1), (15, a2)]) is None
core._state[777] = {"items": {9: a1, 15: a2}, "lines": set(), "rect": (0, 0, 800, 600), "scale": 1.0, "mode": "uia", "pid": 1}
try:
    server._by_name(777, "Abrir"); c["real ambiguity is explained"] = False
except server.ToolError as e:
    c["real ambiguity lists each one with its container and position"] = "Recientes" in str(e) and "Plantillas" in str(e) and "at 30,20" in str(e)

# ---- 3) parameter names used in real failed calls ----
c["key(keys='f5') is understood"] = server.remap("key", {"window": "x", "keys": "f5"}) == {"window": "x", "name": "f5"}
c["click(id='50') is understood"] = server.remap("click", {"window": "x", "id": "50"}) == {"window": "x", "target": "50"}
c["open_app(app='mspaint') is understood"] = server.remap("open_app", {"app": "mspaint"}) == {"command": "mspaint"}
c["a real parameter name always wins over an alias"] = server.remap("key", {"window": "x", "name": "enter", "keys": "f5"})["name"] == "enter"
c["drag points as JSON [[x,y],[x,y]]"] = server.parse_points("[[300,300],[500,300]]") == "300,300;500,300"
c["drag points separated by spaces"] = server.parse_points("10,20 30,40") == "10,20;30,40"

# ---- 4) programs installed but not on PATH ----
tmp = tempfile.mkdtemp(); fake_exe = os.path.join(tmp, "FOOAPP.EXE"); open(fake_exe, "w").close()
orig = apps._app_paths, apps.shutil.which, apps.start_menu_shortcuts, apps.store_apps
apps._app_paths = lambda n: fake_exe if n.lower() == "fooapp.exe" else None
apps.shutil.which = lambda n: None
apps.start_menu_shortcuts = lambda: {}; apps.store_apps = lambda: {"mi app de tienda": "Pub.App_123!App"}
r = apps.resolve("fooapp /e")
c["a program found through App Paths keeps its arguments"] = r[0] == f'"{fake_exe}" /e' and r[2] == "fooapp" and "App Paths" in r[1]
r = apps.resolve("Mi App de Tienda")
c["a Store app is started by its name"] = r[0] == 'explorer.exe "shell:AppsFolder\\Pub.App_123!App"' and r[2] == "explorer"
c["an unknown program is reported, not guessed"] = apps.resolve("nonexistentprog")[2] is None
apps._app_paths, apps.shutil.which, apps.start_menu_shortcuts, apps.store_apps = orig
c["a settings page by name"] = apps.resolve("Configuraci\u00f3n")[0] == 'explorer.exe "ms-settings:"'

# ---- 5) raw Windows errors become sentences ----
import pywintypes
c["error 1400 says the window closed"] = "window closed" in server._clean_error(pywintypes.error(1400, "GetClassName", "x"))
c["error 1447 says there are no scroll bars"] = "no scroll bars" in server._clean_error(pywintypes.error(1447, "PostMessage", "x"))
e740 = OSError(); e740.winerror = 740
c["error 740 explains administrator rights"] = "administrator" in server._clean_error(e740)

# ---- 6) a window with no size does not divide by zero ----
saved = (g.GetWindowRect, g.IsIconic, g.GetWindowText)
g.GetWindowRect = lambda h: (100, 100, 100, 100); g.IsIconic = lambda h: False; g.GetWindowText = lambda h: "x"
try:
    r = core._look(123456, "auto", None)
    c["look of a zero-size window answers instead of ZeroDivisionError"] = r["mode"] == "none" and "no size" in r["why"]
finally:
    g.GetWindowRect, g.IsIconic, g.GetWindowText = saved

# ---- 7) keys carry their scancode ----
lp = core.key_lparam(0x0D)
c["Enter carries its scancode and repeat count"] = (lp >> 16) & 0xFF == 0x1C and lp & 0xFFFF == 1
c["arrows are extended keys"] = core.key_lparam(0x28) & (1 << 24) and not core.key_lparam(0x0D) & (1 << 24)
c["key-up has the transition bits"] = core.key_lparam(0x0D, True) & 0xC0000000 == 0xC0000000

# ---- 8) pixels are understood by the verdict ----
c["a pixel change at the element is an observed effect"] = server.verdict("ok (mouse message at 1,1)", "pixels changed at the element and around it (2.0% of the window)").startswith("effect observed")
c["no pixel change is a warning"] = "NO VISIBLE EFFECT" in server.verdict("ok (x)", "nothing changed on screen (pixels compared before and after)")

# ---- Spotify's search box on its home page: a web combobox with a writable value but NO TextPattern and NO inner field. It was taken
# for a <select> (expand, look for an option) and typing was refused. A <select> opens a MENU, a text input with suggestions a LISTBOX
# (measured in Chromium on a test page): only the popup tells them apart.
from pc_control import actions


class _Combo:
    def __init__(self, props): self.AriaProperties = props
    def GetPattern(self, pid): return None
    def GetChildren(self): return []


c["Spotify-like search combobox (listbox popup, no TextPattern, no children) takes typed text"] = actions._types_text(
    _Combo("expanded=true;haspopup=listbox;multiline=false;multiselectable=false;required=false;hasactions=false"))
c["a <select> (menu popup) is still a choice among options"] = not actions._types_text(
    _Combo("expanded=false;haspopup=menu;multiline=false;multiselectable=false"))
c["a combobox with no popup information is not assumed to take text"] = not actions._types_text(_Combo(""))

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"usage regression verification passed ({len(c)} checks)" if not bad else f"usage regression verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
