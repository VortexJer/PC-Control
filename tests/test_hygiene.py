"""Higiene de un repo open source: licencia, README, requisitos y NINGUN dato personal en lo que se publicaria."""
import os, re, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", ".unlazy"}
SKIP_FILES = {"GATES.md"}          # ledger local de verificacion: esta en .gitignore y lleva rutas de la maquina
PERSONAL = [r"joaquin", r"escrivarej", r"vortexjer", r"c:[\\/]users", r"/c/users", r"appdata", r"@gmail\.", r"\bgithub\.com/[a-z0-9-]+/"]

def scan(text, patterns):
    return [p for p in patterns if re.search(p, text, re.I)]

# control positivo: el escaner DEBE detectar cada tipo de dato personal
for sample in ["C:" + chr(92) + "Users" + chr(92) + "Pepe", "C:/Users/Pepe", "mi correo pepe@gmail.com", "https://github.com/alguien/repo", "/c/Users/pepe"]:
    assert scan(sample, GENERIC), f"el escaner no detecta: {sample}"
assert not scan("texto neutro sobre ventanas y tokens", PERSONAL), "falso positivo"

files = []
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in SKIP_DIRS]
    files += [os.path.join(dp, f) for f in fn if f not in SKIP_FILES and not f.endswith((".jpg", ".png", ".pyc"))]
problems = {}
for f in files:
    hits = scan(open(f, encoding="utf-8", errors="ignore").read(), PERSONAL)
    if hits: problems[os.path.relpath(f, ROOT)] = hits

checks = {
    "sin datos personales": not problems,
    "LICENSE MIT": "MIT License" in open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read(),
    "README con secciones": all(h in open(os.path.join(ROOT, "README.md"), encoding="utf-8").read() for h in ("## What it does", "## Safety model", "## Install", "## License")),
    "requirements.txt": os.path.getsize(os.path.join(ROOT, "requirements.txt")) > 50,
    ".gitignore ignora GATES.md": "GATES.md" in open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read(),
}
for k, v in checks.items(): print(("OK   " if v else "FAIL ") + k)
if problems: print("DATOS PERSONALES EN:", problems)
ok = all(checks.values())
print(f"hygiene verification passed ({len(files)} archivos revisados)" if ok else "hygiene verification FAILED")
sys.exit(0 if ok else 1)
