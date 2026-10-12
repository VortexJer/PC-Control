"""What the USER reads on screen (the notice banner and the permission questions), in the language of their Windows.
Everything the model reads stays in English. PC_CONTROL_LANG=en|es overrides the detection."""
import ctypes
from . import envvars

TEXT = {
    "en": {
        "notice": "If you touch this application, Claude will not be able to act on it. Put it back as it was ({w}x{h}) and do not touch it "
                  "any more: when you do, Claude carries on by itself.",
        "wants": "Claude wants to {action}.",
        "ask_all": "PC-Control asks you about every window Claude accesses.",
        "protected": "This is a PROTECTED window ({cat}): it could show or change sensitive things (commands, passwords, private data).",
        "once": 'A plain "yes" only counts for THIS time. If you choose "don\'t ask again", PC-Control applies it only to THIS window '
                "(not to the others) for as long as this session lasts.",
        "sensitive": "Claude wants to {what_action} in {what}.",
        "sensitive2": "PC-Control considers it sensitive and needs your permission every time.",
        "allow": "Allow it?",
        "window": "the window «{title}» (program: {proc})",
        "program": "the program «{proc}»",
        "open": "OPEN {what}",
        "type": "TYPE «{text}» into {what}",
        "key": "PRESS the key «{name}» in {what}",
        "click": "CLICK (element {target}) on {what}",
        "verbs": {"drag": "DRAG the mouse (draw, select or move) in", "look": "VIEW the contents of", "click": "CLICK on",
                  "type": "TYPE into", "key": "PRESS a key in", "scroll": "SCROLL", "read": "READ the whole text of"},
        "use": "USE",
        "cats": {},
    },
    "es": {
        "notice": "Si tocas esta aplicación, Claude no podrá actuar en ella. Déjala como estaba ({w}x{h}) y no la toques más: "
                  "en cuanto lo hagas, Claude sigue solo.",
        "wants": "Claude quiere {action}.",
        "ask_all": "PC-Control te pregunta por cada ventana a la que Claude accede.",
        "protected": "Es una ventana PROTEGIDA ({cat}): podría mostrar o cambiar cosas delicadas (comandos, contraseñas, datos privados).",
        "once": 'Un "sí" normal vale solo para ESTA vez. Si eliges "no volver a preguntar", PC-Control lo aplica solo a ESTA ventana '
                "(no a las demás) mientras dure esta sesión.",
        "sensitive": "Claude quiere {what_action} en {what}.",
        "sensitive2": "PC-Control lo considera delicado y necesita tu permiso cada vez.",
        "allow": "¿Lo permites?",
        "window": "la ventana «{title}» (programa: {proc})",
        "program": "el programa «{proc}»",
        "open": "ABRIR {what}",
        "type": "ESCRIBIR «{text}» en {what}",
        "key": "PULSAR la tecla «{name}» en {what}",
        "click": "HACER CLIC (elemento {target}) en {what}",
        "verbs": {"drag": "ARRASTRAR el ratón (dibujar, seleccionar o mover) en", "look": "VER el contenido de", "click": "HACER CLIC en",
                  "type": "ESCRIBIR en", "key": "PULSAR una tecla en", "scroll": "DESPLAZAR", "read": "LEER todo el texto de"},
        "use": "USAR",
        "cats": {"password manager or authenticator": "gestor de contraseñas o autenticador", "terminal or console": "terminal o consola",
                 "system administration": "administración del sistema", "remote access": "acceso remoto",
                 "crypto wallet or finance": "monedero cripto o finanzas", "AI client": "cliente de IA", "mail": "correo",
                 "messaging or video calls": "mensajería o videollamadas", "VPN or security": "VPN o seguridad",
                 "signing or identity": "firma o identidad", "development with credentials": "desarrollo con credenciales",
                 "banking, payments or passwords (by the window title)": "banca, pagos o contraseñas (por el título de la ventana)"},
    },
}


def lang():
    forced = (envvars.get("LANG", "") or "").strip().lower()[:2]
    if forced in TEXT:
        return forced
    try:
        return "es" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x0A else "en"     # primary language 0x0A = Spanish
    except Exception:
        return "en"


def T(key, **kw):
    s = TEXT[lang()].get(key, TEXT["en"][key])
    return s.format(**kw) if kw and isinstance(s, str) else s


def category(cat):
    return TEXT[lang()]["cats"].get(cat, cat)
