"""Real Excel on the hidden desktop: read the cells, write values and formulas by address without touching the user's selection,
save with the save shortcut of Office's language. Skipped if Excel or openpyxl is not installed."""
import os, sys, tempfile, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
EXCEL = r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE"
try:
    import openpyxl
except ImportError:
    openpyxl = None
if not os.path.exists(EXCEL) or openpyxl is None:
    print("excel verification passed (skipped: Excel or openpyxl not installed)"); sys.exit(0)
from pc_control import actions, core, office
from pc_control.hidden import DESK

book = os.path.join(tempfile.gettempdir(), "pc-control_test_book.xlsx")
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"
ws.append(["Product", "Price", "Units"])
for i in range(1, 30):
    ws.append([f"Item {i}", i * 1.5, i])
wb.create_sheet("Other"); wb.save(book)
res = {}


def check(name, ok, detail=""):
    res[name] = bool(ok); print(("OK   " if ok else "FAIL ") + name + (f" | {str(detail)[:150]}" if detail else ""))


try:
    DESK.open(f'"{EXCEL}" /x "{book}"')
    R = DESK.run; hw = None
    for _ in range(45):
        time.sleep(1)
        w = [h for h, t, _ in R(lambda: DESK._enum()) if "pc-control_test_book" in t]
        if w:
            hw = w[0]; break
    assert hw, "Excel did not open the book"
    time.sleep(2)
    sel0 = R(lambda: office.word.call(lambda: office.excel_app(hw).Selection.Address))
    s = R(lambda: office.summary(hw))
    check("look explains how to reach the cells", "Data" in s and "A1:C30" in s, s)
    out = R(lambda: actions.read_text(str(hw)))
    check("read gives the cells with row numbers and column letters", "\tA\tB\tC" in out and "\n30\tItem 29\t43.5\t29" in out, out.split("\n")[0])
    out = R(lambda: office.write(hw, "B2", "99"))
    v = R(lambda: office.word.call(lambda: office.excel_app(hw).ActiveSheet.Range("B2").Value))
    check("a number is written as a number", v == 99 and "99" in out, out)
    out = R(lambda: office.write(hw, "D2", "=B2*C2"))
    check("a formula is written and computed", "«99»" in out, out)
    out = R(lambda: office.write(hw, "Other!A1", "hello"))
    v = R(lambda: office.word.call(lambda: office.excel_app(hw).ActiveWorkbook.Worksheets("Other").Range("A1").Value))
    check("another sheet by address", v == "hello", out)
    out = R(lambda: office.write(hw, "E2", "=1/0"))
    check("a formula error is reported", "WARNING" in out, out)
    out = R(lambda: office.write(hw, "E3", "=)(+"))
    check("an invalid formula is refused, not crashed", out.startswith("did not type"), out)
    sel1 = R(lambda: office.word.call(lambda: office.excel_app(hw).Selection.Address))
    check("the user's selection is not touched", sel0 == sel1, f"{sel0} -> {sel1}")
    combo = R(lambda: office.save_combo(hw)); other = ("ctrl", "s") if combo == ("ctrl", "g") else ("ctrl", "g")
    t0 = os.path.getmtime(book)
    out = R(lambda: actions.shortcut(str(hw), "+".join(combo))); time.sleep(1)
    check(f"{'+'.join(combo)} (Office's save in its language) saves through COM", os.path.getmtime(book) > t0, out)
    if other == ("ctrl", "s"):
        out = R(lambda: actions.shortcut(str(hw), "ctrl+s"))
        check("ctrl+s in a Spanish Office explains that save is ctrl+g", "ctrl+g" in out and out.startswith("did not"), out)
    check("office.is_cell recognises addresses, not ids", office.is_cell("B3") and office.is_cell("'My sheet'!A1:C2") and not office.is_cell("12"))
finally:
    DESK.close()
good = len(res) >= 10 and all(res.values())
print(f"excel verification {'passed' if good else 'FAILED'} ({sum(res.values())}/{len(res)} checks)")
sys.exit(0 if good else 1)
