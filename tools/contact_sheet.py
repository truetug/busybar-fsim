"""Склеить кадры fsim в одну PNG с увеличением: contact_sheet.py <out.png> <scale> <cols> frame1.png ..."""
import sys
import png


def load(path):
    width, height, rows, info = png.Reader(filename=path).asRGB8()
    return width, height, [list(row) for row in rows]


def main():
    out, scale, cols, paths = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4:]
    frames = [load(p) for p in paths]
    width = max(f[0] for f in frames) * scale
    height = max(f[1] for f in frames) * scale
    gap = 4
    rows_count = (len(frames) + cols - 1) // cols
    sheet_w = cols * (width + gap)
    sheet_h = rows_count * (height + gap)
    sheet = [[40] * (sheet_w * 3) for _ in range(sheet_h)]
    for index, (w, h, rows) in enumerate(frames):
        ox = (index % cols) * (width + gap)
        oy = (index // cols) * (height + gap)
        for y in range(h * scale):
            src = rows[y // scale]
            dst = sheet[oy + y]
            for x in range(w * scale):
                s = (x // scale) * 3
                d = (ox + x) * 3
                dst[d:d + 3] = src[s:s + 3]
    png.Writer(sheet_w, sheet_h, greyscale=False).write(open(out, "wb"), sheet)


main()
