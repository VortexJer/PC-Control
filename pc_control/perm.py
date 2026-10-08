"""Permisos que da el USUARIO cuando el cliente (Claude Code) no admite preguntas desde el servidor.

Claude Code si deja que un hook PreToolUse devuelva "ask": el propio Claude Code le muestra al usuario su cuadro de permiso y el
modelo no puede contestarlo. El hook (hook.py) y el servidor (server.py) se hablan por marcas en ~/.pc-control/perm:

  asked/<firma>    el hook pidio permiso y la llamada llego (= el usuario dijo que si). El servidor la consume una vez.
  pending/<firma>  el servidor necesitaba permiso y no pudo preguntar: el hook, al ver la MISMA llamada repetida, pregunta.
  approved.json    ventanas protegidas ya aprobadas por el usuario (clave "categoria|proceso"), validas varias horas.
"""
import hashlib, json, os, time

ASK_TTL = 180.0                    # segundos que vale una marca asked/pending
APPROVED_TTL = 4 * 3600.0          # cuanto dura el si del usuario a una ventana protegida
SIG_KEYS = ("window", "target", "name", "command", "text")


def home():
    from . import envvars
    return envvars.get("HOME") or os.path.join(os.path.expanduser("~"), ".pc-control")


def sig(tool, args):
    """Firma estable de una llamada: la misma para el hook (que ve los argumentos tal cual) y para el servidor (que ve ademas los por defecto)."""
    d = {k: str(args[k]) for k in SIG_KEYS if args.get(k) not in (None, "")}
    return hashlib.sha1(json.dumps([tool, d], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


def _dir(kind):
    d = os.path.join(home(), "perm", kind)
    os.makedirs(d, exist_ok=True)
    return d


def mark(kind, s):
    open(os.path.join(_dir(kind), s), "w").close()


def fresh(kind, s):
    f = os.path.join(_dir(kind), s)
    return os.path.exists(f) and time.time() - os.path.getmtime(f) < ASK_TTL


def take(kind, s):
    """True (y borra la marca) si hay una marca reciente: cada permiso se gasta una sola vez."""
    ok = fresh(kind, s)
    try:
        os.remove(os.path.join(_dir(kind), s))
    except OSError:
        pass
    return ok


def _approved_file():
    return os.path.join(home(), "perm", "approved.json")


def approved(key):
    try:
        return time.time() - json.load(open(_approved_file(), encoding="utf-8")).get(key, 0) < APPROVED_TTL
    except (OSError, ValueError):
        return False


def approve(key):
    _dir("asked")
    try:
        data = json.load(open(_approved_file(), encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[key] = time.time()
    json.dump(data, open(_approved_file(), "w", encoding="utf-8"))
