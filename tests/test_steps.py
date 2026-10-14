"""steps(): several actions in one call. Uses fake tool functions: it checks the order, the stops (failure, no effect, permission, unknown
action) and the short answer for the middle steps. No window is touched."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from pc_control import server, policy

calls, c = [], {}
def fake(name, reply):
    def f(window, **kw):
        calls.append((name, window, kw))
        r = reply(kw) if callable(reply) else reply
        if isinstance(r, Exception):
            raise r
        return r
    return f

steps = server.RAW["steps"]
real = dict(server.RAW)
try:
    server.RAW.update(click=fake("click", "pressed Save | changes: a dialog | did its job"), type=fake("type", "typed | changes: x | ok"),
                      key=fake("key", "sent enter | changes: closed | ok"))
    out = steps("w", [{"do": "click", "target": "Save"}, {"do": "type", "target": "Name", "text": "hi", "replace": True}, {"do": "key", "name": "enter"}])
    c["runs in order with its arguments"] = [x[0] for x in calls] == ["click", "type", "key"] and calls[1][2] == {"target": "Name", "text": "hi", "replace": True}
    lines = out.splitlines()
    c["middle steps are short, the last one is complete"] = "changes" not in lines[0] and "changes" in lines[2]
    calls.clear()
    server.RAW["click"] = fake("click", "did not press: gone | NOT EXECUTED")
    out = steps("w", [{"do": "click", "target": "A"}, {"do": "key", "name": "enter"}])
    c["stops at a step that was not executed"] = len(calls) == 1 and "STOPPED" in out
    calls.clear()
    server.RAW["click"] = fake("click", policy.NeedsPermission("x", "q"))
    out = steps("w", [{"do": "click", "target": "A"}, {"do": "key", "name": "enter"}])
    c["stops when the user's permission is needed (never asks for the whole batch)"] = len(calls) == 1 and "permission" in out
    out = steps("w", [{"do": "format", "target": "A"}])
    c["unknown action stops"] = "unknown action" in out
    try:
        steps("w", [])
        c["empty list is refused"] = False
    except server.ToolError:
        c["empty list is refused"] = True
finally:
    server.RAW.clear(); server.RAW.update(real)
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
ok = len(c) == 6 and all(c.values())
print(f"steps verification {'passed' if ok else 'FAILED'}")
sys.exit(0 if ok else 1)