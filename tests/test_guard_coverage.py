"""Every call that sends input to an app runs inside _act (the focus guard + put-back + verification), including fallbacks.

A click that had no visible effect used to be retried through UI Automation OUTSIDE the guard: the retry let the app take the focus.
This test parses pc_control/server.py and fails if any input-sending call (core.click/invoke/type_text/key/drag, actions.scroll/
scroll_into_view/select_text) is reachable other than through a function passed to _act. Positive control: an injected unguarded
call in a copy of the source must be caught.
"""
import ast, os, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "pc_control", "server.py"), encoding="utf-8").read()
INPUT = {("core", "click"), ("core", "invoke"), ("core", "type_text"), ("core", "key"), ("core", "drag"),
         ("actions", "scroll"), ("actions", "scroll_into_view"), ("actions", "select_text")}


def unguarded(src):
    tree = ast.parse(src)
    parent = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parent[ch] = node
    def is_act(call):
        return isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_act"
    def passed_to_act(fn_node):
        """fn_node (Lambda or FunctionDef) is given to _act directly, or by the name it is bound to in the enclosing function."""
        p = parent.get(fn_node)
        if is_act(p) and fn_node in p.args:
            return True
        name = fn_node.name if isinstance(fn_node, ast.FunctionDef) else None
        if isinstance(p, ast.Assign) and len(p.targets) == 1 and isinstance(p.targets[0], ast.Name):
            name = p.targets[0].id
        if isinstance(p, ast.IfExp):                                   # act = (lambda: ...) if direct else (lambda: ...)
            q = parent.get(p)
            if isinstance(q, ast.Assign) and isinstance(q.targets[0], ast.Name):
                name = q.targets[0].id
        if not name:
            return False
        scope = p
        while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.Module)):
            scope = parent.get(scope)
        for n in ast.walk(scope):
            if is_act(n) and any(isinstance(a, ast.Name) and a.id == name for a in n.args):
                return True
        return False
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)):
            continue
        if (node.func.value.id, node.func.attr) not in INPUT:
            continue
        a, ok = parent.get(node), False
        while a is not None:
            if isinstance(a, ast.With) and any("FocusGuard.for_window" in ast.unparse(i.context_expr) for i in a.items):
                ok = True                                              # guarded explicitly
                break
            if isinstance(a, ast.If) and "_term(" in ast.unparse(a.test):
                ok = True     # documented exception: a Windows Terminal view is scrolled by wheel messages on the console path, never activated
                break
            if isinstance(a, (ast.Lambda, ast.FunctionDef)):
                if passed_to_act(a):
                    ok = True
                    break
                if isinstance(a, ast.FunctionDef) and a.name in ("_act", "_act_body"):
                    ok = True
                    break
            a = parent.get(a)
        if not ok:
            bad.append((node.lineno, f"{node.func.value.id}.{node.func.attr}"))
    return bad


bad = unguarded(SRC)
# positive control: the old fallback shape (invoke called directly after a guarded click) must be detected
control = SRC.replace("def _surface_tag(h):", "def _unguarded_probe(hwnd, t):\n    return core.invoke(str(hwnd), t)\n\n\ndef _surface_tag(h):", 1)
caught = unguarded(control)
ok = True
if bad:
    print("UNGUARDED input calls in server.py:", bad); ok = False
if not any(name == "core.invoke" for _, name in caught):
    print("positive control NOT caught: the checker is blind"); ok = False
counted = sum(1 for n in ast.walk(ast.parse(SRC)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name) and (n.func.value.id, n.func.attr) in INPUT)
if counted < 8:
    print(f"only {counted} input calls found: the list of input functions is out of date"); ok = False
if not ok:
    sys.exit(1)
print(f"guard coverage verification passed ({counted} input calls, all through _act; positive control caught)")
