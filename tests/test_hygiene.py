"""Open source repo hygiene: license, README, requirements and NO personal data in what would be published."""
import os, re, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", ".unlazy", "dist", "build", "pc_control.egg-info"}
SKIP_FILES = {"GATES.md", "@AutomationLog.txt", ".hygiene-local.txt", "test_hygiene.py"}   # local/ignored; this test carries no data
GENERIC = [r"c:[\\/]users", r"/c/users", r"app" + r"data", r"@(gmail|outlook|hotmail|yahoo)\.", r"github\.com/[a-z0-9-]+/"]
LOCAL = os.path.join(ROOT, ".hygiene-local.txt")        # your own names/usernames, one per line (regex); NOT published
PERSONAL = GENERIC + ([l.strip() for l in open(LOCAL, encoding="utf-8") if l.strip()] if os.path.exists(LOCAL) else [])

# the project's own public repository URL is intended to be published (its owner is the repo owner, by design)
ALLOWED = ["github.com/VortexJer/PC-Control"]

def scan(text, patterns):
    for a in ALLOWED:
        text = re.sub(re.escape(a), "", text, flags=re.I)
    return [p for p in patterns if re.search(p, text, re.I)]

# positive control: the scanner MUST detect each kind of personal data
for sample in ["C:" + chr(92) + "Users" + chr(92) + "Pepe", "C:/Users/Pepe", "my email pepe@gmail.com", "https://github.com/someone/repo", "/c/Users/pepe"]:
    assert scan(sample, GENERIC), f"the scanner does not detect: {sample}"
assert not scan("neutral text about windows and tokens", GENERIC), "false positive"

files = []
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in SKIP_DIRS]
    files += [os.path.join(dp, f) for f in fn if f not in SKIP_FILES and not f.endswith((".jpg", ".png", ".pyc"))]
problems = {}
for f in files:
    hits = scan(open(f, encoding="utf-8", errors="ignore").read(), PERSONAL)
    if hits: problems[os.path.relpath(f, ROOT)] = hits

checks = {
    "no personal data": not problems,
    "MIT LICENSE": "MIT License" in open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read(),
    "README with sections": all(h in open(os.path.join(ROOT, "README.md"), encoding="utf-8").read() for h in ("## Install", "## Uninstall", "## Permission modes", "## License")),
    "requirements.txt": os.path.getsize(os.path.join(ROOT, "requirements.txt")) > 50,
    ".gitignore ignores GATES.md": "GATES.md" in open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read(),
}
for k, v in checks.items(): print(("OK   " if v else "FAIL ") + k)
if problems: print("PERSONAL DATA IN:", problems)
ok = all(checks.values())
print(f"hygiene verification passed ({len(files)} files, {len(PERSONAL)} patterns, local: {os.path.exists(LOCAL)})" if ok else "hygiene verification FAILED")
sys.exit(0 if ok else 1)
