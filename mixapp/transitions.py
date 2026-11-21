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
MAX_STRETCH = 0.05       # au-delà de 5 % d'écart de tempo : transition à effet, pas de calage
SYNC_MIN = 0.01          # en dessous de 1 % d'écart : rien à resynchroniser
SYNC_BARS = 16           # retour au tempo d'origine, en douceur, après l'intro
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


def _match_resume(tail_src: np.ndarray, native: np.ndarray, guess: int,
                  win: int = int(0.4 * SR), search: int = int(0.06 * SR)) -> int:
    """Position dans `native` qui prolonge exactement la fin de `tail_src`
    (corrélation sur les 0,4 dernières secondes, ± 60 ms autour de `guess`)."""
    ref = tail_src[-win:].mean(1).astype(np.float64)
    lo = guess - win - search
    seg_ = _slice(native, max(0, lo), win + 2 * search).mean(1).astype(np.float64)
    if lo < 0 or len(seg_) < win + 2 * search or np.std(ref) < 1e-4:
        return guess
    c = np.correlate(seg_, ref, mode="valid")
    e = np.sqrt(np.convolve(seg_ ** 2, np.ones(win), mode="valid")) * np.linalg.norm(ref) + 1e-9
    nc = c / e
    k = int(np.argmax(nc))
    return lo + k + win if nc[k] > 0.6 else guess


def _match_start(y: np.ndarray, guess: int, native: np.ndarray,
                 win: int = int(0.4 * SR), search: int = int(0.06 * SR)) -> int:
    """Position dans `y` (audio étiré) où commence exactement `native`."""
    ref = native[:win].mean(1).astype(np.float64)
    lo = guess - search
    seg_ = _slice(y, max(0, lo), win + 2 * search).mean(1).astype(np.float64)
    if lo < 0 or len(seg_) < win + 2 * search or np.std(ref) < 1e-4:
        return guess
    c = np.correlate(seg_, ref, mode="valid")
    e = np.sqrt(np.convolve(seg_ ** 2, np.ones(win), mode="valid")) * np.linalg.norm(ref) + 1e-9
    nc = c / e
    k = int(np.argmax(nc))
    return lo + k if nc[k] > 0.6 else guess


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


def key_match(a: Track, b: Track) -> str:
    """'ok' (même clé, voisine ou relative), 'proche' (±2 ou diagonale), 'conflit', '?'."""
    def cam(t):
        try:
            return t.row["camelot"]
        except (KeyError, IndexError, TypeError):
            return None
    ca, cb = cam(a), cam(b)
    if not ca or not cb:
        return "?"
    from .analysis import camelot_neighbors
    if cb in camelot_neighbors(ca):
        return "ok"
    na, la, nb_, lb = int(ca[:-1]), ca[-1], int(cb[:-1]), cb[-1]
    d = min((na - nb_) % 12, (nb_ - na) % 12)
    if (la == lb and d == 2) or (la != lb and d == 1):
        return "proche"
    return "conflit"


def key_shift_for(a: Track, b: Track, max_st: int = 2):
    """Plus petite transposition de b (demi-tons, ±max_st) qui rend sa clé
    compatible avec celle de a, ou None. +1 demi-ton = +7 sur la roue Camelot."""
    try:
        ca, cb = a.row["camelot"], b.row["camelot"]
    except (KeyError, IndexError, TypeError):
        return None
    if not ca or not cb:
        return None
    from .analysis import camelot_neighbors
    ok = camelot_neighbors(ca)
    n, letter = int(cb[:-1]), cb[-1]
    for st in (1, -1, 2, -2):
        if abs(st) <= max_st and f"{(n - 1 + 7 * st) % 12 + 1}{letter}" in ok:
            return st
    return None


GRID_SHARP_MIN = 1.3     # en dessous, les kicks ne sont pas assez nets pour caler


def bars_for(seconds: float | None, bar: float) -> int | None:
    """Nombre de mesures entières le plus proche d'une durée choisie (réglette)."""
    if not seconds:
        return None
    return int(max(1, round(seconds / max(bar, 0.5))))


def plan_beatmatch(a: Track, b: Track, seconds: float | None = None):
    """Calcule où et comment enchaîner a → b en calant les tempos. None si impossible.

    Comme un DJ : tempos proches + clés compatibles → long mélange ;
    clés qui frottent → mélange court (4 mesures) pour que les harmonies
    ne se superposent presque pas ; kicks pas nets ou tempos trop éloignés →
    pas de calage (la transition se fera à l'écho)."""
    if not (a.analyzed and b.analyzed):
        return None
    r = b.bpm / a.bpm
    if abs(r - 1) > MAX_STRETCH:
        return None
    if a.row.get("_hl_echo_out") or b.row.get("_hl_echo_in"):
        return None          # temps forts : des paroles d'un côté → sortie en écho, pas de mélange
    key = key_match(a, b)
    intro_bars = (b.intro_end - b.first_beat) / b.bar
    L = int(np.clip(_round4(intro_bars), MIX_MIN_BARS, MIX_MAX_BARS))
    if key == "proche":
        L = min(L, 8)
    shift = 0
    if key == "conflit":
        st = key_shift_for(a, b)
        if st is not None:
            # comme le « key shift » d'une table DJ : b entre transposé dans la
            # clé de a, puis revient doucement à sa vraie clé pendant que a s'efface
            shift, L = st, min(L, 16)
        else:
            L = 4
    if abs(r - 1) > 0.05:              # gros écart de tempo : glissement court
        L = min(L, 8)
    hl = b.row.get("_hl_mix_bars") or a.row.get("_hl_mix_bars")
    if hl:
        # mode « temps forts » : on entre en plein morceau (souvent chanté) →
        # superposition courte, pas de transposition
        L, shift = int(hl), 0
    want = bars_for(seconds, a.bar)
    if want:
        # durée choisie par l'utilisateur (réglette), en mesures entières ;
        # on ne l'allonge jamais au-delà de ce qui est sûr (clés qui frottent,
        # passage instrumental en mode temps forts)
        L = min(want, L) if (key == "conflit" and not shift) or hl else want
    ov_a = _overlap_start(a, L)
    # le « drop » de b (fin de son intro) tombe à la fin de la superposition
    b_start = max(b.first_beat, b.intro_end - L * b.bar)
    if b.row.get("_hl_entry") is not None:
        b_start = float(b.row["_hl_entry"])
    # les kicks sont-ils assez nets aux deux endroits pour un calage propre ?
    Lc = max(L, 4)            # mélange très court : on juge la netteté sur 4 mesures
    n = 4 * Lc
    sa = _grid_phase(_slice(a.audio, int(ov_a * SR), int(Lc * a.bar * SR)),
                     np.arange(n + 1) * 60.0 / a.bpm)[1]
    sb = _grid_phase(_slice(b.audio, int(b_start * SR), int(Lc * b.bar * SR)),
                     np.arange(n + 1) * 60.0 / b.bpm)[1]
    if min(sa, sb) < GRID_SHARP_MIN:
        return None
    if shift:
        key = "transposé"
    if hl:
        key = key if key != "conflit" else "conflit_hl"
    why = {"transposé": f"clé de l'entrant ajustée ({shift:+d} demi-ton) puis rétablie",
           "ok": "clés compatibles", "proche": "clés voisines", "conflit": "clés éloignées : mélange court", "conflit_hl": "clés éloignées",
           "?": "clé inconnue"}[key]
    # « sync » : b démarre au tempo de a, garde ce tempo pendant le mélange
    # (a n'est pas touché), puis revient tout doucement au sien après son intro.
    # Pas en mode temps forts (b entre juste avant son refrain : on ne touche
    # pas au tempo pendant un refrain).
    sync = SYNC_MIN <= abs(r - 1) <= MAX_STRETCH and not hl
    if sync:
        why = why + ", sync du tempo"
    return dict(r=r, L=L, ov_a=ov_a, b_start=b_start, key=key, sharp=(sa, sb), shift=shift, sync=sync,
                why=f"Mix au tempo {a.bpm:.0f}→{b.bpm:.0f} BPM, {why}, {L} mesures")


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


MARGIN = int(0.5 * SR)       # musique lue en plus avant / après chaque transition


def _stretch_beats(x: np.ndarray, bpm: float, tempos: np.ndarray, pre: int, post: int,
                   pitch: np.ndarray | None = None):
    """Étire x (temps n°0 à l'échantillon `pre`) pour que son temps j dure
    60/tempos[j]. Les marges `pre` / `post` (jouées au tempo de début / de fin)
    absorbent le démarrage et l'arrêt du moteur d'étirement : on les retire
    ensuite. Renvoie (audio étiré, longueur de la marge de début une fois étirée)."""
    beat_in = 60.0 / bpm * SR
    bounds = pre + np.round(np.arange(len(tempos) + 1) * beat_in).astype(int)
    factor = np.empty(len(x))
    factor[:bounds[0]] = tempos[0] / bpm
    for j, tp in enumerate(tempos):
        factor[bounds[j]:bounds[j + 1]] = tp / bpm
    factor[bounds[-1]:] = tempos[-1] / bpm
    kw = {} if pitch is None else {"pitch_shift_in_semitones": pitch.astype(np.float64)}
    y = pedalboard.time_stretch(np.ascontiguousarray(x.T), SR,
                                stretch_factor=factor.astype(np.float64), **kw).T
    return y, int(round(pre / (tempos[0] / bpm)))


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
    M_ = MARGIN
    # entrées avec une marge de vraie musique avant et après : aucun décalage ni
    # démarrage du moteur d'étirement ne laisse de trou (silence) dans le tampon
    a_ext = _slice(a.audio, s_ov - M_, n_a_in + 2 * M_)
    b_ext = _slice(b.audio, b_s - M_, n_b_in + 2 * M_)
    a_in, b_in = a_ext[M_:M_ + n_a_in], b_ext[M_:M_ + n_b_in]

    sync = plan.get("sync", False)
    G = SYNC_BARS if sync else 0
    if sync:
        tempos = np.full(nb, a.bpm)                     # pendant le mélange : tempo de a
        n_b_tail = int(round(G * b.bar * SR))
        b_ext = _slice(b.audio, b_s - M_, n_b_in + n_b_tail + 2 * M_)
        tempos_b = np.concatenate([tempos, _glide(4 * G, a.bpm, b.bpm)])
    else:
        tempos = _glide(nb, a.bpm, b.bpm)
        n_b_tail = 0
        tempos_b = tempos
    out_beats = np.concatenate([[0.0], np.cumsum(60.0 / tempos)])     # s
    ov_len = int(round(out_beats[-1] * SR))
    full_len = int(round(np.sum(60.0 / tempos_b) * SR))

    # Grille réelle de chaque morceau à l'endroit de la transition (les kicks
    # mesurés, pas seulement la grille calculée sur tout le morceau)
    def local_err(x, bpm):
        e, sharp = _grid_phase(x, np.arange(nb + 1) * 60.0 / bpm)
        return e if sharp >= 1.5 else 0
    e_a, e_b = local_err(a_in, a.bpm), local_err(b_in, b.bpm)
    rel = int(np.clip(e_b - e_a, -M_ // 2, M_ // 2))     # retard de b sur a, à compenser

    def take(y, start, n=None):
        """n (ov_len) échantillons de y à partir de `start` (toujours de la vraie musique)."""
        n = ov_len if n is None else n
        start = max(0, start)
        seg_ = y[start:start + n]
        if len(seg_) < n:               # ne devrait pas arriver grâce aux marges
            seg_ = np.pad(seg_, ((0, n - len(seg_)), (0, 0)), mode="edge")
        return np.ascontiguousarray(seg_, dtype=np.float32)

    shift = plan.get("shift", 0)
    b_tail = None
    if sync:
        # a joue tel quel ; seul b est étiré (au tempo de a, puis retour au sien)
        a_ov = take(a_ext, M_)
        pitch = None
        if shift:
            bar_in = int(round(b.bar * SR))
            g0, g1 = M_ + (L // 2) * bar_in, M_ + (L - 1) * bar_in
            k = np.clip((np.arange(len(b_ext)) - g0) / max(g1 - g0, 1), 0, 1)
            pitch = shift * (0.5 + 0.5 * np.cos(np.pi * k))
        yb, pb = _stretch_beats(b_ext, b.bpm, tempos_b, M_, M_, pitch)
        out_b = np.concatenate([[0.0], np.cumsum(60.0 / tempos_b)])
        d_b = _stretch_offset(b_in, np.arange(nb + 1) * 60.0 / b.bpm,
                              yb[pb:pb + ov_len + M_], out_beats)
        b_full = take(yb, pb + d_b + rel, full_len)
        b_ov, b_tail = b_full[:ov_len], b_full[ov_len:]
        offs = (0, d_b + rel)
    elif abs(r - 1) < 0.002 and not shift:
        a_ov, b_ov = take(a_ext, M_), take(b_ext, M_ + rel)
        offs = (0, rel)
    else:
        ya, pa = _stretch_beats(a_ext, a.bpm, tempos, M_, M_)
        pitch = None
        if shift:
            # transposé jusqu'à la moitié, retour en douceur, vraie clé 1 mesure avant la fin
            bar_in = int(round(b.bar * SR))
            g0, g1 = M_ + (L // 2) * bar_in, M_ + (L - 1) * bar_in
            k = np.clip((np.arange(len(b_ext)) - g0) / max(g1 - g0, 1), 0, 1)
            pitch = shift * (0.5 + 0.5 * np.cos(np.pi * k))
        yb, pb = _stretch_beats(b_ext, b.bpm, tempos, M_, M_, pitch)
        # Rubber Band décale un peu les attaques : on mesure ce retard et on l'enlève
        d_a = _stretch_offset(a_in, np.arange(nb + 1) * 60.0 / a.bpm,
                              ya[pa:pa + ov_len + M_], out_beats)
        d_b = _stretch_offset(b_in, np.arange(nb + 1) * 60.0 / b.bpm,
                              yb[pb:pb + ov_len + M_], out_beats)
        # au tout début, a joue encore à son tempo : on recale précisément le
        # début étiré sur l'original pour que l'entrée dans le tampon soit inaudible
        d_a = _match_start(ya, pa + d_a, a_ext[M_:]) - pa
        a_ov, b_ov = take(ya, pa + d_a), take(yb, pb + d_b + rel)
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
    out_start = at_bar(max(L // 2, L - max(4, q)))      # a s'efface sur la dernière phrase
    swap = np.clip((t - mid) / max(mid_end - mid, 1), 0, 1)
    swap = 0.5 - 0.5 * np.cos(np.pi * swap)
    g_b_high = np.sin(np.pi / 2 * np.clip(t / in_end, 0, 1))
    g_a_high = np.cos(np.pi / 2 * np.clip((t - out_start) / max(ov_len - out_start, 1), 0, 1))
    duck = 1 - 0.16 * np.minimum(g_b_high, g_a_high)     # -1,5 dB quand les deux sont pleins
    a_high[out_start:] = _hpf_sweep(a_high[out_start:], 150.0, 3000.0)
    buf = (a_low * (1 - swap)[:, None] + a_high * (g_a_high * duck)[:, None]
           + b_low * swap[:, None] + b_high * (g_b_high * duck)[:, None]).astype(np.float32)

    _declick_join(buf, a.audio[s_ov:s_ov + 1323], 1323)
    if b_tail is not None and len(b_tail):
        buf = np.concatenate([buf, b_tail]).astype(np.float32)   # b seul, retour à son tempo
    # Où reprendre b d'origine : on cherche, autour de la position théorique,
    # l'endroit où la fin du tampon (qui n'est plus que b) colle exactement à b.
    b_end = _match_resume(b_full if b_tail is not None else b_ov, b.audio,
                          b_s + n_b_in + n_b_tail + rel)
    _declick_tail(buf, _slice(b.audio, b_end - 1323, 1323), 1323)     # fondu de 30 ms
    return Transition("beatmatch", s_ov, buf, b, 0, b_s, b_end=b_end,
                      info=dict(plan, ov_len=ov_len, offsets=offs, bar_pos=bar_pos, grid_err=(e_a, e_b),
                                stems=stems))


def render_echo_out(src: np.ndarray, pos: int, bpm: float, b: Track,
                    tail_bars: int = 3, b_start: float | None = None,
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
    b_seg = _soft_entry(_slice(b.audio, b_s, b_len), b)
    out[b_off:] += b_seg
    return Transition("echo", pos if src_start is None else src_start, out, b, b_off, b_s,
                      info=dict(bpm=bpm))


def _soft_entry(seg: np.ndarray, b: Track) -> np.ndarray:
    """Entrée en douceur du morceau suivant après un effet : le volume monte
    sur une mesure et les basses n'arrivent qu'au 1er temps de la mesure
    suivante (comme un DJ qui remonte les basses sur la console)."""
    n = len(seg)
    if n == 0:
        return seg
    seg = seg.astype(np.float32).copy()
    bar = int(round(b.bar * SR))
    # transition très courte : tout (volume et basses) doit être en place avant
    # la fin du tampon, sinon les basses reviendraient d'un coup au raccord
    bar = max(1, min(bar, n - int(0.08 * SR)))
    t = np.arange(n, dtype=np.float32)
    k = min(int(0.01 * SR), n)
    vol = np.clip(0.45 + 0.55 * np.sin(np.pi / 2 * np.clip(t / max(bar, 1), 0, 1)), 0, 1)
    vol[:k] *= np.linspace(0, 1, k)
    low, high = _split_bass(seg)
    lo_g = np.clip((t - bar) / max(int(0.06 * SR), 1), 0, 1)       # basses au temps 1 suivant
    return (low * (lo_g * vol)[:, None] + high * vol[:, None]).astype(np.float32)


def render_filter_echo(a: Track, pos: int, b: Track, b_start=None, tail_bars: int = 3) -> Transition:
    """Filtre qui ferme les graves sur les 2 dernières mesures de a, puis écho."""
    pre = int(round(min(2, max(1, tail_bars - 1)) * a.bar * SR))
    s0 = max(0, pos - pre)
    swept = _hpf_sweep(a.audio[s0:pos].astype(np.float32), 60.0, 1400.0)
    _declick_join(swept, a.audio[s0:s0 + 441])
    tr = render_echo_out(a.audio, pos, a.bpm, b, tail_bars=tail_bars, b_start=b_start)
    head = _hpf_sweep(tr.buffer[: int(60.0 / a.bpm * SR)].copy(), 1400.0, 1500.0)
    tr.buffer[: len(head)] = head
    buf = np.concatenate([swept, tr.buffer]).astype(np.float32)
    return Transition("filtre", s0, buf, b, tr.b_offset + len(swept), tr.b_start,
                      info=dict(bpm=a.bpm))


def render_pullup_out(a: Track, pos: int, b: Track, b_start=None) -> Transition:
    """« Pull-up » de DJ (scratch, rembobinage, écho) puis le morceau suivant."""
    from .effects import render_pullup
    tail = render_pullup(a.audio, pos, a.bpm)
    gesture = int((60.0 / a.bpm + 1.1) * SR)           # 2 scratchs d'un demi-temps + rembobinage
    bs = b.first_beat if b_start is None else b_start
    b_s = int(round(bs * SR))
    b_len = max(0, len(tail) - gesture)
    b_seg = _soft_entry(_slice(b.audio, b_s, b_len), b)
    out = tail.copy()
    out[gesture:] = tail[gesture:] * 0.7 + b_seg
    return Transition("pullup", pos, out.astype(np.float32), b, gesture, b_s, info=dict(bpm=a.bpm))


def render_effect_out(a: Track, pos: int, b: Track, b_start=None, tail_bars: int = 3) -> Transition:
    """Transition à effet quand le calage est impossible : l'appli varie les
    effets (écho, filtre + écho, pull-up), le pull-up seulement quand le
    morceau qui part est énergique."""
    k = (int(a.id) * 7 + int(b.id) * 3) % 3
    if k == 2:
        lv = 20 * np.log10(np.sqrt(np.mean(_slice(a.audio, pos - int(2 * a.bar * SR),
                                                   int(2 * a.bar * SR)) ** 2)) + 1e-9)
        if lv < 20 * np.log10(TARGET_RMS) - 3:
            k = 1
    if k == 1:
        return render_filter_echo(a, pos, b, b_start, tail_bars)
    if k == 2:
        return render_pullup_out(a, pos, b, b_start)
    return render_echo_out(a.audio, pos, a.bpm, b, tail_bars=tail_bars, b_start=b_start)


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


def render_fade_at(a: Track, pos: int, b: Track, b_start=None,
                   seconds: float | None = None) -> Transition:
    """Fondu enchaîné posé sur le temps : a baisse, b monte (puissance constante),
    sur une durée en mesures entières, b démarrant sur son premier temps."""
    bars = int(np.clip(bars_for(seconds or 6.0, a.bar) or 3, 1, 16))
    n = int(round(bars * a.bar * SR))
    n = max(int(0.5 * SR), min(n, len(a.audio) - pos))
    if b_start is not None:
        b_s = int(round(b_start * SR))
    else:
        b_s = int(round(b.first_beat * SR)) if b.analyzed else 0
    a_seg = _slice(a.audio, pos, n)
    b_seg = _slice(b.audio, b_s, n)
    w = np.linspace(0, np.pi / 2, n, dtype=np.float32)[:, None]
    buf = (a_seg * np.cos(w) + b_seg * np.sin(w)).astype(np.float32)
    _declick_join(buf, a.audio[pos:pos + 441])
    return Transition("fade", pos, buf, b, 0, b_s)


STYLES = {"auto": "Auto", "cale": "Mix calé", "echo": "Écho", "filtre": "Filtre",
          "pullup": "Pull-up", "fondu": "Fondu"}


def _effect_pos(a: Track) -> int:
    t = min(a.outro_start, a.grid_end - 2 * a.bar)
    return int(round(a.next_beat_after(t) * SR))


def render_auto(a: Track, b: Track, seconds: float | None = None,
                style: str | None = None) -> Transition:
    """Choisit la meilleure transition propre possible entre a et b :
    calage tempo + clé si possible, sinon écho sur le temps, sinon fondu.
    `seconds` : durée voulue (réglette), None = l'appli décide.
    `style` : transition imposée par un pad (cale, echo, filtre, pullup, fondu) ;
    si elle est impossible, l'appli prend la solution propre la plus proche
    et l'explique dans info["fallback"]."""
    if style and style != "auto":
        tr = _render_forced(a, b, seconds, style)
        if tr is not None:
            tr.info["forced"] = style
            return tr
        tr = render_auto(a, b, seconds)
        tr.info["forced"] = style
        tr.info["fallback"] = (f"{STYLES.get(style, style)} impossible ici"
                               + (f" ({tr.info['why'].split(' : ', 1)[1]})" if " : " in tr.info.get("why", "") else "")
                               + f" : {_KIND_FR.get(tr.kind, tr.kind)} à la place")
        return tr
    plan = plan_beatmatch(a, b, seconds)
    if plan:
        tr = render_beatmatch(a, b, plan)
        tr.info["why"] = plan["why"]
        return tr
    if a.analyzed:
        # Tempos incompatibles ou kicks pas nets : écho à la fin du corps du
        # morceau (début d'outro), b démarre proprement sur son 1er temps
        t = min(a.outro_start, a.grid_end - 2 * a.bar)
        pos = int(round(a.next_beat_after(t) * SR))
        tr = render_effect_out(a, pos, b, b_start=b.row.get("_hl_entry"),
                               tail_bars=int(np.clip(bars_for(seconds, a.bar) or 3, 1, 8)))
        r = b.bpm / a.bpm if b.analyzed else 1
        tr.info["why"] = ({"filtre": "Filtre + écho", "pullup": "Pull-up"}.get(tr.kind, "Écho")
                          + " : " + (
                          "pas de passage instrumental pour mélanger sans couper les paroles"
                          if (a.row.get("_hl_echo_out") or b.row.get("_hl_echo_in")) else
                          f"tempos trop éloignés ({a.bpm:.0f}→{b.bpm:.0f} BPM)"
                          if abs(r - 1) > MAX_STRETCH else "rythme pas assez net pour caler"))
        return tr
    tr = render_fade(a, b, seconds or 6.0)
    tr.info["why"] = "Fondu enchaîné"
    return tr


_KIND_FR = {"beatmatch": "mix calé", "echo": "écho", "filtre": "filtre", "pullup": "pull-up",
            "fade": "fondu"}


def _render_forced(a: Track, b: Track, seconds, style) -> Transition | None:
    """Transition choisie au pad ; None si elle est impossible pour ces deux morceaux."""
    if not a.analyzed:
        return None if style != "fondu" else _named(render_fade(a, b, seconds or 6.0), "Fondu (choisi)")
    b_start = b.row.get("_hl_entry")
    if style == "cale":
        plan = plan_beatmatch(a, b, seconds)
        if not plan:
            return None
        tr = render_beatmatch(a, b, plan)
        return _named(tr, "Mix calé (choisi) : " + plan["why"])
    pos = _effect_pos(a)
    tail = int(np.clip(bars_for(seconds, a.bar) or 3, 1, 8))
    if style == "echo":
        tr = render_echo_out(a.audio, pos, a.bpm, b, tail_bars=tail, b_start=b_start)
    elif style == "filtre":
        tr = render_filter_echo(a, pos, b, b_start, tail)
    elif style == "pullup":
        tr = render_pullup_out(a, pos, b, b_start)
    elif style == "fondu":
        if a.row.get("_hl_echo_out") or b.row.get("_hl_echo_in"):
            return None                     # un fondu superposerait des paroles
        tr = render_fade_at(a, pos, b, b_start, seconds)
    else:
        return None
    return _named(tr, f"{STYLES[style]} (choisi au pad)")


def _named(tr: Transition, why: str) -> Transition:
    tr.info["why"] = why
    return tr
