"""Java apps whose accessibility bridge is OFF get exact guidance (and are still read from pixels); `pc-control java enable/disable/status`
edits only its own lines of the user's Java accessibility settings; apps PC-Control opens get the bridge only for themselves.

Uses a temporary settings file (the user's real ~/.accessibility.properties is never written by this test) and the Swing test app.
"""
import os, sys, tempfile, time, subprocess
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import win32gui
from pc_control import core, java, cli

c = {}
tmp = tempfile.mkdtemp(); props = os.path.join(tmp, ".accessibility.properties")
with open(props, "w", encoding="latin-1") as f:
    f.write("# user settings\nassistive_technologies=com.example.Magnifier\nsome.other=1\n")
real_before = open(java.PROPS, encoding="latin-1").read() if os.path.exists(java.PROPS) else None
out = cli.java_command("enable", props)
txt = open(props, encoding="latin-1").read()
c["enable adds the bridge next to another assistive technology"] = "assistive_technologies=com.example.Magnifier,com.sun.java.accessibility.AccessBridge" in txt
c["enable keeps every foreign line"] = "# user settings" in txt and "some.other=1" in txt
c["status reports it on"] = java.props_state(props) == "enabled" and any("enabled" in l for l in out)
cli.java_command("enable", props)
c["enabling twice does not duplicate"] = open(props, encoding="latin-1").read().count("AccessBridge") == 1
cli.java_command("disable", props)
txt = open(props, encoding="latin-1").read()
c["disable removes only the bridge"] = "AccessBridge" not in txt and "com.example.Magnifier" in txt and "some.other=1" in txt
p2 = os.path.join(tmp, "only-ours.properties")
cli.java_command("enable", p2); cli.java_command("disable", p2)
c["a file that only had our lines is removed again"] = not os.path.exists(p2)
real_after = open(java.PROPS, encoding="latin-1").read() if os.path.exists(java.PROPS) else None
c["the user's real Java settings were not touched by this test"] = real_before == real_after

c["a Java launch gets the bridge for that process only"] = "assistive_technologies" in (java.launch_env('javaw -jar app.jar') or {}).get("JAVA_TOOL_OPTIONS", "")
c["... also .jar and .jnlp files"] = java.launch_env('"C:\\x\\forms.jnlp"') is not None
c["a non-Java program gets nothing"] = java.launch_env("notepad.exe") is None
c["the client DLL is found on this machine"] = bool(java.find_dlls())

# live: the Swing app WITHOUT the bridge -> guidance + pixels; nothing breaks
jdk = os.path.dirname(java.find_dlls()[0])
ev = os.path.join(tmp, "ev.txt"); title = f"PCC Java Off {os.getpid()}"
env = dict(os.environ); env.pop("JAVA_TOOL_OPTIONS", None)
r = core.open_behind(f'"{os.path.join(jdk, "javaw.exe")}" -cp "{os.path.join(ROOT, "tests", "java_app")}" PccSwingTest "{ev}" "{title}"', "javaw", env=env)
hwnd = next((w["hwnd"] for w in r["windows"] if w["title"] == title), None)
try:
    time.sleep(1.0)
    c["a Java window without the bridge is recognised as such"] = hwnd is not None and core.surface(hwnd) == "java-off"
    res = core.look(str(hwnd))
    c["look tells the model exactly how to turn it on"] = "pc-control java enable" in res.get("note", "")
    c["... and still reads the window from its pixels"] = res["mode"] in ("ocr", "uia+ocr", "image") and ("Saludar" in (res.get("text") or "") or res["mode"] == "image")
finally:
    if hwnd and win32gui.IsWindow(hwnd):
        win32gui.PostMessage(hwnd, 0x0010, 0, 0)

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"java setup verification passed ({len(c)} checks)" if not bad else f"java setup verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
