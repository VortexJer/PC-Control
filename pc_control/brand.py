"""The same tool for every client; only what people see is adapted. Under Codex (PC_CONTROL_CLIENT=codex, set by its registration)
the texts say Codex instead of Claude and cyan instead of orange, matching the cursor's colour (cursor.accent). Applied to tool
descriptions, answers, errors, permission questions and on-screen notices; never to behaviour."""
import re
from . import envvars

_CODEX = [(re.compile(r"\b([Aa])n orange\b"), r"\1 cyan"), (re.compile(r"Claude Code"), "Codex"), (re.compile(r"Claude's"), "Codex's"),
          (re.compile(r"\bClaude\b"), "Codex"), (re.compile(r"\borange\b"), "cyan"), (re.compile(r"\bOrange\b"), "Cyan"),
          (re.compile(r"\bnaranja\b"), "cian")]


def client():
    return "codex" if envvars.get("CLIENT", "").strip().lower() == "codex" else "claude"


def text(s):
    """`s` as the current client should read it (unchanged under Claude)."""
    if not isinstance(s, str) or client() != "codex":
        return s
    for rx, new in _CODEX:
        s = rx.sub(new, s)
    return s
