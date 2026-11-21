"""Run a batch of PC-Control tool calls through the REAL MCP server (a fresh `python -m pc_control.server` of this repo).

  python tools/mcp_batch.py calls.json        calls.json = [["open_app", {"command": "..."}], ["look", {"window": "..."}], ...]
  python tools/mcp_batch.py - < calls.json

A value "$name" in arguments is replaced by a capture: a call can be ["look", {...}, {"save": "name", "regex": "\\[(\\d+):Save"}].
Prints each call, whether it was an error and its text (cut to --width chars). Used for hands-on checks and by tools/app_matrix.py.
"""
import asyncio, json, os, re, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def text_of(res):
    return "\n".join(x.text for x in res.content if getattr(x, "type", "") == "text")


async def run_calls(calls, env=None, mode="bypass", on_result=None):
    tmp = tempfile.mkdtemp()
    params = StdioServerParameters(command=sys.executable, args=["-m", "pc_control.server"], cwd=ROOT,
                                   env={**os.environ, "PC_CONTROL_HOME": os.path.join(tmp, "home"), "PC_CONTROL_MODE": mode,
                                        "PYTHONUTF8": "1", **(env or {})})
    out, saved = [], {}
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for item in calls:
                tool, args = item[0], dict(item[1])
                opts = item[2] if len(item) > 2 else {}
                if tool == "sleep":
                    await asyncio.sleep(args.get("s", 1)); continue
                args = {k: (saved.get(v[1:], v) if isinstance(v, str) and v.startswith("$") else v) for k, v in args.items()}
                t0 = time.time()
                try:
                    res = await s.call_tool(tool, args)
                    err, txt = bool(getattr(res, "isError", False)), text_of(res)
                except Exception as e:
                    err, txt = True, f"{e.__class__.__name__}: {e}"
                if opts.get("save"):
                    m = re.search(opts.get("regex", r"\| (\d+) "), txt)       # default: the hwnd open_app reports
                    saved[opts["save"]] = m.group(1) if m else ""
                rec = {"tool": tool, "args": args, "error": err, "text": txt, "secs": round(time.time() - t0, 2)}
                out.append(rec)
                if on_result:
                    on_result(rec)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    width = 900
    argv = sys.argv[1:]
    if "--width" in argv:
        i = argv.index("--width"); width = int(argv[i + 1]); del argv[i:i + 2]
    src = sys.stdin.read() if not argv or argv[0] == "-" else open(argv[0], encoding="utf-8").read()
    def show(r):
        print(f"--- {r['tool']} {json.dumps(r['args'], ensure_ascii=False)[:160]}  [{'ERROR' if r['error'] else 'ok'} {r['secs']}s]")
        print(r["text"][:width])
    asyncio.run(run_calls(json.loads(src), on_result=show))
