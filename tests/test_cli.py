"""install / uninstall / status / pause: lo deja todo montado en Claude y lo deshace sin tocar nada ajeno.

Usa un `claude` falso y carpetas temporales: no toca la configuracion real.
"""
import json, os, shutil, sys, tempfile
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmp = tempfile.mkdtemp(prefix="pcsight_cli_")
ch, ph = os.path.join(tmp, "claude"), os.path.join(tmp, "home")
os.environ["PCSIGHT_CLAUDE_HOME"], os.environ["PCSIGHT_HOME"] = ch, ph
from pcsight import cli

c = {}
calls = []
def fake(args):
    calls.append(args); return 0, "pcsight: Status: Connected"
settings = os.path.join(ch, "settings.json"); skill_md = os.path.join(ch, "skills", "pcsight", "SKILL.md"); record = os.path.join(ch, "pcsight-install.json")
os.makedirs(ch)
original = {"permissions": {"allow": ["Bash(ls)"], "deny": ["Bash(rm)"]}, "model": "x", "env": {"A": "1"}}
json.dump(original, open(settings, "w"))
rd = lambda p: json.load(open(p, encoding="utf-8"))

try:
    # 1) install basico
    cli.install("auto", "user", False, runner=fake, python="PY")
    c["install: quita un registro previo y registra"] = calls[0] == ["mcp", "remove", "pcsight", "-s", "user"] and calls[1][:5] == ["mcp", "add", "pcsight", "-s", "user"]
    c["install: modo y python en el comando"] = "PCSIGHT_MODE=auto" in calls[1] and calls[1][-3:] == ["PY", "-m", "pcsight.server"]
    s = open(skill_md, encoding="utf-8").read()
    c["install: escribe la skill con su marca"] = s.startswith("---") and "name: pcsight" in s and cli.MARKER in s and "[ULTIMA USADA]" in s
    c["install: no toca settings sin --allow-reads"] = rd(settings) == original
    c["install: deja el registro"] = rd(record)["mode"] == "auto" and rd(record)["allow_added"] == []
    # 2) con --allow-reads, conservando lo ajeno
    cli.install("strict", "user", True, runner=fake, python="PY")
    st = rd(settings)
    c["allow-reads: anade las dos lecturas"] = all(t in st["permissions"]["allow"] for t in cli.READ_TOOLS)
    c["allow-reads: conserva lo que habia"] = "Bash(ls)" in st["permissions"]["allow"] and st["permissions"]["deny"] == ["Bash(rm)"] and st["model"] == "x" and st["env"] == {"A": "1"}
    c["allow-reads: hace copia de seguridad"] = os.path.exists(settings + ".pcsight-bak") and rd(settings + ".pcsight-bak") == original
    c["install: modo strict en el comando"] = "PCSIGHT_MODE=strict" in calls[-1]
    # 3) idempotente
    cli.install("strict", "user", True, runner=fake, python="PY")
    allow = rd(settings)["permissions"]["allow"]
    c["idempotente: sin duplicados"] = all(allow.count(t) == 1 for t in cli.READ_TOOLS)
    # 4) uninstall deshace solo lo nuestro
    n = len(calls); cli.uninstall(runner=fake)
    c["uninstall: quita el servidor"] = calls[n] == ["mcp", "remove", "pcsight", "-s", "user"]
    c["uninstall: quita la skill"] = not os.path.exists(os.path.dirname(skill_md))
    st = rd(settings)
    c["uninstall: deja settings como estaba"] = st["permissions"]["allow"] == ["Bash(ls)"] and st["permissions"]["deny"] == ["Bash(rm)"] and st["model"] == "x"
    c["uninstall: borra su registro"] = not os.path.exists(record)
    # 5) una skill ajena con el mismo nombre no se toca ni al instalar ni al desinstalar
    os.makedirs(os.path.dirname(skill_md)); open(skill_md, "w", encoding="utf-8").write("mi skill propia")
    cli.install("auto", "user", False, runner=fake, python="PY")
    c["skill ajena: install no la pisa"] = open(skill_md, encoding="utf-8").read() == "mi skill propia"
    cli.uninstall(runner=fake)
    c["skill ajena: uninstall no la borra"] = os.path.exists(skill_md)
    os.remove(skill_md)
    # 6) uninstall sin nada instalado, install que falla, modo invalido
    shutil.rmtree(os.path.join(ch, "skills"), ignore_errors=True)
    try: cli.uninstall(runner=lambda a: (1, "not found")); c["uninstall sin nada instalado no falla"] = True
    except Exception: c["uninstall sin nada instalado no falla"] = False
    try: cli.install("auto", "user", False, runner=lambda a: (0, "") if a[1] == "remove" else (1, "boom"), python="PY"); c["install falla si claude falla"] = False
    except RuntimeError: c["install falla si claude falla"] = True
    try: cli.install("loquesea", runner=fake); c["modo invalido rechazado"] = False
    except ValueError: c["modo invalido rechazado"] = True
    # 7) estado y pausa
    ok = cli.status(runner=fake); bad = cli.status(runner=lambda a: (1, "No MCP server found"))
    c["status: registrado y conectado"] = ok["registrado"] and ok["conectado"]
    c["status: no registrado"] = not bad["registrado"] and not bad["conectado"]
    cli.set_pause(True); c["pause crea PAUSE"] = os.path.exists(os.path.join(ph, "PAUSE")) and cli.status(runner=fake)["pausado"]
    cli.set_pause(False); c["resume la quita"] = not os.path.exists(os.path.join(ph, "PAUSE"))
    cli.set_pause(False); c["resume sin pausa no falla"] = True
    os.makedirs(ph, exist_ok=True); open(os.path.join(ph, "last_error.log"), "w").write("x")
    cli.install("auto", runner=fake, python="PY"); cli.uninstall(purge=True, runner=fake)
    c["--purge borra ~/.pcsight"] = not os.path.exists(ph)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

bad = [k for k, v in c.items() if not v]
for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
print(f"cli verification passed ({len(c)} comprobaciones)" if not bad else f"cli verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
