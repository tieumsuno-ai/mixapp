"""Mode « Temps forts » : ne garder que le meilleur passage de chaque morceau.

On découpe le morceau en phrases de 4 mesures (calées sur la grille), et on
note chaque phrase :
  - son énergie (volume global et graves : le refrain / le drop « tape ») ;
  - sa répétition : un refrain revient plusieurs fois avec les mêmes accords
    (similarité des profils harmoniques « chroma » entre phrases éloignées).
Les phrases les mieux notées, consécutives, forment les refrains. On entre
une ou deux phrases avant le 1er refrain (pour garder la montée) et on sort
à la fin d'un refrain, sur une frontière de phrase :
  - court : ~1 min à 1 min 50 (le 1er refrain) ;
  - moyen : ~2 min à 3 min 20 (jusqu'à la fin du 2e refrain si possible).
"""

from __future__ import annotations

import numpy as np

from .transitions import SR, Track

MODES = {
    # entrée (phrases avant le refrain), durée mini / maxi visée (s)
    "on": dict(lead=2, min_s=110.0, max_s=200.0, runs=2),
}
MODES["moyen"] = MODES["court"] = MODES["on"]        # anciens réglages


def _features(t: Track):
    """Chroma, énergie et graves par mesure."""
    y = t.audio.mean(axis=1)[::2].astype(np.float32)          # 22 050 Hz suffit
    sr = SR // 2
    n_fft, hop = 4096, 2048
    n_frames = 1 + (len(y) - n_fft) // hop
    if n_frames < 8:
        return None
    win = np.hanning(n_fft).astype(np.float32)
    import time
    spec = np.empty((n_frames, n_fft // 2 + 1), np.float32)
    base = np.arange(n_fft)[None, :]
    for i0 in range(0, n_frames, 256):                          # petits blocs (interface fluide)
        idx = base + hop * np.arange(i0, min(i0 + 256, n_frames))[:, None]
        spec[i0:i0 + 256] = np.abs(np.fft.rfft(y[idx] * win, axis=1))
        time.sleep(0)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    band = (freqs >= 110) & (freqs <= 4000)
    pc = (np.round(12 * np.log2(freqs[band] / 440.0)) % 12).astype(int)
    chroma_f = np.zeros((n_frames, 12), np.float32)
    for k in range(12):
        chroma_f[:, k] = spec[:, band][:, pc == k].sum(axis=1)
    low_f = spec[:, (freqs >= 40) & (freqs <= 150)].sum(axis=1)
    tot_f = (spec ** 2).sum(axis=1)
    frame_t = (np.arange(n_frames) * hop + n_fft / 2) / sr

    bar = t.bar
    n_bars = int((t.grid_end - t.first_beat) / bar)
    which = np.floor((frame_t - t.first_beat) / bar).astype(int)
    chroma = np.zeros((n_bars, 12))
    energy = np.full(n_bars, -120.0)
    low = np.full(n_bars, -120.0)
    for b in range(n_bars):
        m = which == b
        if m.any():
            c = chroma_f[m].mean(axis=0)
            chroma[b] = c / (np.linalg.norm(c) + 1e-9)
            energy[b] = 10 * np.log10(tot_f[m].mean() + 1e-12)
            low[b] = 20 * np.log10(low_f[m].mean() + 1e-9)
    return chroma, energy, low, n_bars


def _z(x):
    s = np.std(x)
    return (x - np.mean(x)) / s if s > 1e-9 else np.zeros_like(x)


def find(t: Track) -> dict | None:
    """Repère les refrains. Renvoie dict(phrases=[(début_s, score)], runs=[(début_s, fin_s)])."""
    if not t.analyzed:
        return None
    f = _features(t)
    if f is None:
        return None
    chroma, energy, low, n_bars = f
    P = 4
    n_ph = n_bars // P
    if n_ph < 6:
        return None
    vec = np.stack([chroma[p * P:(p + 1) * P].reshape(-1) for p in range(n_ph)])
    vec /= np.linalg.norm(vec, axis=1, keepdims=True) + 1e-9
    sim = vec @ vec.T
    rep = np.zeros(n_ph)
    for p in range(n_ph):
        far = [q for q in range(n_ph) if abs(q - p) >= 3]
        rep[p] = np.sort(sim[p, far])[-2:].mean() if len(far) >= 2 else 0.0
    E = np.array([energy[p * P:(p + 1) * P].mean() for p in range(n_ph)])
    Lw = np.array([low[p * P:(p + 1) * P].mean() for p in range(n_ph)])
    start = [t.first_beat + p * P * t.bar for p in range(n_ph)]
    # On ne cherche le refrain qu'entre l'intro et l'outro « DJ »
    body = np.array([(t.intro_end - 1e-3 <= s < t.outro_start - 1e-3) for s in start])
    if body.sum() < 4:
        body[:] = True
    score = np.full(n_ph, -9.0)
    score[body] = _z(E[body]) + 1.3 * _z(rep[body]) + 0.5 * _z(Lw[body])
    thr = np.percentile(score[body], 65)
    hot = score >= thr
    # refrains = suites d'au moins 2 phrases fortes (on tolère 1 phrase de creux)
    runs, p = [], 0
    while p < n_ph:
        if hot[p]:
            q = p
            while q + 1 < n_ph and (hot[q + 1] or (q + 2 < n_ph and hot[q + 2])):
                q += 1
            if q - p + 1 >= 2:
                runs.append((p, q + 1))
            p = q + 1
        else:
            p += 1
    if not runs:
        best = int(np.argmax(score))
        runs = [(best, min(best + 2, n_ph))]
    ph = lambda k: t.first_beat + k * P * t.bar          # noqa: E731
    return dict(phrases=[(round(start[i], 3), round(float(score[i]), 2)) for i in range(n_ph)],
                runs=[(round(ph(a), 3), round(ph(b), 3)) for a, b in runs])


def window(t: Track, mode: str, info: dict | None = None, vocal=None,
           mix_bars: int = 4) -> dict | None:
    """Passage retenu : entrée, début du refrain et sortie (s)."""
    cfg = MODES.get(mode)
    if cfg is None or not t.analyzed:
        return None
    info = info if info is not None else find(t)
    if not info:
        return None
    runs = info["runs"]
    phrase = 4 * t.bar
    hl = runs[0][0]
    entry = max(t.first_beat, hl - cfg["lead"] * phrase)
    # sortie : fin du dernier refrain retenu, puis ajustée à la durée visée
    k = min(cfg["runs"], len(runs)) - 1
    ends = [r[1] for r in runs]
    exit_ = ends[k]
    while exit_ - entry < cfg["min_s"]:
        later = [e for e in ends if e > exit_]
        exit_ = later[0] if later else exit_ + phrase
        if exit_ >= t.outro_start:
            break
    while exit_ - entry > cfg["max_s"] and exit_ - phrase > hl + 2 * phrase:
        exit_ -= phrase
    exit_ = min(exit_, t.outro_start)
    if exit_ - entry < 30:                     # passage introuvable / trop court
        return None
    win = dict(entry=round(entry, 3), highlight=round(hl, 3), exit=round(exit_, 3))
    if vocal is not None and len(vocal):
        win.update(_respect_vocals(t, cfg, hl, entry, exit_, np.asarray(vocal, float), mix_bars))
    return win


SUNG = 0.25          # part d'une mesure chantée au-delà de laquelle on la considère « avec paroles »


def _respect_vocals(t: Track, cfg, hl, entry, exit_, vocal, L):
    """Règle stricte : on ne coupe jamais de paroles.

    - entrée : sur une phrase instrumentale avant le refrain ; s'il n'y en a
      pas assez près, le morceau démarre à son début (intro) ;
    - sortie : au début d'une phrase instrumentale (le morceau qui part ne joue
      plus que de la musique pendant le mélange) ; s'il n'y en a pas, on ne
      raccourcit pas : sortie normale à l'outro."""
    phrase = 4 * t.bar
    bar_of = lambda s: int(round((s - t.first_beat) / t.bar))          # noqa: E731

    def inst(start_s, n=L):
        b0 = bar_of(start_s)
        if b0 < 0 or b0 + n > len(vocal):
            return False
        seg = vocal[b0:b0 + n]
        return bool(seg.mean() < 0.15 and seg.max() < 0.4)

    k_max = int((t.outro_start - t.first_beat) / phrase + 1e-6)
    grid = [t.first_beat + k * phrase for k in range(0, k_max + 1)]
    out = {"echo_in": False, "echo_out": False}

    starts = [s for s in grid if s + L * t.bar <= hl + 1e-6 and inst(s)]
    new_entry = starts[-1] if (starts and hl - starts[-1] <= 12 * phrase) else t.first_beat
    out["entry"] = round(new_entry, 3)

    lo, hi = cfg["min_s"] * 0.8, cfg["max_s"] * 1.3
    cands = [e for e in grid if e >= hl + 2 * phrase and e <= t.outro_start + 1e-6 and inst(e)]
    good = [e for e in cands if lo <= e - new_entry <= hi]
    target = (cfg["min_s"] + cfg["max_s"]) / 2
    if good:
        best = min(good, key=lambda e: abs(e - new_entry - target))
    elif cands:
        best = min(cands, key=lambda e: abs(e - new_entry - target))
    else:
        best = t.outro_start                    # rien d'instrumental : on ne coupe pas
    out["exit"] = round(best, 3)
    return out


def vocal_bars(frames: np.ndarray, t: Track, hop_s: float = 0.25) -> np.ndarray:
    """Activité vocale par trame (0/1 ou probabilité) → part chantée de chaque mesure."""
    n_bars = int((t.grid_end - t.first_beat) / t.bar) + 1
    out = np.zeros(n_bars)
    for b in range(n_bars):
        a = t.first_beat + b * t.bar
        i0, i1 = int(a / hop_s), int((a + t.bar) / hop_s)
        seg = frames[i0:i1]
        out[b] = float(np.mean(seg)) if len(seg) else 0.0
    return out


def view(t: Track, win: dict | None, now: float | None = None, mix_bars: int = 4) -> Track:
    """Copie légère du morceau dont l'intro / l'outro sont remplacées par le
    passage retenu : le moteur de transitions s'en sert tel quel. `now` :
    position actuelle (le morceau en cours ne peut pas sortir dans le passé)."""
    base = t.row.get("_base") or t.row
    if not win:
        return t if t.row is base else Track(row=dict(base), audio=t.audio, gain=t.gain,
                                              sound_end=t.sound_end)
    row = dict(base)
    row["_base"] = base
    exit_ = win["exit"]
    if now is not None and exit_ < now + 12.0:
        # activé en cours de morceau : on sort à la prochaine phrase disponible
        phrase = 4 * t.bar
        k = np.ceil((now + 12.0 - t.first_beat) / phrase)
        exit_ = min(t.first_beat + k * phrase, base.get("outro_start") or t.grid_end)
    row["outro_start"] = exit_
    row["intro_end"] = win["entry"] + mix_bars * t.bar   # le morceau entre à `entry`
    row["_hl_entry"] = win["entry"]
    row["_hl_mix_bars"] = mix_bars
    row["_hl_echo_in"] = bool(win.get("echo_in"))
    row["_hl_echo_out"] = bool(win.get("echo_out")) and win.get("exit") == exit_
    return Track(row=row, audio=t.audio, gain=t.gain, sound_end=t.sound_end)
