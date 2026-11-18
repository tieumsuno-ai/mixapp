"""Génère des MP3 de test synthétiques au BPM et à la tonalité connus.

Structure de chaque morceau : intro 16 mesures (kick + charley),
corps 32 mesures (+ basse + accords), outro 16 mesures (kick + charley).

    python tools/make_test_tracks.py dossier_de_sortie
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100
NOTE = {"C": 0, "C#": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "Ab": 8,
        "A": 9, "Bb": 10, "B": 11}

TRACKS = [
    # dossier,            artiste,          titre,               bpm,   tonalité
    ("House",             "Daft Pünk",      "Around the Loop",   124.0, "Am"),
    ("House",             "Beyoncé",        "Deep Groove",       126.0, "C"),
    ("House",             "Kerri Chandler", "Rain",              128.0, "Em"),
    ("House",             "Folamour",       "Ivory",             122.0, "Dm"),
    ("Hip-Hop",           "Nas",            "Boom Bap",           92.0, "Gm"),
    ("Hip-Hop",           "MF Doom",        "Rhymes",             88.0, "F"),
    ("Chill/Été 2026",    "Bonobo",         "Kerala",            110.0, "Bbm"),
    ("Chill/Été 2026",    "Tycho",          "Awake",              97.0, "D"),
]


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def kick(n):
    t = np.arange(n) / SR
    f = 50 + 70 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def hat(n, rng):
    t = np.arange(n) / SR
    x = rng.standard_normal(n)
    x = np.diff(x, prepend=0)  # passe-haut grossier
    return x * np.exp(-t * 60) * 0.15


def tone(freqs, n, amp):
    t = np.arange(n) / SR
    out = np.zeros(n)
    for f in freqs:
        for h, a in ((1, 1), (2, 0.4), (3, 0.2)):
            out += a * np.sin(2 * np.pi * f * h * t)
    env = np.minimum(1, t * 50) * np.minimum(1, (n - np.arange(n)) / (0.02 * SR))
    return out * env * amp / len(freqs)


def chords_for(key):
    minor = key.endswith("m")
    root = NOTE[key.rstrip("m")]
    if minor:  # i - VI - III - VII (progression mineure la plus courante en pop)
        degs = [(0, [0, 3, 7]), (8, [0, 4, 7]), (3, [0, 4, 7]), (10, [0, 4, 7])]
    else:      # I - IV - V - I
        degs = [(0, [0, 4, 7]), (5, [0, 4, 7]), (7, [0, 4, 7]), (0, [0, 4, 7])]
    return [(root + d, [root + d + i for i in iv]) for d, iv in degs]


def render(bpm, key, seed=0):
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    nb = int(round(beat * SR))
    lead_in = int(0.3 * SR)
    bars = [("intro", 16), ("body", 32), ("outro", 16)]
    total = lead_in + sum(b for _, b in bars) * 4 * nb + SR
    out = np.zeros(total)
    prog = chords_for(key)
    pos, bar_i = lead_in, 0
    for section, nbars in bars:
        for _ in range(nbars):
            bass_root, chord = prog[(bar_i // 2) % 4]
            for b in range(4):
                s = pos + b * nb
                out[s:s + nb] += 0.8 * kick(nb)
                h = s + nb // 2
                out[h:h + nb // 2] += hat(nb // 2, rng)
                if section == "body":
                    for e in range(2):  # basse en croches
                        bs = s + e * nb // 2
                        out[bs:bs + nb // 2] += tone([midi_hz(36 + bass_root % 12)], nb // 2, 0.5)
            if section == "body":
                out[pos:pos + 4 * nb] += tone([midi_hz(60 + n % 12) for n in chord], 4 * nb, 0.35)
            pos += 4 * nb
            bar_i += 1
    out /= np.abs(out).max() * 1.1
    return out.astype(np.float32)


def main(dest):
    dest = Path(dest)
    for i, (folder, artist, title, bpm, key) in enumerate(TRACKS):
        d = dest / folder
        d.mkdir(parents=True, exist_ok=True)
        y = render(bpm, key, seed=i)
        mp3 = d / f"{i + 1:02d} - {artist} - {title}.mp3"
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            sf.write(tmp.name, y, SR)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", tmp.name,
                            "-b:a", "192k",
                            "-metadata", f"title={title}", "-metadata", f"artist={artist}",
                            "-metadata", f"genre={folder.split('/')[0]}",
                            "-metadata", f"track={i + 1}", "-metadata", f"comment=bpm={bpm} key={key}",
                            str(mp3)], check=True)
        print(f"{mp3}  ({bpm} BPM, {key})")


if __name__ == "__main__":
    main(sys.argv[1])
