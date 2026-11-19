"""Morceaux de démonstration (artistes et titres fictifs, pochettes générées)
pour les tutoriels. Musique synthétique, libre de droits.

    python tools/make_demo_tracks.py dossier_de_sortie
"""

import io
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from mutagen.id3 import APIC, ID3, TCON, TIT2, TPE1, TRCK
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from make_test_tracks import SR, render  # noqa: E402

DEMO = [
    # dossier,          artiste,             titre,               bpm,   clé,  couleurs
    ("Soirée Disco",    "Les Paillettes",    "Boule à facettes",  122.0, "Am", ("#ff5f9e", "#6a2cff")),
    ("Soirée Disco",    "Studio Lumière",    "Danse jusqu'au bout", 124.0, "C", ("#ffb347", "#ff3d68")),
    ("Soirée Disco",    "Funky Mirage",      "Nuit dorée",        124.0, "Em", ("#f9d423", "#e65c00")),
    ("Soirée Disco",    "Les Néons",         "Velours",           126.0, "G",  ("#00c6ff", "#7a00ff")),
    ("Apéro Latino",    "Sol y Ritmo",       "Playa Azul",        118.0, "Dm", ("#00d2a8", "#0061ff")),
    ("Apéro Latino",    "Cumbia Club",       "Fuego Lento",       120.0, "F",  ("#ff7a18", "#af002d")),
    ("Apéro Latino",    "Mar Caribe",        "Bailando Juntos",   120.0, "Am", ("#56ab2f", "#0a7d6b")),
]


def cover(c1, c2, seed):
    rng = np.random.default_rng(seed)
    w = 600
    a, b = np.array(Image.new("RGB", (1, 1), c1).getpixel((0, 0))), \
        np.array(Image.new("RGB", (1, 1), c2).getpixel((0, 0)))
    yy, xx = np.mgrid[0:w, 0:w] / w
    k = ((xx + yy) / 2)[..., None]
    img = Image.fromarray((a * (1 - k) + b * k).astype(np.uint8))
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(6):
        r = rng.integers(60, 220)
        x, y = rng.integers(0, w, 2)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, int(rng.integers(25, 70))))
    for i in range(8):                                   # sillons de vinyle
        r = 120 + i * 14
        d.ellipse([w / 2 - r, w / 2 - r, w / 2 + r, w / 2 + r], outline=(0, 0, 0, 40), width=2)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def main(dest):
    dest = Path(dest)
    for i, (folder, artist, title, bpm, key, cols) in enumerate(DEMO):
        d = dest / folder
        d.mkdir(parents=True, exist_ok=True)
        y = render(bpm, key, seed=10 + i)
        mp3 = d / f"{i + 1:02d} - {artist} - {title}.mp3"
        sf.write(str(mp3), y, SR, format="MP3")
        tags = ID3()
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        tags.add(TCON(encoding=3, text=folder))
        tags.add(TRCK(encoding=3, text=str(i + 1)))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=cover(*cols, i)))
        tags.save(str(mp3))
        print(mp3)


if __name__ == "__main__":
    main(sys.argv[1])
