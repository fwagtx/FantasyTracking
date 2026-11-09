"""Text -> SVG path data, shaped with HarfBuzz (kerning) and outlined with fontTools, so the logo files need no fonts."""
import io, os
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

# Font files (free on Google Fonts). Point BRAND_FONTS at a folder holding them.
FONTS = os.environ.get("BRAND_FONTS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")) + "/"
FILES = {
    "bc900i": "BarlowCondensed-BlackItalic.ttf",
    "bc900": "BarlowCondensed-Black.ttf",
    "bc800i": "BarlowCondensed-ExtraBoldItalic.ttf",
    "lilita": "LilitaOne-Regular.ttf",
    "barlow800": "Barlow-ExtraBold.ttf",
    "mont800": ("Montserrat[wght].ttf", 800),
    "mont900": ("Montserrat[wght].ttf", 900),
}
_cache = {}


class Font:
    def __init__(self, key):
        spec = FILES[key]
        if isinstance(spec, tuple):  # variable font: pin the weight
            from fontTools.varLib.instancer import instantiateVariableFont
            t = instantiateVariableFont(TTFont(FONTS + spec[0]), {"wght": spec[1]})
        else:
            t = TTFont(FONTS + spec)
        t.flavor = None
        buf = io.BytesIO(); t.save(buf)
        self.tt = TTFont(io.BytesIO(buf.getvalue()))
        self.upm = self.tt["head"].unitsPerEm
        self.hb = hb.Font(hb.Face(hb.Blob(buf.getvalue())))
        self.gs = self.tt.getGlyphSet()
        self.order = self.tt.getGlyphOrder()

    def _layout(self, text, size, tracking):
        b = hb.Buffer(); b.add_str(text); b.guess_segment_properties()
        hb.shape(self.hb, b, {"kern": True})
        s = size / self.upm
        out, x = [], 0.0
        for info, pos in zip(b.glyph_infos, b.glyph_positions):
            out.append((self.order[info.codepoint], x + pos.x_offset * s, -pos.y_offset * s))
            x += pos.x_advance * s + tracking
        return out, s

    def path(self, text, size, tracking=0.0, anchor="middle", x=0.0, y=0.0):
        """SVG path data for text whose baseline is at y. anchor: start | middle (by ink bounds) | end."""
        glyphs, s = self._layout(text, size, tracking)
        bp = BoundsPen(self.gs)
        for name, gx, gy in glyphs:
            self.gs[name].draw(TransformPen(bp, (s, 0, 0, -s, gx, gy)))
        x0, y0, x1, y1 = bp.bounds  # in y-down space after the flip
        dx = {"start": -x0, "middle": -(x0 + x1) / 2, "end": -x1}[anchor] + x
        pen = SVGPathPen(self.gs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
        for name, gx, gy in glyphs:
            self.gs[name].draw(TransformPen(pen, (s, 0, 0, -s, gx + dx, gy + y)))
        return pen.getCommands(), (x0 + dx, y0 + y, x1 + dx, y1 + y)


def font(key):
    if key not in _cache:
        _cache[key] = Font(key)
    return _cache[key]
