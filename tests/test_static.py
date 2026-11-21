"""The package code must not contain calls that interfere with the user (AST analysis, not text matching)."""
import ast, glob, os, re, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = {"SetForegroundWindow", "SetFocus", "keybd_event", "SendInput", "SetCursorPos", "mouse_event", "SetActiveWindow",
             "BringWindowToTop", "SwitchToThisWindow", "AttachThreadInput", "pyautogui", "pynput",
             "InjectSyntheticPointerInput", "InjectTouchInput", "BlockInput", "ClipCursor"}

# The ONE allowed exception: giving the foreground BACK to the user's own window while an app left it EMPTY (focus.FocusGuard.hand_back).
ALLOWED = {("hand_back", "SetForegroundWindow"),
           # give-back: a window took the foreground without the user asking; it goes BACK to the user's window (AutoHotkey technique)
           ("_give_back_attached", "SetForegroundWindow"), ("_give_back_attached", "AttachThreadInput")}


def _allowed_nodes(tree):
    ok = set()
    for f in ast.walk(tree):
        if isinstance(f, ast.FunctionDef):
            for n in ast.walk(f):
                name = n.attr if isinstance(n, ast.Attribute) else n.id if isinstance(n, ast.Name) else None
                if (f.name, name) in ALLOWED:
                    ok.add(id(n))
    return ok


def scan(src):
    hits = []
    tree = ast.parse(src); ok = _allowed_nodes(tree)
    for n in ast.walk(tree):
        if id(n) in ok:
            continue
        if isinstance(n, ast.Attribute) and n.attr in FORBIDDEN: hits.append((n.lineno, n.attr))
        elif isinstance(n, ast.Name) and n.id in FORBIDDEN: hits.append((n.lineno, n.id))
        elif isinstance(n, ast.alias) and n.name.split(".")[0] in FORBIDDEN: hits.append((0, n.name))
        elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in FORBIDDEN: hits.append((n.lineno, n.module))
    return hits

# positive controls: the scanner MUST detect each pattern and must NOT flag comments or docstrings
for w in FORBIDDEN:
    assert scan(f"import x\nx.{w}(h)\n") == [(2, w)], f"the scanner does not detect x.{w}"
assert scan("import pyautogui\n"), "does not detect import pyautogui"
assert scan('"""never SetForegroundWindow"""\n# keybd_event\nx = 1\n') == [], "false positive on a docstring/comment"

files = glob.glob(os.path.join(ROOT, "pc_control", "*.py"))
assert files, "there is no code to scan"
bad = {os.path.basename(f): scan(open(f, encoding="utf-8").read()) for f in files}
bad = {k: v for k, v in bad.items() if v}
if bad:
    print("INTRUSIVE CALLS:", bad); sys.exit(1)
NATIVE_ALLOWED = {("hand_back", "SetForegroundWindow"), ("probe_proc", "SetForegroundWindow")}   # probe_proc: the give-back message       # the native twin of focus.FocusGuard.hand_back (gives the foreground BACK)


def _strip_allowed_native(src):
    """Remove the body of each allowed native function, but only the allowed call inside it (anything else there is still scanned)."""
    for fn, call in NATIVE_ALLOWED:
        m = re.search(r"\b" + fn + r"\s*\([^)]*\)\s*\{", src)
        if not m:
            continue
        depth, i = 1, m.end()
        while i < len(src) and depth:
            depth += {"{": 1, "}": -1}.get(src[i], 0); i += 1
        body = re.sub(r"\b" + call + r"\s*\(", "ALLOWED_HAND_BACK(", src[m.end():i])
        src = src[:m.end()] + body + src[i:]
    return src


def scan_native(src):
    src = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"', "", src, flags=re.S)
    src = _strip_allowed_native(src)
    return [name for name in FORBIDDEN if re.search(r"\b" + re.escape(name) + r"\s*\(", src)]


assert not scan_native("static void hand_back(HWND w) { if (x) { SetForegroundWindow(w); } }"), "native hand-back exception not recognised"
assert scan_native("static void other(HWND w) { SetForegroundWindow(w); }") == ["SetForegroundWindow"], "native exception leaks to other functions"
assert scan_native("static void hand_back(HWND w) { SetFocus(w); }") == ["SetFocus"], "native exception leaks to other calls"

for name in FORBIDDEN:
    assert name in scan_native(f"{name}(window);"), ("native positive control", name)
assert not scan_native('/* SetFocus(window); */ const char *s = "SendInput(x)";'), "native comment/string false positive"
for f in glob.glob(os.path.join(ROOT, "native", "*.c")):
    hits = scan_native(open(f, encoding="utf-8").read())
    assert not hits, (f, hits)
print(f"static verification passed ({len(files)} files, {len(FORBIDDEN)} patterns, positive control ok)")
