"""Draw the app icon (assets/icon.png + icon.ico): an open book with sound waves."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
OUT = Path(__file__).resolve().parent.parent / "assets"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # background: rounded square with a vertical gradient
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, bottom = (104, 150, 255), (52, 86, 214)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # open book
    white = (255, 255, 255, 255)
    soft = (226, 234, 255, 255)
    cx, by = S // 2, 690
    d.polygon([(cx, 380), (cx - 300, 330), (cx - 300, by - 10), (cx, by + 40)], fill=white)
    d.polygon([(cx, 380), (cx + 300, 330), (cx + 300, by - 10), (cx, by + 40)], fill=soft)
    d.line([(cx, 380), (cx, by + 40)], fill=(52, 86, 214, 255), width=14)
    for k in range(4):   # text lines on the left page
        y = 430 + k * 58
        d.line([(cx - 250, y - 12), (cx - 60, y + 6)], fill=(150, 170, 230, 255), width=18)
    # sound waves from the right page
    for k, r in enumerate((90, 160, 230)):
        bbox = [cx + 60 - r, 300 - r, cx + 60 + r, 300 + r]
        d.arc(bbox, start=-60, end=10, fill=(255, 255, 255, 255 - k * 55), width=34)
    img.save(OUT / "icon.png")
    img.resize((256, 256), Image.LANCZOS).save(OUT / "icon.ico",
                                               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()
