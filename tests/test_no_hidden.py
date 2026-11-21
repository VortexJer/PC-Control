"""The hidden desktop is a LAST RESORT (the user: "avoid it at all costs, only for super special cases"):
open_app(hidden=true) is refused unless the same program already failed the normal ways in this session or the user turned it on
(PC_CONTROL_HIDDEN=1); nothing else in the package opens anything there by itself."""
import ast, glob, os, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.pop("PC_CONTROL_HIDDEN", None)
from pc_control import server

c = {}
opened = []
server.DESK.open = lambda cmd: opened.append(cmd) or {"pid": 1, "windows": [], "note": "fake"}
server.policy.check_launch = lambda *a, **k: None
call = server.RAW["open_app"]
try:
    call("notepad.exe", hidden=True); c["hidden=true is refused by default"] = False
except server.ToolError as e:
    c["hidden=true is refused by default, with the normal ways to use instead"] = "last resort" in str(e) and "behind=true" in str(e) and not opened
server._NORMAL_FAILED.add("notepad")
r = call("notepad.exe", hidden=True)
c["after the program failed the normal ways, hidden=true is accepted"] = opened == ["notepad.exe"] or (opened and "notepad" in opened[0].lower())
c["... and the answer says it is the last resort"] = "last resort" in r
server._NORMAL_FAILED.clear(); opened.clear()
os.environ["PC_CONTROL_HIDDEN"] = "1"
call("notepad.exe", hidden=True)
c["the user can turn it on explicitly (PC_CONTROL_HIDDEN=1)"] = len(opened) == 1
os.environ.pop("PC_CONTROL_HIDDEN")

# nothing opens apps on the hidden desktop except open_app(hidden=true)
callers = []
for f in glob.glob(os.path.join(ROOT, "pc_control", "*.py")):
    tree = ast.parse(open(f, encoding="utf-8").read())
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "open" and \
                        isinstance(n.func.value, ast.Name) and n.func.value.id == "DESK":
                    callers.append((os.path.basename(f), fn.name))
c["only open_app opens anything on the hidden desktop"] = sorted(set(callers)) == [("server.py", "open_app")]
c["positive control: the scan sees that call"] = ("server.py", "open_app") in callers
schema = next(t for t in server.mcp._tool_manager._tools.values() if t.name == "open_app")
c["the tool tells the model it is the last resort"] = "last resort" in (schema.description or "")

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"no hidden desktop verification passed ({len(c)} checks)" if not bad else f"no hidden desktop verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
