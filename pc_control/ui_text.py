"""What the USER reads on screen (the notice banner and the permission questions), in the language of their Windows.
Everything the model reads stays in English. PC_CONTROL_LANG=en|es overrides the detection."""
import ctypes
from . import envvars

TEXT = {
    "en": {
        "notice": "If you touch this application, Claude will not be able to act on it. Put it back as it was ({w}x{h}) and do not touch it "
                  "any more: when you do, Claude carries on by itself.",
        "wants": "Claude wants to {action}.",
        "wants_protected": "Claude wants to {action} (protected window: {cat}).",
        "ask_all": "PC-Control asks you about every window Claude accesses.",
        "protected": "This is a PROTECTED window ({cat}): it could show or change sensitive things (commands, passwords, private data).",
        "once": 'A plain "yes" only counts for THIS time. If you choose "don\'t ask again", PC-Control applies it only to THIS window '
                "(not to the others) for as long as this session lasts.",
        "sensitive": "Claude wants to {what_action} in {what} (a delicate action).",
        "sensitive2": "PC-Control considers it sensitive and needs your permission every time.",
        "allow": "Allow it?",
        "window": "«{title}»",
        "program": "the program «{proc}»",
        "open": "open {what}",
        "type": "type «{text}» into {what}",
        "key": "press the key «{name}» in {what}",
        "click": "click (element {target}) on {what}",
        "verbs": {"drag": "drag the mouse (draw, select or move) in", "look": "view the contents of", "click": "click on",
                  "type": "type into", "key": "press a key in", "scroll": "scroll", "read": "read the whole text of"},
        "use": "use",
        "param": "question",
        "press_button": "press «{name}»",
        "cats": {},
    },
    "es": {
        "notice": "Si tocas esta aplicación, Claude no podrá actuar en ella. Déjala como estaba ({w}x{h}) y no la toques más: "
                  "en cuanto lo hagas, Claude sigue solo.",
        "wants": "Claude quiere {action}.",
        "wants_protected": "Claude quiere {action} (ventana protegida: {cat}).",
        "ask_all": "PC-Control te pregunta por cada ventana a la que Claude accede.",
        "protected": "Es una ventana PROTEGIDA ({cat}): podría mostrar o cambiar cosas delicadas (comandos, contraseñas, datos privados).",
        "once": 'Un "sí" normal vale solo para ESTA vez. Si eliges "no volver a preguntar", PC-Control lo aplica solo a ESTA ventana '
                "(no a las demás) mientras dure esta sesión.",
        "sensitive": "Claude quiere {what_action} en {what} (es una acción delicada).",
        "sensitive2": "PC-Control lo considera delicado y necesita tu permiso cada vez.",
        "allow": "¿Lo permites?",
        "window": "«{title}»",
        "program": "el programa «{proc}»",
        "open": "abrir {what}",
        "type": "escribir «{text}» en {what}",
        "key": "pulsar la tecla «{name}» en {what}",
        "click": "hacer clic (elemento {target}) en {what}",
        "verbs": {"drag": "arrastrar el ratón (dibujar, seleccionar o mover) en", "look": "ver el contenido de", "click": "hacer clic en",
                  "type": "escribir en", "key": "pulsar una tecla en", "scroll": "desplazar", "read": "leer todo el texto de"},
        "use": "usar",
        "param": "pregunta",
        "press_button": "pulsar «{name}»",
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
