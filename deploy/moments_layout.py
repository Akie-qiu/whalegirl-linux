"""Small pixel helpers for the pinned Qt controls missing from AT-SPI."""
from collections import Counter
import struct
import zlib


def png_rows(path):
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Native comment capture is not PNG')
    offset, compressed = 8, bytearray()
    while offset < len(data):
        size = struct.unpack_from('>I', data, offset)[0]
        tag = data[offset+4:offset+8]
        block = data[offset+8:offset+8+size]
        if tag == b'IHDR':
            width, height, depth, color, _, _, interlace = struct.unpack('>IIBBBBB', block)
        elif tag == b'IDAT':
            compressed.extend(block)
        offset += size+12
    if depth != 8 or color not in (2, 6) or interlace or not (0 < width <= 2048 and 0 < height <= 2048):
        raise ValueError('Unsupported native comment capture')
    channels = 3 if color == 2 else 4
    stride = width*channels
    raw = zlib.decompress(compressed)
    if len(raw) != height*(stride+1):
        raise ValueError('Incomplete native comment capture')
    previous = bytearray(stride)
    rows = []
    for y in range(height):
        start = y*(stride+1)
        kind = raw[start]
        line = bytearray(raw[start+1:start+1+stride])
        if kind not in range(5):
            raise ValueError('Invalid PNG scanline filter')
        for x in range(stride):
            left = line[x-channels] if x >= channels else 0
            up = previous[x]
            corner = previous[x-channels] if x >= channels else 0
            if kind == 1:
                predictor = left
            elif kind == 2:
                predictor = up
            elif kind == 3:
                predictor = (left+up)//2
            elif kind == 4:
                p = left+up-corner
                distances = (abs(p-left), abs(p-up), abs(p-corner))
                predictor = (left, up, corner)[distances.index(min(distances))]
            else:
                predictor = 0
            line[x] = (line[x]+predictor) & 255
        rows.append(line if channels == 3 else bytearray(v for x, v in enumerate(line) if x % 4 != 3))
        previous = line
    return width, height, rows


def comment_dividers(width,rows):
    if width!=548:
        raise ValueError('Unrecognized pinned comment list width')
    # Detail rows draw separators across the content column, including wrapped text.
    dividers = []
    for y, row in enumerate(rows):
        colors = Counter(tuple(row[x*3:x*3+3]) for x in range(160, width-40))
        color, amount = colors.most_common(1)[0]
        if 225 <= min(color) <= max(color) <= 240 and amount >= (width-200)*.94:
            if not dividers or y > dividers[-1]+2:
                dividers.append(y)
    return dividers


def visible_comment_points(path,starts_at_top=False):
    width,height,rows=png_rows(path)
    dividers=comment_dividers(width,rows)
    edges=[0,*[y+1 for y in dividers if y>1],height]
    bands=list(zip(edges,edges[1:]))
    # The first short band at the beginning is the likes/avatar strip.
    if starts_at_top and dividers and dividers[0]<70:bands=bands[1:]
    return [bottom-16 for top,bottom in bands if 24<=bottom-top<=220]


def timeline_comment_points(path):
    """Find text baselines in timeline comments, whose rows have no dividers."""
    width, height, rows = png_rows(path)
    if width != 548:
        raise ValueError('Unrecognized pinned timeline comment width')
    ink = [y for y, row in enumerate(rows) if sum(
        max(row[x*3:x*3+3]) < 190 for x in range(10, width-20)) >= 4]
    bands = []
    for y in ink:
        if bands and y <= bands[-1][-1]+2:
            bands[-1].append(y)
        else:
            bands.append([y])
    return [(band[0]+band[-1])//2 for band in bands if 5 <= len(band) <= 28]


def comment_bands(path, count):
    width, height, rows = png_rows(path)
    if not 1 <= count <= 60:
        raise ValueError('Unrecognized pinned comment list layout')
    dividers=comment_dividers(width,rows)
    if len(dividers) == count:
        start, dividers = dividers[0]+1, dividers[1:]
    elif len(dividers) == count-1:
        start = 12
    else:
        raise ValueError('Comment row count differs from synchronized comment IDs')
    edges = [start, *[y+1 for y in dividers], height]
    bands = list(zip(edges, edges[1:]))
    if any(not 40 <= bottom-top <= 220 for top, bottom in bands):
        raise ValueError('Comment rows are clipped or do not match the pinned layout')
    return bands


def reply_box(path):
    width, height, rows = png_rows(path)
    def green(row, x):
        r, g, b = row[x*3:x*3+3]
        return g > 130 and g-r > 30 and g-b > 15
    ys = [y for y, row in enumerate(rows)
          if sum(green(row, x) for x in range(width)) >= max(180, width*.5)]
    if not ys or not 65 <= ys[-1]-ys[0] <= 180:
        raise ValueError('Native recipient reply composer is not visible')
    top, bottom = ys[0], ys[-1]
    xs = [x for x in range(width) if sum(green(rows[y], x) for y in range(top, bottom+1)) > (bottom-top)*.6]
    if not xs or not 250 <= xs[-1]-xs[0] <= 600:
        raise ValueError('Native reply composer border is ambiguous')
    return {'x': xs[0], 'y': top, 'width': xs[-1]-xs[0]+1, 'height': bottom-top+1}
