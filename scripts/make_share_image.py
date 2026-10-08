"""Fabrique l'image d'aperçu de partage du site (static/images/partage.png, 1200 x 630).

C'est l'image qu'affichent WhatsApp, Telegram, Facebook ou X quand quelqu'un colle un lien du site (balise og:image, voir
templates/_meta.html). Elle est la même pour toutes les pages et ne montre aucun chiffre : un chiffre écrit dans une image
ne suit pas les réglages du site (cote minimale, prix...).

À relancer seulement si le texte ou le style changent :   python scripts/make_share_image.py
Demande Playwright et Chromium (voir README, « Tests »). L'image produite est versionnée dans Git : le site n'a pas besoin de
ce script pour fonctionner. Après un changement, WhatsApp et Telegram gardent l'ancienne image en mémoire un moment : le nom
du fichier porte une empreinte (asset()), l'adresse change donc toute seule dès que l'image change.
"""
import base64
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "static", "images", "partage.png")
FONTS = os.path.join(ROOT, "static", "fonts")

TITRE = ("Pronostics foot", "Résultats publics")
SOUS_TITRE = "Trois combinés, la chance de réussite affichée, les gagnés comme les perdus."


def font_face(family, filename):
    with open(os.path.join(FONTS, filename), "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return (f'@font-face {{ font-family: "{family}"; font-weight: 100 900; '
            f'src: url(data:font/woff2;base64,{data}) format("woff2"); }}')


def html():
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><style>
{font_face("Big Shoulders Display", "big-shoulders-display-latin.woff2")}
{font_face("Onest", "onest-latin.woff2")}
* {{ box-sizing: border-box; margin: 0; }}
body {{ width: 1200px; height: 630px; overflow: hidden; background: #0a0f2c; color: #eef0f8;
       font-family: "Onest", sans-serif; position: relative; }}
.glow {{ position: absolute; right: -180px; top: -160px; width: 760px; height: 760px; border-radius: 50%;
        background: radial-gradient(circle, rgba(227,179,65,0.20) 0%, rgba(227,179,65,0) 68%); }}
.brand {{ position: absolute; left: 72px; top: 64px; display: flex; align-items: center; }}
.brand svg {{ width: 58px; height: 58px; margin-right: 16px; }}
.brand span {{ font-family: "Big Shoulders Display"; font-weight: 800; font-size: 44px; letter-spacing: 0.06em;
              text-transform: uppercase; line-height: 1; }}
h1 {{ position: absolute; left: 72px; top: 186px; font-family: "Big Shoulders Display"; font-weight: 800;
     text-transform: uppercase; font-size: 100px; line-height: 0.95; letter-spacing: 0.004em; }}
h1 em {{ display: block; font-style: normal; color: #e3b341; }}
p {{ position: absolute; left: 72px; top: 452px; width: 600px; font-size: 30px; line-height: 1.35; color: #a5add0; }}

/* un coupon dessiné : papier clair, souche dorée, aucune ligne lisible */
.slip {{ position: absolute; left: 836px; top: 118px; width: 290px; transform: rotate(5deg);
        filter: drop-shadow(0 22px 34px rgba(0,0,0,0.45)); }}
.paper {{ background: #e8eaf1; border-radius: 14px 14px 0 0; padding: 24px 24px 8px;
         -webkit-mask: radial-gradient(circle 11px at 0 100%, transparent 97%, #000) left / 51% 100% no-repeat,
                       radial-gradient(circle 11px at 100% 100%, transparent 97%, #000) right / 51% 100% no-repeat; }}
.title {{ height: 22px; width: 130px; border-radius: 11px; background: #0a0f2c; margin-bottom: 18px; }}
.leg {{ display: flex; justify-content: space-between; align-items: center; padding: 16px 0;
       border-top: 2px solid rgba(10,15,44,0.16); }}
.leg i {{ display: block; height: 13px; border-radius: 7px; background: #a9afc8; }}
.leg i + i {{ margin-top: 11px; }}
.leg b {{ display: block; width: 62px; height: 32px; border-radius: 9px; background: #0a0f2c; }}
.stub {{ background: #e3b341; border-radius: 0 0 14px 14px; padding: 24px 24px 20px; margin-top: -1px;
        display: flex; justify-content: space-between; align-items: flex-end;
        -webkit-mask: radial-gradient(circle 11px at 0 0, transparent 97%, #000) left / 51% 100% no-repeat,
                      radial-gradient(circle 11px at 100% 0, transparent 97%, #000) right / 51% 100% no-repeat; }}
.stub b {{ display: block; width: 118px; height: 54px; border-radius: 12px; background: #0a0f2c; }}
.stub u {{ display: block; width: 70px; height: 30px; border-radius: 9px; background: rgba(10,15,44,0.55); }}
</style></head><body>
<div class="glow"></div>
<div class="brand">
  <svg viewBox="0 0 32 32"><path fill="#E3B341" d="M7 7h18a3 3 0 0 1 3 3v3.2a2.8 2.8 0 0 0 0 5.6V22a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-3.2a2.8 2.8 0 0 0 0-5.6V10a3 3 0 0 1 3-3z"/><rect x="9" y="11" width="11" height="2.2" rx="1.1" fill="#0A0F2C"/><rect x="9" y="14.9" width="14" height="2.2" rx="1.1" fill="#0A0F2C"/><rect x="9" y="18.8" width="8" height="2.2" rx="1.1" fill="#0A0F2C"/></svg>
  <span>Triple Elite VIP</span>
</div>
<h1>{TITRE[0]}<em>{TITRE[1]}</em></h1>
<p>{SOUS_TITRE}</p>
<div class="slip">
  <div class="paper">
    <div class="title"></div>
    <div class="leg"><div><i style="width:130px"></i><i style="width:92px"></i></div><b></b></div>
    <div class="leg"><div><i style="width:116px"></i><i style="width:104px"></i></div><b></b></div>
    <div class="leg"><div><i style="width:140px"></i><i style="width:80px"></i></div><b></b></div>
  </div>
  <div class="stub"><b></b><u></u></div>
</div>
</body></html>"""


def main():
    from playwright.sync_api import sync_playwright

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=1)
        page.set_content(html())
        page.evaluate("document.fonts.ready.then(() => document.fonts.size)")
        page.wait_for_timeout(300)
        page.screenshot(path=OUT)
        browser.close()
    print(f"{OUT}  ({os.path.getsize(OUT) // 1024} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
