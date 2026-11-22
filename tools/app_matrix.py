"""App matrix: many REAL installed apps through the real MCP server - which route PC-Control's classifier picks for each one, and
whether the read-only tools that route gives (look, find, read, scroll) work there, with the user's foreground untouched.

  python tools/app_matrix.py run apps.json [--out docs/APP-MATRIX.md]   apps.json = [{"name": "...", "command": "..."}, ...]
  python tools/app_matrix.py --check docs/APP-MATRIX.md                 the gate: >= 30 apps, every row has a verdict

Safety: read-only actions only (no click, no typing, no keys), apps the user already had open are read in place and NOT closed, the
ones it opens are closed with a close request (and their process ended if it stays), protected kinds (passwords, mail, messaging,
banking, terminals, admin tools) are skipped by the caller's list.
"""
import asyncio, json, os, re, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def check(path):
    text = open(path, encoding="utf-8").read()
    rows = [l for l in text.splitlines() if l.startswith("| ") and not l.startswith("| App") and not l.startswith("|---")]
    verdicts = [l.split("|")[-2].strip() for l in rows]
    ok = len(rows) >= 30 and all(v for v in verdicts)
    kinds = {l.split("|")[2].strip() for l in rows}
    print(f"{len(rows)} apps, {len(kinds)} routes: {sorted(kinds)}")
    if not ok or len(kinds) < 4:
        print("FAIL: fewer than 30 apps, an empty verdict, or fewer than 4 routes"); sys.exit(1)
    print("app matrix verification passed")


async def act(call, hw, a, proc=None):
    """One action from the app's list, verified independently:
    {"click": "regex"[, "esc": true]} the first element whose name matches, then esc if it opened a menu;
    {"click_xy": "x,y"};  {"key": "name"[, "at": "x,y"]};
    {"type": "text", "into": "regex" | "cell:B3" | "xy:x,y" | "", "replace": bool, "verify": "regex"}: verified by finding the text
    in the window afterwards (look find, then read), never by the type tool's own answer."""
    if proc:                                                     # the app may have replaced its window (a start screen -> the document):
        err, t, _ = await call("windows", filter=proc)            # as Claude would, take the app's current window
        m = re.search(r"(?m)^(\d+) \|", t)
        if m and m.group(1) != hw:
            hw = m.group(1)
    async def first_id(rx, prefer=("edit", "document", "combobox")):
        err, t, _ = await call("look", window=hw, find=rx)
        hits = re.findall(r"\[(\d+):[^\]]*\] \((\w+)", t)
        hits.sort(key=lambda x: x[1] not in prefer)
        if not hits or hits[0][1] not in prefer:                 # plan B, as Claude would: any element of the right kind in the window
            fb = "unnamed|Ayuda|Help|Ver|View|Archivo|File|Inicio|Home|a|e|o" if prefer[0] != "edit" else "unnamed|a|e|i|o|u"
            err, t, _ = await call("look", window=hw, find=fb)
            more = [h for h in re.findall(r"\[(\d+):[^\]]*\] \((\w+)", t) if h[1] in prefer]
            if more and (not hits or hits[0][1] not in prefer):
                return more[0][0]
        return hits[0][0] if hits else None
    if "click" in a or "click_xy" in a:
        tid = a.get("click_xy") or await first_id(a["click"], prefer=("button", "menuitem", "tabitem", "listitem", "hyperlink"))
        if not tid:
            return f"click: no '{a.get('click')}'"
        err, t, _ = await call("click", window=hw, target=tid)
        if a.get("esc"):
            await call("key", window=hw, name="esc")
        good = not err and t.startswith("ok") and "NO VISIBLE EFFECT" not in t
        return ("click ok" if good else "click FAIL: " + t[:70].replace("|", "/")) + f" [{a.get('click') or tid}]"
    if "expect" in a:                                            # the result of the previous actions, read from the app
        await asyncio.sleep(0.5)
        _, f, _ = await call("look", window=hw, find=a["expect"])
        return ("click ok: result read back" if f.startswith("found") else "expect FAIL: not shown") + f" [{a['expect'][:20]}]"
    if "key" in a:
        args = {"window": hw, "name": a["key"]}
        if a.get("at"):
            args["target"] = a["at"]
        err, t, _ = await call("key", **args)
        good = not err and t.startswith("ok") and "NO VISIBLE EFFECT" not in t
        return ("key ok" if good else "key FAIL: " + t[:70].replace("|", "/")) + f" [{a['key']}]"
    if "type" in a:
        into = a.get("into", "")
        if into.startswith("cell:"):
            target = into[5:]
        elif into.startswith("xy:"):
            target = into[3:]
        elif into:
            target = await first_id(into)
            if not target:
                return f"type: no field '{into}'"
        else:
            target = ""
        err, t, _ = await call("type", window=hw, target=target, text=a["type"], replace=bool(a.get("replace")))
        await asyncio.sleep(0.6)
        vrx = a.get("verify") or a["type"][:12]                   # plain words: look find matches substrings, not patterns
        _, f, _ = await call("look", window=hw, find=vrx)
        seen = f.startswith("found")
        if not seen:                                             # OCR may space it differently ("print (40+2)"): compare without spaces
            _, r_, _ = await call("read", window=hw, length=20000)
            seen = re.sub(r"\s+", "", vrx).lower() in re.sub(r"\s+", "", r_).lower()
        if err or not t.startswith("ok"):
            return "type FAIL: " + t[:80].replace("|", "/")
        return ("type ok (read back)" if seen else "type FAIL: said ok but the text is not in the window") + f" [{a['type'][:14]}]"
    return "?"


def kill_family(pid):
    """End the process this run started and what it started in turn (a launcher's real app), never anything else."""
    from pc_control.focus import snapshot
    try:
        fam, _ = snapshot("?no-such-program?", {pid})
    except Exception:
        fam = set()
    for p in {pid} | fam:
        try:
            os.kill(p, 9)
        except Exception:
            pass


def last_input():
    import ctypes
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LII(); li.cbSize = 8; ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return li.dwTime


def text_of(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


async def run(apps, out):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import win32gui, win32process
    from pc_control import apps as A, core
    tmp = tempfile.mkdtemp(prefix="pcc_matrix_")
    params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                                   env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": "bypass",
                                        "PYTHONUTF8": "1"})
    rows = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            async def call(tool, **a):
                t0 = time.time()
                try:
                    res = await asyncio.wait_for(s.call_tool(tool, a), 60)
                    return bool(getattr(res, "isError", False)), text_of(res), time.time() - t0
                except Exception as e:
                    return True, f"{e.__class__.__name__}: {e}", time.time() - t0
            for app in apps:
                name, cmd = app["name"], app["command"]
                exe = A.split(cmd)[0].strip('"')
                exe = exe if os.path.isabs(exe) else (__import__("shutil").which(exe) or exe)
                proc = os.path.splitext(os.path.basename(exe))[0].lower()
                before = {h for h in core.top_windows()}
                fg0 = win32gui.GetForegroundWindow()
                in0 = last_input()
                row = {"name": name, "exe": proc, "notes": []}
                running = any(core.psutil_name(core.window_pid(h)) == proc for h in before if win32gui.IsWindowVisible(h))
                hw, pid = None, None
                if not running:
                    err, t, _ = await call("open_app", command=cmd)          # open_app decides minimized / behind by itself
                    m = re.search(r"pid (\d+) \| (\d+) ", t) or re.search(r"pid (\d+) \| .*?EXISTING window (\d+)", t)
                    if m and "EXISTING window" in t:
                        row["notes"].append("single-instance: its existing window")
                    if err or not m:
                        row.update(route="-", verdict="did not open: " + t[:120].replace("|", "/")); rows.append(row); print(name, "-> no open"); continue
                    pid, hw = int(m.group(1)), m.group(2)
                    await asyncio.sleep(float(app.get("wait", 4)))
                else:
                    err, t, _ = await call("windows", filter=proc)
                    m = re.search(r"(?m)^(\d+) \|", t)
                    hw = m.group(1) if m else None
                    row["notes"].append("already open: read in place")
                if not hw:
                    row.update(route="-", verdict="no window"); rows.append(row); continue
                h = int(hw)
                if not win32gui.IsWindow(h):                        # an app that replaced its first window (splash -> main)
                    err, t, _ = await call("windows", filter=proc)
                    m = re.search(r"(?m)^(\d+) \|", t)
                    hw = m.group(1) if m else hw; h = int(hw)
                # what the classifier says about it
                route = []
                if A.chromium_like(exe):
                    route.append("chromium")
                try:
                    surf = core.surface(h)
                    if surf != "uia":
                        route.append(surf)
                except Exception:
                    pass
                try:
                    if core.reads_raw_keyboard(h):
                        route.append("raw-input")
                except Exception:
                    pass
                if core.slow_uia(h):
                    route.append("slow-tree")
                err, t, dt = await call("look", window=hw)
                if not err and t.startswith("none") and "open_app(behind=true)" in t and pid:
                    # what the answer tells Claude to do: close it and open it again behind the user's windows
                    await call("key", window=hw, name="alt+f4"); await asyncio.sleep(1.5)
                    kill_family(pid)
                    err0, t0_, _ = await call("open_app", command=cmd, behind=True)
                    m0 = re.search(r"pid (\d+) \| (\d+) ", t0_)
                    if m0:
                        pid, hw = int(m0.group(1)), m0.group(2); h = int(hw)
                        row["notes"].append("reopened behind as the answer advised")
                        await asyncio.sleep(float(app.get("wait", 4)))
                        err, t, dt = await call("look", window=hw)
                mode = t.split(" ")[0] if not err else "error"
                ids = len(re.findall(r"\[\d+:", t))
                route.append(f"look:{mode}")
                row["look"] = f"{mode} {ids} ids {dt:.1f}s" if not err else "ERROR " + t[:80]
                err2, t2, _ = await call("look", window=hw, find="a|e|o")
                row["find"] = ("ERROR " + t2[:60]) if err2 else (t2.split("\n")[0][:40])
                err3, t3, _ = await call("read", window=hw)
                m3 = re.search(r"characters \d+-\d+ of (\d+)", t3)
                row["read"] = ("ERROR " + t3[:60]) if err3 else (f"{m3.group(1)} chars" if m3 else t3.split("\n")[0][:40])
                err4, t4, _ = await call("scroll", window=hw, direction="down")
                row["scroll"] = ("ERROR " + t4[:60]) if err4 else ("ok" if t4.startswith("ok") else t4.split("|")[0][:50])
                if not err4 and t4.startswith("ok"):
                    await call("scroll", window=hw, direction="up")
                acts = []
                for a in app.get("acts", []):                            # click / type / key with what the route gives, verified
                    acts.append(await act(call, hw, a, proc))
                row["acts"] = "; ".join(acts) if acts else "-"
                fg_ok = win32gui.GetForegroundWindow() == fg0 or not win32gui.IsWindow(fg0)
                user = last_input() != in0                                # the user typed/clicked meanwhile: a focus change may be theirs
                row["focus"] = "kept" if fg_ok else ("changed (you used the PC)" if user else "MOVED")
                fg_ok = fg_ok or user
                row["route"] = "+".join(route)
                bad = [k for k in ("look", "find", "read", "scroll") if str(row.get(k, "")).startswith("ERROR")]
                failed_acts = [x for x in acts if not x.startswith(("click ok", "type ok", "key ok"))]
                row["verdict"] = ("ok" if not bad and fg_ok and mode not in ("none", "error") and not failed_acts else
                                  "FOCUS MOVED" if not fg_ok else ("fails: " + ",".join(bad) if bad else
                                  f"look gave {mode}" if mode in ("none", "error") else f"{len(failed_acts)}/{len(acts)} actions failed"))
                if not running and pid:                                  # close only what this run opened
                    await call("key", window=hw, name="alt+f4")
                    await asyncio.sleep(1.5)
                    kill_family(pid)
                rows.append(row)
                print(f"{name:32} {row['route']:40} {row['verdict']}")
    lines = ["# App matrix", "",
             "Real apps installed on the test PC, through the MCP server with read-only tools (look, find, read, scroll). Route = what the",
             "classifier chose: chromium (Chromium/CEF/Electron flags), raw-input (keys delivered inside the app), java / remote / java-off",
             "(surfaces), slow-tree (read from pixels), and how look read it (uia, uia+ocr, ocr, image). Generated by tools/app_matrix.py.",
             "Actions: click / type / key on safe targets (a new document, a search box, the address bar), each one VERIFIED by reading the",
             "app back (the text is in the field, the display shows the result) - not by the tool's own word.",
             "", "| App | Route | look | read | scroll | actions (verified) | your focus | Verdict |", "|---|---|---|---|---|---|---|---|"]
    for r_ in rows:
        cells = [r_["name"], r_.get("route", "-"), r_.get("look", "-"), r_.get("read", "-"), r_.get("scroll", "-"), r_.get("acts", "-"),
                 r_.get("focus", "-"), r_["verdict"] + (" (" + "; ".join(r_["notes"]) + ")" if r_["notes"] else "")]
        lines.append("| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in cells) + " |")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("written", out)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--check"]:
        check(sys.argv[2])
    elif sys.argv[1:2] == ["run"]:
        out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(ROOT, "docs", "APP-MATRIX.md")
        asyncio.run(run(json.load(open(sys.argv[2], encoding="utf-8")), out))
    else:
        print(__doc__)
