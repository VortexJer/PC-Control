"""La regla de coste/calidad: nunca manda texto vacio, caro o que no cubre la ventana."""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pcsight import core

cases = [  # (text_tok, img_tok, text_chars, blind, content) -> esperado
    ((300, 851, 900, 2, 40), "text"),      # barato y bien cubierto: texto
    ((0, 851, 0, 0, 0), "image"),          # texto vacio: imagen
    ((20, 851, 39, 1, 10), "image"),       # casi vacio: imagen
    ((1650, 814, 4000, 2, 65), "image"),   # texto mas caro que la imagen: imagen
    ((814, 814, 2900, 2, 65), "text"),     # empate: gana el texto (exacto)
    ((300, 851, 900, 47, 48), "image"),    # el texto no cubre la ventana: imagen
    ((300, 851, 900, 29, 48), "text"),     # 60% justo no supera el umbral
    ((300, 851, 900, 30, 48), "image"),    # 62%: supera el umbral
]
bad = [(args, want, core.choose(*args)[0]) for args, want in cases if core.choose(*args)[0] != want]
assert core.choose(0, 851, 0, 0, 0)[1].startswith("texto vacio"), "el motivo debe explicarse"
# el coste de imagen sigue la formula documentada ceil(w/28)*ceil(h/28)
assert core.img_cost(1456, 819) == 52 * 30 == 1560 and core.img_cost(640, 360) == 23 * 13 == 299
if bad:
    print("FALLOS:", bad); sys.exit(1)
print(f"decide verification passed ({len(cases)} casos)")
