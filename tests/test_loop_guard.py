"""Repeating an identical failing call is a loop. The 2nd identical failure says so and what to change; from then on, the same call is
refused WITHOUT acting until the window changes; any change (another target, the window changing, a success) lets calls through again.
Measured in real sessions: 44 identical consecutive repeats and 15 same-failure pairs in 1250 calls."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pc_control import server

ran = []
state = {"fp": "window-A", "result": "ok (mouse message at 1,1) | changes: no changes | WARNING: NO VISIBLE EFFECT; check with look"}
server._fingerprint = lambda window: state["fp"]


@server.tool
def fake_click(window: str, target: str, confirm: bool = False) -> str:
    """test tool"""
    ran.append(target)
    return state["result"]


@server.tool
def fake_error(window: str, confirm: bool = False) -> str:
    """test tool"""
    ran.append("err")
    raise LookupError("no window with 'Paint' in its title or program name: call windows()")


call = server.RAW["fake_click"]
c = {}
r1 = call("w", "7")
c["1st failure is reported as is"] = "NO VISIBLE EFFECT" in r1 and "SAME FAILURE" not in r1
r2 = call("w", "7")
c["2nd identical failure says it is the same and what to change"] = r2.startswith("SAME FAILURE AS BEFORE (2 times)") and "aim differently" in r2
try:
    call("w", "7"); c["3rd identical call is refused without acting"] = False
except server.ToolError as e:
    c["3rd identical call is refused without acting"] = "REPEATED FAILURE, not executed again" in str(e) and ran == ["7", "7"]
r = call("w", "8")
c["another target is a different call: it runs"] = ran[-1] == "8" and "SAME FAILURE" not in r
state["fp"] = "window-B"
call("w", "7")
c["after the window changed, the same call runs again"] = ran[-1] == "7" and len(ran) == 4
state["result"] = "ok (BM_CLICK) | changes: + Saved | effect observed"
state["fp"] = "window-C"                                    # something changed (the user fixed it, a dialog closed...)
call("w", "7")
state["result"] = "ok (mouse message) | changes: no changes | WARNING: NO VISIBLE EFFECT; check with look"
r = call("w", "7")
c["a success clears the record (the next failure counts from 1)"] = "SAME FAILURE" not in r
call_e = server.RAW["fake_error"]
for _ in range(2):
    try:
        call_e("Paint")
    except server.ToolError as e:
        last = str(e)
c["errors count too, with their own advice"] = last.startswith("SAME FAILURE AS BEFORE (2 times)") and "windows()" in last
try:
    call_e("Paint"); c["a 3rd identical error is refused"] = False
except server.ToolError as e:
    c["a 3rd identical error is refused"] = "REPEATED FAILURE" in str(e) and ran.count("err") == 2
@server.tool
def fake_perm(window: str, confirm: bool = False) -> str:
    """test tool"""
    ran.append("perm")
    raise server.policy.NeedsPermission("Claude wants to close a window it did not open. Do you allow it?")


asked = 0
for _ in range(4):
    try:
        server.RAW["fake_perm"]("w")
    except server.policy.NeedsPermission:
        asked += 1
c["permission requests are never counted as failures (they must be repeated exactly)"] = asked == 4 and ran.count("perm") == 4
# the record is bounded
for i in range(300):
    server._loop_record("x", {"window": str(i)}, "did not press | NOT EXECUTED", True)
c["the failure memory is bounded"] = len(server._FAILS) <= 201

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"loop guard verification passed ({len(c)} checks)" if not bad else f"loop guard verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
