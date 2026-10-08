"""Politica de permisos de pcsight: tres modos, como los de Claude Code.

  auto    (por defecto) Filtra las apps por categoria, sin lista que mantener a mano:
          bloquea gestores de contrasenas, terminales, administracion del sistema, acceso remoto, monederos cripto,
          el propio cliente de IA y cualquier ventana cuyo titulo sea de banca/pagos/contrasenas.
          Las acciones delicadas (pagar, eliminar, enviar, instalar...) exigen confirm=true, es decir, que la IA
          haya pedido permiso al usuario. Nunca escribe en un campo de contrasena.
  strict  Ademas, actuar solo sobre las apps de PCSIGHT_ALLOW o ~/.pcsight/allow.txt.
  bypass  Equivale a --dangerously-skip-permissions: sin filtros ni confirmaciones. Solo queda el interruptor de pausa.

El modo se elige con la variable PCSIGHT_MODE.
"""
import os, re

MODES = ("strict", "ask", "auto", "bypass")

BLOCKED_APPS = {
    "gestor de contrasenas": {"keepass", "keepassxc", "bitwarden", "1password", "lastpass", "dashlane", "enpass", "nordpass", "roboform"},
    "terminal o consola": {"windowsterminal", "wt", "cmd", "powershell", "pwsh", "powershell_ise", "conhost", "mintty", "putty", "wsl", "bash"},
    "administracion del sistema": {"regedit", "mmc", "taskmgr", "consent", "secpol", "gpedit", "eventvwr", "msconfig", "perfmon", "systemsettings"},
    "acceso remoto": {"anydesk", "teamviewer", "mstsc", "rustdesk", "parsecd", "vncviewer"},
    "monedero cripto": {"exodus", "electrum", "ledgerlive", "atomic", "trezorsuite"},
    "cliente de IA": {"claude"},
}
SENSITIVE_TITLE = re.compile(r"(banco|\bbank|paypal|bizum|tarjeta|credit card|contrase|password|monedero|wallet|hacienda|sede electr)", re.I)
SENSITIVE_ACTION = re.compile(
    r"\b(pagar|pago|comprar|compra|buy|pay|purchase|checkout|place order|eliminar|borrar|delete|remove|quitar|vaciar|"
    r"enviar|send|publicar|publish|transferir|transfer|instalar|install|desinstalar|uninstall|formatear|format|restablecer|reset|"
    r"cerrar sesi|sign out|log out)", re.I)
PASSWORD_NAME = re.compile(r"(contrase|password|\bpin\b|cvv|cvc|clave|secret|token|api key)", re.I)


def mode():
    m = os.environ.get("PCSIGHT_MODE", "auto").strip().lower()
    return m if m in MODES else "auto"


def blocked_category(proc, title=""):
    """Categoria por la que se bloquea una ventana, o None."""
    p = (proc or "").lower()
    for cat, procs in BLOCKED_APPS.items():
        if p in procs:
            return cat
    if SENSITIVE_TITLE.search(title or ""):
        return "banca, pagos o contrasenas (por el titulo de la ventana)"
    return None


def check_window(m, proc, title, act, allowed):
    """Lanza PermissionError si la ventana no se puede leer (act=False) o manejar (act=True)."""
    if m == "bypass":
        return
    cat = blocked_category(proc, title)
    if cat:
        raise PermissionError(f"bloqueada por categoria: {cat} ('{proc}'). Con PCSIGHT_MODE=bypass se desactiva.")
    if m == "strict" and act and proc not in allowed:
        raise PermissionError(f"actuar sobre '{proc}' no esta permitido (modo strict). Anadelo a PCSIGHT_ALLOW o ~/.pcsight/allow.txt.")


def check_launch(m, exe, allowed):
    if m == "bypass":
        return
    cat = blocked_category(exe)
    if cat:
        raise PermissionError(f"abrir '{exe}' esta bloqueado por categoria: {cat}.")
    if m == "strict" and exe not in allowed:
        raise PermissionError(f"abrir '{exe}' no esta permitido (modo strict). Anadelo a PCSIGHT_ALLOW o ~/.pcsight/allow.txt.")


def check_click(m, name, confirm):
    """Una accion delicada exige confirm=true (la IA debe haber pedido permiso al usuario)."""
    if m == "auto" and not confirm and name and SENSITIVE_ACTION.search(name):
        raise PermissionError(f"accion delicada ('{name[:40]}'). Pide permiso al usuario y repite con confirm=true.")


def check_type(m, name, is_password):
    """Nunca se escribe en un campo de contrasena (salvo bypass)."""
    if m != "bypass" and (is_password or (name and PASSWORD_NAME.search(name))):
        raise PermissionError(f"no se escribe en campos de contrasena o secretos ('{(name or '')[:40]}'). Con PCSIGHT_MODE=bypass se desactiva.")
