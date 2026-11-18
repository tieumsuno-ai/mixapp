"""Transitions entre deux morceaux.

Trois types, choisis automatiquement :

1. **Mix calé sur le tempo** (écart de tempo ≤ 8 %), à la manière d'un DJ :
   - la superposition commence sur une frontière de phrase (début de l'outro
     du morceau sortant) et dure autant que l'intro du morceau entrant (8 à
     32 mesures) : son « drop » tombe pile à la fin de la transition ;
   - pendant la superposition, le tempo glisse en douceur de celui du morceau
     sortant à celui du morceau entrant, temps par temps, les deux restant
     calés ; hors de la transition, chaque morceau joue à son tempo d'origine ;
   - le morceau entrant arrive sans basses mais à plein volume dès la 1re
     phrase ; échange des basses à mi-parcours, sur une mesure ; le morceau
     sortant s'efface sur la dernière phrase avec un filtre qui s'ouvre ;
   - les silences de fin de fichier sont ignorés.

2. **Sortie en écho** (tempos trop éloignés, ou touche « suivant ») :
   le morceau sortant part dans un écho calé sur son tempo, filtré, qui
   s'éteint sur 2 mesures pendant que le suivant démarre au temps suivant.

3. **Fondu enchaîné simple** (morceau non analysé).

Toutes les fonctions travaillent sur des tableaux (échantillons, 2 canaux)
float32 à 44,1 kHz.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pedalboard
from .audio_io import read_stereo
from scipy.signal import butter, sosfiltfilt

SR = 44100
MAX_STRETCH = 0.08       # au-delà de 8 % d'écart de tempo : pas de calage
MIX_MIN_BARS = 8         # superposition minimale…
MIX_MAX_BARS = 32        # … et maximale
BASS_SPLIT_HZ = 180.0    # séparation basses / reste pour l'échange des basses
STRETCH_LATENCY = 0.008   # retard des attaques après étirement (mesuré)
DEBUG_STEMS = False      # tests : garder les deux pistes séparées dans info["stems"]
TARGET_RMS = 0.16        # niveau cible (≈ -16 dBFS) pour égaliser les volumes


# ---------------------------------------------------------------- morceau chargé

@dataclass
class Track:
    row: dict
    audio: np.ndarray                 # (n, 2) float32, volume déjà égalisé
    gain: float = 1.0
    sound_end: float = 0.0            # fin du son (hors silence final), en s

    @property
    def id(self):
        return self.row["id"]

    @property
    def analyzed(self) -> bool:
        return bool(self.row.get("bpm"))

    @property
    def bpm(self) -> float:
        return float(self.row["bpm"])

    @property
    def beat(self) -> float:
        return 60.0 / self.bpm

    @property
    def bar(self) -> float:
        return 4 * self.beat

    @property
    def first_beat(self) -> float:
        return float(self.row.get("first_beat") or 0.0)

    @property
    def length(self) -> float:
        return len(self.audio) / SR

    @property
    def grid_end(self) -> float:
        """Fin de la mesure qui contient la fin du son (silence final ignoré)."""
        end = self.sound_end or self.length
        n = int(np.ceil((end - self.first_beat) / self.bar - 0.05))
        return self.first_beat + max(n, 1) * self.bar

    @property
    def intro_end(self) -> float:
        return float(self.row.get("intro_end") or self.first_beat)

    @property
    def outro_start(self) -> float:
        v = self.row.get("outro_start")
        return float(v) if v else self.grid_end

    def next_beat_after(self, t: float) -> float:
        k = np.ceil((t - self.first_beat) / self.beat)
        return self.first_beat + max(k, 0) * self.beat

    @property
    def label(self) -> str:
        return f"{self.row.get('artist') or '?'} — {self.row.get('title') or '?'}"


def load_track(row: dict) -> Track:
    a = read_stereo(row["path"], SR)
    # Égalisation du volume sur le « corps » du morceau
    t = Track(row=dict(row), audio=a)
    s0 = int(t.intro_end * SR) if t.analyzed else 0
    s1 = int(t.outro_start * SR) if t.analyzed else len(a)
    body = a[s0:s1] if s1 - s0 > SR * 5 else a
    rms = float(np.sqrt(np.mean(body ** 2))) if len(body) else 0.0
    g = float(np.clip(TARGET_RMS / rms, 0.3, 3.0)) if rms > 1e-5 else 1.0
    # Jamais d'écrêtage : un morceau peu compressé (années 80…) n'est monté
    # que jusqu'à ce que ses crêtes atteignent -0,5 dB.
    peak = float(np.percentile(np.abs(a), 99.995)) if len(a) else 0.0
    if peak > 1e-5:
        g = min(g, 0.94 / peak)
    t.audio *= g
    t.gain = g
    # Fin du son : dernière trame à moins de 30 dB sous le niveau du corps
    mono = np.abs(t.audio).max(axis=1)
    hop = 2048
    frames = mono[: len(mono) // hop * hop].reshape(-1, hop).max(axis=1)
    loud = np.where(frames > TARGET_RMS * 10 ** (-30 / 20))[0]
    t.sound_end = (loud[-1] + 1) * hop / SR if len(loud) else t.length
    return t


# ---------------------------------------------------------------- rendu d'une transition

@dataclass
class Transition:
    kind: str                 # "beatmatch", "echo", "fade"
    a_end: int                # échantillon du morceau sortant où commence le tampon
    buffer: np.ndarray        # audio de la transition (n, 2)
    b: Track                  # morceau entrant
    b_offset: int             # position dans le tampon où démarre le morceau entrant
    b_start: int              # échantillon du morceau entrant joué à b_offset
    info: dict = field(default_factory=dict)
    b_end: int | None = None  # échantillon de b à reprendre après le tampon (si b étiré)

    @property
    def b_resume(self) -> int:
        """Échantillon du morceau entrant à reprendre après le tampon."""
        if self.b_end is not None:
            return self.b_end
        return self.b_start + len(self.buffer) - self.b_offset


def _slice(audio: np.ndarray, s0: int, n: int) -> np.ndarray:
    out = np.zeros((n, 2), np.float32)
    s0c = max(s0, 0)
    seg = audio[s0c:s0 + n]
    out[s0c - s0:s0c - s0 + len(seg)] = seg
    return out


def _split_bass(x: np.ndarray):
    """Sépare basses / reste, à phase nulle : basses + reste = signal d'origine."""
    sos = butter(4, BASS_SPLIT_HZ, btype="low", fs=SR, output="sos")
    low = sosfiltfilt(sos, x, axis=0).astype(np.float32)
    return low, x - low


def _hpf_sweep(x: np.ndarray, f0: float, f1: float, chunk: int = 1024) -> np.ndarray:
    """Filtre passe-haut dont la fréquence monte de f0 à f1 (courbe exponentielle)."""
    hp1 = pedalboard.HighpassFilter(cutoff_frequency_hz=f0)
    hp2 = pedalboard.HighpassFilter(cutoff_frequency_hz=f0)
    board = pedalboard.Pedalboard([hp1, hp2])
    out = np.empty_like(x)
    n = len(x)
    for i in range(0, n, chunk):
        f = f0 * (f1 / f0) ** (i / max(n - 1, 1))
        hp1.cutoff_frequency_hz = hp2.cutoff_frequency_hz = f
        out[i:i + chunk] = board(np.ascontiguousarray(x[i:i + chunk].T), SR, reset=False).T
    return out


def _attack_env(x: np.ndarray, hop: int = 32):
    sos = butter(2, 200, fs=SR, output="sos")
    low = np.abs(sosfiltfilt(sos, x.mean(axis=1)))
    env = low[: len(low) // hop * hop].reshape(-1, hop).max(axis=1)
    return np.maximum(np.diff(env, prepend=env[0] if len(env) else 0), 0)


def _grid_phase(x: np.ndarray, beats_s: np.ndarray, max_ms: float = 40.0):
    """Décalage (échantillons) qui aligne le mieux les temps attendus `beats_s`
    (en s depuis le début de x) sur les attaques graves de x, cherché dans
    ±max_ms. Renvoie (décalage, netteté du pic)."""
    hop = 32
    env = _attack_env(x, hop)
    if len(env) == 0 or env.max() <= 0:
        return 0, 0.0
    pos = np.asarray(beats_s[1:-1]) * SR / hop            # on saute le 1er et le dernier
    pos = pos[pos < len(env) - max_ms / 1000 * SR / hop - 1]
    if len(pos) < 4:
        return 0, 0.0
    offs = np.arange(-max_ms, max_ms + 0.25, 0.25) / 1000 * SR / hop
    idx = np.arange(len(env))
    scores = np.array([np.interp(o + pos, idx, env).mean() for o in offs])
    j = int(np.argmax(scores))
    return int(round(offs[j] * hop)), float(scores[j] / (np.median(scores) + 1e-9))


def _stretch_offset(orig, beats_orig, stretched, beats_new) -> int:
    """Retard (échantillons) introduit par l'étirement sur la grille rythmique."""
    o1, s1 = _grid_phase(orig, beats_orig)
    o2, s2 = _grid_phase(stretched, beats_new)
    if min(s1, s2) < 1.5:      # pas d'attaques nettes : valeur moyenne mesurée
        return int(STRETCH_LATENCY * SR)
    return int(np.clip(o2 - o1, -0.03 * SR, 0.03 * SR))


def _declick_join(buffer: np.ndarray, original: np.ndarray, n: int = 441):
    """Raccord sans clic au début du tampon (fondu de 10 ms depuis l'original)."""
    n = min(n, len(buffer), len(original))
    w = np.linspace(0, 1, n, dtype=np.float32)[:, None]
    buffer[:n] = original[:n] * (1 - w) + buffer[:n] * w


def _declick_tail(buffer: np.ndarray, original: np.ndarray, n: int = 441):
    """Raccord sans clic à la fin du tampon (fondu de 10 ms vers l'original)."""
    n = min(n, len(buffer), len(original))
    w = np.linspace(0, 1, n, dtype=np.float32)[:, None]
    buffer[-n:] = buffer[-n:] * (1 - w) + original[-n:] * w


def _round4(x: float) -> int:
    return int(4 * round(x / 4))


def _overlap_start(a: Track, L: int) -> float:
    """Début de la superposition dans a, sur une frontière de phrase (4 mesures).

    On part du début de l'outro ; si l'outro est trop courte pour L mesures,
    on recule de phrase en phrase : le morceau entrant arrive alors sous la
    dernière phrase chantée, comme le ferait un DJ.
    """
    ov = a.outro_start
    floor = a.first_beat + 4 * a.bar
    while ov + L * a.bar > a.grid_end + a.bar and ov - 4 * a.bar >= floor:
        ov -= 4 * a.bar
    return max(ov, floor)


def plan_beatmatch(a: Track, b: Track):
    """Calcule où et comment enchaîner a → b en calant les tempos. None si impossible."""
    if not (a.analyzed and b.analyzed):
        return None
    r = b.bpm / a.bpm
    if abs(r - 1) > MAX_STRETCH:
        return None
    intro_bars = (b.intro_end - b.first_beat) / b.bar
    L = int(np.clip(_round4(intro_bars), MIX_MIN_BARS, MIX_MAX_BARS))
    ov_a = _overlap_start(a, L)
    # le « drop » de b (fin de son intro) tombe à la fin de la superposition
    b_start = max(b.first_beat, b.intro_end - L * b.bar)
    return dict(r=r, L=L, ov_a=ov_a, b_start=b_start)


def lock_point(a: Track, lead: float = 25.0) -> float:
    """Moment (s) où la transition suivante est figée et préparée.

    Le morceau suivant n'est pas encore connu : on prend le cas où la
    superposition commencerait le plus tôt (la plus longue).
    """
    if not a.analyzed:
        return max(0.0, a.length - 6.0 - lead)
    return max(0.0, _overlap_start(a, MIX_MAX_BARS) - lead)


def _glide(n_beats: int, bpm_a: float, bpm_b: float) -> np.ndarray:
    """Tempo de chaque temps pendant la superposition : glissement en douceur."""
    if n_beats < 2:
        return np.full(n_beats, bpm_b)
    u = np.arange(n_beats) / (n_beats - 1)
    return bpm_a + (bpm_b - bpm_a) * (0.5 - 0.5 * np.cos(np.pi * u))


def _stretch_beats(x: np.ndarray, bpm: float, tempos: np.ndarray) -> np.ndarray:
    """Étire x (qui commence sur un temps) pour que son temps j dure 60/tempos[j]."""
    beat_in = 60.0 / bpm * SR
    bounds = np.round(np.arange(len(tempos) + 1) * beat_in).astype(int)
    factor = np.empty(len(x))
    for j, tp in enumerate(tempos):
        factor[bounds[j]:bounds[j + 1]] = tp / bpm
    factor[bounds[-1]:] = tempos[-1] / bpm
    return pedalboard.time_stretch(np.ascontiguousarray(x.T), SR,
                                   stretch_factor=factor.astype(np.float64)).T


def render_beatmatch(a: Track, b: Track, plan: dict) -> Transition:
    """Superposition de L mesures pendant laquelle le tempo glisse de a vers b.

    Les deux morceaux sont étirés temps par temps sur le même tempo commun,
    qui part de celui de a et arrive à celui de b : avant la transition, a joue
    à son tempo d'origine ; après, b joue au sien. Les voix ne sont jamais
    étirées hors de la superposition.
    """
    r, L = plan["r"], plan["L"]
    nb = 4 * L
    s_ov = int(round(plan["ov_a"] * SR))
    b_s = int(round(plan["b_start"] * SR))
    n_a_in = int(round(L * a.bar * SR))
    n_b_in = int(round(L * b.bar * SR))
    a_in = _slice(a.audio, s_ov, n_a_in)
    b_in = _slice(b.audio, b_s, n_b_in)

    tempos = _glide(nb, a.bpm, b.bpm)
    out_beats = np.concatenate([[0.0], np.cumsum(60.0 / tempos)])     # s
    ov_len = int(round(out_beats[-1] * SR))

    # Grille réelle de chaque morceau à l'endroit de la transition (les kicks
    # mesurés, pas seulement la grille calculée sur tout le morceau)
    def local_err(x, bpm):
        e, sharp = _grid_phase(x, np.arange(nb + 1) * 60.0 / bpm)
        return e if sharp >= 1.5 else 0
    e_a, e_b = local_err(a_in, a.bpm), local_err(b_in, b.bpm)
    rel = e_b - e_a          # retard de b sur a, à compenser

    def place(y, shift):
        y = y[shift:] if shift >= 0 else np.pad(y, ((-shift, 0), (0, 0)))
        y = y[:ov_len]
        return np.pad(y, ((0, ov_len - len(y)), (0, 0))).astype(np.float32)

    if abs(r - 1) < 0.002:
        a_ov, b_ov = place(a_in, 0), place(b_in, rel)
        offs = (0, rel)
    else:
        ya = _stretch_beats(a_in, a.bpm, tempos)
        yb = _stretch_beats(b_in, b.bpm, tempos)
        # Rubber Band décale un peu les attaques : on mesure ce retard et on l'enlève
        d_a = _stretch_offset(a_in, np.arange(nb + 1) * 60.0 / a.bpm, ya, out_beats)
        d_b = _stretch_offset(b_in, np.arange(nb + 1) * 60.0 / b.bpm, yb, out_beats)
        a_ov, b_ov = place(ya, d_a), place(yb, d_b + rel)
        offs = (d_a, d_b + rel)

    stems = (a_ov.copy(), b_ov.copy()) if DEBUG_STEMS else None
    # --- échange des basses, sur des frontières de mesure (tempo variable)
    bar_pos = (out_beats[::4] * SR).astype(int)                       # début de chaque mesure
    def at_bar(i):
        return bar_pos[min(i, len(bar_pos) - 1)]
    a_low, a_high = _split_bass(a_ov)
    b_low, b_high = _split_bass(b_ov)
    t = np.arange(ov_len, dtype=np.float32)
    q = max(1, L // 4)
    in_end = at_bar(q)                                  # b monte sur la 1re phrase
    mid, mid_end = at_bar(L // 2), at_bar(L // 2 + 1)   # échange sur 1 mesure
    out_start = at_bar(L - max(4, q))                   # a s'efface sur la dernière
    swap = np.clip((t - mid) / max(mid_end - mid, 1), 0, 1)
    swap = 0.5 - 0.5 * np.cos(np.pi * swap)
    g_b_high = np.sin(np.pi / 2 * np.clip(t / in_end, 0, 1))
    g_a_high = np.cos(np.pi / 2 * np.clip((t - out_start) / max(ov_len - out_start, 1), 0, 1))
    duck = 1 - 0.16 * np.minimum(g_b_high, g_a_high)     # -1,5 dB quand les deux sont pleins
    a_high[out_start:] = _hpf_sweep(a_high[out_start:], 150.0, 3000.0)
    buf = (a_low * (1 - swap)[:, None] + a_high * (g_a_high * duck)[:, None]
           + b_low * swap[:, None] + b_high * (g_b_high * duck)[:, None]).astype(np.float32)

    _declick_join(buf, a.audio[s_ov:s_ov + 441])
    b_end = b_s + n_b_in + rel          # b a été recalé de `rel` : on reprend d'autant plus loin
    _declick_tail(buf, _slice(b.audio, b_end - 441, 441))
    return Transition("beatmatch", s_ov, buf, b, 0, b_s, b_end=b_end,
                      info=dict(plan, ov_len=ov_len, offsets=offs, bar_pos=bar_pos, grid_err=(e_a, e_b),
                                stems=stems))


def render_echo_out(src: np.ndarray, pos: int, bpm: float, b: Track,
                    tail_bars: int = 2, b_start: float | None = None,
                    src_start: int | None = None) -> Transition:
    """Sortie en écho à partir de src[pos] (pos doit tomber sur un temps).

    Le temps joué à partir de `pos` « part » dans un écho calé sur le tempo
    (croche), filtré vers les aigus, qui s'éteint sur `tail_bars` mesures. Le
    son direct s'efface sur ce temps ; le morceau suivant entre au temps
    d'après, sur la grille (moins de 1,5 s après l'appui sur la touche).
    """
    beat = 60.0 / bpm * SR
    dry_len = int(beat)
    tail_len = int(tail_bars * 4 * beat)
    seg = _slice(src, pos, dry_len)
    env = np.ones(dry_len, np.float32)
    q = dry_len // 4
    env[q:] = np.cos(np.linspace(0, np.pi / 2, dry_len - q))
    x = np.zeros((tail_len, 2), np.float32)
    x[:dry_len] = seg * env[:, None]

    delay = pedalboard.Delay(delay_seconds=0.5 * 60.0 / bpm, feedback=0.55, mix=1.0)
    wet = delay(np.ascontiguousarray(x.T), SR).T
    wet = _hpf_sweep(wet, 200.0, 1800.0)
    fade = np.linspace(1, 0, tail_len, dtype=np.float32) ** 1.5
    out = x + 0.7 * wet * fade[:, None]
    _declick_join(out, src[pos:pos + 441])

    # Morceau suivant : entre 1 temps après, avec 50 ms d'avance pour ne pas couper l'attaque
    bs = b.first_beat if b_start is None else b_start
    pre = int(min(bs, 0.05) * SR)
    b_s = int(round(bs * SR)) - pre
    b_off = dry_len - pre
    b_len = tail_len - b_off
    b_seg = _slice(b.audio, b_s, b_len)
    fin = np.ones(b_len, np.float32)
    k = min(int(0.01 * SR), b_len)
    fin[:k] = np.linspace(0, 1, k)
    out[b_off:] += b_seg * fin[:, None]
    return Transition("echo", pos if src_start is None else src_start, out, b, b_off, b_s,
                      info=dict(bpm=bpm))


def render_echo_stop(src: np.ndarray, pos: int, bpm: float, tail_bars: int = 2) -> np.ndarray:
    """Arrêt en douceur : le temps joué part dans un écho filtré qui s'éteint
    sur `tail_bars` mesures, puis silence. Renvoie l'audio de cette fin."""
    beat = 60.0 / bpm * SR
    dry_len = int(beat)
    tail_len = int(tail_bars * 4 * beat)
    seg = _slice(src, pos, dry_len)
    env = np.ones(dry_len, np.float32)
    q = dry_len // 4
    env[q:] = np.cos(np.linspace(0, np.pi / 2, dry_len - q))
    x = np.zeros((tail_len, 2), np.float32)
    x[:dry_len] = seg * env[:, None]
    delay = pedalboard.Delay(delay_seconds=0.5 * 60.0 / bpm, feedback=0.55, mix=1.0)
    wet = delay(np.ascontiguousarray(x.T), SR).T
    wet = _hpf_sweep(wet, 200.0, 1800.0)
    fade = np.linspace(1, 0, tail_len, dtype=np.float32) ** 1.5
    out = (x + 0.7 * wet * fade[:, None]).astype(np.float32)
    _declick_join(out, src[pos:pos + 441])
    k = int(0.05 * SR)                       # dernier fondu : fin parfaitement nette
    out[-k:] *= np.linspace(1, 0, k, dtype=np.float32)[:, None]
    return out


def render_fade(a: Track, b: Track, seconds: float = 6.0) -> Transition:
    """Fondu enchaîné simple (morceau non analysé)."""
    n = int(seconds * SR)
    s0 = max(0, len(a.audio) - n)
    n = len(a.audio) - s0
    a_seg = a.audio[s0:].copy()
    b_s = int(b.first_beat * SR) if b.analyzed else 0
    b_seg = _slice(b.audio, b_s, n)
    w = np.linspace(0, np.pi / 2, n, dtype=np.float32)[:, None]
    buf = a_seg * np.cos(w) + b_seg * np.sin(w)
    return Transition("fade", s0, buf, b, 0, b_s)


def render_auto(a: Track, b: Track) -> Transition:
    """Choisit la meilleure transition possible entre a et b."""
    plan = plan_beatmatch(a, b)
    if plan:
        return render_beatmatch(a, b, plan)
    if a.analyzed:
        # Tempos incompatibles : écho à la fin du corps du morceau (début d'outro)
        t = min(a.outro_start, a.grid_end - 2 * a.bar)
        pos = int(round(a.next_beat_after(t) * SR))
        return render_echo_out(a.audio, pos, a.bpm, b)
    return render_fade(a, b)
