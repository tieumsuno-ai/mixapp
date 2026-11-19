"""Effets d'arrêt façon platine : arrêt 33 tours et « pull-up » (rembobinage scratché).

Chaque fonction prend l'audio source, la position de départ (échantillons) et le
tempo, et renvoie l'audio de la fin (à jouer à la place de la suite du morceau).
"""
from __future__ import annotations

import numpy as np
import pedalboard

from .transitions import SR, _declick_join, _hpf_sweep, _slice


def _varispeed(src: np.ndarray, pos: int, speed: np.ndarray) -> np.ndarray:
    """Lit src à partir de pos à la vitesse donnée échantillon par échantillon
    (1 = normal, 0 = arrêt, négatif = à l'envers), comme un disque sous la main :
    la hauteur suit la vitesse."""
    p = pos + np.concatenate([[0.0], np.cumsum(speed[:-1])])
    p = np.clip(p, 0, len(src) - 2)
    i = p.astype(np.int64)
    f = (p - i).astype(np.float32)[:, None]
    return src[i] * (1 - f) + src[i + 1] * f


def render_turntable_stop(src: np.ndarray, pos: int, bpm: float, seconds: float = 1.8) -> np.ndarray:
    """Arrêt 33 tours : le plateau ralentit doucement jusqu'à l'arrêt."""
    n = int(seconds * SR)
    t = np.linspace(0, 1, n)
    speed = (1 - t) ** 1.6                        # freinage progressif, plus doux à la fin
    out = _varispeed(src, pos, speed).astype(np.float32)
    # filtre qui se referme avec la vitesse (le son s'assombrit)
    lp = pedalboard.Pedalboard([pedalboard.LowpassFilter(cutoff_frequency_hz=18000)])
    y = np.empty_like(out)
    blk = 1024
    for s in range(0, n, blk):
        v = speed[min(s + blk // 2, n - 1)]
        lp[0].cutoff_frequency_hz = float(250 + 17000 * v ** 2)
        y[s:s + blk] = lp(np.ascontiguousarray(out[s:s + blk].T), SR, reset=False).T
    k = int(0.03 * SR)
    y[-k:] *= np.linspace(1, 0, k, dtype=np.float32)[:, None]
    _declick_join(y, src[pos:pos + 441])
    return y


def render_pullup(src: np.ndarray, pos: int, bpm: float) -> np.ndarray:
    """« Pull-up » de DJ : deux petits scratchs, rembobinage rapide du disque,
    le tout parti dans un écho qui s'éteint."""
    beat = 60.0 / bpm
    parts = []
    # 1) deux allers-retours de scratch sur un demi-temps chacun
    sc = int(beat / 2 * SR)
    t = np.linspace(0, 1, sc)
    for _ in range(2):
        parts.append(np.sin(2 * np.pi * t) * 1.6)          # avant puis arrière
    # 2) rembobinage : la main tire le disque en arrière, vite, puis le lâche
    rw = int(1.1 * SR)
    t = np.linspace(0, 1, rw)
    parts.append(-4.0 * np.sin(np.pi * t ** 0.6) * (1 - t) ** 0.4)
    speed = np.concatenate(parts)
    dry = _varispeed(src, pos, speed).astype(np.float32)
    env = np.ones(len(dry), np.float32)
    k = int(0.25 * SR)
    env[-k:] = np.linspace(1, 0, k) ** 2
    dry *= env[:, None]
    # 3) écho au demi-temps qui prolonge le geste sur 2 mesures
    tail = int(8 * beat * SR)
    x = np.zeros((len(dry) + tail, 2), np.float32)
    x[:len(dry)] = dry
    delay = pedalboard.Delay(delay_seconds=0.5 * beat, feedback=0.5, mix=1.0)
    wet = _hpf_sweep(delay(np.ascontiguousarray(x.T), SR).T, 250.0, 2000.0)
    fade = np.linspace(1, 0, len(x), dtype=np.float32) ** 1.4
    out = (x + 0.6 * wet * fade[:, None]).astype(np.float32)
    peak = np.abs(out).max()
    if peak > 0.95:
        out *= 0.95 / peak
    k = int(0.05 * SR)
    out[-k:] *= np.linspace(1, 0, k, dtype=np.float32)[:, None]
    _declick_join(out, src[pos:pos + 441])
    return out
