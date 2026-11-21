"""Native decision matrix plus a real visible app that repeatedly tries to steal focus."""
import ctypes
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import win32gui
from pc_control.focus import FocusGuard, library
from input_watch import InputWatch

dll = library()
for same, mouse, switching, empty in itertools.product((0, 1), repeat=4):
    assert bool(dll.pc_focus_allow(same, mouse, switching, empty)) == bool(same or mouse or switching)  # an empty foreground is NOT permission
assert not dll.pc_focus_install(0), "global hooks must be rejected"

with tempfile.TemporaryDirectory(prefix="pc-control-focus-") as tmp, InputWatch() as watch:
    state = Path(tmp) / "state.json"
    guard = FocusGuard("pc-control-disposable-test").start()
    p = None
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 4
        native_path = Path(dll._name)
        p = guard.launch([sys.executable, str(ROOT / "tests" / "focus_test_app.py"), str(state), str(native_path)], si)
        deadline = time.monotonic() + 15
        latest = {}
        while time.monotonic() < deadline:
            time.sleep(0.15)
            try:
                latest = json.loads(state.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            assert not latest["foreground"], "adversarial app stole foreground"
            if latest["attempts"] >= 24:
                break
        assert latest.get("attempts", 0) >= 24, (latest, p.poll(), guard.errors)
        assert dll.pc_focus_owned(latest["edit"], latest["hwnd"]), "foreground editor must be allowed"
        assert dll.pc_focus_owned(latest["popup"], latest["hwnd"]), "owned dialog must be allowed"
        assert dll.pc_focus_owned(latest["hwnd"], latest["popup"]), "returning from a dialog must be allowed"
        assert not dll.pc_focus_owned(latest["other"], latest["hwnd"]), "same PID is not ownership"
        assert not dll.pc_focus_owned(latest["hwnd"], latest["other"]), "other foreground root must be protected"
        assert all(win32gui.IsWindowVisible(latest[key]) for key in ("hwnd", "other", "popup")), "test panels must be visible"
        assert latest["blocked_activation"] > 0 and latest["blocked_focus"] > 0, latest
        assert not any(hwnd in (latest["hwnd"], latest["edit"]) for _, hwnd, _ in watch.events), watch.events
        print("Native policy: all 16 user/automatic activation cases passed")
        print("Visible app: 24 activation/focus attempts blocked, no foreground/focus event targeted the app")
        guard.close()
        from pc_control import server
        old_blocked = latest["blocked_activation"]
        def provoke_again():
            win32gui.PostMessage(latest["hwnd"], 0x8000 + 7, 0, 0)
            time.sleep(0.3)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    state_now = json.loads(state.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    time.sleep(0.05)
                    continue
                assert not state_now["foreground"], "action guard allowed focus stealing"
                if state_now["attempts"] >= 24 and state_now["blocked_activation"] > old_blocked:
                    return "ok (runtime guard blocked the second sequence)"
                time.sleep(0.1)
            raise AssertionError("runtime guard sequence did not finish")
        result = server._act(latest["hwnd"], False, provoke_again)
        assert "runtime guard blocked" in result
        assert not any(hwnd in (latest["hwnd"], latest["edit"]) for _, hwnd, _ in watch.events), watch.events
        print("Runtime MCP action wrapper: 24 further activation/focus attempts blocked")
        time.sleep(3)
    finally:
        if p is not None:
            if latest.get("hwnd") and win32gui.IsWindow(latest["hwnd"]):
                win32gui.PostMessage(latest["hwnd"], 0x0010, 0, 0)
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.terminate(); p.wait(timeout=3)
        guard.close()
print("focus guard verification passed")
