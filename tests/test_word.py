"""Word: Claude types at ITS OWN point in the document, without depending on the user's selection, and the user can work at the same time.

Tested against a simulated Word (never against the user's real Word: lesson learned with Notepad). The simulation reproduces
what matters: bookmarks that shift when text is edited before them, formatting by ranges, the user's selection and rejected calls.
"""
import os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pywintypes
from pc_control import word

FMT = ("bold", "italic", "underline", "strike", "sub", "super")


class Doc:
    def __init__(self, text):
        self.text = text + "\r"                                      # a Word document always ends with a paragraph mark
        self.fmt = [dict.fromkeys(FMT, 0) for _ in self.text]
        self.align = [0] * len(self.text)
        self.marks = {}                                              # collapsed bookmarks: name -> position
        self.sel = [0, 0]                                            # the USER's selection
        self.busy = 0
        self.Bookmarks = Bookmarks(self)
        self.Content = type("C", (), {"End": property(lambda s: len(self.text))})()

    def insert(self, p, s):
        self.text = self.text[:p] + s + self.text[p:]
        self.fmt[p:p] = [dict(self.fmt[max(0, p - 1)]) for _ in s]   # inherits the previous character's format, like Word
        self.align[p:p] = [self.align[max(0, p - 1)]] * len(s)
        for k, v in self.marks.items():
            if v > p: self.marks[k] = v + len(s)
        for i in (0, 1):
            if self.sel[i] > p: self.sel[i] += len(s)

    def delete(self, a, b):
        n = b - a
        self.text = self.text[:a] + self.text[b:]; del self.fmt[a:b]; del self.align[a:b]
        for k, v in self.marks.items(): self.marks[k] = a if a < v <= b else (v - n if v > b else v)

    def Range(self, a, b): return R(self, a, b)
    def ComputeStatistics(self, kind): return len(self.text.split())


class Bookmarks:
    def __init__(self, doc): self.doc, self.ShowHidden = doc, False
    def Exists(self, name):
        if name.startswith("_") and not self.ShowHidden: return False      # hidden bookmarks are only visible with ShowHidden
        return name in self.doc.marks
    def __call__(self, name):                                        # as in Word: a Bookmark, which has .Range
        p = self.doc.marks[name]
        return type("Bookmark", (), {"Range": R(self.doc, p, p)})()
    def Add(self, name, rng): self.doc.marks[name] = rng.Start


class FontProxy:
    def __init__(self, r): object.__setattr__(self, "r", r)
    def __setattr__(self, name, val):
        key = {"Bold": "bold", "Italic": "italic", "Underline": "underline", "StrikeThrough": "strike", "Subscript": "sub", "Superscript": "super"}[name]
        for i in range(self.r.start, self.r.end): self.r.doc.fmt[i][key] = int(val)


class PF:
    def __init__(self, r): object.__setattr__(self, "r", r)
    def __setattr__(self, name, val):
        d, t = self.r.doc, self.r.doc.text
        a = t.rfind("\r", 0, self.r.start) + 1                       # the whole paragraph at each end
        b = t.find("\r", max(self.r.end - 1, self.r.start)) + 1
        for i in range(a, b): d.align[i] = val


class R:
    def __init__(self, doc, a, b): self.doc, self.start, self.end = doc, a, b
    Start = property(lambda s: s.start); End = property(lambda s: s.end)
    Text = property(lambda s: s.doc.text[s.start:s.end])
    Font = property(lambda s: FontProxy(s)); ParagraphFormat = property(lambda s: PF(s))
    def InsertAfter(self, s):
        if self.doc.busy > 0:                                        # Word is busy serving the user
            self.doc.busy -= 1
            raise pywintypes.com_error(-2147418111, "The call was rejected by the callee", None, None)
        self.doc.insert(self.end, s); self.end += len(s)
    def Delete(self): self.doc.delete(self.start, self.end); self.end = self.start


class Win:
    def __init__(self, doc): self.Hwnd, self.Document = 4242, doc
    def GetPoint(self, a, b, c, d, rng): return (100 + rng.Start, 300, 0, 20)


class App:
    def __init__(self, doc): self.Windows = [Win(doc)]


def fresh(text="Hello world.\rSecond paragraph."):
    d = Doc(text); word._get_app = lambda: App(d); word._pending.clear(); word.RETRY.update(tries=20, wait=0.01)
    return d

def para(d, i):
    ps = d.text.split("\r"); return ps[i]

c = {}
H = 4242
# 1) first use: goes to the end of the document (inside the last paragraph) and says where
d = fresh()
m = word.type_text(H, " One.")
c["first use: at the end of the document"] = para(d, 1) == "Second paragraph. One." and "paragraph 2" in m and "Second paragraph." in m
c["the reply says the paragraph, after which words, and that the selection is not touched"] = "your selection is not touched" in m and "words" in m

# 2) THE USER CLICKS SOMEWHERE ELSE: Claude's text still goes to its point
d.sel[:] = [3, 3]
word.type_text(H, " Two.")
c["the user clicks elsewhere: the text still goes to its point"] = para(d, 1) == "Second paragraph. One. Two." and para(d, 0) == "Hello world."
c["the user's selection does not move"] = d.sel == [3, 3]

# 3) the user edits BEFORE Claude's point: the bookmark shifts and stays in place
d.insert(0, "AAAA")
word.type_text(H, " Three.")
c["the user types before: Claude's point shifts with the text"] = para(d, 1) == "Second paragraph. One. Two. Three." and para(d, 0) == "AAAAHello world."

# 4) formatting: the buttons record a format for Claude's text, they do NOT touch the user's selection
d = fresh(); d.sel[:] = [0, 4]
msg = word.format_click(H, "Bold")
c["Bold: clear message and not 'None'"] = bool(msg) and "bold" in msg and "not to your selection" in msg
word.type_text(H, " BOLD")
n = len(" BOLD"); end = d.text.index(" BOLD") + n
c["what Claude types comes out bold"] = all(d.fmt[i]["bold"] == 1 for i in range(end - n, end))
c["the user's text (and selection) is NOT made bold"] = all(d.fmt[i]["bold"] == 0 for i in range(0, end - n))
word.format_click(H, "Bold")                                          # again: it is removed
word.type_text(H, " normal")
s = d.text.index(" normal")
c["pressing again removes the bold even if the previous text is bold (not inherited)"] = all(d.fmt[i]["bold"] == 0 for i in range(s, s + 7))
word.format_click(H, "Italic"); word.format_click(H, "UnderlineGallery"); word.type_text(H, " IU")
s = d.text.index(" IU")
c["italic and underline too"] = all(d.fmt[i]["italic"] == 1 and d.fmt[i]["underline"] == 1 for i in range(s, s + 3))
c["a button that is not a formatting one returns None (the normal click continues)"] = word.format_click(H, "InsertTab") is None and word.format_click(H, "") is None

# 5) alignment and paragraph breaks
d = fresh("Line1")
word.format_click(H, "AlignCenter"); word.type_text(H, "\nTitle")
p2 = d.text.index("Title")
c["a line break becomes a new paragraph (paragraph mark)"] = d.text.split("\r")[:2] == ["Line1", "Title"]
c["centered alignment only on Claude's paragraph"] = d.align[p2] == 1 and d.align[0] == 0
word.format_click(H, "AlignLeft"); word.type_text(H, "\rBody")
c["left alignment for the next paragraph"] = d.align[d.text.index("Body")] == 0 and d.align[p2] == 1

# 6) replace: replaces what Claude typed last, never the user's text
d = fresh("User text.")
word.type_text(H, " DRAFT")
word.type_text(H, " FINAL", replace=True)
c["replace only replaces Claude's last text"] = para(d, 0) == "User text. FINAL"

# 7) Word is busy with the user: rejected calls are retried
d = fresh(); d.busy = 3
t0 = time.time(); m = word.type_text(H, " busy")
c["Word busy (call rejected 3 times): it retries and types"] = para(d, 1).endswith(" busy") and d.busy == 0 and "ok (" in m
d = fresh(); d.busy = 10**6; word.RETRY.update(tries=3, wait=0.01)
try: word.type_text(H, " x"); c["Word endlessly busy: it ends up failing with the real error (does not hang)"] = False
except pywintypes.com_error: c["Word endlessly busy: it ends up failing with the real error (does not hang)"] = True
word.RETRY.update(tries=20, wait=0.01)

# 8) typing in small chunks so it is visible, with Claude's insertion point on screen
d = fresh(); seen = []
word.type_text(H, " abcdefgh", progress=lambda x, y, h: seen.append((x, y, h)))
c["with progress: types in chunks and reports where the point is each time"] = len(seen) >= 3 and seen == sorted(seen) and para(d, 1).endswith(" abcdefgh")
c["caret(): the position of Claude's point, not the user's caret"] = word.caret(H) is not None and word.caret(H)[2] == 20

# 9) a new document: the bookmark is per document, the first use goes to the end
d = fresh("Other doc")
c["new document: first use at the end"] = word.anchor_pos(d) == len(d.text) - 1

# 10) keys in Word: they go to CLAUDE's point, never to the user's caret (enter landed in the wrong paragraph in the real test)
from pc_control import core
real_cls = core.win32gui.GetClassName; core.win32gui.GetClassName = lambda h: "OpusApp" if h == H else real_cls(h)
core.find_window = lambda q: H
d = fresh("Hi.\rUser text")
word.type_text(H, " Claude."); d.sel[:] = [2, 2]                             # the user leaves their caret somewhere else
before = d.text
m = core.key(str(H), "enter")
c["enter in Word: done at Claude's point and says the paragraph"] = d.text == before + "\r" and "paragraph" in m and "Claude" in m
c["enter in Word: the user's caret does not move"] = d.sel == [2, 2]
word.type_text(H, "Z")
m = core.key(str(H), "backspace")
c["backspace in Word: deletes Claude's last character"] = d.text.endswith("\r") and "Z" not in d.text and m.startswith("ok")
core.key(str(H), "backspace")
c["backspace: never deletes the user's text or what Claude did not type"] = "did not delete" in core.key(str(H), "backspace") and d.text.startswith("Hi.\rUser text Claude.")
m = core.key(str(H), "left")
c["arrows in Word: not sent (they would move the user's caret)"] = m.startswith("did not press") and "YOUR caret" in m
core.win32gui.GetClassName = real_cls

bad = [k for k, v in c.items() if not v]
for k, v in c.items(): print(("OK   " if v else "FAIL ") + k)
print(f"word verification passed ({len(c)} checks)" if not bad else f"word verification FAILED ({len(bad)})")
sys.exit(1 if bad else 0)
