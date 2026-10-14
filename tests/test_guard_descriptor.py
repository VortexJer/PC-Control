"""tools/guard_descriptor.py: the Guard descriptor is regenerated from the live tool list. A new tool must be classified (inherit or
review) before a release: this test fails until it is, which is what keeps Guard's entry from going stale."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import guard_descriptor as gd

SAMPLE = '''{
  "name": "PC-Control",
  "tools": [
    { "name": "windows", "state": "inherit" },
    { "name": "click", "state": "review" },
    { "name": "other", "state": "review" }
  ],
  "saferAlternatives": [
    "Keep click on review: they can press buttons, write text and start programs.",
    "Install an exact version from PyPI, for example pip install claude-pc-control==0.1.0, and run pc-control status."
  ]
}
'''
c = {}
live = gd.server_tools()
inherit, review, unknown = gd.classify(live)
c["every tool of the server has a decision (add new ones to INHERIT or REVIEW in tools/guard_descriptor.py)"] = unknown == []
c["no tool is listed that the server does not have (a removed tool leaves the table)"] = set(gd.INHERIT) | set(gd.REVIEW) <= set(live)
c["the tools that act are review, the ones that only look or point are inherit"] = {"click", "type", "key", "drag", "steps", "open_app"} <= set(review) and {"look", "read", "show", "wait_for"} <= set(inherit)

new = gd.update_text(SAMPLE, "9.9.9", ["windows", "wait_for"], ["click", "steps"])
c["a new tool appears in the list with its state"] = '{ "name": "wait_for", "state": "inherit" }' in new and '{ "name": "steps", "state": "review" }' in new
c["'other' stays last and review"] = new.index('"other"') > new.index('"steps"') and '{ "name": "other", "state": "review" }\n  ]' in new
c["the pinned version is updated"] = "claude-pc-control==9.9.9" in new and "0.1.0" not in new
c["the review sentence lists the review tools"] = "Keep click and steps on review:" in new
c["running it again changes nothing"] = gd.update_text(new, "9.9.9", ["windows", "wait_for"], ["click", "steps"]) == new
c["everything else in the file is left alone"] = '"name": "PC-Control"' in new and "write text and start programs." in new

i2, r2, u2 = gd.classify(live + ["brand_new_tool"])
c["a tool nobody classified is treated as review and reported"] = u2 == ["brand_new_tool"] and "brand_new_tool" in r2
try:
    gd.update_text('{"nothing": 1}', "1.0.0", [], [])
    c["a descriptor that does not look as expected is refused, not mangled"] = False
except SystemExit:
    c["a descriptor that does not look as expected is refused, not mangled"] = True

for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
ok = all(c.values())
print("guard-descriptor verification passed" if ok else "guard-descriptor verification FAILED")
sys.exit(0 if ok else 1)