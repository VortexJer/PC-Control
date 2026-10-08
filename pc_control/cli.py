"""Linea de comandos de pcsight: instalar y desinstalar en Claude Code, estado, pausa.

    pcsight install [--mode auto|strict|bypass] [--scope user|project|local] [--allow-reads]
    pcsight uninstall [--purge]
    pcsight status
    pcsight pause | resume
    pcsight serve                     (el servidor MCP, lo que lanza Claude)

install deja todo montado: registra el servidor en Claude Code y escribe una skill con las instrucciones de uso.
uninstall lo deshace sin tocar nada que no pusiera install (lleva la cuenta en ~/.claude/pcsight-install.json).
"""
import argparse, json, os, shutil, subprocess, sys

MARKER = "<!-- installed-by: pcsight -->"
READ_TOOLS = ["mcp__pcsight__windows", "mcp__pcsight__look"]
SKILL = f"""---
name: pcsight
description: Usar cuando el usuario pida ver, leer u operar una app o ventana de Windows (Word, Explorador, Studio...), o pregunte como usar la app que tiene abierta. Controla apps sin traerlas al frente ni mover su raton ni su teclado.
---
{MARKER}

# pcsight: Claude en el PC

Herramientas `mcp__pcsight__*`: `windows`, `look`, `click`, `type`, `key`, `open_app`.

## Flujo
1. `windows` lista las ventanas, la mas reciente primero. `[EN USO]` = donde trabaja el usuario ahora;
   `[ULTIMA USADA]` = la que tenia delante antes de hablar contigo. Si dice "esta app" o "como uso esto", es esa.
2. `look(ventana)` devuelve lo mas barato que sirva (arbol de texto, OCR o imagen con marcas numeradas). Los elementos
   salen como `[id:nombre]`.
3. `click(ventana, id)` y `type(ventana, id, texto)` actuan por id. Devuelven solo lo que cambio: no hace falta mirar de nuevo.

## Reglas
- Nada se enfoca, el raton y el teclado del usuario no se tocan. No pidas atajos (`ctrl+s`): no se soportan.
- Modo `auto`: las acciones delicadas (pagar, eliminar, enviar, instalar) piden `confirm=true`: **pregunta antes al usuario**.
  Nunca se escribe en campos de contrasena. Apps bloqueadas por categoria (gestores de claves, terminales...) no se pueden usar.
- `open_app` abre la app minimizada con su boton en la barra de tareas; con `hidden=true`, en un escritorio oculto.
  Una app minimizada se lee por texto, no por imagen.
- Word: `type` escribe por su automatizacion COM en el documento de esa ventana (confirma con el contador de palabras).
- "sin confirmar" en una respuesta = el programa no pudo comprobar el efecto. Comprueba con `look` antes de dar nada por hecho.
- Si el usuario pide ver como funciona una app, explicale lo que ves con `look`, no actues sin que lo pida.
"""


def pcsight_home():
    return os.environ.get("PCSIGHT_HOME") or os.path.join(os.path.expanduser("~"), ".pcsight")


def claude_home():
    return os.environ.get("PCSIGHT_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")


def _paths():
    ch = claude_home()
    return {"settings": os.path.join(ch, "settings.json"), "skill": os.path.join(ch, "skills", "pcsight"),
            "record": os.path.join(ch, "pcsight-install.json")}


def run_claude(args):
    """Ejecuta `claude <args>`; devuelve (codigo, salida)."""
    exe = shutil.which("claude")
    if not exe:
        return 127, "no se encontro la orden 'claude' en el PATH (instala Claude Code primero)"
    p = subprocess.run([exe] + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def _load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def install(mode="auto", scope="user", allow_reads=False, runner=run_claude, python=None):
    """Registra pcsight en Claude Code y deja su skill. Devuelve una lista de lineas para mostrar."""
    if mode not in ("auto", "strict", "bypass"):
        raise ValueError(f"modo desconocido: {mode}")
    py = python or sys.executable
    pth = _paths(); log = []
    runner(["mcp", "remove", "pcsight", "-s", scope])                       # idempotente: si ya estaba, se rehace
    code, out = runner(["mcp", "add", "pcsight", "-s", scope, "-e", f"PCSIGHT_MODE={mode}", "-e", "PYTHONUTF8=1", "--", py, "-m", "pcsight.server"])
    if code != 0:
        raise RuntimeError(f"claude mcp add fallo: {out}")
    log.append(f"servidor MCP registrado ({scope}, modo {mode})")

    skill_md = os.path.join(pth["skill"], "SKILL.md")
    if os.path.exists(skill_md) and MARKER not in open(skill_md, encoding="utf-8").read():
        log.append("ya hay una skill 'pcsight' que no es nuestra: no se toca")
        wrote_skill = False
    else:
        os.makedirs(pth["skill"], exist_ok=True)
        open(skill_md, "w", encoding="utf-8").write(SKILL)
        log.append(f"skill escrita en {skill_md}")
        wrote_skill = True

    record = _load(pth["record"], {})
    added = set(record.get("allow_added", []))
    if allow_reads:
        s = _load(pth["settings"], {})
        allow = s.setdefault("permissions", {}).setdefault("allow", [])
        if not os.path.exists(pth["settings"] + ".pcsight-bak") and os.path.exists(pth["settings"]):
            shutil.copy(pth["settings"], pth["settings"] + ".pcsight-bak")
        for tool in READ_TOOLS:
            if tool not in allow:
                allow.append(tool); added.add(tool)
        _save(pth["settings"], s)
        log.append("lecturas (windows, look) permitidas sin preguntar")
    _save(pth["record"], {"scope": scope, "mode": mode, "skill": wrote_skill or record.get("skill", False), "allow_added": sorted(added)})
    log.append("listo: abre una sesion nueva de Claude Code para usar las herramientas mcp__pcsight__*")
    return log


def uninstall(purge=False, scope="user", runner=run_claude):
    """Deshace lo que hizo install, y solo eso."""
    pth = _paths(); log = []
    record = _load(pth["record"], {})
    scope = record.get("scope", scope)
    code, out = runner(["mcp", "remove", "pcsight", "-s", scope])
    log.append("servidor MCP quitado" if code == 0 else f"servidor MCP: nada que quitar ({out[:60]})")

    skill_md = os.path.join(pth["skill"], "SKILL.md")
    if os.path.exists(skill_md):
        if MARKER in open(skill_md, encoding="utf-8").read():
            shutil.rmtree(pth["skill"], ignore_errors=True); log.append("skill quitada")
        else:
            log.append("la skill 'pcsight' no es nuestra: no se toca")
    added = record.get("allow_added", [])
    if added and os.path.exists(pth["settings"]):
        s = _load(pth["settings"], {})
        allow = s.get("permissions", {}).get("allow", [])
        s.setdefault("permissions", {})["allow"] = [a for a in allow if a not in added]
        if not s["permissions"]["allow"]:
            s["permissions"].pop("allow")
        if not s["permissions"]:
            s.pop("permissions")
        _save(pth["settings"], s); log.append("permisos de lectura retirados")
    if os.path.exists(pth["record"]):
        os.remove(pth["record"])
    if purge and os.path.isdir(pcsight_home()):
        shutil.rmtree(pcsight_home(), ignore_errors=True); log.append("datos de ~/.pcsight borrados")
    return log


def status(runner=run_claude):
    pth = _paths(); record = _load(pth["record"], {})
    code, out = runner(["mcp", "get", "pcsight"])
    skill_md = os.path.join(pth["skill"], "SKILL.md")
    return {"registrado": code == 0, "conectado": code == 0 and "Connected" in out, "modo": record.get("mode"),
            "skill": os.path.exists(skill_md) and MARKER in open(skill_md, encoding="utf-8").read(),
            "pausado": os.path.exists(os.path.join(pcsight_home(), "PAUSE"))}


def set_pause(on):
    f = os.path.join(pcsight_home(), "PAUSE")
    if on:
        os.makedirs(pcsight_home(), exist_ok=True); open(f, "w").close()
    elif os.path.exists(f):
        os.remove(f)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pcsight", description="Claude en el PC: instalar, desinstalar y controlar pcsight.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install", help="registrar en Claude Code y dejar la skill")
    i.add_argument("--mode", choices=["auto", "strict", "bypass"], default="auto")
    i.add_argument("--scope", choices=["user", "project", "local"], default="user")
    i.add_argument("--allow-reads", action="store_true", help="permitir windows y look sin preguntar")
    u = sub.add_parser("uninstall", help="quitar todo lo que puso install")
    u.add_argument("--purge", action="store_true", help="borrar tambien ~/.pcsight (registro de errores, lista blanca)")
    sub.add_parser("status", help="ver si esta instalado y conectado")
    sub.add_parser("pause", help="interruptor de parada: todas las herramientas se niegan")
    sub.add_parser("resume", help="quitar la pausa")
    sub.add_parser("serve", help="lanzar el servidor MCP (lo que ejecuta Claude)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "install":
            print("\n".join(install(a.mode, a.scope, a.allow_reads)))
        elif a.cmd == "uninstall":
            print("\n".join(uninstall(a.purge)))
        elif a.cmd == "status":
            for k, v in status().items(): print(f"{k}: {v}")
        elif a.cmd in ("pause", "resume"):
            set_pause(a.cmd == "pause"); print("en pausa" if a.cmd == "pause" else "reanudado")
        elif a.cmd == "serve":
            from .server import main as serve
            serve()
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
