"""The README's tested-apps table stays honest: the apps the public asked for are listed, every "verified" row points to a test file
that exists (or a dated hands-on check), and nothing untested is called verified."""
import os, re, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
sec = text[text.index("## Tested with"):]
sec = sec[:sec.index("\n## ", 5)]
rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in sec.splitlines()
        if line.startswith("|") and not line.startswith("|---") and not line.startswith("| App ")]
checks = {}
names = " ".join(r[0] for r in rows)
for want in ("Word", "Excel", "PowerPoint", "Brave", "Spotify", "Blender", "Acrobat", "Premiere"):
    checks[f"'{want}' is in the table"] = want in names
bad = []
for app, kind, status, how in rows:
    if status.startswith("verified"):
        files = re.findall(r"`(test_\w+\.py)`", how)
        dated = re.search(r"\d{4}-\d{2}-\d{2}", status) and "hands-on" in how
        if not files and not dated:
            bad.append(f"{app}: verified without a test or a dated hands-on check")
        for f in files:
            if not os.path.exists(os.path.join(ROOT, "tests", f)):
                bad.append(f"{app}: {f} does not exist")
checks["every verified row is backed (test file exists or dated hands-on)"] = not bad
checks["Premiere is not claimed as verified"] = all(not (r[0].startswith("Adobe Premiere") and r[2].startswith("verified")) for r in rows)
checks["the table has rows"] = len(rows) >= 10
for k, v in checks.items():
    print(("ok   " if v else "FAIL ") + k)
for b in bad:
    print("   " + b)
if not all(checks.values()):
    sys.exit(1)
print(f"readme apps verification passed ({len(rows)} rows)")
