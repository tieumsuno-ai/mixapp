"""Détection du chant (où il y a des paroles dans un morceau).

Modèle « Spleeter 2 stems » de Deezer (licence MIT), converti en ONNX et
exécuté avec onnxruntime : il estime la part de voix dans le spectrogramme.
On n'en garde qu'une carte « ça chante / ça ne chante pas » par quart de
seconde, mise en cache (une seule analyse par morceau, en fond).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np

SR = 44100
N_FFT, HOP, T_SEG, F_BINS = 4096, 1024, 512, 1024
HOP_S = 0.25                    # résolution de la carte (s)
MODEL = Path(__file__).parent / "models" / "spleeter2.onnx"

_sess = None
_lock = threading.Lock()


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return MODEL.exists()


def _session():
    global _sess
    with _lock:
        if _sess is None:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2          # ne pas monopoliser la machine pendant la soirée
            so.inter_op_num_threads = 1
            _sess = ort.InferenceSession(str(MODEL), so, providers=["CPUExecutionProvider"])
    return _sess


def _stft_mag(x: np.ndarray) -> np.ndarray:
    """|STFT| (frames, F_BINS, 2) comme Spleeter (fenêtre de Hann, 4096 / 1024)."""
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)
    pad = np.pad(x, ((N_FFT, N_FFT), (0, 0)))
    n = 1 + (len(pad) - N_FFT) // HOP
    out = np.empty((n, F_BINS, 2), np.float32)
    for c in range(2):
        idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
        for i0 in range(0, n, 2048):                  # par blocs (mémoire)
            fr = pad[idx[i0:i0 + 2048], c] * win
            out[i0:i0 + 2048, :, c] = np.abs(np.fft.rfft(fr, axis=1))[:, :F_BINS]
    return out


def activity(stereo: np.ndarray) -> np.ndarray:
    """Carte du chant : 1 = paroles, 0 = instrumental, par tranche de 0,25 s."""
    mag = _stft_mag(stereo.astype(np.float32))
    n = len(mag)
    n_seg = -(-n // T_SEG)
    padded = np.zeros((n_seg * T_SEG, F_BINS, 2), np.float32)
    padded[:n] = mag
    sess = _session()
    voc = np.zeros_like(padded)
    acc = np.zeros_like(padded)
    for s in range(n_seg):
        seg = padded[s * T_SEG:(s + 1) * T_SEG][None]
        v, a = sess.run(None, {"mix:0": seg})
        voc[s * T_SEG:(s + 1) * T_SEG] = v[0]
        acc[s * T_SEG:(s + 1) * T_SEG] = a[0]
    voc, acc = voc[:n], acc[:n]
    mask = voc ** 2 / (voc ** 2 + acc ** 2 + 1e-10)
    e_voc = ((mask * mag) ** 2).sum(axis=(1, 2))
    e_mix = (mag ** 2).sum(axis=(1, 2))
    # frames STFT (décalées de N_FFT par le remplissage) → tranches de 0,25 s
    t = (np.arange(n) * HOP - N_FFT + N_FFT / 2) / SR
    k = int(np.ceil(t[-1] / HOP_S)) + 1
    which = np.clip((t / HOP_S).astype(int), 0, k - 1)
    ev = np.bincount(which, e_voc, k)
    em = np.bincount(which, e_mix, k)
    cnt = np.maximum(np.bincount(which, minlength=k), 1)
    ref = np.percentile(em[em > 0] / cnt[em > 0], 95) if (em > 0).any() else 1.0
    vdb = 10 * np.log10(ev / cnt / ref + 1e-12)          # niveau de la voix / niveau fort du morceau
    rel = 10 * np.log10((ev + 1e-12) / (em + 1e-12))     # part de la voix dans le mélange
    return ((rel > -7) & (vdb > -24)).astype(np.uint8)


# ------------------------------------------------------------------ cache disque

class VocalCache:
    """Cartes du chant calculées en fond et gardées sur disque."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.mem: dict[str, np.ndarray] = {}

    def _file(self, key: str) -> Path:
        import hashlib
        return self.folder / (hashlib.md5(key.encode()).hexdigest() + ".npy")

    def get(self, key: str):
        if key in self.mem:
            return self.mem[key]
        f = self._file(key)
        if f.exists():
            try:
                a = np.load(f)
                self.mem[key] = a
                return a
            except Exception:
                return None
        return None

    def put(self, key: str, a: np.ndarray):
        self.mem[key] = a
        try:
            np.save(self._file(key), a)
        except Exception:
            pass
