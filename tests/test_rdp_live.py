"""Remote Desktop live, through the REAL MCP server: mstsc connects to a test RDP server (xrdp in the isolated WSL distro 'pcc-rdp', see
docs/RESEARCH-JAVA-RDP.md), and Claude's tools work INSIDE the remote session: the login screen is read (OCR of a covered window),
credentials are typed, a remote button is clicked, text is typed into a remote editor, the view is scrolled - all as input messages to
the client, with pixel verification. The user's foreground window, mouse and keyboard are checked to stay theirs.

Needs: WSL distro 'pcc-rdp' with xrdp on port 3390 and user pcc (tools/rdp_testenv.py sets it up). Without it: FAILS (not skipped)."""
import asyncio, os, re, socket, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import win32api, win32gui, win32process
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pc_control import core

c = {}


def port_open():
    try:
        socket.create_connection(("localhost", 3390), 1).close(); return True     # WSL forwards localhost (IPv6 here)
    except OSError:
        return False


keep = subprocess.Popen(["wsl.exe", "-d", "pcc-rdp", "-u", "root", "--", "bash", "-c", "systemctl start xrdp; exec sleep 900"],
                        creationflags=0x08000000, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(180):                                     # a cold WSL start (systemd, xrdp) can take a minute
    if port_open():
        break
    time.sleep(0.5)
c["test RDP server reachable (xrdp in WSL distro pcc-rdp, port 3390)"] = port_open()


def text_of(res):
    return "\n".join(x.text for x in res.content if getattr(x, "type", "") == "text")


async def call(s, tool, **args):
    try:
        res = await s.call_tool(tool, args)
        return bool(getattr(res, "isError", False)), text_of(res)
    except Exception as e:
        return True, str(e)


def mstsc_windows():
    out = {}
    for h in core.top_windows():
        try:
            if core.psutil_name(win32process.GetWindowThreadProcessId(h)[1]) == "mstsc":
                out[h] = (win32gui.GetClassName(h), win32gui.GetWindowText(h))
        except Exception:
            pass
    return out


def ids(look_text):
    return {m.group(2).strip(): m.group(1) for m in re.finditer(r"\[(\d+):([^\]=]+)", look_text)}


async def run(s):
    out = {}
    out["open"] = await call(s, "open_app", command="mstsc /v:localhost:3390 /w:1024 /h:700")
    session = None
    for _ in range(60):                                      # the certificate prompt of OUR test server: accepted for this connection only
        ws = mstsc_windows()
        sess = [h for h, (cls, _) in ws.items() if cls == "TscShellContainerClass"]
        if sess:
            session = sess[0]; break
        dlg = [h for h, (cls, t) in ws.items() if cls == "#32770"]
        for h in dlg:
            f = await call(s, "look", window=str(h), find="Sí|Yes")
            out.setdefault("cert_find", f)
            i = re.search(r"\[(\d+):(S[ií]|Yes)\]", f[1])
            if i:
                out["cert"] = await call(s, "click", window=str(h), target=i.group(1))
        await asyncio.sleep(0.5)
    out["session"] = session
    if not session:
        return out
    await asyncio.sleep(2.5)
    w = str(session)
    out["windows"] = await call(s, "windows", filter="mstsc")
    out["look_login"] = await call(s, "look", window=w)
    out["type_user"] = await call(s, "type", window=w, text="pcc")
    out["tab"] = await call(s, "key", window=w, name="tab")
    out["type_pass"] = await call(s, "type", window=w, text="pcc-test-2026")
    out["enter"] = await call(s, "key", window=w, name="enter")
    for _ in range(20):
        await asyncio.sleep(1)
        lk = await call(s, "look", window=w, mode="text")
        if "Aceptar remoto" in lk[1] or "Remote test window" in lk[1]:
            break
    out["look_desktop"] = lk
    i = ids(lk[1])
    btn = next((v for k, v in i.items() if "Aceptar remoto" in k), None)
    out["click_btn"] = await call(s, "click", window=w, target=btn) if btn else (True, "no button id")
    await asyncio.sleep(1.0)
    out["after_btn"] = await call(s, "look", window=w, mode="text")
    # the editor (mousepad) has the remote focus once the dialog is gone: type numbered lines, then scroll back up
    lines = "".join(f"linea remota {n:02d}\n" for n in range(1, 41)) + "FIN DEL TEXTO REMOTO"
    out["type_text"] = await call(s, "type", window=w, text=lines)
    await asyncio.sleep(1.0)
    out["after_type"] = await call(s, "look", window=w, mode="text")
    out["scroll_up"] = await call(s, "scroll", window=w, direction="up", amount=10)
    await asyncio.sleep(1.0)
    out["after_scroll"] = await call(s, "look", window=w, mode="text")
    out["read"] = await call(s, "read", window=w)
    return out


fg0, cur0, tick0 = win32gui.GetForegroundWindow(), win32api.GetCursorPos(), core._input_tick()
tmp = tempfile.mkdtemp()
params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                               env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": "bypass",
                                    "PYTHONUTF8": "1", "PC_CONTROL_CURSOR": "off"})


async def main():
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await run(s)

out = {}
try:
    if c["test RDP server reachable (xrdp in WSL distro pcc-rdp, port 3390)"]:
        out = asyncio.run(main())
finally:
    for h in mstsc_windows():
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(win32process.GetWindowThreadProcessId(h)[1])], capture_output=True)
        except Exception:
            pass
    keep.kill()
for k in ("open", "cert_find", "cert", "look_login", "type_user", "type_pass", "enter", "look_desktop", "click_btn", "after_btn", "type_text", "after_type", "scroll_up", "after_scroll"):
    if k in out:
        print(f"   {k}: {out[k][1][:260]!r}")
c["mstsc opened by name, behind, without the focus"] = "open" in out and not out["open"][0] and "took the keyboard focus" not in out["open"][1]
c["the RDP session window appeared"] = bool(out.get("session"))
c["windows() marks the session as a remote screen"] = "[remote: Remote Desktop]" in out.get("windows", (0, ""))[1]
c["look reads the remote login screen from a COVERED window (OCR)"] = "username" in out.get("look_login", (0, ""))[1].lower()
c["look says how the remote screen is operated"] = "remote session" in out.get("look_login", (0, ""))[1]
c["typing the user name went into the remote session (pixels changed)"] = "effect observed on screen" in out.get("type_user", (0, ""))[1]
c["the login worked: the remote desktop shows its window"] = "Aceptar remoto" in out.get("look_desktop", (0, ""))[1] or "Remote test window" in out.get("look_desktop", (0, ""))[1]
c["a remote button found by OCR is clicked (the dialog closed)"] = not out.get("click_btn", (1, ""))[0] and "Aceptar remoto" not in out.get("after_btn", (0, "Aceptar remoto"))[1]
c["text typed into the remote editor shows up on the remote screen"] = "FIN DEL TEXTO REMOTO" in out.get("after_type", (0, ""))[1]
c["scroll moves the remote view (earlier lines visible again)"] = re.search(r"linea remota 0[1-9]", out.get("after_scroll", (0, ""))[1]) is not None and "effect observed" in out.get("scroll_up", (0, ""))[1]
c["read gives the remote screen's text (OCR)"] = "linea remota" in out.get("read", (0, ""))[1]
c["no loop guard or false success in the run"] = not any("REPEATED FAILURE" in v[1] for v in out.values() if isinstance(v, tuple))
user = core._input_tick() != tick0
c["the user's foreground window was never taken (or the user changed it themselves)"] = win32gui.GetForegroundWindow() == fg0 or user
c["the user's mouse never moved (or the user moved it)"] = win32api.GetCursorPos() == cur0 or user

bad = [k for k, v in c.items() if not v]
for k, v in c.items():
    print(("OK   " if v else "FAIL ") + k)
print(f"rdp live verification passed ({len(c)} checks)" if not bad else f"rdp live verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
