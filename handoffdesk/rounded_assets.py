"""Small antialiased nine-slice surfaces, generated locally without dependencies.

Only immutable PNG bytes are shared. PhotoImages belong to their interpreter
and are retained by the theme; hover and redraw do not create new images.
"""
from functools import lru_cache
import struct
import zlib


def _rgb(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _inside(x, y, left, top, right, bottom, radius):
    if x < left or y < top or x > right or y > bottom:
        return False
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def _chunk(kind, payload):
    return (struct.pack('!I', len(payload)) + kind + payload
            + struct.pack('!I', zlib.crc32(kind + payload) & 0xffffffff))


@lru_cache(maxsize=128)
def rounded_png(fill, outline=None, *, size=32, radius=9, stroke=1, inset=1):
    """Return an RGBA PNG suitable for a ttk image element's border slicing."""
    fill_rgb = _rgb(fill)
    edge_rgb = _rgb(outline or fill)
    samples = 4
    offsets = [(i + .5) / samples for i in range(samples)]
    rows = bytearray()
    outer = (inset, inset, size - inset, size - inset, radius)
    inner = (inset + stroke, inset + stroke, size - inset - stroke,
             size - inset - stroke, max(0, radius - stroke))
    for y in range(size):
        rows.append(0)
        for x in range(size):
            count = 0
            total = [0, 0, 0]
            for dy in offsets:
                for dx in offsets:
                    if not _inside(x + dx, y + dy, *outer):
                        continue
                    color = fill_rgb if _inside(x + dx, y + dy, *inner) else edge_rgb
                    count += 1
                    for channel in range(3):
                        total[channel] += color[channel]
            if count:
                rows.extend(round(value / count) for value in total)
                rows.append(round(255 * count / (samples * samples)))
            else:
                rows.extend((0, 0, 0, 0))
    header = struct.pack('!2I5B', size, size, 8, 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', header) + _chunk(b'IDAT', zlib.compress(bytes(rows))) + _chunk(b'IEND', b'')


@lru_cache(maxsize=8)
def chevron_png(color, width=12, height=10, stroke=1.5):
    """A small open chevron; no rectangle, native triangle or opaque backing."""
    rgb = _rgb(color)
    segments = ((1.5, 2.5, width / 2, 6.5), (width / 2, 6.5, width - 1.5, 2.5))

    def near_segment(x, y, line):
        ax, ay, bx, by = line
        dx, dy = bx - ax, by - ay
        t = min(1, max(0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        return (x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2 <= (stroke / 2) ** 2

    rows = bytearray()
    offsets = [(i + .5) / 4 for i in range(4)]
    for y in range(height):
        rows.append(0)
        for x in range(width):
            coverage = sum(any(near_segment(x + dx, y + dy, line) for line in segments)
                           for dx in offsets for dy in offsets)
            rows.extend((*rgb, round(255 * coverage / 16)))
    header = struct.pack('!2I5B', width, height, 8, 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', header) + _chunk(b'IDAT', zlib.compress(bytes(rows))) + _chunk(b'IEND', b'')
