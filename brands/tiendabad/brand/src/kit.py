"""LA TIENDITA BAD logo system -> standalone SVG files (text outlined, no fonts needed)."""
import math, os, sys
from textpath import font

P = {
    "ink": "#0B0D1A", "rosa": "#E4007C", "rosa_osc": "#B0005F", "amarillo": "#FFD21E", "amarillo_luz": "#FFF3A8",
    "amarillo_sombra": "#FFC400", "oro": "#B06A00", "blanco": "#FFFFFF", "blanco_osc": "#E6DDE8",
    "turquesa": "#13B5C6", "turquesa_luz": "#3FD6E3", "turquesa_osc": "#0B8796", "noche": "#1A1F7A", "morado": "#2A0F5E",
}
UID = [0]
BAD_GRAD = [(0, P["amarillo_luz"]), (0.44, P["amarillo_luz"]), (0.46, P["amarillo"]), (1, P["amarillo_sombra"])]


class Doc:
    def __init__(self):
        self.defs, self.parts, self.bb, self.n = [], [], [1e9, 1e9, -1e9, -1e9], 0

    def uid(self, p):
        UID[0] += 1
        return f"{p}{UID[0]}"

    def grow(self, x0, y0, x1, y1):
        b = self.bb
        self.bb = [min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)]

    def svg(self, margin=24, square=False, bg=None, size=None):
        x0, y0, x1, y1 = self.bb
        x0 -= margin; y0 -= margin; x1 += margin; y1 += margin
        if square:
            w, h = x1 - x0, y1 - y0
            s = max(w, h)
            x0 -= (s - w) / 2; y0 -= (s - h) / 2; x1, y1 = x0 + s, y0 + s
        w, h = x1 - x0, y1 - y0
        dims = f' width="{size[0]}" height="{size[1]}"' if size else ""
        back = f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{bg}"/>' if bg else ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}"{dims}>'
                f'<defs>{"".join(self.defs)}</defs>{back}{"".join(self.parts)}</svg>'), (w, h)


def awning(doc, cx, top, width, n=9, canopy=108, valance=44, inset=46, ol=12, shadow=12, mono=None, rail=True):
    x0, x1 = cx - width / 2, cx + width / 2
    y0, y1, y2 = top, top + canopy, top + canopy + valance
    w = width / n; r = w / 2
    t0, t1 = x0 + inset, x1 - inset
    tw = (t1 - t0) / n
    sil = f"M{t0:.1f},{y0:.1f} L{t1:.1f},{y0:.1f} L{x1:.1f},{y1:.1f} L{x1:.1f},{y2:.1f} "
    for i in range(n - 1, -1, -1):
        sil += f"A{r:.1f},{r:.1f} 0 0 1 {x0 + i * w:.1f},{y2:.1f} "
    sil += f"L{x0:.1f},{y1:.1f} Z"
    canopy_i = lambda i: f"M{t0 + i * tw:.1f},{y0:.1f} L{t0 + (i + 1) * tw:.1f},{y0:.1f} L{x0 + (i + 1) * w:.1f},{y1:.1f} L{x0 + i * w:.1f},{y1:.1f} Z"
    val_i = lambda i: f"M{x0 + i * w:.1f},{y1:.1f} L{x0 + (i + 1) * w:.1f},{y1:.1f} L{x0 + (i + 1) * w:.1f},{y2:.1f} A{r:.1f},{r:.1f} 0 0 1 {x0 + i * w:.1f},{y2:.1f} Z"
    g = []
    if mono is None:
        ink = P["ink"]
        g.append(f'<path d="{sil}" transform="translate(0,{shadow})" fill="{ink}" stroke="{ink}" stroke-width="{2 * ol}" stroke-linejoin="round"/>')
        g.append(f'<path d="{sil}" fill="{ink}" stroke="{ink}" stroke-width="{2 * ol}" stroke-linejoin="round"/>')
        for i in range(n):
            a, b = (P["rosa"], P["rosa_osc"]) if i % 2 == 0 else (P["blanco"], P["blanco_osc"])
            g.append(f'<path d="{canopy_i(i)}" fill="{a}"/><path d="{val_i(i)}" fill="{b}"/>')
        g.append(f'<path d="M{x0:.1f},{y1:.1f} H{x1:.1f}" stroke="{ink}" stroke-width="{ol * 0.6:.1f}"/>')
        if rail:
            g.append(f'<rect x="{t0 - 18:.1f}" y="{y0 - 14:.1f}" width="{t1 - t0 + 36:.1f}" height="24" rx="12" fill="{ink}"/>')
        doc.grow(x0 - ol, y0 - 14, x1 + ol, y2 + r + ol + shadow)
    else:
        sw = ol * 1.1
        for i in range(0, n, 2):
            g.append(f'<path d="{canopy_i(i)}" fill="{mono}"/><path d="{val_i(i)}" fill="{mono}"/>')
        g.append(f'<path d="{sil}" fill="none" stroke="{mono}" stroke-width="{sw:.1f}" stroke-linejoin="round"/>')
        g.append(f'<path d="M{x0:.1f},{y1:.1f} H{x1:.1f}" stroke="{mono}" stroke-width="{sw * 0.7:.1f}"/>')
        if rail:
            g.append(f'<rect x="{t0 - 18:.1f}" y="{y0 - 14:.1f}" width="{t1 - t0 + 36:.1f}" height="24" rx="12" fill="{mono}"/>')
        doc.grow(x0 - sw / 2, y0 - 14, x1 + sw / 2, y2 + r + sw / 2)
    doc.parts.append("".join(g))
    return y2 + r


def word(doc, text, key, size, x, base, fill, ol, tracking=0.0, anchor="middle", ex=(), grad=None, mono=None):
    d, bb = font(key).path(text, size, tracking, anchor, x, base)
    g = []
    if mono is None:
        ink = P["ink"]
        for dy, col in ex:
            g.append(f'<path d="{d}" transform="translate(0,{dy})" fill="{col}" stroke="{col}" stroke-width="{2 * ol}" stroke-linejoin="round"/>')
        g.append(f'<path d="{d}" fill="{ink}" stroke="{ink}" stroke-width="{2 * ol}" stroke-linejoin="round"/>')
        if grad:
            gid = doc.uid("grad")
            doc.defs.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="0" y1="{bb[1]:.1f}" x2="0" y2="{bb[3]:.1f}">' +
                            "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in grad) + "</linearGradient>")
            g.append(f'<path d="{d}" fill="url(#{gid})"/>')
        else:
            g.append(f'<path d="{d}" fill="{fill}"/>')
        deep = max([dy for dy, _ in ex] or [0])
        doc.grow(bb[0] - ol, bb[1] - ol, bb[2] + ol, bb[3] + ol + deep)
    else:
        g.append(f'<path d="{d}" fill="{mono}"/>')
        doc.grow(*bb)
    doc.parts.append("".join(g))
    return bb


def plate(doc, x0, y0, x1, y1, fill, ol=12, shadow=10, rx=24, mono=None):
    if mono is None:
        doc.parts.append(f'<rect x="{x0:.1f}" y="{y0 + shadow:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" rx="{rx}" fill="{P["ink"]}" stroke="{P["ink"]}" stroke-width="{ol}"/>'
                         f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" rx="{rx}" fill="{fill}" stroke="{P["ink"]}" stroke-width="{ol}"/>')
        doc.grow(x0 - ol / 2, y0 - ol / 2, x1 + ol / 2, y1 + shadow + ol / 2)
    else:
        doc.parts.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" rx="{rx}" fill="none" stroke="{mono}" stroke-width="{ol * 1.1:.1f}"/>')
        doc.grow(x0 - ol, y0 - ol, x1 + ol, y1 + ol)


# ---------------- lockups ----------------
def primary(mono=None):
    d = Doc(); cx = 500; aw_top = 330
    m = font("bc900i").path("LA TIENDITA", 128, 6, "middle", cx, 0)[1]
    cap = m[3] - m[1]
    base = aw_top - 14 - 30
    padx = 42
    bb = (m[0], base - cap, m[2], base)
    plate(d, bb[0] - padx, bb[1] - 30, bb[2] + padx, aw_top - 14, P["rosa"], mono=mono)
    word(d, "LA TIENDITA", "bc900i", 128, cx, base, P["blanco"], 10, tracking=6, ex=[(8, P["ink"])], mono=mono)
    awning(d, cx, aw_top, 840, n=9, canopy=108, valance=44, inset=46, mono=mono)
    word(d, "BAD", "bc900i", 440, cx, 905, None, 22, ex=[(28, P["ink"]), (16, P["oro"])], grad=BAD_GRAD, mono=mono)
    return d


def icon(mono=None):
    d = Doc(); cx = 500
    awning(d, cx, 210, 860, n=9, canopy=120, valance=48, inset=50, mono=mono)
    word(d, "BAD", "bc900i", 470, cx, 830, None, 23, ex=[(30, P["ink"]), (17, P["oro"])], grad=BAD_GRAD, mono=mono)
    return d


def horizontal(mono=None):
    d = Doc(); size = 230; gap = size * 0.24
    f = font("bc900i")
    a = f.path("LA TIENDITA", size, 7, "start", 0, 0)[1]; b = f.path("BAD", size, 2, "start", 0, 0)[1]
    w1, w2 = a[2] - a[0], b[2] - b[0]; total = w1 + gap + w2
    x = 1000 - total / 2; base = 700; cap = a[3] - a[1]
    n = 15; width = total + 150
    awning(d, 1000, base - cap - 78 - 44 - 104 - 40, width, n=n, canopy=104, valance=44, inset=52, mono=mono)
    word(d, "LA TIENDITA", "bc900i", size, x, base, P["blanco"], 13, tracking=7, anchor="start", ex=[(18, P["ink"])], mono=mono)
    word(d, "BAD", "bc900i", size, x + w1 + gap, base, None, 13, tracking=2, anchor="start", ex=[(18, P["ink"]), (10, P["oro"])], grad=BAD_GRAD, mono=mono)
    return d


def watermark():
    """Video bug: mini awning over a dark pill reading LA TIENDITA BAD."""
    d = Doc(); size = 64; f = font("bc900i")
    a = f.path("LA TIENDITA", size, 2.5, "start", 0, 0)[1]; b = f.path("BAD", size, 1, "start", 0, 0)[1]
    w1, w2 = a[2] - a[0], b[2] - b[0]; gap = 16; total = w1 + gap + w2
    cx = 300; x = cx - total / 2; base = 236; cap = a[3] - a[1]
    px0, px1, py0, py1 = x - 34, x + total + 34, base - cap - 24, base + 24
    d.parts.append(f'<rect x="{px0:.1f}" y="{py0:.1f}" width="{px1 - px0:.1f}" height="{py1 - py0:.1f}" rx="{(py1 - py0) / 2:.1f}" fill="{P["ink"]}" fill-opacity=".82" stroke="#fff" stroke-opacity=".9" stroke-width="5"/>')
    d.grow(px0 - 3, py0 - 3, px1 + 3, py1 + 3)
    awning(d, cx, py0 - 84, (px1 - px0) * 0.62, n=7, canopy=44, valance=20, inset=16, ol=6, shadow=5, rail=False)
    word(d, "LA TIENDITA", "bc900i", size, x, base, P["blanco"], 0.01, tracking=2.5, anchor="start")
    word(d, "BAD", "bc900i", size, x + w1 + gap, base, P["amarillo"], 0.01, tracking=1, anchor="start")
    return d


def awning_only(width=800, n=9):
    d = Doc(); awning(d, 500, 200, width, n=n, canopy=76, valance=36, inset=40)
    return d


def bad_word():
    d = Doc(); word(d, "BAD", "bc900i", 440, 500, 800, None, 22, ex=[(28, P["ink"]), (16, P["oro"])], grad=BAD_GRAD)
    return d


def embed(dst, src, x, y, scale):
    """Place logo doc `src` with its top-left at (x, y), scaled."""
    bx0, by0, bx1, by1 = src.bb
    dst.defs += src.defs
    dst.parts.append(f'<g transform="translate({x:.1f},{y:.1f}) scale({scale:.4f}) translate({-bx0:.1f},{-by0:.1f})">' + "".join(src.parts) + "</g>")
    return (bx1 - bx0) * scale, (by1 - by0) * scale


def sunburst(doc, cx, cy, R, n=24, op=0.07, rot=0.0):
    for i in range(n):
        a = rot + i * 2 * math.pi / n; b = a + math.pi / n * 0.9
        doc.parts.append(f'<path d="M{cx},{cy} L{cx + R * math.cos(a):.0f},{cy + R * math.sin(a):.0f} L{cx + R * math.cos(b):.0f},{cy + R * math.sin(b):.0f} Z" fill="#fff" opacity="{op}"/>')


if __name__ == "__main__":
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    jobs = {
        "la-tiendita-bad-logo": primary(), "la-tiendita-bad-logo-white": primary(P["blanco"]), "la-tiendita-bad-logo-black": primary(P["ink"]),
        "la-tiendita-bad-icon": icon(), "la-tiendita-bad-icon-white": icon(P["blanco"]), "la-tiendita-bad-icon-black": icon(P["ink"]),
        "la-tiendita-bad-horizontal": horizontal(), "la-tiendita-bad-horizontal-white": horizontal(P["blanco"]), "la-tiendita-bad-horizontal-black": horizontal(P["ink"]),
        "la-tiendita-bad-watermark": watermark(),
        "video-awning": awning_only(), "video-bad": bad_word(),
    }
    for name, doc in jobs.items():
        svg, (w, h) = doc.svg(margin=8 if ("watermark" in name or "video-" in name) else 30, square="icon" in name)
        open(os.path.join(out, name + ".svg"), "w").write(svg)
        print(f"{name}: {w:.0f}x{h:.0f}")
