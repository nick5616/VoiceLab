"""PNG in and out with numpy and zlib only (no imaging library): the lab writes its screenshots with
write_png (filter 0, so reading them back is one decompress), and read_png reads those fast and any other
8-bit RGB/RGBA PNG more slowly. Plus the few pixel operations the evals need: crops, outlines, scaling."""
import struct
import zlib

import numpy as np

SIGNATURE = b"\x89PNG\r\n\x1a\n"
LEVEL = 3                       # zlib level: screenshots compress well already at 3, and it is quick
CHANNELS = {2: 3, 6: 4, 0: 1, 4: 2}     # PNG colour type -> channels (grey, grey+alpha, RGB, RGBA)


def _chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_png(pixels, level=LEVEL):
    """PNG bytes for an (h, w, 3|4) uint8 array."""
    pixels = np.ascontiguousarray(pixels, dtype=np.uint8)
    h, w, c = pixels.shape
    kind = {3: 2, 4: 6}[c]
    raw = np.zeros((h, w * c + 1), dtype=np.uint8)      # each row: filter byte 0, then the pixels
    raw[:, 1:] = pixels.reshape(h, w * c)
    header = struct.pack(">IIBBBBB", w, h, 8, kind, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw.tobytes(), level)) + \
        _chunk(b"IEND", b"")


def write_png(path, pixels, level=LEVEL):
    with open(path, "wb") as f:
        f.write(encode_png(pixels, level))


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(data, h, stride, bpp):
    rows = np.frombuffer(data, dtype=np.uint8).reshape(h, stride + 1)
    filters, body = rows[:, 0], rows[:, 1:].copy()
    if not filters.any():
        return body
    prev = np.zeros(stride, dtype=np.uint8)
    for y in range(h):
        line, kind = body[y], filters[y]
        if kind == 1:                               # Sub: a running sum per channel
            px = line.reshape(-1, bpp).astype(np.int64)
            line[:] = (np.cumsum(px, axis=0) % 256).astype(np.uint8).reshape(-1)
        elif kind == 2:                             # Up
            line[:] = (line.astype(np.int16) + prev) % 256
        elif kind in (3, 4):                        # Average, Paeth: sequential, byte by byte
            out = line.astype(np.int64).tolist()
            up = prev.astype(np.int64).tolist()
            for i in range(stride):
                a = out[i - bpp] if i >= bpp else 0
                c = up[i - bpp] if i >= bpp else 0
                pred = (a + up[i]) // 2 if kind == 3 else _paeth(a, up[i], c)
                out[i] = (out[i] + pred) % 256
            line[:] = np.array(out, dtype=np.uint8)
        prev = line
    return body


def decode_png(blob):
    """(h, w, channels) uint8 array from PNG bytes (8-bit, non-interlaced)."""
    if blob[:8] != SIGNATURE:
        raise ValueError("not a PNG")
    pos, idat, header = 8, [], None
    while pos < len(blob):
        length, kind = struct.unpack(">I4s", blob[pos:pos + 8])
        data = blob[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            idat.append(data)
        elif kind == b"IEND":
            break
    w, h, depth, color, _, _, interlace = header
    if depth != 8 or interlace or color not in CHANNELS:
        raise ValueError(f"unsupported PNG (depth {depth}, colour type {color}, interlace {interlace})")
    c = CHANNELS[color]
    body = _unfilter(zlib.decompress(b"".join(idat)), h, w * c, c)
    return body.reshape(h, w, c)


def read_png(path):
    with open(path, "rb") as f:
        return decode_png(f.read())


def rgb(pixels):
    """The RGB channels of an image array (grey is repeated; alpha is dropped)."""
    if pixels.shape[2] >= 3:
        return pixels[:, :, :3]
    return np.repeat(pixels[:, :, :1], 3, axis=2)


def crop(pixels, rect):
    """pixels inside rect [x, y, w, h], clipped to the image."""
    x, y, w, h = (int(round(v)) for v in rect)
    H, W = pixels.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    return pixels[y0:max(y0, y1), x0:max(x0, x1)]


def outline(pixels, rect, color, width=2):
    """Draw a rectangle outline into an RGB(A) array in place."""
    x, y, w, h = (int(round(v)) for v in rect)
    H, W = pixels.shape[:2]
    ink = np.array(list(color) + [255] * (pixels.shape[2] - len(color)), dtype=np.uint8)[:pixels.shape[2]]
    for x0, y0, x1, y1 in ((x, y, x + w, y + width), (x, y + h - width, x + w, y + h),
                           (x, y, x + width, y + h), (x + w - width, y, x + w, y + h)):
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if x1 > x0 and y1 > y0:
            pixels[y0:y1, x0:x1] = ink
    return pixels


def fill(pixels, rect, color, alpha=0.25):
    """Blend a colour over a rectangle in place."""
    region = crop(pixels, rect)
    if region.size:
        tint = np.array(color, dtype=float)
        region[:, :, :3] = (region[:, :, :3] * (1 - alpha) + tint * alpha).astype(np.uint8)
    return pixels


def shrink(pixels, factor):
    """Downscale by an integer factor (box average)."""
    if factor <= 1:
        return pixels
    h, w = pixels.shape[0] // factor * factor, pixels.shape[1] // factor * factor
    p = pixels[:h, :w].astype(np.float32)
    p = p.reshape(h // factor, factor, w // factor, factor, -1).mean(axis=(1, 3))
    return p.astype(np.uint8)
