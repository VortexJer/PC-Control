"""Real PowerPoint on the hidden desktop: read() gives every slide's text and its speaker notes. Skipped if PowerPoint is not installed."""
import os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
PPT = r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE"
if not os.path.exists(PPT):
    print("powerpoint verification passed (skipped: PowerPoint not installed)"); sys.exit(0)
from pc_control import actions, office
from pc_control.hidden import DESK

ok = False
try:
    DESK.open(f'"{PPT}" /S')
    R = DESK.run; hw = None
    for _ in range(45):
        time.sleep(1)
        w = [h for h, t, _ in R(lambda: DESK._enum()) if R(lambda h=h: office.is_powerpoint(h))]
        if w:
            hw = w[0]; break
    assert hw, "PowerPoint did not open"
    time.sleep(2)

    def build():
        import pythoncom, win32com.client
        pythoncom.CoInitialize()
        app = win32com.client.GetActiveObject("PowerPoint.Application")
        pres = app.Presentations.Count and app.Presentations(1) or app.Presentations.Add()
        if pres.Windows.Count == 0:
            pres.NewWindow()
        s1 = pres.Slides.Add(1, 1); s1.Shapes(1).TextFrame.TextRange.Text = "Quarterly review"; s1.Shapes(2).TextFrame.TextRange.Text = "Sales up 12%"
        s1.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange.Text = "Mention the new market"
        s2 = pres.Slides.Add(2, 2); s2.Shapes(1).TextFrame.TextRange.Text = "Next steps"
        tb = s2.Shapes.AddTable(2, 2); tb.Table.Cell(1, 1).Shape.TextFrame.TextRange.Text = "Owner"; tb.Table.Cell(2, 2).Shape.TextFrame.TextRange.Text = "Q3"
        return None
    hw = R(build) or hw
    out = R(lambda: actions.read_text(str(hw), None, 0, 4000))
    print(out)
    ok = all(k in out for k in ("PowerPoint presentation (COM)", "--- slide 1 ---", "Quarterly review", "Sales up 12%", "(notes) Mention the new market",
                                "--- slide 2 ---", "Next steps", "Owner", "Q3"))
finally:
    DESK.close()
print("powerpoint verification " + ("passed" if ok else "FAILED"))
sys.exit(0 if ok else 1)
