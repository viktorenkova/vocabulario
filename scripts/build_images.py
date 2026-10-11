"""Original simple pictograms, drawn locally; no downloaded media or font dependency."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "data/images"


def build():
    ROOT.mkdir(parents=True, exist_ok=True)
    for word in (
        "casa",
        "sol",
        "luna",
        "árbol",
        "manzana",
        "pera",
        "libro",
        "mesa",
        "silla",
        "huevo",
        "flor",
        "montaña",
    ):
        image = Image.new("RGB", (640, 480), "#f6f2e9")
        d = ImageDraw.Draw(image)
        if word == "casa":
            d.rectangle((150, 210, 490, 420), fill="#f4c76b", outline="#343d52", width=7)
            d.polygon([(110, 220), (320, 60), (530, 220)], fill="#c96455", outline="#343d52", width=7)
            d.rectangle((280, 300, 355, 420), fill="#61514c")
            d.rectangle((180, 265, 235, 320), fill="#91cbe3", outline="#343d52", width=5)
            d.rectangle((400, 265, 455, 320), fill="#91cbe3", outline="#343d52", width=5)
        elif word == "sol":
            import math

            for i in range(12):
                a = i * math.pi / 6
                d.line(
                    (
                        320 + 130 * math.cos(a),
                        240 + 130 * math.sin(a),
                        320 + 180 * math.cos(a),
                        240 + 180 * math.sin(a),
                    ),
                    fill="#df9b28",
                    width=12,
                )
            d.ellipse((210, 130, 430, 350), fill="#f7c34c", outline="#df9b28", width=7)
        elif word == "luna":
            d.ellipse((180, 70, 460, 410), fill="#f3ce74")
            d.ellipse((280, 40, 510, 345), fill="#f6f2e9")
        elif word == "árbol":
            d.rectangle((285, 250, 355, 420), fill="#956949")
            for bounds in [(180, 130, 355, 315), (285, 125, 465, 315), (230, 60, 410, 235)]:
                d.ellipse(bounds, fill="#5b9870", outline="#397055", width=5)
        elif word in {"manzana", "pera"}:
            d.line((320, 130, 330, 75), fill="#785c41", width=12)
            d.ellipse((335, 65, 415, 105), fill="#56834d")
            if word == "manzana":
                d.ellipse((170, 135, 365, 390), fill="#d86155")
                d.ellipse((280, 135, 470, 390), fill="#d86155")
            else:
                d.ellipse((265, 105, 375, 285), fill="#a8be65")
                d.ellipse((205, 210, 435, 410), fill="#a8be65")
        elif word == "libro":
            d.rounded_rectangle((180, 85, 465, 405), radius=14, fill="#507fa5", outline="#343d52", width=7)
            d.rectangle((180, 365, 450, 395), fill="#fffaf0", outline="#343d52", width=4)
            d.line((220, 90, 220, 360), fill="#343d52", width=6)
            d.rectangle((270, 145, 410, 160), fill="#a8c4d5")
        elif word == "mesa":
            d.rectangle((120, 150, 520, 200), fill="#b18460", outline="#78583d", width=5)
            for x in (150, 445):
                d.rectangle((x, 200, x + 30, 410), fill="#b18460", outline="#78583d", width=5)
        elif word == "silla":
            d.rectangle((230, 80, 410, 240), fill="#b18460", outline="#78583d", width=5)
            d.rectangle((220, 240, 425, 280), fill="#b18460", outline="#78583d", width=5)
            for x in (235, 380):
                d.rectangle((x, 280, x + 25, 415), fill="#b18460")
        elif word == "huevo":
            d.ellipse((215, 75, 425, 405), fill="#fffdfa", outline="#b69e85", width=6)
        elif word == "flor":
            d.line((320, 210, 320, 420), fill="#56834d", width=14)
            d.ellipse((325, 300, 410, 345), fill="#56834d")
            for bounds in [
                (240, 100, 330, 210),
                (310, 100, 400, 210),
                (210, 180, 320, 270),
                (320, 180, 430, 270),
                (275, 220, 365, 330),
            ]:
                d.ellipse(bounds, fill="#d887a3")
            d.ellipse((280, 175, 360, 255), fill="#f4c76b")
        else:
            d.polygon([(80, 410), (290, 70), (500, 410)], fill="#8796a8")
            d.polygon([(240, 150), (290, 70), (340, 150), (307, 140), (290, 169), (268, 145)], fill="#ffffff")
            d.polygon([(345, 410), (440, 190), (575, 410)], fill="#a3adb5")
        image.save(ROOT / (word + ".png"))
    print("12 original pictograms created")


if __name__ == "__main__":
    build()
