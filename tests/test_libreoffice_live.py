"""LibreOffice is typed through UNO (its keys need the system focus; its document's accessible value echoes text it never applies):
Writer at Claude's own point (append, multi-line, replace = only what Claude typed), Calc's active cell and a cell by address,
Math's formula - each read back from the document itself - with the apps opened behind (also when soffice is already running and
the launcher hands the window to it) and the user's foreground never moved. Skipped when LibreOffice is not installed."""
import asyncio, os, subprocess, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import win32gui
from pc_control import libreoffice

PROG = os.path.join(os.environ.get("ProgramFiles", ""), "LibreOffice", "program")
if not os.path.exists(os.path.join(PROG, "soffice.exe")):
    print("libreoffice live verification passed (skipped: LibreOffice is not installed)")
    sys.exit(0)

tmp = tempfile.mkdtemp(prefix="pcc_lo_")
checks = {}


def text(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def kill():
    for im in ("soffice.bin", "soffice.exe"):
        subprocess.run(["taskkill", "/IM", im, "/F"], capture_output=True)


async def main(call):
    fg0 = win32gui.GetForegroundWindow()
    hw = {}
    for app in ("swriter", "scalc", "smath"):          # the 2nd and 3rd are handed to the soffice the 1st started
        _, o = await call("open_app", command=f'"{os.path.join(PROG, app + ".exe")}" --norestore', behind=True)
        import re
        m = re.search(r"\| (\d+) ", o)
        hw[app] = m.group(1) if m else ""
        time.sleep(4)
    checks["the three apps opened behind, each with its own window"] = all(hw.values()) and len(set(hw.values())) == 3
    _, a = await call("type", window=hw["swriter"], text="Hola LibreOffice")
    _, b = await call("type", window=hw["swriter"], text=" y adios\nlinea dos")
    _, c = await call("type", window=hw["swriter"], text=" (cambiado)", replace=True)
    doc = libreoffice.document(int(hw["swriter"]))
    body = doc.getText().getString().replace("\r\n", "\n") if doc else ""
    checks["Writer: typing confirmed by reading the document back"] = all("read back and confirmed" in x for x in (a, b, c))
    checks["Writer: append + replace changed only what Claude typed"] = body.strip() == "Hola LibreOffice (cambiado)"
    _, d = await call("type", window=hw["scalc"], text="=40+2")
    _, e = await call("type", window=hw["scalc"], target="B3", text="hola celda")
    sheet = libreoffice.document(int(hw["scalc"])).getCurrentController().getActiveSheet()
    checks["Calc: the active cell takes a formula (shows 42)"] = "«42»" in d and sheet.getCellRangeByName("A1").getValue() == 42
    checks["Calc: a cell by address, the selection untouched"] = sheet.getCellRangeByName("B3").getString() == "hola celda" and "cell B3" in e
    _, f = await call("type", window=hw["smath"], text="a over b")
    checks["Math: the formula, read back"] = "read back and confirmed" in f and libreoffice.document(int(hw["smath"])).Formula == "a over b"
    _, r1 = await call("read", window=hw["swriter"])
    _, r2 = await call("read", window=hw["scalc"])
    checks["read gives the Writer document and Calc's cells (not a toolbar box)"] = (
        "Hola LibreOffice (cambiado)" in r1 and "42" in r2 and "hola celda" in r2)
    _, g = await call("type", window=hw["swriter"], target="B3", text="x")
    checks["a cell address in Writer is refused, nothing typed"] = "did not type" in g
    checks["the foreground never moved"] = win32gui.GetForegroundWindow() == fg0


async def session(fn):
    params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                                   env={**os.environ, "PC_CONTROL_HOME": tmp, "PC_CONTROL_MODE": "bypass", "PYTHONUTF8": "1"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            async def call(tool, **a):
                res = await s.call_tool(tool, a)
                return bool(getattr(res, "isError", False)), text(res)
            return await fn(call)


kill()
try:
    asyncio.run(session(main))
finally:
    kill()
for k, v in checks.items():
    print(("ok   " if v else "FAIL ") + k)
if len(checks) < 9 or not all(checks.values()):
    sys.exit(1)
print(f"libreoffice live verification passed ({len(checks)} checks)")
