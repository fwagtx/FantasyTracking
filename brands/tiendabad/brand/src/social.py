"""Social images for LA TIENDITA BAD, as SVG canvases (all text outlined)."""
import math, os, sys
from kit import P, Doc, primary, icon, horizontal, embed, word, BAD_GRAD
from textpath import font


def canvas(W, H, d):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            f'<defs>{"".join(d.defs)}</defs>{"".join(d.parts)}</svg>')


def background(d, W, H, cx, cy, R, rays=28, op=0.075):
    gid = d.uid("bg")
    d.defs.append(f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" cx="{cx}" cy="{cy}" r="{R}">'
                  f'<stop offset="0" stop-color="{P["turquesa_luz"]}"/><stop offset=".5" stop-color="{P["turquesa"]}"/><stop offset="1" stop-color="{P["turquesa_osc"]}"/></radialGradient>')
    d.parts.append(f'<rect width="{W}" height="{H}" fill="url(#{gid})"/>')
    L = 2.5 * max(W, H)
    for i in range(rays):
        a = i * 2 * math.pi / rays; b = a + math.pi / rays
        d.parts.append(f'<path d="M{cx},{cy} L{cx + L * math.cos(a):.0f},{cy + L * math.sin(a):.0f} L{cx + L * math.cos(b):.0f},{cy + L * math.sin(b):.0f} Z" fill="#fff" opacity="{op}"/>')


def fit(src, maxw, maxh):
    w, h = src.bb[2] - src.bb[0], src.bb[3] - src.bb[1]
    return min(maxw / w, maxh / h), w, h


def pill(d, text, size, x, y_mid, anchor="start", pad=24):
    dpath, bb = font("mont800").path(text, size, 0, anchor, x, 0)
    cap = -bb[1]
    base = y_mid + cap / 2 - 1
    dpath, bb = font("mont800").path(text, size, 0, anchor, x, base)
    h = cap + 2 * pad * 0.62
    d.parts.append(f'<rect x="{bb[0] - pad:.1f}" y="{y_mid - h / 2:.1f}" width="{bb[2] - bb[0] + 2 * pad:.1f}" height="{h:.1f}" rx="{h / 2:.1f}" fill="{P["ink"]}" fill-opacity=".86" stroke="#fff" stroke-opacity=".92" stroke-width="3"/>')
    d.parts.append(f'<path d="{dpath}" fill="#fff"/>')


def profile(logo, name, out):
    W = H = 1080; d = Doc()
    background(d, W, H, 540, 470, 760)
    lg = logo()
    s, w, h = fit(lg, 780 if logo is icon else 700, 600 if logo is icon else 660)
    embed(d, lg, (W - w * s) / 2, (H - h * s) / 2 + (6 if logo is icon else 0), s)
    open(os.path.join(out, name), "w").write(canvas(W, H, d))


def youtube(out):
    """2560x1440; phones show only the middle 1546x423 (x 507-2053, y 508-931), so everything sits in there."""
    W, H = 2560, 1440; d = Doc()
    lg = primary(); s, w, h = fit(lg, 560, 380)
    f = font("bc900i")
    t1 = f.path("LA TIENDA DE FORTNITE", 100, 2, "start", 0, 0)[1]; tw = t1[2] - t1[0]
    gap = 80; group = w * s + gap + tw
    lx = 507 + (1546 - group) / 2; ly = 720 - h * s / 2
    background(d, W, H, lx + w * s / 2, 720, 1500)
    embed(d, lg, lx, ly, s)
    tx = lx + w * s + gap
    word(d, "LA TIENDA DE FORTNITE", "bc900i", 100, tx, 652, P["blanco"], 9, tracking=2, anchor="start", ex=[(9, P["ink"])])
    word(d, "CADA DÍA, EN ESPAÑOL", "bc900i", 76, tx + 3, 750, P["amarillo"], 8, tracking=2, anchor="start", ex=[(7, P["ink"])])
    pill(d, "Código BAD · #EpicPartner", 28, tx + 26, 812)
    dpath, bb = font("mont800").path("Hecho por fans · sin afiliación con Epic Games", 22, 0, "start", tx + 4, 878)
    d.parts.append(f'<path d="{dpath}" fill="#fff" fill-opacity=".9" stroke="{P["ink"]}" stroke-width="5" stroke-linejoin="round" paint-order="stroke"/>')
    d.parts.append(f'<path d="{dpath}" fill="#fff"/>')
    assert tx + tw < 2053 - 20, "banner text leaves the safe area"
    open(os.path.join(out, "youtube-banner-2560x1440.svg"), "w").write(canvas(W, H, d))


def facebook(out):
    W, H = 1640, 624; d = Doc()
    background(d, W, H, 820, 250, 1100)
    lg = horizontal(); s, w, h = fit(lg, 880, 290)
    embed(d, lg, (W - w * s) / 2, 70, s)
    word(d, "La tienda de Fortnite cada día, en español", "mont900", 40, 820, 70 + h * s + 70, P["blanco"], 5, anchor="middle", ex=[(5, P["ink"])])
    pill(d, "Código BAD · #EpicPartner", 26, 820, 70 + h * s + 132, anchor="middle")
    open(os.path.join(out, "facebook-cover-1640x624.svg"), "w").write(canvas(W, H, d))


if __name__ == "__main__":
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    profile(icon, "profile-1080.svg", out)
    profile(primary, "profile-with-name-1080.svg", out)
    youtube(out); facebook(out)
    print("ok")
