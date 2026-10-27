"""Colour maths for the evals: WCAG relative luminance and contrast, and the text and background colours
sampled from a screenshot under a widget."""
import numpy as np

QUANT = 4                       # colours are grouped in steps of this much per channel before counting
MIN_INK_PIXELS = 3              # a sampled ink colour must cover at least this many pixels
NEAR = 24                       # a declared colour counts as drawn when a pixel is this close (max channel diff)


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in rgb[:3])


def from_hex(text):
    text = text.lstrip("#")
    if len(text) == 8:          # #aarrggbb (Qt's name(HexArgb))
        text = text[2:]
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def luminance(rgb):
    """WCAG 2.x relative luminance of an sRGB colour (0-255 channels)."""
    c = np.asarray(rgb[:3], dtype=float) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


def contrast(a, b):
    """WCAG contrast ratio of two colours (hex strings or RGB tuples), 1-21."""
    a = from_hex(a) if isinstance(a, str) else a
    b = from_hex(b) if isinstance(b, str) else b
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _colors(region):
    """(colours (n, 3) ints, counts) of a region after quantising, most common first."""
    px = region[:, :, :3].reshape(-1, 3).astype(np.int32) // QUANT * QUANT
    if not len(px):
        return np.zeros((0, 3), dtype=np.int32), np.zeros(0, dtype=np.int64)
    keys = (px[:, 0] << 16) | (px[:, 1] << 8) | px[:, 2]
    uniq, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts, kind="stable")
    uniq, counts = uniq[order], counts[order]
    cols = np.stack([(uniq >> 16) & 255, (uniq >> 8) & 255, uniq & 255], axis=1)
    return cols, counts


def sample(region, declared=None):
    """{"bg", "fg", "contrast", "declared_drawn"} of a text widget's pixels: the background is the most
    common colour, the ink the colour (covering MIN_INK_PIXELS or more) with the most contrast against it.
    With `declared` (the widget's own text colour, hex), the ink is that colour when some pixel is drawn
    close to it, since anti-aliasing rarely lets small text reach its true colour across a whole sample.
    None for an empty region."""
    cols, counts = _colors(region)
    if not len(cols):
        return None
    bg = cols[0]
    ink, best = bg, 1.0
    for col, n in zip(cols[1:], counts[1:]):
        if n < MIN_INK_PIXELS:
            break
        ratio = contrast(tuple(col), tuple(bg))
        if ratio > best:
            ink, best = col, ratio
    drawn = False
    if declared:
        want = np.array(from_hex(declared))
        px = region[:, :, :3].reshape(-1, 3).astype(np.int32)
        drawn = bool(len(px)) and bool((np.abs(px - want).max(axis=1) <= NEAR).sum() >= MIN_INK_PIXELS)
        if drawn:
            declared_ratio = contrast(tuple(want), tuple(bg))
            if declared_ratio > best:
                ink, best = want, declared_ratio
    return {"bg": to_hex(bg), "fg": to_hex(ink), "contrast": round(best, 2), "declared_drawn": drawn,
            "solid": bool(counts[0] / max(1, counts.sum()) > 0.3)}
