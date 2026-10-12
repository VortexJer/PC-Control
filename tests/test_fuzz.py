"""Bad and odd inputs through the real MCP protocol: every answer must be a clear sentence, never a Python crash
(AttributeError, TypeError, KeyError, IndexError, ValueError...), and nothing may hang. The test app runs on the hidden desktop."""
import asyncio, os, re, shutil, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, ROOT)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

tmp = tempfile.mkdtemp(prefix="pc-control_fuzz_")
host = os.path.join(tmp, "pc_control_host.exe")            # powershell under another name (the real one is a protected terminal)
shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), host)
state = os.path.join(tmp, "s.json")
CRASH = re.compile(r"\b(AttributeError|TypeError|KeyError|IndexError|ValueError|NameError|UnboundLocalError|ZeroDivisionError|"
                   r"RecursionError|AssertionError|Traceback|NoneType|object has no attribute|com_error)\b")
results = []
NAMED = []


def text_of(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


async def call(s, tool, **args):
    t0 = time.time()
    try:
        res = await asyncio.wait_for(s.call_tool(tool, args), 90)
        out = text_of(res)
    except asyncio.TimeoutError:
        out = "HUNG (no answer in 90 s)"
    except Exception as e:
        out = f"CLIENT {e.__class__.__name__}: {e}"
    results.append((tool, args, out, round(time.time() - t0, 1)))
    return out


async def run(s):
    w = "pc-control-test2"
    out = await call(s, "open_app", command=f'"{host}" -STA -NoProfile -ExecutionPolicy Bypass -File "{os.path.join(HERE, "test_app2.ps1")}" "{state}"', hidden=True)
    for _ in range(30):
        if "pc-control-test2" in await call(s, "windows"):
            break
        await asyncio.sleep(0.5)
    await call(s, "look", window=w)
    cases = [
        ("look", dict(window="no-such-window-xyz")),
        ("look", dict(window="")),
        ("look", dict(window=w, mode="banana")),
        ("look", dict(window=w, find="")),
        ("look", dict(window=w, find="|||")),
        ("look", dict(window=w, find="!")),
        ("look", dict(window=w, find="City 7", wait=-5)),
        ("look", dict(window=w, find="zzz", wait=1)),
        ("look", dict(window="99999999")),
        ("click", dict(window=w, target="9999")),
        ("click", dict(window=w, target="abc")),
        ("click", dict(window=w, target="")),
        ("click", dict(window=w, target="10,")),
        ("click", dict(window=w, target="-5,-5")),
        ("click", dict(window=w, target="1.5")),
        ("type", dict(window=w, target="9999", text="x")),
        ("type", dict(window=w, target="abc", text="x")),
        ("type", dict(window=w, target="A1", text="x")),
        ("type", dict(window=w, target="1", text="")),
        ("type", dict(window=w, target="1", text="emoji 😀 ñ 中文 ​")),
        ("key", dict(window=w, name="")),
        ("key", dict(window=w, name="ctrl+")),
        ("key", dict(window=w, name="+")),
        ("key", dict(window=w, name="ctrl+alt+shift+win+q")),
        ("key", dict(window=w, name="banana")),
        ("key", dict(window=w, name="F13")),
        ("scroll", dict(window=w, direction="sideways")),
        ("scroll", dict(window=w, direction="down", amount=-3)),
        ("scroll", dict(window=w, direction="down", amount=100000)),
        ("scroll", dict(window=w, direction="down", target="9999")),
        ("scroll", dict(window=w, direction="down", target="abc")),
        ("scroll", dict(window=w, direction="DOWN")),
        ("read", dict(window=w, target="9999")),
        ("read", dict(window=w, target="abc")),
        ("read", dict(window=w, start=-10, length=-1)),
        ("read", dict(window=w, start=10**9)),
        ("drag", dict(window=w, points="")),
        ("drag", dict(window=w, points="1,1")),
        ("drag", dict(window=w, points="a,b;c,d")),
        ("drag", dict(window=w, points="10,10;20,20", shape="hexagon")),
        ("open_app", dict(command="")),
        ("open_app", dict(command="no_such_program_xyz.exe")),
        ("open_app", dict(command='"unterminated')),
    ]
    for tool, args in cases:
        await call(s, tool, **args)
    # targets by NAME (what a cheaper model reaches for first)
    await call(s, "look", window=w)
    NAMED.append(("click by exact name", (await call(s, "click", window=w, target="Deep button")).startswith("ok")))
    NAMED.append(("type on a drop-down by its name", "Green" in await call(s, "type", window=w, target="Color", text="green")))
    NAMED.append(("an ambiguous name lists the ids instead of guessing", "elements match" in await call(s, "click", window=w, target="Cit")
                  or "elements match" in results[-1][2]))
    NAMED.append(("a name found only out of view still works", (await call(s, "click", window=w, target="City 77")).startswith("ok")))
    NAMED.append(("an unknown name says so", "no element named" in await call(s, "click", window=w, target="Nope Button")))
    out = await call(s, "read", window=w, target="Long text", find="Line 77 ")
    NAMED.append(("read(find=) returns only the matching lines, numbered", out.count("Line 77 of the long text") == 1 and "77: " in out and "Line 78" not in out))
    # the window closes in the middle: every tool must say so, not crash
    await call(s, "key", window=w, name="alt+f4")
    await asyncio.sleep(1.5)
    for tool, args in (("look", dict(window=w)), ("click", dict(window=w, target="1")), ("scroll", dict(window=w)),
                       ("read", dict(window=w)), ("type", dict(window=w, target="1", text="x"))):
        await call(s, tool, **args)


params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                               env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": "bypass", "PYTHONUTF8": "1"})


async def main():
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            await run(s)

asyncio.run(main())
bad = [(t, a, o, d) for t, a, o, d in results if CRASH.search(o) or o.startswith(("HUNG", "CLIENT")) or d > 60]
for name, ok in NAMED:
    print(("OK   " if ok else "FAIL ") + name)
bad += [("named", {}, n, 0) for n, ok in NAMED if not ok]
for t, a, o, d in results:
    flag = "BAD " if (t, a, o, d) in bad else "ok  "
    print(f"{flag}{d:5}s {t}({', '.join(f'{k}={v!r}' for k, v in a.items() if k != 'command')[:60]}) -> {o[:150]!r}")
shutil.rmtree(tmp, ignore_errors=True)
print(f"fuzz verification {'passed' if not bad else 'FAILED'} ({len(results) - len(bad)}/{len(results)} clean answers)")
sys.exit(0 if not bad else 1)
