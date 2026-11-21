"""Java apps (Swing, AWT, Oracle Forms, applets, Java Web Start) through the Java Access Bridge (JAB), the interface screen readers use.

Windows UI Automation sees a Java window as ONE empty frame. The bridge sees its real controls (names, roles, states, positions) and can
act on them the way a screen reader does: an action (doClick), a selection or a text set, inside the Java program. None of that moves the
mouse, presses keys or needs the window to have the focus.

  * WindowsAccessBridge-64.dll is the client half; it ships with every JRE/JDK since Java 8 (bin folder). The Java app must load the
    other half: `pc-control java enable` (what `jabswitch -enable` does), or apps that PC-Control opens get it through JAVA_TOOL_OPTIONS.
  * Every bridge call runs on ONE thread with its own message loop (the bridge talks back through window messages).
  * An action that opens a modal dialog blocks inside Java until the dialog closes: actions run on a disposable thread with a deadline.
  * Java reports positions in its own (scaled) pixels: they are converted to the window's real pixels with the frame's measured scale.
"""
import ctypes, ctypes.wintypes as wt, glob, os, queue, threading, time, winreg

JOBJECT64 = ctypes.c_int64
jint = ctypes.c_int
MAX_STRING, SHORT_STRING = 1024, 256
JAVA_CLASSES = ("SunAwtFrame", "SunAwtDialog", "SunAwtWindow", "SunAwtCanvas", "SunAwtEmbeddedFrame", "SunAwtPanel")
JAVA_PROCS = {"java", "javaw", "jp2launcher", "javaws", "jwrapper", "frmsal", "eclipse", "idea64", "jconsole", "sqldeveloper64W",
              "sqldeveloper", "dbeaver", "netbeans64", "jmc"}
ENABLE_PROPERTY = "-Djavax.accessibility.assistive_technologies=com.sun.java.accessibility.AccessBridge"


class AccessibleContextInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar * MAX_STRING), ("description", ctypes.c_wchar * MAX_STRING),
                ("role", ctypes.c_wchar * SHORT_STRING), ("role_en_US", ctypes.c_wchar * SHORT_STRING),
                ("states", ctypes.c_wchar * SHORT_STRING), ("states_en_US", ctypes.c_wchar * SHORT_STRING),
                ("indexInParent", jint), ("childrenCount", jint), ("x", jint), ("y", jint), ("width", jint), ("height", jint),
                ("accessibleComponent", wt.BOOL), ("accessibleAction", wt.BOOL), ("accessibleSelection", wt.BOOL),
                ("accessibleText", wt.BOOL), ("accessibleInterfaces", wt.BOOL)]


class AccessibleTextInfo(ctypes.Structure):
    _fields_ = [("charCount", jint), ("caretIndex", jint), ("indexAtPoint", jint)]


class AccessibleActionInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar * SHORT_STRING)]


class AccessibleActions(ctypes.Structure):
    _fields_ = [("actionsCount", jint), ("actionInfo", AccessibleActionInfo * 256)]


class AccessibleActionsToDo(ctypes.Structure):
    _fields_ = [("actionsCount", jint), ("actions", AccessibleActionInfo * 32)]


class AccessibleTableInfo(ctypes.Structure):
    _fields_ = [("caption", JOBJECT64), ("summary", JOBJECT64), ("rowCount", jint), ("columnCount", jint),
                ("accessibleContext", JOBJECT64), ("accessibleTable", JOBJECT64)]


class VisibleChildrenInfo(ctypes.Structure):
    _fields_ = [("returnedChildrenCount", jint), ("children", JOBJECT64 * 256)]


IFACE_VALUE, IFACE_ACTION, IFACE_SELECTION, IFACE_TABLE, IFACE_TEXT = 1, 2, 8, 16, 32

# Java role (en_US) -> PC-Control kind. Containers (panel, scroll pane, viewport, layered pane...) are walked but not listed.
ROLES = {"push button": "button", "toggle button": "checkbox", "check box": "checkbox", "radio button": "radiobutton",
         "text": "edit", "password text": "edit", "combo box": "combobox", "list": "list", "list item": "listitem",
         "menu": "menuitem", "menu item": "menuitem", "check box menu item": "menuitem", "radio button menu item": "menuitem",
         "page tab": "tabitem", "table": "table", "tree": "tree", "label": "text", "hyperlink": "hyperlink",
         "spin box": "edit", "slider": "slider", "progress bar": "text", "tool tip": "text", "alert": "text",
         "editbar": "edit", "date editor": "edit"}
CLICKABLE = {"button", "checkbox", "radiobutton", "edit", "combobox", "listitem", "menuitem", "tabitem", "hyperlink", "slider"}
NAMELESS_OK = {"edit", "combobox", "checkbox", "radiobutton"}
SKIP_ROLES = {"scroll bar", "glass pane", "filler", "separator", "popup menu"}       # popup menu: its entries are listed by their menu


def find_dlls(extra_dirs=()):
    """Every WindowsAccessBridge-64.dll on this machine: the target JVM's own first, then JAVA_HOME, installed JREs/JDKs, System32."""
    dirs = [d for d in extra_dirs if d]
    if os.environ.get("JAVA_HOME"):
        dirs.append(os.path.join(os.environ["JAVA_HOME"], "bin"))
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in (r"SOFTWARE\JavaSoft\JDK", r"SOFTWARE\JavaSoft\JRE", r"SOFTWARE\JavaSoft\Java Runtime Environment",
                     r"SOFTWARE\JavaSoft\Java Development Kit", r"SOFTWARE\Eclipse Adoptium\JDK", r"SOFTWARE\Eclipse Adoptium\JRE"):
            try:
                with winreg.OpenKey(root, base) as k:
                    for i in range(64):
                        try:
                            ver = winreg.EnumKey(k, i)
                        except OSError:
                            break
                        for sub in (ver, ver + r"\hotspot\MSI"):
                            try:
                                with winreg.OpenKey(k, sub) as vk:
                                    for name in ("JavaHome", "Path"):
                                        try:
                                            dirs.append(os.path.join(winreg.QueryValueEx(vk, name)[0], "bin"))
                                        except OSError:
                                            pass
                            except OSError:
                                pass
            except OSError:
                pass
    for pf in {os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", r"C:\Program Files")}:
        for vendor in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "Amazon Corretto", "BellSoft", "Semeru", "OpenJDK", "Oracle"):
            dirs += glob.glob(os.path.join(pf, vendor, "*", "bin")) + glob.glob(os.path.join(pf, vendor, "*", "jre", "bin"))
    dirs += glob.glob(os.path.join(os.path.expanduser("~"), ".pc-control-testenv", "jdk*", "*", "bin"))
    dirs.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    out, seen = [], set()
    for d in dirs:
        p = os.path.join(d, "WindowsAccessBridge-64.dll")
        if os.path.isfile(p) and os.path.normcase(p) not in seen:       # Windows file names ignore case: windowsaccessbridge-64.dll too
            seen.add(os.path.normcase(p)); out.append(p)
    return out


class _Bridge:
    """The bridge DLL on its own thread with a message loop. call(fn) runs fn(dll) there and returns its result."""
    def __init__(self):
        self.q = queue.Queue(); self.thread = None; self.dll = None; self.path = None; self.error = None
        self.ready = threading.Event(); self.lock = threading.Lock()

    def start(self, hint_dirs=()):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.dll is not None
            dlls = find_dlls(hint_dirs)
            if not dlls:
                self.error = "no Java Access Bridge on this machine (WindowsAccessBridge-64.dll comes with every Java 8+ runtime)"
                return False
            self.ready.clear()
            self.thread = threading.Thread(target=self._run, args=(dlls,), name="pc-control-java", daemon=True)
            self.thread.start()
        self.ready.wait(5)
        return self.dll is not None

    def _run(self, dlls):
        u = ctypes.windll.user32; msg = wt.MSG()
        for p in dlls:
            try:
                self.dll = _prototypes(ctypes.CDLL(p)); self.path = p
                break
            except OSError as e:
                self.error = f"could not load {p}: {e}"
        if self.dll is None:
            self.ready.set(); return
        try:
            self.dll.Windows_run()
        except Exception as e:
            self.error = f"the bridge did not start: {e}"; self.dll = None; self.ready.set(); return
        end = time.time() + 0.6                                     # JVMs answer the bridge's hello through messages to this thread
        while time.time() < end:
            while u.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
                u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)
        self.ready.set()
        while True:
            while u.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
                u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
            try:
                fn, box, done = self.q.get(timeout=0.01)
            except queue.Empty:
                continue
            try:
                box.append((True, fn(self.dll)))
            except BaseException as e:                              # the caller sees the real error
                box.append((False, e))
            done.set()

    def call(self, fn, timeout=12.0):
        if not self.start():
            raise JavaUnavailable(self.error or "Java Access Bridge not available")
        if threading.current_thread() is self.thread:
            return fn(self.dll)
        box, done = [], threading.Event()
        self.q.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("the Java app did not answer the accessibility bridge in time (it may be busy or showing a modal dialog)")
        ok, val = box[0]
        if not ok:
            raise val
        return val


class JavaUnavailable(Exception):
    pass


def _prototypes(d):
    P, B = ctypes.POINTER, wt.BOOL
    sig = {"Windows_run": (None, []),
           "isJavaWindow": (B, [wt.HWND]),
           "getAccessibleContextFromHWND": (B, [wt.HWND, P(ctypes.c_long), P(JOBJECT64)]),
           "getHWNDFromAccessibleContext": (wt.HWND, [ctypes.c_long, JOBJECT64]),
           "getAccessibleContextInfo": (B, [ctypes.c_long, JOBJECT64, P(AccessibleContextInfo)]),
           "getAccessibleChildFromContext": (JOBJECT64, [ctypes.c_long, JOBJECT64, jint]),
           "getAccessibleParentFromContext": (JOBJECT64, [ctypes.c_long, JOBJECT64]),
           "releaseJavaObject": (None, [ctypes.c_long, JOBJECT64]),
           "getAccessibleActions": (B, [ctypes.c_long, JOBJECT64, P(AccessibleActions)]),
           "doAccessibleActions": (B, [ctypes.c_long, JOBJECT64, P(AccessibleActionsToDo), P(jint)]),
           "setTextContents": (B, [ctypes.c_long, JOBJECT64, ctypes.c_wchar_p]),
           "getAccessibleTextInfo": (B, [ctypes.c_long, JOBJECT64, P(AccessibleTextInfo), jint, jint]),
           "getAccessibleTextRange": (B, [ctypes.c_long, JOBJECT64, jint, jint, ctypes.c_wchar_p, ctypes.c_short]),
           "getCurrentAccessibleValueFromContext": (B, [ctypes.c_long, JOBJECT64, ctypes.c_wchar_p, ctypes.c_short]),
           "getVisibleChildrenCount": (jint, [ctypes.c_long, JOBJECT64]),
           "getAccessibleTableInfo": (B, [ctypes.c_long, JOBJECT64, P(AccessibleTableInfo)]),
           "getVisibleChildren": (B, [ctypes.c_long, JOBJECT64, jint, P(VisibleChildrenInfo)]),
           "addAccessibleSelectionFromContext": (None, [ctypes.c_long, JOBJECT64, ctypes.c_int]),
           "clearAccessibleSelectionFromContext": (None, [ctypes.c_long, JOBJECT64]),
           "isAccessibleChildSelectedFromContext": (B, [ctypes.c_long, JOBJECT64, ctypes.c_int]),
           "getAccessibleSelectionFromContext": (JOBJECT64, [ctypes.c_long, JOBJECT64, ctypes.c_int]),
           "getAccessibleSelectionCountFromContext": (ctypes.c_int, [ctypes.c_long, JOBJECT64]),
           "isSameObject": (B, [ctypes.c_long, JOBJECT64, JOBJECT64])}
    for name, (res, args) in sig.items():
        f = getattr(d, name)
        f.restype, f.argtypes = res, args
    return d


BRIDGE = _Bridge()


# ---------- a Java element dressed as a UI Automation control: the rest of PC-Control (identity checks, live position, policy) works on it ----------
class _Rect:
    __slots__ = ("left", "top", "right", "bottom")
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b
    def width(self):
        return self.right - self.left
    def height(self):
        return self.bottom - self.top


UIA_NAME = {"button": "ButtonControl", "checkbox": "CheckBoxControl", "radiobutton": "RadioButtonControl", "edit": "EditControl",
            "combobox": "ComboBoxControl", "list": "ListControl", "listitem": "ListItemControl", "menuitem": "MenuItemControl",
            "tabitem": "TabItemControl", "table": "TableControl", "tree": "TreeControl", "text": "TextControl",
            "hyperlink": "HyperlinkControl", "slider": "SliderControl", "dataitem": "DataItemControl"}


class JRef:
    """One Java accessible object (vm, context) plus what was read about it. Duck-types the few UI Automation members PC-Control reads."""
    NativeWindowHandle = 0
    AutomationId = ""
    HelpText = ""

    def __init__(self, vm, ac, info, kind, scale, origin, hwnd):
        self.vm, self.ac, self.kind, self.scale, self.origin, self.hwnd = vm, ac, kind, scale, origin, hwnd
        self.role = info.role_en_US; self.states = set(s.strip() for s in info.states_en_US.split(",") if s.strip())
        self.ifaces = info.accessibleInterfaces; self.index = info.indexInParent
        self._name = info.name

    # -- read live from Java --
    def info(self):
        def f(d):
            i = AccessibleContextInfo()
            if not d.getAccessibleContextInfo(self.vm, self.ac, ctypes.byref(i)):
                raise LookupError("the Java element is gone")
            return i
        return BRIDGE.call(f)

    def screen_rect(self, info=None):
        i = info or self.info()
        s = self.scale
        return _Rect(round(i.x * s), round(i.y * s), round((i.x + i.width) * s), round((i.y + i.height) * s))

    @property
    def BoundingRectangle(self):
        try:
            return self.screen_rect()
        except Exception:
            return _Rect(0, 0, 0, 0)

    @property
    def IsOffscreen(self):
        try:
            st = set(s.strip() for s in self.info().states_en_US.split(","))
            return "showing" not in st
        except Exception:
            return True

    @property
    def ControlTypeName(self):
        return UIA_NAME.get(self.kind, "CustomControl")

    @property
    def Name(self):
        try:
            return self.info().name
        except Exception:
            return self._name

    @property
    def IsPassword(self):
        return self.role == "password text"

    @property
    def IsEnabled(self):
        try:
            return "enabled" in set(s.strip() for s in self.info().states_en_US.split(","))
        except Exception:
            return False

    @property
    def HasKeyboardFocus(self):
        try:
            return "focused" in set(s.strip() for s in self.info().states_en_US.split(","))
        except Exception:
            return False

    def GetParentControl(self):
        return None

    def __getattr__(self, name):                                     # GetInvokePattern, GetValuePattern...: none (UIA routes are skipped)
        if name.startswith("Get") and name.endswith("Pattern"):
            return lambda: None
        raise AttributeError(name)


class JCell(JRef):
    """A cell of a Java table. Swing hands out ONE renderer for every cell, so a cell is (its table, its index): its name is read
    live from the table at that index, and pressing it selects it through the table's own selection."""
    def __init__(self, vm, table_ac, table_info, index, name, scale, origin, hwnd):
        super().__init__(vm, table_ac, table_info, "dataitem", scale, origin, hwnd)
        self.cell_index, self._name = index, name

    @property
    def Name(self):
        def f(d):
            ch = d.getAccessibleChildFromContext(self.vm, self.ac, self.cell_index)
            if not ch:
                return self._name
            try:
                i = AccessibleContextInfo(); d.getAccessibleContextInfo(self.vm, ch, ctypes.byref(i))
                return i.name or (_text(d, self.vm, ch, 200) or "")
            finally:
                d.releaseJavaObject(self.vm, ch)
        try:
            return BRIDGE.call(f)
        except Exception:
            return self._name

    @property
    def IsPassword(self):
        return False


def select_cell(cell):
    """Select a table cell (its row) through the table's accessible selection: what clicking it does, without the mouse."""
    def job(d):
        d.clearAccessibleSelectionFromContext(cell.vm, cell.ac)
        d.addAccessibleSelectionFromContext(cell.vm, cell.ac, cell.cell_index)
        return bool(d.isAccessibleChildSelectedFromContext(cell.vm, cell.ac, cell.cell_index))
    return BRIDGE.call(job)


def _text(d, vm, ac, limit=4000):
    ti = AccessibleTextInfo()
    if not d.getAccessibleTextInfo(vm, ac, ctypes.byref(ti), 0, 0):
        return None
    n = max(0, min(ti.charCount, limit))
    if n == 0:
        return ""
    out, pos = [], 0
    while pos < n:                                                   # the range call takes at most a short's worth of characters
        end = min(n, pos + 4000) - 1
        buf = ctypes.create_unicode_buffer(4002)
        if not d.getAccessibleTextRange(vm, ac, pos, end, buf, 4001):
            break
        out.append(buf.value); pos = end + 1
    return "".join(out)


def text_of(ref):
    return BRIDGE.call(lambda d: _text(d, ref.vm, ref.ac, 10 ** 6))


def _kind(role, states, ifaces):
    k = ROLES.get(role)
    if k == "edit" and "editable" not in states and role == "text":
        return "text"                                                # a read-only text area: shown as text
    if k is None and ifaces & IFACE_ACTION and role not in ("panel", "root pane", "layered pane", "viewport", "scroll pane", "frame",
                                                             "dialog", "internal frame", "menu bar", "tool bar", "page tab list", "window"):
        return "button"                                              # Oracle Forms and custom toolkits: anything that can be pressed
    return k


def is_java_window(hwnd):
    import win32gui
    try:
        if win32gui.GetClassName(hwnd) in JAVA_CLASSES:
            return True
    except Exception:
        return False
    return False


def _jvm_dir(hwnd):
    import win32process
    try:
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        k = ctypes.windll.kernel32; h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(520); n = wt.DWORD(520)
            k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
            return os.path.dirname(buf.value)
        finally:
            k.CloseHandle(h)
    except Exception:
        return None


def bridge_state(hwnd):
    """'on' if the bridge sees this Java window, 'off' if the app did not load it, 'missing' if there is no bridge DLL."""
    if not BRIDGE.start((_jvm_dir(hwnd),)):
        return "missing"
    ok = False
    for _ in range(6):                                               # a JVM that just started answers the bridge's hello a moment later
        try:
            ok = BRIDGE.call(lambda d: bool(d.isJavaWindow(hwnd)))
        except Exception:
            ok = False
        if ok:
            break
        time.sleep(0.25)
    return "on" if ok else "off"


GUIDE_OFF = ("this is a Java app and its accessibility bridge is OFF, so its controls cannot be read or pressed exactly. Fix (once, by the user): "
             "run `pc-control java enable` (or `jabswitch -enable`) and restart the Java app; apps opened with open_app get it automatically. "
             "Meanwhile the window is read with OCR/image and clicked by position")

_REFS = {}                                                           # hwnd -> refs kept by the last walk (released on the next one)


def _release(d, vm, refs):
    for r in refs:
        try:
            d.releaseJavaObject(vm, r)
        except Exception:
            pass


def walk(hwnd, rect, budget_s=6.0, max_nodes=2500, offscreen_ok=False, keep=True):
    """The Java window's elements as core.Item objects (src 'jab'). Positions are relative to the window, in its real pixels."""
    from .core import Item
    import win32gui
    if not BRIDGE.start((_jvm_dir(hwnd),)):
        raise JavaUnavailable(BRIDGE.error)

    def job(d):
        vm, root = ctypes.c_long(), JOBJECT64()
        if not d.getAccessibleContextFromHWND(hwnd, ctypes.byref(vm), ctypes.byref(root)):
            raise JavaUnavailable("off")
        vm = vm.value; root = root.value
        ri = AccessibleContextInfo(); d.getAccessibleContextInfo(vm, root, ctypes.byref(ri))
        wl, wt_, wr, wb = win32gui.GetWindowRect(hwnd)
        scale = (wr - wl) / ri.width if ri.width > 0 else 1.0       # Java's pixels -> the window's real pixels (HiDPI)
        if not 0.5 <= scale <= 4:
            scale = 1.0
        items, keep_refs, drop = [], [], []
        stack = [(root, 0, None, None)]; n = 0; t0 = time.time()
        while stack:
            ac, depth, parent, cell = stack.pop(); n += 1
            if n > max_nodes or time.time() - t0 > budget_s:
                drop.append(ac); drop.extend(x[0] for x in stack); break
            info = AccessibleContextInfo()
            if not d.getAccessibleContextInfo(vm, ac, ctypes.byref(info)):
                drop.append(ac); continue
            states = set(s.strip() for s in info.states_en_US.split(",") if s.strip())
            role = info.role_en_US
            showing = "showing" in states or depth == 0 or cell is not None     # table cells report no state: their table is showing
            if (not showing and not offscreen_ok) or role in SKIP_ROLES:
                drop.append(ac); continue
            kind = _kind(role, states, info.accessibleInterfaces)
            name = (info.name or "").strip()
            kept = False
            if kind:
                value = ""
                if kind in ("edit", "text") and info.accessibleText and role != "password text":
                    try:
                        value = (_text(d, vm, ac, 200) or "").replace("\r", " ").replace("\n", " ").strip()
                    except Exception:
                        value = ""
                if kind == "text" and not name:
                    name = value; value = ""
                if kind == "combobox":
                    buf = ctypes.create_unicode_buffer(256)
                    if info.accessibleInterfaces & IFACE_VALUE and d.getCurrentAccessibleValueFromContext(vm, ac, buf, 255):
                        value = buf.value
                    if not value and info.childrenCount:            # the shown entry: the selected child of its list
                        value = _combo_selected(d, vm, ac) or ""
                if kind in ("checkbox", "radiobutton"):
                    value = "on" if ("checked" in states or "selected" in states and kind == "radiobutton") else "off"
                if not name and kind in NAMELESS_OK:
                    name = "(unnamed " + {"edit": "text field", "combobox": "dropdown", "checkbox": "checkbox", "radiobutton": "radio button"}[kind] + ")"
                x, y, w, h = info.x, info.y, info.width, info.height
                if (w <= 0 or h <= 0) and cell is not None:              # a table cell: its place from the table's rows and columns
                    tx, ty, tw, th, rows, cols = cell
                    i = info.indexInParent if info.indexInParent >= 0 else 0
                    cw, rh = tw / max(1, cols), th / max(1, rows)
                    x, y, w, h = tx + (i % cols) * cw, ty + (i // cols) * rh, cw, rh
                if name and ((w > 0 and h > 0) or offscreen_ok):        # out of view (closed menu, hidden tab): no place, still pressable
                    r = ((round(x * scale) - wl, round(y * scale) - wt_, round((x + w) * scale) - wl, round((y + h) * scale) - wt_)
                         if w > 0 and h > 0 else (0, 0, 0, 0))
                    it = Item(kind, name[:120], r, JRef(vm, ac, info, kind, scale, (wl, wt_), hwnd), "jab", kind in CLICKABLE or kind == "list")
                    it.value = value[:80]; it.parent = parent
                    if "enabled" not in states:
                        it.value = (it.value + " (disabled)").strip()
                    items.append(it); keep_refs.append(ac); kept = True
            if kind in ("text", "edit", "combobox", "checkbox", "radiobutton", "button") and role not in ("menu",):
                if not kept:
                    drop.append(ac)
                continue                                             # leaves: their children are their own parts
            count = info.childrenCount
            if role == "table" and count:                            # cells: read each one NOW (one shared renderer answers for all)
                ti = AccessibleTableInfo()
                cols = rows = 0
                if d.getAccessibleTableInfo(vm, ac, ctypes.byref(ti)):
                    rows, cols = ti.rowCount, ti.columnCount
                    _release(d, vm, [o for o in (ti.caption, ti.summary, ti.accessibleTable) if o])
                if cols > 0:
                    idxs = range(min(count, 400))
                    if count > 400 and not offscreen_ok:
                        vc = VisibleChildrenInfo()
                        if d.getVisibleChildren(vm, ac, 0, ctypes.byref(vc)):
                            vis = []
                            for k in range(vc.returnedChildrenCount):
                                ci = AccessibleContextInfo(); d.getAccessibleContextInfo(vm, vc.children[k], ctypes.byref(ci))
                                vis.append(ci.indexInParent); d.releaseJavaObject(vm, vc.children[k])
                            idxs = [i for i in vis if i >= 0][:400]
                    cw, rh = info.width / cols, info.height / max(1, rows)
                    for i in idxs:
                        ch = d.getAccessibleChildFromContext(vm, ac, i)
                        if not ch:
                            continue
                        ci = AccessibleContextInfo(); d.getAccessibleContextInfo(vm, ch, ctypes.byref(ci))
                        cname = (ci.name or (_text(d, vm, ch, 200) or "")).strip()
                        d.releaseJavaObject(vm, ch)
                        if not cname:
                            continue
                        x, y = info.x + (i % cols) * cw, info.y + (i // cols) * rh
                        r = (round(x * scale) - wl, round(y * scale) - wt_, round((x + cw) * scale) - wl, round((y + rh) * scale) - wt_)
                        it = Item("dataitem", cname[:120], r, JCell(vm, ac, info, i, cname, scale, (wl, wt_), hwnd), "jab", True)
                        it.parent = (kind or role, name)
                        items.append(it)
                    keep_refs.append(ac)
                    continue
            kids = []
            if count > 200 and not offscreen_ok:                     # a big table or list: only what is on screen
                vc = VisibleChildrenInfo()
                if d.getVisibleChildren(vm, ac, 0, ctypes.byref(vc)):
                    kids = list(vc.children[:vc.returnedChildrenCount])
            else:
                for i in range(min(count, 400)):
                    ch = d.getAccessibleChildFromContext(vm, ac, i)
                    if ch:
                        kids.append(ch)
            pkind = (kind or role, name)
            stack.extend((ch, depth + 1, pkind, None) for ch in reversed(kids))
            if not kept:
                drop.append(ac)
        if keep:
            _release(d, vm, [a for a in drop if a != root])
        else:                                                        # a comparison only needs the names: give everything back
            _release(d, vm, drop + keep_refs)
        return vm, items, keep_refs

    vm, items, refs = BRIDGE.call(job, timeout=budget_s + 6)
    if keep:                                                         # ids of the last readings stay usable; older ones are given back
        gens = _REFS.setdefault(hwnd, [])
        gens.append((vm, refs))
        while len(gens) > 3:
            ovm, orefs = gens.pop(0)
            BRIDGE.call(lambda d: _release(d, ovm, orefs))
    return items


def _combo_selected(d, vm, combo):
    """The entry a Swing combo shows: its own selection (AccessibleJComboBox answers with the selected entry)."""
    try:
        if d.getAccessibleSelectionCountFromContext(vm, combo) < 1:
            return None
        ch = d.getAccessibleSelectionFromContext(vm, combo, 0)
        if not ch:
            return None
        ci = AccessibleContextInfo(); d.getAccessibleContextInfo(vm, ch, ctypes.byref(ci)); d.releaseJavaObject(vm, ch)
        return ci.name
    except Exception:
        return None


def _find_list(d, vm, ac, depth=0, owned=None):
    """The list that holds a combo's entries: combo -> popup -> scroll pane -> viewport -> list (Swing), searched a few levels down."""
    owned = [] if owned is None else owned
    i = AccessibleContextInfo()
    if not d.getAccessibleContextInfo(vm, ac, ctypes.byref(i)):
        return None, owned
    if depth and i.role_en_US == "list":
        return ac, owned
    if depth >= 5:
        return None, owned
    for k in range(min(i.childrenCount, 8)):
        ch = d.getAccessibleChildFromContext(vm, ac, k)
        if not ch:
            continue
        owned.append(ch)
        found, _ = _find_list(d, vm, ch, depth + 1, owned)
        if found:
            return found, owned
    return None, owned


# ---------- acting ----------
ACTION_DEADLINE = 2.5               # an action that opens a modal dialog does not return until it closes


def _actions(d, vm, ac):
    acts = AccessibleActions()
    if not d.getAccessibleActions(vm, ac, ctypes.byref(acts)):
        return []
    return [acts.actionInfo[i].name for i in range(acts.actionsCount)]


def _run_with_deadline(fn):
    """Run fn(dll) on a disposable thread: if Java does not return in ACTION_DEADLINE (a modal dialog it opened is waiting), the call is
    reported as delivered-and-busy instead of freezing PC-Control."""
    box, done = [], threading.Event()
    def worker():
        try:
            box.append((True, fn(BRIDGE.dll)))
        except BaseException as e:
            box.append((False, e))
        done.set()
    if not BRIDGE.start():
        raise JavaUnavailable(BRIDGE.error)
    threading.Thread(target=worker, name="pc-control-java-action", daemon=True).start()
    if not done.wait(ACTION_DEADLINE):
        return "busy"
    ok, val = box[0]
    if not ok:
        raise val
    return val


def do_action(ref, prefer=("click", "toggle", "press", "togglePopup")):
    """Press the element through its own Java action (what a screen reader does). Returns a result text or None if it has none."""
    names = BRIDGE.call(lambda d: _actions(d, ref.vm, ref.ac))
    if not names:
        return None
    low = [n.lower() for n in names]
    pick = next((names[low.index(p.lower())] for p in prefer if p.lower() in low), names[0])
    def job(d):
        todo = AccessibleActionsToDo(); todo.actionsCount = 1; todo.actions[0].name = pick
        fail = jint(-1)
        return bool(d.doAccessibleActions(ref.vm, ref.ac, ctypes.byref(todo), ctypes.byref(fail)))
    r = _run_with_deadline(job)
    if r == "busy":
        return f"ok (Java action «{pick}» delivered; the app is still busy with it, probably a dialog it opened: look again)"
    return f"ok (Java action «{pick}» through the accessibility bridge)" if r else None


def do_named(ref, names, label):
    """Run the FIRST of these Java actions that the element offers (text editor actions such as notify-field-accept = Enter).
    None if it offers none of them: nothing is run."""
    offered = BRIDGE.call(lambda d: _actions(d, ref.vm, ref.ac))
    pick = next((n for n in names if n in offered), None)
    if pick is None:
        return None
    def job(d):
        todo = AccessibleActionsToDo(); todo.actionsCount = 1; todo.actions[0].name = pick
        fail = jint(-1)
        return bool(d.doAccessibleActions(ref.vm, ref.ac, ctypes.byref(todo), ctypes.byref(fail)))
    r = _run_with_deadline(job)
    if r == "busy":
        return f"ok ({label}: Java action «{pick}» delivered; the app is still busy with it, probably a dialog: look again)"
    return f"ok ({label}: Java action «{pick}» through the accessibility bridge)" if r else None


def select_in_parent(ref):
    """A tab or a list entry: selected through its container's selection (the way a screen reader picks it)."""
    def job(d):
        parent = d.getAccessibleParentFromContext(ref.vm, ref.ac)
        if not parent:
            return False
        try:
            pi = AccessibleContextInfo(); d.getAccessibleContextInfo(ref.vm, parent, ctypes.byref(pi))
            if not pi.accessibleInterfaces & IFACE_SELECTION and not pi.accessibleSelection:
                return False
            d.clearAccessibleSelectionFromContext(ref.vm, parent)
            d.addAccessibleSelectionFromContext(ref.vm, parent, ref.index)
            return bool(d.isAccessibleChildSelectedFromContext(ref.vm, parent, ref.index))
        finally:
            d.releaseJavaObject(ref.vm, parent)
    return BRIDGE.call(job)


def select_option(ref, text):
    """Pick the entry called `text` in a Java combo box or list (no popup opened, no focus). Result text, or None if not found."""
    want = text.strip().casefold()
    def job(d):
        lst, owned = ref.ac, []
        if ref.kind == "combobox":
            lst, owned = _find_list(d, ref.vm, ref.ac)
            if not lst:
                _release(d, ref.vm, owned)
                return None
        try:
            li = AccessibleContextInfo(); d.getAccessibleContextInfo(ref.vm, lst, ctypes.byref(li))
            names = []
            for i in range(min(li.childrenCount, 500)):
                ch = d.getAccessibleChildFromContext(ref.vm, lst, i)
                if not ch:
                    continue
                ci = AccessibleContextInfo(); d.getAccessibleContextInfo(ref.vm, ch, ctypes.byref(ci)); d.releaseJavaObject(ref.vm, ch)
                names.append(ci.name)
            idx = next((i for i, n in enumerate(names) if n.strip().casefold() == want), None)
            if idx is None:
                idx = next((i for i, n in enumerate(names) if want and want in n.casefold()), None)
            if idx is None:
                return ("not found", names[:12])
            target = ref.ac                                          # a combo takes the selection itself (it sets its selected index)
            d.clearAccessibleSelectionFromContext(ref.vm, target) if ref.kind != "combobox" else None
            d.addAccessibleSelectionFromContext(ref.vm, target, idx)
            return ("ok", names[idx])
        finally:
            _release(d, ref.vm, owned)
    r = BRIDGE.call(job)
    if r is None:
        return None
    if r[0] == "not found":
        return f"did not select: no entry «{text[:40]}» in this list. Entries: " + ", ".join(f"«{n[:30]}»" for n in r[1])
    shown = None
    try:
        shown = BRIDGE.call(lambda d: _combo_selected(d, ref.vm, ref.ac)) if ref.kind == "combobox" else r[1]
    except Exception:
        pass
    ok = shown is not None and shown.strip().casefold() == r[1].strip().casefold()
    return (f"selected «{r[1][:40]}» through the Java accessibility bridge | read back: "
            + ("the control now shows it" if ok else f"WARNING: it shows something else («{(shown or '')[:30]}»)"))


def set_text(ref, text, replace):
    """Write into a Java text field through the bridge (no keys, no focus) and read it back."""
    def job(d):
        old = _text(d, ref.vm, ref.ac, 10 ** 6) if not replace else ""
        if old is None and not replace:
            return None
        return bool(d.setTextContents(ref.vm, ref.ac, text if replace else old + text))
    r = BRIDGE.call(job)
    if r is None:
        return None
    if not r:
        return "did not type: the Java field refused the text (it is read-only, or its toolkit does not allow setting text through accessibility)"
    now = text_of(ref) or ""
    norm = lambda s: s.replace("\r", "").replace("\n", "")
    ok = norm(now).strip() == norm(text).strip() if replace else norm(text).strip() in norm(now)
    tail = now[-50:]
    return ("ok (Java setTextContents through the accessibility bridge)" + " | read back: "
            + ("the field now contains the text" if ok else "WARNING: the field does NOT contain the typed text") + f' ("{tail}")')


# ---------- enabling the bridge for the user (pc-control java enable/disable/status) ----------
PROPS = os.path.join(os.path.expanduser("~"), ".accessibility.properties")
OUR_LINES = ("assistive_technologies=com.sun.java.accessibility.AccessBridge", "screen_magnifier_present=true")


def props_state(path=None):
    path = path or PROPS
    try:
        txt = open(path, encoding="latin-1").read()
    except OSError:
        return "absent"
    for ln in txt.splitlines():
        k, _, v = ln.partition("=")
        if k.strip() == "assistive_technologies" and "com.sun.java.accessibility.AccessBridge" in v:
            return "enabled"
    return "present-without-bridge"


def enable(path=None):
    """Add the bridge to the user's Java accessibility properties (what `jabswitch -enable` does), keeping every other line."""
    path = path or PROPS
    lines = []
    try:
        lines = open(path, encoding="latin-1").read().splitlines()
    except OSError:
        pass
    out, have_at, have_mag = [], False, False
    for ln in lines:
        k, _, v = ln.partition("=")
        k = k.strip()
        if k == "assistive_technologies":
            techs = [t.strip() for t in v.split(",") if t.strip()]
            if "com.sun.java.accessibility.AccessBridge" not in techs:
                techs.append("com.sun.java.accessibility.AccessBridge")
            out.append("assistive_technologies=" + ",".join(techs)); have_at = True
        elif k == "screen_magnifier_present":
            out.append(ln); have_mag = True
        else:
            out.append(ln)
    if not have_at:
        out.append(OUR_LINES[0])
    if not have_mag:
        out.append(OUR_LINES[1])
    with open(path, "w", encoding="latin-1", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    return path


def disable(path=None):
    """Remove only the bridge from the user's Java accessibility properties (other assistive technologies stay)."""
    path = path or PROPS
    try:
        lines = open(path, encoding="latin-1").read().splitlines()
    except OSError:
        return None
    out = []
    for ln in lines:
        k, _, v = ln.partition("=")
        if k.strip() == "assistive_technologies":
            techs = [t.strip() for t in v.split(",") if t.strip() and t.strip() != "com.sun.java.accessibility.AccessBridge"]
            if techs:
                out.append("assistive_technologies=" + ",".join(techs))
        elif ln.strip() == OUR_LINES[1]:
            continue
        else:
            out.append(ln)
    if any(l.strip() for l in out):
        with open(path, "w", encoding="latin-1", newline="\n") as f:
            f.write("\n".join(out) + "\n")
    else:
        os.remove(path)
    return path


def launch_env(command, env=None):
    """The environment for open_app: a Java program gets the bridge through JAVA_TOOL_OPTIONS (only for that process)."""
    import shlex
    try:
        exe = os.path.splitext(os.path.basename(shlex.split(command, posix=False)[0].strip('"')))[0].lower()
    except (ValueError, IndexError):
        return None
    jarlike = command.lower().rstrip('" ').endswith((".jar", ".jnlp"))
    if exe not in JAVA_PROCS and not jarlike:
        return None
    env = dict(os.environ if env is None else env)
    cur = env.get("JAVA_TOOL_OPTIONS", "")
    if "assistive_technologies" not in cur:
        env["JAVA_TOOL_OPTIONS"] = (cur + " " + ENABLE_PROPERTY).strip()
    return env


def read_all(hwnd):
    """Every text of a Java window, in reading order: labels, field contents, table cells, whole text areas (also scrolled out of view).
    Password fields are never read."""
    import win32gui
    items = walk(hwnd, win32gui.GetWindowRect(hwnd), offscreen_ok=True, budget_s=8.0)
    out, seen = [], set()
    for it in sorted(items, key=lambda i: (i.rect[1] // 10, i.rect[0])):
        if it.ctrl is not None and it.ctrl.IsPassword:
            continue
        line = it.name
        if it.kind in ("edit", "text") and it.ctrl is not None and it.ctrl.ifaces & IFACE_TEXT:
            try:
                full = text_of(it.ctrl)
                if full and full.strip() and full.strip() != it.name.strip():
                    line = (it.name + ": " if it.kind == "edit" else "") + full
            except Exception:
                pass
        elif it.value:
            line = f"{it.name} = {it.value}"
        if line and line not in seen:
            seen.add(line); out.append(line)
    return "\n".join(out)
