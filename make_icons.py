"""Run once to (re)create the app icons: python make_icons.py"""
from PIL import Image, ImageDraw

INK, AMBER, AMBER_DARK = "#0C1B24", "#FFB547", "#C98418"

def make_icon(size, rounded, path):
    S = 1024  # draw big, then shrink for smooth edges
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle((0, 0, S, S), radius=int(S * 0.22), fill=INK)
    else:
        d.rectangle((0, 0, S, S), fill=INK)   # full square: needed for maskable / iOS icons
    left, right, top, bottom = 0.22 * S, 0.78 * S, 0.20 * S, 0.72 * S
    radius = int(S * 0.09)
    d.rounded_rectangle((left, top + 0.05 * S, right, bottom + 0.05 * S), radius=radius, fill=AMBER_DARK)  # keycap side
    d.rounded_rectangle((left, top, right, bottom), radius=radius, fill=AMBER)                             # keycap top
    cx, cy = S / 2, (top + bottom) / 2
    d.rectangle((cx - 0.15 * S, cy - 0.15 * S, cx + 0.15 * S, cy - 0.085 * S), fill=INK)  # T bar
    d.rectangle((cx - 0.035 * S, cy - 0.085 * S, cx + 0.035 * S, cy + 0.15 * S), fill=INK)  # T stem
    img.resize((size, size), Image.LANCZOS).save(path)

make_icon(192, True, "public/icons/icon-192.png")
make_icon(512, True, "public/icons/icon-512.png")
make_icon(512, False, "public/icons/icon-maskable-512.png")
make_icon(180, False, "public/icons/apple-touch-icon.png")
