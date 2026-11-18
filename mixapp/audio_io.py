"""Lecture des fichiers audio.

On utilise libsndfile (via soundfile, fourni tout compilé pour Mac et Windows)
et non le lecteur MP3 de pedalboard : sur certains MP3 (beaucoup d'extended
mixes du commerce), ce dernier décale un canal de plusieurs centaines de
millisecondes par rapport à l'autre, ce qui donne un son « en écho ».
"""

from __future__ import annotations

import numpy as np
import soundfile as sf
import soxr


def read(path: str, sr: int) -> np.ndarray:
    """Renvoie l'audio (échantillons, canaux) en float32 à la fréquence `sr`."""
    x, file_sr = sf.read(path, dtype="float32", always_2d=True)
    if file_sr != sr:
        x = soxr.resample(x, file_sr, sr, quality="HQ").astype(np.float32)
    return np.ascontiguousarray(x)


def read_stereo(path: str, sr: int) -> np.ndarray:
    x = read(path, sr)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return np.ascontiguousarray(x[:, :2])


def read_mono(path: str, sr: int) -> np.ndarray:
    return read(path, sr).mean(axis=1).astype(np.float32)
