"""Keeps PC-Control's entry in HOL Guard in step with the tools this server really has.

Guard lists PC-Control in `contributions/mcp-servers/mcp.pc-control.json` (in the hashgraph-online/hol-guard repo): one line per tool
with its state (inherit = Guard's default, review = Guard asks), and the version it recommends. When a tool is added here, that file goes
stale. This script rewrites ONLY those parts of the file from the live server, so the diff is small and nothing else is touched.

    python tools/guard_descriptor.py --guard-repo PATH            # show what would change
    python tools/guard_descriptor.py --guard-repo PATH --check    # exit 1 if it is out of date (for a release checklist)
    python tools/guard_descriptor.py --guard-repo PATH --write    # update the file, then run Guard's own trust check

It never opens a pull request: sending the change to Guard stays a human decision.
"""
import argparse, difflib, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DESCRIPTOR = os.path.join("contributions", "mcp-servers", "mcp.pc-control.json")
# One decision per tool, here and only here. Tools that only look, read or point are inherit; anything that presses, types, runs a
# shortcut, drags or starts a program is review. A NEW tool is review until someone decides otherwise (and tests/test_guard_descriptor.py
# fails until it is listed below).
INHERIT = ("windows", "look", "read", "show", "scroll", "wait_for")
REVIEW = ("click", "type", "key", "drag", "steps", "open_app")
CATCH_ALL = "other"                                  # Guard's name for "any tool not listed": always review, always last


def server_tools():
    from pc_control.server import mcp
    return sorted(mcp._tool_manager._tools)


def classify(names):
    """(inherit, review, unknown): ordered like the tables above; unknown tools are treated as review, the cautious choice."""
    names = set(names)
    unknown = sorted(names - set(INHERIT) - set(REVIEW))
    return [n for n in INHERIT if n in names], [n for n in REVIEW if n in names] + unknown, unknown


def _join(names):
    if not names:
        return "no tool"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def update_text(text, version, inherit, review):
    """The descriptor with the tool list, the 'keep ... on review' sentence and the pinned version brought up to date."""
    rows = [f'    {{ "name": "{n}", "state": "inherit" }},' for n in inherit] + [f'    {{ "name": "{n}", "state": "review" }},' for n in review]
    rows.append(f'    {{ "name": "{CATCH_ALL}", "state": "review" }}')
    out, n1 = re.subn(r'("tools": \[\n)(.*?)(\n  \],)', lambda m: m.group(1) + "\n".join(rows) + m.group(3), text, count=1, flags=re.S)
    out, n2 = re.subn(r"Keep [^\"]*? on review:", f"Keep {_join(review)} on review:", out, count=1)
    out, n3 = re.subn(r"claude-pc-control==[0-9][0-9.]*", f"claude-pc-control=={version}", out)
    if not (n1 and n2 and n3):
        raise SystemExit("the descriptor does not look as expected (tool list, 'Keep ... on review' sentence or version pin missing): update it by hand")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Update Guard's PC-Control descriptor from the live tool list.")
    ap.add_argument("--guard-repo", required=True, help="a checkout of hashgraph-online/hol-guard (or your fork)")
    ap.add_argument("--version", help="version to pin (default: this checkout's)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="exit 1 if the descriptor is out of date")
    g.add_argument("--write", action="store_true", help="write the change and run Guard's trust check")
    a = ap.parse_args(argv)
    path = os.path.join(a.guard_repo, DESCRIPTOR)
    if not os.path.exists(path):
        raise SystemExit(f"not found: {path}")
    version = a.version
    if not version:
        import pc_control
        version = pc_control.__version__
    inherit, review, unknown = classify(server_tools())
    if unknown:
        print(f"NEW tool(s) without a decision: {', '.join(unknown)} -> listed as review; add them to INHERIT or REVIEW in this script", file=sys.stderr)
    old = open(path, encoding="utf-8", newline="").read()
    eol = "\r\n" if "\r\n" in old else "\n"
    new = update_text(old.replace("\r\n", "\n"), version, inherit, review).replace("\n", eol)
    if new == old:
        print(f"in sync (version {version}, {len(inherit) + len(review)} tools)")
        return 0
    sys.stdout.writelines(difflib.unified_diff(old.replace("\r\n", "\n").splitlines(True), new.replace("\r\n", "\n").splitlines(True), "current", "updated"))
    if a.write:
        open(path, "w", encoding="utf-8", newline="").write(new)
        print(f"\nwritten: {path}")
        script = os.path.join(a.guard_repo, "scripts", "refresh_extension_artifacts.py")
        if os.path.exists(script):
            r = subprocess.run([sys.executable, script, "--check-trust"], cwd=a.guard_repo, env={**os.environ, "PYTHONPATH": "src"},
                               capture_output=True, text=True)
            print("Guard trust check:", (r.stdout + r.stderr).strip()[-300:] or r.returncode)
            return r.returncode
        return 0
    return 1 if a.check else 0


if __name__ == "__main__":
    sys.exit(main())