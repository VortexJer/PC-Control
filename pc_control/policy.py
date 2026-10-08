"""PC-Control permission policy: four modes, like Claude Code's.

  auto    (default) Filters apps by category, with no list to maintain by hand:
          PROTECTS password managers, terminals, system administration, remote access, crypto wallets,
          the AI client itself and any window whose title looks like banking/payments/passwords: they appear in the list
          marked [PROTECTED] and the SERVER asks the user (MCP elicitation) before using them; the AI cannot answer (they are not blocked).
          Sensitive actions (pay, delete, send, install...) require the user's answer, i.e. the AI
          must have asked the user for permission. It never types into a password field.
  ask     Asks the user about EVERY window (the first time Claude uses it in the session; a yes lasts for that session and that window).
          The protected windows of `auto` are asked about the same way. For anyone who wants the lowest possible risk.
  strict  Additionally, act only on the apps in PC_CONTROL_ALLOW or ~/.pc-control/allow.txt.
  bypass  Equivalent to --dangerously-skip-permissions: no filters or confirmations. Only the pause switch remains.

The mode is chosen with the PC_CONTROL_MODE variable. The default is `follow`: it follows Claude Code's permission mode
(bypass -> bypass, anything else -> auto). It can be set by hand to auto, ask, strict or bypass.
"""
import os, re
from . import envvars

MODES = ("strict", "ask", "auto", "bypass")

BLOCKED_APPS = {
    "password manager or authenticator": {
        "keepass", "keepassxc", "kpcli", "bitwarden", "1password", "1password8", "op", "lastpass", "lastpassbroker", "dashlane", "enpass",
        "nordpass", "roboform", "keeper", "keeperpasswordmanager", "protonpass", "proton pass", "zohovault", "myki", "stickypassword",
        "psono", "buttercup", "passbolt", "padloc", "logmeonce", "truekey", "norton password manager", "kaspersky password manager",
        "authy", "authy desktop", "winauth", "2fas", "yubikeymanager", "yubioauthenticator", "ykman", "authenticator", "microsoft authenticator",
        "steamguard", "gpg4win", "kleopatra", "gpa", "veracrypt", "cryptomator", "bitlocker", "bdeunlock"},
    "terminal or console": {
        "windowsterminal", "wt", "cmd", "powershell", "pwsh", "powershell_ise", "conhost", "openconsole", "mintty", "putty", "puttytel", "kitty",
        "kitty_portable", "plink", "psftp", "wsl", "wslhost", "wslg", "bash", "sh", "zsh", "fish", "git-bash", "git-cmd", "alacritty",
        "wezterm", "wezterm-gui", "hyper", "tabby", "terminus", "cmder", "conemu", "conemu64", "fluentterminal", "termius", "securecrt", "mobaxterm",
        "xshell", "ttermpro", "teraterm", "xterm", "rxvt", "urxvt", "nu", "ubuntu", "debian", "kali", "kali-linux", "opensuse", "ubuntu2204",
        "ubuntu2404", "python_console", "idle", "ipython", "jupyter-qtconsole", "cscript", "wscript", "mshta", "wmic", "ssh", "scp", "telnet"},
    "system administration": {
        "regedit", "regedt32", "mmc", "taskmgr", "consent", "secpol", "gpedit", "eventvwr", "msconfig", "perfmon", "systemsettings",
        "control", "compmgmt", "devmgmt", "diskmgmt", "services", "lusrmgr", "netplwiz", "sysdm", "systempropertiesadvanced",
        "systempropertiesprotection", "systempropertiesperformance", "taskschd", "resmon", "msinfo32", "dxdiag", "cleanmgr", "diskpart",
        "bcdedit", "dism", "sfc", "gpupdate", "gpresult", "rsop", "certmgr", "certlm", "wf", "firewall", "windowsdefender", "securityhealthui",
        "securityhealthsystray", "msmpeng", "mpcmdrun", "wsreset", "slui", "wusa", "winver", "powertoys", "processhacker", "systeminformer",
        "procexp", "procexp64", "procmon", "procmon64", "autoruns", "autoruns64", "tcpview", "sysinternals", "hwinfo64", "cpuz", "gpuz",
        "msiexec", "setup", "installer", "uninstall", "unins000", "regsvr32", "rundll32", "dllhost", "taskhostw", "sihost", "winlogon",
        "lockapp", "logonui", "useraccountcontrolsettings", "computerdefaults", "optionalfeatures", "appwiz", "ncpa", "inetcpl"},
    "remote access": {
        "anydesk", "teamviewer", "teamviewer_desktop", "tv_w32", "tv_x64", "mstsc", "msra", "quickassist", "rustdesk", "parsecd", "parsec",
        "vncviewer", "vncserver", "winvnc", "tightvnc", "tvnserver", "tvnviewer", "realvnc", "vncguilauncher", "ultravnc", "uvnc_launcher",
        "radmin", "rutserv", "rutview", "remoting_desktop", "chromoting", "splashtop", "srservice", "supremo", "ammyyadmin", "logmein",
        "lmi_rescue", "gotomypc", "gotoassist", "dwagent", "dwservice", "nxplayer", "nxclient", "screenconnect", "connectwisecontrol",
        "bomgar", "beyondtrust", "dameware", "mikogo", "zohoassist", "remotepc", "aeroadmin", "ultraviewer", "rdcman", "mremoteng", "vmconnect",
        "virtualbox", "vboxmanage", "vmware", "vmware-vmx", "hyper-v"},
    "crypto wallet or finance": {
        "exodus", "electrum", "electrum-ltc", "ledgerlive", "ledger live", "atomic", "atomicwallet", "trezorsuite", "trezor-suite", "bitcoin-qt",
        "bitcoin", "bitcoind", "monero-wallet-gui", "monero-wallet-cli", "wasabi", "wassabee", "sparrow", "coinomi", "jaxx", "jaxxliberty",
        "guarda", "mycrypto", "metamask", "phantom", "trustwallet", "binance", "coinbase", "kraken", "bisq", "zecwallet", "feather",
        "daedalus", "yoroi", "mist", "geth", "revolut", "n26", "wise", "paypal", "ynab", "moneydance", "quicken", "gnucash", "mint"},
    "AI client": {
        "claude", "claudecode", "claude-code", "chatgpt", "codex", "gemini", "copilot", "cursor", "windsurf", "perplexity", "lmstudio",
        "ollama", "jan", "gpt4all", "kimi", "deepseek", "grok", "poe", "msty"},
    "mail": {
        "outlook", "olk", "hxoutlook", "hxmail", "thunderbird", "emclient", "mailspring", "eudora", "mailbird", "postbox", "claws-mail",
        "evolution", "kmail", "mailwasher", "protonmail", "tutanota", "tuta", "spark", "newton", "canary", "foxmail", "windowscommunicationsapps"},
    "messaging or video calls": {
        "whatsapp", "whatsapp.root", "telegram", "signal", "discord", "discordptb", "discordcanary", "slack", "teams", "ms-teams", "msteams",
        "skype", "skypeapp", "viber", "line", "wechat", "weixin", "qq", "element", "riot", "threema", "wire", "session", "messenger", "zoom",
        "webex", "ciscowebex", "gotomeeting", "bluejeans", "whereby", "jitsi", "mumble", "teamspeak", "ts3client", "guilded", "revolt",
        "zulip", "mattermost", "rocketchat", "keybase", "briar", "simplex", "beeper"},
    "VPN or security": {
        "openvpn", "openvpn-gui", "openvpnserv", "nordvpn", "nordvpn-service", "protonvpn", "protonvpn-service", "wireguard", "wg", "expressvpn",
        "expressvpn-service", "mullvad", "mullvad-vpn", "surfshark", "cyberghost", "pia-client", "privateinternetaccess", "windscribe", "tunnelbear",
        "hotspotshield", "ipvanish", "vyprvpn", "hide.me", "hidemyass", "tailscale", "tailscale-ipn", "zerotier", "zerotier_desktop_ui",
        "cloudflare warp", "warp", "forticlient", "fortitray", "anyconnect", "vpnui", "globalprotect", "pangpa", "pulsesecure", "juniper",
        "checkpoint", "sonicwall", "netextender", "avast", "avastui", "avg", "avgui", "bitdefender", "bdagent", "kaspersky", "avp", "eset",
        "ekrn", "egui", "norton", "nortonsecurity", "mcafee", "mcuicnt", "malwarebytes", "mbam", "sophos", "trendmicro", "tmbmsrv", "webroot",
        "wireshark", "dumpcap", "fiddler", "burpsuite", "burp", "mitmproxy", "charles", "zap", "nmap", "zenmap", "metasploit", "ettercap", "hashcat",
        "johntheripper", "ghidra", "x64dbg", "ida", "ida64", "ollydbg", "windbg", "cheatengine"},
    "signing or identity": {
        "autofirma", "clave", "cl@ve", "dnie", "dnie_modulo", "fnmt", "configuradorfnmt", "docusign", "adobesign", "signaturit", "safenet", "safenetauthenticationclient",
        "pkcs11", "opensc", "yubikey", "idprotect", "onespan", "digipass"},
    "development with credentials": {
        "docker desktop", "docker", "dockerdesktop", "kubectl", "k9s", "lens", "terraform", "vault", "ansible", "gitkraken", "sourcetree", "githubdesktop",
        "tortoisegit", "tortoisesvn", "filezilla", "winscp", "cyberduck", "transmit", "forklift", "dbeaver", "heidisql", "navicat", "datagrip",
        "sqlserverms", "ssms", "mongodbcompass", "robo3t", "studio3t", "redisinsight", "pgadmin4", "mysqlworkbench", "postman", "insomnia",
        "hoppscotch", "bruno", "awsvpnclient", "azuredatastudio", "storageexplorer", "gcloud"},
}
SENSITIVE_TITLE = re.compile(
    r"(banco|\bbank|banque|bancaire|\bbanca\b|konto|bankkonto|paypal|bizum|tarjeta|credit card|debit card|kreditkarte|carte bancaire|carta di credito|"
    r"\biban\b|\bswift\b|\bcvv\b|\bcvc\b|\bpin\b|\bsepa\b|transferencia|transfer(?:ir|ence)|ingreso|nomina|n.mina|factura|invoice|"
    r"santander|bbva|caixabank|caixa|sabadell|bankinter|openbank|unicaja|abanca|ibercaja|kutxabank|cajamar|cajasur|laboral kutxa|evo banco|"
    r"revolut|\bn26\b|\bwise\b|stripe|binance|coinbase|kraken|bitpanda|crypto\.com|metamask|ledger|trezor|chase|wells fargo|bank of america|"
    r"citibank|hsbc|barclays|lloyds|natwest|monzo|deutsche bank|commerzbank|sparkasse|postbank|bnp|cr.dit agricole|soci.t. g.n.rale|ing direct|"
    r"contrase|password|passwort|kennwort|mot de passe|senha|passphrase|frase de recuperaci|recovery phrase|seed phrase|semilla|"
    r"clave privada|private key|api key|secret key|clave secreta|token de acceso|access token|bearer|\bssh\b|\bvault\b|credencial|credential|"
    r"2fa|two-factor|dos pasos|autenticaci|authenticat|verificaci.n en dos|one-time|c.digo de verificaci|verification code|"
    r"sign in|signin|log in|login|iniciar sesi|inicio de sesi|identif[ií]cate|acceso seguro|secure login|"
    r"monedero|wallet|hacienda|agencia tributaria|aeat|sede electr|seguridad social|\birpf\b|\bsepe\b|\bdgt\b|clave pin|cl@ve|certificado digital|"
    r"dni|pasaporte|passport|n.mero de la seguridad|historia cl.nica|historial m.dico|medical record|salud|patient|paciente|"
    r"aws management|amazon web services|azure portal|google cloud|cloud console|cpanel|plesk|root@|admin panel|panel de administraci|administrator|"
    r"gmail|outlook|inbox|bandeja de entrada|whatsapp|telegram|messenger|direct message|mensajes directos)", re.I)
SENSITIVE_ACTION = re.compile(
    r"\b(pagar|pago|comprar|compra|buy|pay|purchase|checkout|place order|eliminar|borrar|delete|remove|quitar|vaciar|"
    r"enviar|send|publicar|publish|transferir|transfer|instalar|install|desinstalar|uninstall|formatear|format|restablecer|reset|"
    r"cerrar sesi|sign out|log out|"
    r"payer|acheter|supprimer|effacer|envoyer|installer|desinstaller|d.sinstaller|se d.connecter|"        # French
    r"bezahlen|kaufen|l.schen|entfernen|senden|installieren|deinstallieren|abmelden|"                      # German
    r"pagare|acquista|elimina|cancella|invia|installa|disinstalla|esci|"                                   # Italian
    r"excluir|apagar|desinstalar|terminar sess)", re.I)
PASSWORD_NAME = re.compile(r"(contrase|password|passwort|kennwort|mot de passe|senha|\bpin\b|cvv|cvc|clave|secret|token|api key)", re.I)


def follow_mode():
    """PC_CONTROL_MODE=follow (default): PC-Control follows CLAUDE CODE's permission mode.
    bypass -> asks nothing; anything else -> only the windows in the filter.
    If Claude Code's mode is unknown (no hook), it behaves like auto."""
    try:
        from . import perm
        pm = perm.cc_mode().strip().lower()
    except Exception:
        pm = ""
    if not pm:
        return "auto"
    return "bypass" if "bypass" in pm else "auto"          # no questions about apps outside the filter, whatever Claude Code's mode is


def mode():
    m = envvars.get("MODE", "follow").strip().lower()
    if m == "follow" or m not in MODES:
        return follow_mode()
    return m


def blocked_category(proc, title=""):
    """Category a window is blocked under, or None."""
    p = (proc or "").lower()
    for cat, procs in BLOCKED_APPS.items():
        if p in procs:
            return cat
    if SENSITIVE_TITLE.search(title or ""):
        return "banking, payments or passwords (by the window title)"
    return None


class NeedsPermission(PermissionError):
    """The USER's permission is needed. The server asks them itself (the model cannot answer for them)."""
    def __init__(self, question, key=None, detail=""):
        self.question = question; self.key = key; self.detail = detail      # key: if the user says yes to a protected window, it holds for the rest of the session
        super().__init__(question + " PC-Control asks the user directly; without their answer it is not executed.")


ASK_ALL = "window"        # category of the NON-protected windows when the mode is `ask` (every window is asked about)


def needs_ask(m, proc, title=""):
    """Why the user's permission is needed for this window: the protected category, ASK_ALL (ask mode) or None. Used by the server and the hook."""
    if m == "bypass":
        return None
    return blocked_category(proc, title) or (ASK_ALL if m == "ask" else None)


def window_key(cat, proc, hwnd=0):
    """Key of the "yes, and do not ask again this session": per specific WINDOW (not per program)."""
    return f"{cat}|{proc}|{hwnd}"


def _ask(cat, what, hwnd=0):
    if cat == ASK_ALL:
        return NeedsPermission(f"Claude wants to use the '{what}' window (ask mode: every window is asked about). Do you allow it?", key=window_key(cat, what, hwnd))
    return NeedsPermission(f"Claude wants to use a PROTECTED window ({cat}: '{what}'). Do you allow it?", key=window_key(cat, what, hwnd))


def check_window(m, proc, title, act, allowed, confirm=False, hwnd=0):
    """Raise PermissionError if the window needs permission (protected, or any in ask mode; and confirm=False) or is not allowed (strict mode)."""
    why = needs_ask(m, proc, title)
    if why and not confirm:
        raise _ask(why, proc, hwnd)
    if m == "strict" and act and proc not in allowed:
        raise PermissionError(f"acting on '{proc}' is not allowed (strict mode). Add it to PC_CONTROL_ALLOW or ~/.pc-control/allow.txt.")


def check_launch(m, exe, allowed, confirm=False):
    why = needs_ask(m, exe)
    if why and not confirm:
        raise _ask(why, exe, 0)
    if m == "strict" and exe not in allowed:
        raise PermissionError(f"opening '{exe}' is not allowed (strict mode). Add it to PC_CONTROL_ALLOW or ~/.pc-control/allow.txt.")


def check_click(m, name, confirm):
    """A sensitive action requires confirm=True, which only the server sets after asking the user."""
    if m != "bypass" and not confirm and name and SENSITIVE_ACTION.search(name):
        raise NeedsPermission(f"Claude wants to press a sensitive action: '{name[:40]}'. Do you allow it?", detail=f"press the button «{name[:60]}», which looks like a sensitive action (pay, delete, send, install...)")


def check_type(m, name, is_password):
    """Never type into a password field (except in bypass)."""
    if m != "bypass" and (is_password or (name and PASSWORD_NAME.search(name))):
        raise PermissionError(f"typing into password or secret fields is not allowed ('{(name or '')[:40]}'). It is disabled with PC_CONTROL_MODE=bypass.")
