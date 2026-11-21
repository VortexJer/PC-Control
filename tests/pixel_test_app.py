"""Tk test app (no useful accessibility tree: the kind of app only pixels can verify). The button paints a square and counts clicks into
the file given as the first argument; the empty area does nothing."""
import sys, tkinter as tk

out = sys.argv[1] if len(sys.argv) > 1 else "pcc-pixel.txt"
title = sys.argv[2] if len(sys.argv) > 2 else "PCC Pixel Test"
n = {"clicks": 0}
root = tk.Tk(); root.title(title); root.geometry("520x360+120+120")
cv = tk.Canvas(root, width=220, height=220, bg="white", highlightthickness=0); cv.place(x=270, y=40)
lab = tk.Label(root, text="Clicks 0", font=("Segoe UI", 14)); lab.place(x=40, y=120)
txt = tk.Text(root, width=24, height=4, font=("Segoe UI", 11)); txt.place(x=40, y=200)
colors = ["#d33", "#3a3", "#33d", "#da3"]


def paint():
    n["clicks"] += 1
    cv.create_rectangle(20, 20, 200, 200, fill=colors[n["clicks"] % 4], outline="")
    lab.config(text=f"Clicks {n['clicks']}")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"{n['clicks']}\n{txt.get('1.0', 'end').strip()}")


def on_key(_e=None):
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"{n['clicks']}\n{txt.get('1.0', 'end').strip()}")


tk.Button(root, text="Pintar", font=("Segoe UI", 14), command=paint).place(x=40, y=40, width=160, height=50)
txt.bind("<KeyRelease>", on_key)
root.mainloop()
