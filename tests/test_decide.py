"""The cost/quality rule: never sends text that is empty, expensive or does not cover the window."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pc_control import core

cases = [  # (text_tok, img_tok, text_chars, blind, content) -> expected
    ((300, 851, 900, 2, 40), "text"),      # cheap and well covered: text
    ((0, 851, 0, 0, 0), "image"),          # empty text: image
    ((20, 851, 39, 1, 10), "image"),       # almost empty: image
    ((1650, 814, 4000, 2, 65), "image"),   # text more expensive than the image: image
    ((814, 814, 2900, 2, 65), "text"),     # tie: text wins (exact)
    ((300, 851, 900, 47, 48), "image"),    # the text does not cover the window: image
    ((300, 851, 900, 28, 48), "text"),     # 58%: under the threshold
    ((300, 851, 900, 29, 48), "image"),    # 60.4%: over the threshold
]
bad = [(args, want, core.choose(*args)[0]) for args, want in cases if core.choose(*args)[0] != want]
assert core.choose(0, 851, 0, 0, 0)[1].startswith("empty text"), "the reason must be explained"
# the image cost follows the documented formula ceil(w/28)*ceil(h/28)
assert core.img_cost(1456, 819) == 52 * 30 == 1560 and core.img_cost(640, 360) == 23 * 13 == 299
# the capture size adapts to the screen (it is not fixed) and the labels scale with the image
edges = {1280: 768, 1366: 768, 1920: 1024, 2560: 1344, 3840: 1344}
assert all(core.auto_edge(k) == v for k, v in edges.items()), {k: core.auto_edge(k) for k in edges}
assert core.auto_edge(1920) != core.auto_edge(3840) and core.auto_edge(1366) != core.auto_edge(1920)
assert core.mark_font(1024) == 11 and core.mark_font(1344) > 11 and core.mark_font(768) == 9 and core.mark_font(300) == 9
import win32gui
assert core.screen_long_for(win32gui.GetDesktopWindow()) >= 800, "a window's monitor has a reasonable size"
if bad:
    print("FAILURES:", bad); sys.exit(1)
print(f"decide verification passed ({len(cases)} cases)")
