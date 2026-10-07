"""Génère les icônes de l'application (static/icons/*.png) à partir du logo du site (celui de templates/base.html).

À relancer seulement si le logo change :   python scripts/make_icons.py
Demande Playwright et Chromium (voir README, « Tests »). Les images produites sont versionnées dans Git : le site
n'a pas besoin de ce script pour fonctionner.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "static", "icons")

NUIT = "#0A0F2C"       # fond du site
OR = "#E3B341"         # or du logo

# Le billet du logo (templates/base.html), recadré sur son contour : x 4 à 28, y 7 à 25.
BILLET = (
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="4 7 24 18" width="{{w}}" height="{{h}}">'
    f'<path fill="{OR}" d="M7 7h18a3 3 0 0 1 3 3v3.2a2.8 2.8 0 0 0 0 5.6V22a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-3.2a2.8 2.8 0 0 0 0-5.6V10a3 3 0 0 1 3-3z"/>'
    f'<rect x="9" y="11" width="11" height="2.2" rx="1.1" fill="{NUIT}"/>'
    f'<rect x="9" y="14.9" width="14" height="2.2" rx="1.1" fill="{NUIT}"/>'
    f'<rect x="9" y="18.8" width="8" height="2.2" rx="1.1" fill="{NUIT}"/>'
    f'</svg>'
)

# (fichier, taille en pixels, part de la largeur occupée par le billet).
# L'icône « maskable » reste dans la zone de sécurité (cercle central de 80 %) : Android peut la découper en rond.
ICONS = (
    ("icon-192.png", 192, 0.66),
    ("icon-512.png", 512, 0.66),
    ("icon-maskable-512.png", 512, 0.60),
    ("apple-touch-icon.png", 180, 0.66),
)


def render(browser, name, size, share):
    width = round(size * share)
    height = round(width * 18 / 24)
    page = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
    page.set_content(
        f'<!doctype html><html><body style="margin:0;width:{size}px;height:{size}px;overflow:hidden;'
        f'background:{NUIT};display:flex;align-items:center;justify-content:center">'
        + BILLET.format(w=width, h=height) + "</body></html>")
    path = os.path.join(OUT, name)
    page.screenshot(path=path, omit_background=False)
    page.close()
    return path


def main():
    from playwright.sync_api import sync_playwright

    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, size, share in ICONS:
            print(f"{render(browser, name, size, share)}  ({size}x{size})")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
