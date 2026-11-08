"""Erzeugt das Programmsymbol installer/MarketIntel.ico (und ui/static/icon.png).

Nur zur Entwicklungszeit (Pillow, HPND-Lizenz); das Ergebnis wird eingecheckt.
Motiv: steigende Säulen mit Lupe – Marktanalyse. Aufruf: python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BLUE = (28, 92, 171, 255)
WHITE = (255, 255, 255, 255)
ACCENT = (235, 104, 52, 255)


def draw(size: int) -> Image.Image:
    scale = 4  # Überabtastung für glatte Kanten
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s * 0.2, fill=BLUE)
    base = s * 0.78
    width = s * 0.13
    for i, height in enumerate((0.2, 0.32, 0.44)):
        x = s * (0.12 + i * 0.16)
        d.rounded_rectangle((x, base - s * height, x + width, base), radius=width * 0.25, fill=WHITE)
    cx, cy, r = s * 0.68, s * 0.36, s * 0.13
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ACCENT, width=max(1, int(s * 0.055)))
    d.line(
        (cx + r * 0.7, cy + r * 0.7, cx + r * 1.45, cy + r * 1.45),
        fill=ACCENT,
        width=max(1, int(s * 0.065)),
    )
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big = draw(256)
    ico = ROOT / "installer" / "MarketIntel.ico"
    big.save(ico, sizes=[(n, n) for n in sizes], append_images=[draw(n) for n in sizes[:-1]])
    png = ROOT / "src" / "market_intel" / "ui" / "static" / "icon.png"
    draw(64).save(png, optimize=True)
    print(ico, png)


if __name__ == "__main__":
    main()
