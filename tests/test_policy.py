"""Permission policy: four modes (strict, ask, auto, bypass), filtering by category and confirmation of sensitive actions."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempfile
os.environ['PC_CONTROL_HOME'] = tempfile.mkdtemp(prefix='pc-control_pol_')       # so it does not read the mode of the real Claude Code session
os.environ.pop('PC_CONTROL_MODE', None)
from pc_control import policy, perm

def raises(fn, *a):
    try: fn(*a); return False
    except PermissionError: return True

c = {}
# blocked categories (auto mode) and normal apps that must NOT be blocked
for proc in ("keepass", "bitwarden", "powershell", "windowsterminal", "regedit", "taskmgr", "anydesk", "exodus", "claude"):
    c[f"auto blocks {proc}"] = raises(policy.check_window, "auto", proc, "x", False, set())
for proc in ("winword", "excel", "explorer", "notepad", "chrome", "code", "calc", "pc_control_host"):
    c[f"auto lets {proc} through"] = not raises(policy.check_window, "auto", proc, "Document", True, set())
# sensitive titles in any app
for title in ("My Bank - Google Chrome", "PayPal: payments", "Password manager", "Crypto wallet"):
    c[f"auto blocks title '{title[:18]}'"] = raises(policy.check_window, "auto", "chrome", title, False, set())
for title in ("Quarterly report - Word", "Downloads - Explorer", "Roblox Studio"):
    c[f"auto lets title '{title[:18]}' through"] = not raises(policy.check_window, "auto", "winword", title, True, set())
# strict: also an allowlist to act (reading does not require it)
c["strict requires an allowlist to act"] = raises(policy.check_window, "strict", "winword", "x", True, set())
c["strict acts on an allowed app"] = not raises(policy.check_window, "strict", "winword", "x", True, {"winword"})
c["strict does not require an allowlist to read"] = not raises(policy.check_window, "strict", "winword", "x", False, set())
c["strict keeps the categories"] = raises(policy.check_window, "strict", "keepass", "x", False, {"keepass"})
# bypass: nothing is filtered
for proc in ("keepass", "powershell", "claude"):
    c[f"bypass lets {proc} through"] = not raises(policy.check_window, "bypass", proc, "My Bank", True, set())
# launching apps
c["auto does not open a terminal"] = raises(policy.check_launch, "auto", "cmd", set())
c["auto opens a normal app"] = not raises(policy.check_launch, "auto", "mspaint", set())
c["strict does not open without an allowlist"] = raises(policy.check_launch, "strict", "mspaint", set())
c["bypass opens anything"] = not raises(policy.check_launch, "bypass", "cmd", set())
# sensitive actions: require confirm; normal ones do not
for name in ("Delete all", "Pay now", "Send message", "Install", "Buy", "Delete history", "Publish", "Sign out", "Transfer"):
    c[f"auto requires confirm: {name}"] = raises(policy.check_click, "auto", name, False) and not raises(policy.check_click, "auto", name, True)
for name in ("Save", "Open", "Next", "Cancel", "Press", "Accept", "Search", "Enable option", "Download"):
    c[f"auto does not bother: {name}"] = not raises(policy.check_click, "auto", name, False)
c["bypass does not ask for confirm"] = not raises(policy.check_click, "bypass", "Delete all", False)
c["strict also asks for confirm"] = raises(policy.check_click, "strict", "Delete all", False)
# other Windows 11 languages: sensitive actions and sensitive titles are recognized all the same
for name in ("Eliminar todo", "Pagar ahora", "Comprar", "Borrar historial", "Cerrar sesion", "Transferir",
             "Payer maintenant", "Supprimer", "Envoyer", "Jetzt bezahlen", "Loeschen".replace("oe", chr(246)), "Senden", "Installieren", "Acquista", "Invia", "Excluir", "Terminar sessao"):
    c[f"languages: requires confirm: {name}"] = raises(policy.check_click, "auto", name, False)
for name in ("Enregistrer", "Ouvrir", "Speichern", "Offnen", "Salva", "Apri", "Guardar", "Seguinte", "Abrir", "Siguiente", "Cancelar", "Aceptar", "Buscar", "Descargar"):
    c[f"languages: does not bother: {name}"] = not raises(policy.check_click, "auto", name, False)
for title in ("Banque en ligne - Chrome", "Mein Konto - Sparkasse", "Mot de passe oublie", "Kreditkarte beantragen", "Senha do banco",
              "Mi Banco - Google Chrome", "Gestor de contrasenas", "Wallet de cripto"):
    c[f"languages: blocks title '{title[:20]}'"] = raises(policy.check_window, "auto", "chrome", title, False, set())
for name in ("Mot de passe", "Passwort", "Senha", "Kennwort"):
    c[f"languages: does not type into '{name}'"] = raises(policy.check_type, "auto", name, False)

# password fields
c["auto does not type into a password field (by control)"] = raises(policy.check_type, "auto", "Field", True)
c["auto does not type into 'Password'"] = raises(policy.check_type, "auto", "Password", False)
c["auto does not type into 'API key'"] = raises(policy.check_type, "auto", "API key", False)
c["auto types into a normal field"] = not raises(policy.check_type, "auto", "Name field", False)
c["bypass types anywhere"] = not raises(policy.check_type, "bypass", "Password", True)
# mode selection
for val, want in (("", "auto"), ("auto", "auto"), ("STRICT", "strict"), ("bypass", "bypass"), ("nonsense", "auto")):
    if val: os.environ["PC_CONTROL_MODE"] = val
    else: os.environ.pop("PC_CONTROL_MODE", None)
    c[f"mode '{val or '(unset)'}' -> {want}"] = policy.mode() == want

# the environment variables of the previous version (PCSIGHT_*) are still honoured; if both are set, the new one wins
os.environ.pop("PC_CONTROL_MODE", None); os.environ["PCSIGHT_MODE"] = "bypass"
c["environment: PCSIGHT_MODE (old name) is still honoured"] = policy.mode() == "bypass"
os.environ["PC_CONTROL_MODE"] = "strict"
c["environment: if both are set, PC_CONTROL_MODE wins"] = policy.mode() == "strict"
os.environ.pop("PC_CONTROL_MODE"); os.environ.pop("PCSIGHT_MODE")

# ---- ASK mode: ALL windows are asked about; auto only asks about the ones in the filter ----
for proc in ("winword", "notepad", "chrome", "calc", "explorer"):
    c[f"ask requires permission for {proc}"] = raises(policy.check_window, "ask", proc, "Document", False, set())
    c[f"ask lets {proc} through with the user's yes"] = not raises(policy.check_window, "ask", proc, "Document", False, set(), True)
c["ask keeps the protected categories"] = raises(policy.check_window, "ask", "keepass", "x", False, set())
c["ask also asks when opening any app"] = raises(policy.check_launch, "ask", "calc", set())
c["auto does NOT ask when opening a normal app"] = not raises(policy.check_launch, "auto", "calc", set())
c["bypass never asks (not even in ask)"] = not raises(policy.check_window, "bypass", "winword", "x", True, set())
e = None
try: policy.check_window("ask", "winword", "Report", True, set(), False, 4242)
except policy.NeedsPermission as ex: e = ex
c["the permission key is PER WINDOW (program + hwnd), not per program"] = e is not None and e.key == policy.window_key(policy.ASK_ALL, "winword", 4242)
c["two windows of the same program have different keys"] = policy.window_key("window", "winword", 1) != policy.window_key("window", "winword", 2)
c["needs_ask: auto/ask/bypass"] = (policy.needs_ask("auto", "winword") is None and policy.needs_ask("ask", "winword") == policy.ASK_ALL
                                   and policy.needs_ask("ask", "keepass") != policy.ASK_ALL and policy.needs_ask("bypass", "keepass") is None)
c["ask mode is accepted"] = (os.environ.__setitem__("PC_CONTROL_MODE", "ask") or policy.mode()) == "ask"
os.environ.pop("PC_CONTROL_MODE", None)

# ---- extended list (every name I could think of) ----
EXTRA = {
    "manager/authenticator": ("keeper", "protonpass", "authy", "2fas", "veracrypt", "gpg4win", "psono", "passbolt"),
    "terminal": ("alacritty", "wezterm-gui", "mobaxterm", "termius", "securecrt", "ttermpro", "cmder", "conemu64", "kali", "ubuntu", "wslhost", "openconsole", "git-bash"),
    "admin": ("regedt32", "devmgmt", "diskmgmt", "services", "compmgmt", "lusrmgr", "procexp64", "processhacker", "autoruns", "certmgr", "msiexec", "winlogon"),
    "remote": ("teamviewer", "rustdesk", "ultravnc", "radmin", "screenconnect", "quickassist", "msra", "vmware", "virtualbox", "mremoteng"),
    "crypto/finance": ("ledgerlive", "trezor-suite", "electrum", "bitcoin-qt", "metamask", "coinomi", "revolut", "ynab"),
    "AI": ("chatgpt", "cursor", "windsurf", "ollama", "lmstudio", "claude"),
    "mail": ("outlook", "olk", "thunderbird", "emclient", "mailspring", "protonmail"),
    "messaging": ("whatsapp", "telegram", "signal", "discord", "slack", "ms-teams", "zoom", "skype", "webex"),
    "vpn/security": ("openvpn", "nordvpn", "wireguard", "tailscale", "mullvad", "wireshark", "burpsuite", "bitdefender", "avast", "malwarebytes", "ghidra"),
    "signing/identity": ("autofirma", "dnie", "docusign", "yubikey", "opensc"),
    "development": ("docker desktop", "filezilla", "winscp", "dbeaver", "heidisql", "postman", "ssms", "kubectl"),
}
for cat, procs in EXTRA.items():
    for proc in procs:
        c[f"auto protects {proc} ({cat})"] = policy.needs_ask("auto", proc) is not None
# and office, leisure and normal utility apps still do NOT ask in auto
for proc in ("winword", "excel", "powerpnt", "notepad", "calc", "mspaint", "explorer", "vlc", "spotify", "chrome", "firefox", "msedge", "code",
             "photoshop", "blender", "steam", "robloxstudiobeta", "obs64", "gimp", "sumatrapdf", "acrord32", "notepad++", "snippingtool"):
    c[f"auto does NOT ask about {proc}"] = policy.needs_ask("auto", proc, "Untitled document") is None
# new titles (other languages, specific banks, credentials, cloud consoles)
for title in ("Your IBAN - Chrome", "BBVA - Global position", "Santander personal", "Revolut Business", "Enter your CVV", "Seed phrase backup",
              "Sign in - Google Accounts", "Sign in to your account", "AWS Management Console", "Azure portal", "Inbox (3) - Gmail",
              "Agencia Tributaria - Renta", "Verification code", "Private key SSH", "WhatsApp Web"):
    c[f"auto protects the title '{title[:24]}'"] = policy.needs_ask("auto", "chrome", title) is not None
for title in ("Quarterly report - Word", "Downloads", "Roblox Studio - Place1", "Kitchen: pasta recipes", "Document1 - Word", "Calculator", "Map of Madrid - Chrome"):
    c[f"auto does NOT protect the title '{title[:24]}'"] = policy.needs_ask("auto", "chrome", title) is None

# ---- by default it FOLLOWS Claude Code's permission mode ----
os.environ.pop("PC_CONTROL_MODE", None)
c["without knowing Claude Code's mode: behaves like auto"] = policy.mode() == "auto"
for cc, want in (("bypassPermissions", "bypass"), ("auto", "auto"), ("default", "auto"), ("plan", "auto"), ("acceptEdits", "auto"), ("dontAsk", "auto")):
    perm.set_cc_mode(cc)
    c[f"Claude Code in '{cc}' -> PC-Control '{want}'"] = policy.mode() == want
perm.set_cc_mode("bypassPermissions")
c["Claude Code in bypass: does not ask even for a terminal"] = not raises(policy.check_window, policy.mode(), "windowsterminal", "x", True, set())
perm.set_cc_mode("default")
c["Claude Code in normal mode: a window that is not in the filter does NOT ask"] = not raises(policy.check_window, policy.mode(), "winword", "Doc", True, set())
c["... but one in the filter (terminal) DOES ask, in any mode other than bypass"] = raises(policy.check_window, policy.mode(), "windowsterminal", "x", True, set())
perm.set_cc_mode("auto")
c["Claude Code in auto: normal window passes, terminal asks"] = (not raises(policy.check_window, policy.mode(), "winword", "Doc", True, set())
                                                                      and raises(policy.check_window, policy.mode(), "windowsterminal", "x", True, set()))
os.environ["PC_CONTROL_MODE"] = "strict"; perm.set_cc_mode("bypassPermissions")
c["a PC_CONTROL_MODE set by hand overrides Claude Code's"] = policy.mode() == "strict"
os.environ["PC_CONTROL_MODE"] = "follow"; c["PC_CONTROL_MODE=follow follows Claude Code"] = policy.mode() == "bypass"
os.environ.pop("PC_CONTROL_MODE", None)
c["one session's mode does not mix with another's"] = (setattr(perm, "session_id", lambda: "other-1") or policy.mode()) == "auto"

bad = [k for k, v in c.items() if not v]
for k in bad: print("FAIL", k)
print(f"policy verification passed ({len(c)} cases)" if not bad else f"policy verification FAILED ({len(bad)} of {len(c)})")
sys.exit(1 if bad else 0)
