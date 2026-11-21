"""G1: the research document exists, cites its sources with links and records every decision the code depends on."""
import os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
p = os.path.join(ROOT, "docs", "RESEARCH-JAVA-RDP.md")
fails = []
if not os.path.exists(p):
    print("research verification FAILED: docs/RESEARCH-JAVA-RDP.md missing"); sys.exit(1)
s = open(p, encoding="utf-8").read()
links = set(re.findall(r"\]\((https?://[^)\s]+)\)", s))
if len(links) < 10:
    fails.append(f"only {len(links)} distinct source links (need >= 10)")
for section in ("## 1.", "## 2. Java", "## 3.", "## 4. Remote Desktop", "## 5. Tools borrowed", "## 6. Limits"):
    if section not in s:
        fails.append(f"missing section {section}")
for decision in ("JAVA_TOOL_OPTIONS", "pc-control java enable", "doAccessibleActions", "setTextContents", "modal dialog", "WM_ACTIVATE",
                 "scancode", "WM_CHAR", "IMsRdpInputSink", "SendKeys", "aardwolf", "hand_back", "empty foreground", "pixel", "loop guard",
                 "1250"):
    if decision.lower() not in s.lower():
        fails.append(f"decision not recorded: {decision}")
# every code-level decision mentioned exists in the code (the document must not describe code that is not there)
code = "".join(open(os.path.join(ROOT, "pc_control", f), encoding="utf-8").read() for f in os.listdir(os.path.join(ROOT, "pc_control")) if f.endswith(".py"))
for sym in ("hand_back", "setTextContents", "doAccessibleActions", "WM_ACTIVATE", "pixel_change", "_loop_check", "JAVA_TOOL_OPTIONS"):
    if sym not in code:
        fails.append(f"the document cites {sym} but the code does not have it")
if fails:
    print("research verification FAILED:", *fails, sep="\n  "); sys.exit(1)
print(f"research verification passed ({len(links)} source links)")
