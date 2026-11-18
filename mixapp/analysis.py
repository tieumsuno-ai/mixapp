"""Analyse audio : BPM + grille rythmique, tonalité (Camelot), intro / outro.

Tout est calculé une seule fois par morceau puis stocké en base.
Bibliothèques : pedalboard (lecture MP3, Mac/Windows/Linux) et librosa.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed

import librosa
import numpy as np
from .audio_io import read_mono
from scipy.signal import butter, sosfiltfilt

SR = 22050          # fréquence d'échantillonnage pour l'analyse (suffisant, 2x plus rapide)
HOP = 512
BPM_MIN, BPM_MAX = 78.0, 160.0   # plage « DJ » : 64 BPM -> 128, 180 -> 90

NOTES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
NOTES_FR = ["Do", "Do#", "Ré", "Mib", "Mi", "Fa", "Fa#", "Sol", "Lab", "La", "Sib", "Si"]

# Profils de tonalité de Krumhansl-Kessler (majeur / mineur). Testés contre
# ceux de Temperley et d'EDMA sur de vrais morceaux de soirée : les plus justes.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


# ---------------------------------------------------------------- utilitaires

def load_mono(path: str, sr: int = SR) -> np.ndarray:
    return read_mono(path, sr)


def camelot(pitch_class: int, minor: bool) -> str:
    fifths = (pitch_class * 7) % 12
    if minor:
        return f"{(fifths + 4) % 12 + 1}A"
    return f"{(fifths + 7) % 12 + 1}B"


def key_label(pitch_class: int, minor: bool) -> str:
    return NOTES[pitch_class] + ("m" if minor else "")


def camelot_neighbors(code: str) -> set[str]:
    """Tonalités compatibles : même code, ±1, et relative majeur/mineur."""
    n, letter = int(code[:-1]), code[-1]
    other = "B" if letter == "A" else "A"
    return {code, f"{(n % 12) + 1}{letter}", f"{((n - 2) % 12) + 1}{letter}", f"{n}{other}"}


# ---------------------------------------------------------------- tempo

def _fold_bpm(bpm: float) -> float:
    while bpm < BPM_MIN:
        bpm *= 2
    while bpm >= BPM_MAX:
        bpm /= 2
    return bpm


TEMPO_HOP = 256   # ~11,6 ms de résolution pour caler la grille
ONSET_LATENCY = 0.014
KEY_SR = 11025


def _grid_scores(env: np.ndarray, frames_per_sec: float, bpms: np.ndarray, n_phase: int = 48):
    """Pour chaque tempo candidat : meilleur score de grille et phase associée.

    Le score est la force d'attaque moyenne lue sur une grille régulière.
    Une grille au bon tempo et à la bonne phase tombe sur toutes les attaques
    (kicks, caisses claires) ; un tempo double tombe aussi entre les temps,
    un demi-tempo en rate la moitié : leur moyenne est plus faible.
    """
    idx = np.arange(len(env))
    best_score = np.empty(len(bpms))
    best_phase = np.empty(len(bpms))
    for i, bpm in enumerate(bpms):
        period = frames_per_sec * 60.0 / bpm
        phases = np.linspace(0, period, n_phase, endpoint=False)
        k = np.arange(int((len(env) - period) / period))
        pos = phases[:, None] + k[None, :] * period
        vals = np.interp(pos.ravel(), idx, env).reshape(pos.shape)
        s = vals.mean(axis=1)
        j = int(np.argmax(s))
        best_score[i], best_phase[i] = s[j], phases[j]
    return best_score, best_phase


def detect_tempo(y: np.ndarray, sr: int = SR):
    """Renvoie (bpm, first_beat, beat_times) avec une grille à tempo constant.

    Recherche en deux passes (grossière sur 78–160 BPM, puis fine à 0,01 BPM)
    du tempo et de la phase qui alignent le mieux une grille régulière sur les
    attaques du morceau. Pas de numba : fonctionne pareil sur Mac et Windows.
    """
    def _norm(e):
        e = np.maximum(e - np.median(e), 0)
        return e / e.max() if e.max() > 0 else e

    # Attaques sur tout le spectre + attaques dans le grave (kick, basse).
    # Le grave compte double : sinon la grille se cale sur les charleys,
    # qui tombent souvent sur les contretemps en house.
    env_all = _norm(librosa.onset.onset_strength(y=y, sr=sr, hop_length=TEMPO_HOP))
    mel_low = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=TEMPO_HOP,
                                             n_mels=16, fmax=180)
    env_low = _norm(librosa.onset.onset_strength(S=librosa.power_to_db(mel_low), sr=sr,
                                                 hop_length=TEMPO_HOP))
    env = env_all + 2 * env_low
    if env.max() <= 0:
        return 120.0, 0.0, np.array([])
    env /= env.max()
    fps = sr / TEMPO_HOP

    # Passe grossière (pas de 0,5 BPM) sur des fenêtres de 20 s : sur tout un
    # morceau long, une grille fausse de 0,25 BPM dériverait de plus d'un temps.
    coarse = np.arange(BPM_MIN, BPM_MAX, 0.5)
    win = int(20 * fps)
    starts = range(0, max(1, len(env) - win + 1), win) if len(env) > win else [0]
    sc = np.mean([_grid_scores(env[s:s + win], fps, coarse, n_phase=24)[0] for s in starts],
                 axis=0)
    b0 = coarse[int(np.argmax(sc))]
    fine = np.arange(b0 - 0.6, b0 + 0.6, 0.01)
    sc, ph = _grid_scores(env, fps, fine, n_phase=64)
    j = int(np.argmax(sc))
    bpm = float(fine[j])
    # Précision au centième : un arrondi à l'entier ferait dériver la grille de
    # plusieurs dizaines de ms sur un morceau de 4 minutes (l'affichage arrondit).
    bpm = round(bpm, 2)
    period = 60.0 / bpm
    # La courbe d'attaques réagit ~14 ms après le début réel du son (mesuré)
    first = ph[j] / fps - ONSET_LATENCY
    # Un kick tout au début du fichier : ne pas renvoyer la grille au temps suivant
    first = max(first, 0.0) if first > -0.03 else first % period
    # Premier temps réellement audible (ignore le silence de début)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    t_rms = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=HOP)
    audible = t_rms[np.argmax(rms > rms.max() * 0.05)] if rms.max() > 0 else 0.0
    while first + period * 0.5 < audible:
        first += period
    grid = np.arange(first, len(y) / sr, period)
    return round(bpm, 1), float(first), grid


# ---------------------------------------------------------------- tonalité

def harmonic_part(y: np.ndarray, sr: int = SR) -> np.ndarray:
    """Partie harmonique (voix, accords, mélodie) sans la batterie, à 11 kHz.

    11 kHz suffisent pour les notes (jusqu'à Do8) : analyse 3x plus rapide.
    """
    y = librosa.resample(y, orig_sr=sr, target_sr=KEY_SR)
    return librosa.effects.harmonic(y, margin=4, n_fft=1024)


def detect_key(y: np.ndarray, sr: int = SR, yh: np.ndarray | None = None):
    """Renvoie (pitch_class, minor, confiance)."""
    if yh is None:
        yh = harmonic_part(y, sr)
    sr = KEY_SR
    # 2) chroma NON normalisé par trame : les passages sans notes (kick seul,
    #    silences) pèsent peu ; on ignore l'extrême grave (< Do2) où sonne le kick
    chroma = librosa.feature.chroma_cqt(y=yh, sr=sr, hop_length=HOP * 2, norm=None,
                                        fmin=librosa.note_to_hz("C2"), n_octaves=6)
    profile = chroma.mean(axis=1)
    best = (-2.0, 0, False)
    scores = []
    for pc in range(12):
        for minor, tmpl in ((False, _MAJOR), (True, _MINOR)):
            r = np.corrcoef(profile, np.roll(tmpl, pc))[0, 1]
            scores.append(r)
            if r > best[0]:
                best = (r, pc, minor)
    scores.sort(reverse=True)
    confidence = float(scores[0] - scores[1])
    return best[1], best[2], confidence


# ---------------------------------------------------------------- structure

def _bar_levels(sig: np.ndarray, sr: int, first_beat: float, bar: float, n: int):
    out = np.empty(n)
    for i in range(n):
        seg = sig[int((first_beat + i * bar) * sr):int((first_beat + (i + 1) * bar) * sr)]
        out[i] = 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9) if len(seg) else -120
    return out


def detect_intro_outro(y: np.ndarray, first_beat: float, bpm: float, sr: int = SR,
                       yh: np.ndarray | None = None):
    """Repère la fin de l'intro et le début de l'outro, calés sur les phrases.

    Les intros / outros « DJ » des extended mixes sont la rythmique sans voix
    ni mélodie : leur volume global est le même que le reste du morceau, mais
    la partie harmonique dans les médiums (300–3 500 Hz : voix, accords,
    mélodie) y est bien plus faible. On mesure ce niveau mesure par mesure :
    l'intro se termine à la première paire de mesures qui atteint le niveau du
    corps du morceau (à 9 dB près), l'outro commence après la dernière.
    Résultats arrondis à la phrase de 4 mesures.
    """
    bar = 4 * 60.0 / bpm
    duration = len(y) / sr
    n_bars = int((duration - first_beat) // bar)
    if n_bars < 8:
        return first_beat, first_beat, duration, 0.5
    if yh is None:
        yh = harmonic_part(y, sr)
    sos = butter(4, [300, 3500], btype="band", fs=KEY_SR, output="sos")
    mid = sosfiltfilt(sos, yh)
    lv = _bar_levels(mid, KEY_SR, first_beat, bar, n_bars)
    full = _bar_levels(y, sr, first_beat, bar, n_bars)

    thr = np.percentile(lv, 75) - 9.0
    on = lv >= thr
    pair = on[:-1] & on[1:]
    if pair.any():
        i_in = int(np.argmax(pair))
        i_out = int(len(pair) - np.argmax(pair[::-1]))     # dernière mesure « pleine »
    else:
        i_in, i_out = 0, n_bars - 1

    def snap(i):  # phrase de 4 mesures la plus proche
        return int(4 * round(i / 4))

    i_in = min(snap(i_in), n_bars)
    i_out_end = min(max(snap(i_out + 1), i_in + 1), n_bars)
    intro_end = first_beat + i_in * bar
    outro_start = min(first_beat + i_out_end * bar, duration)

    energy = float(np.clip((float(np.mean(full)) + 40) / 40, 0, 1))
    return first_beat, intro_end, outro_start, energy


# ---------------------------------------------------------------- premier temps de la mesure

def detect_downbeat(y: np.ndarray, yh: np.ndarray, first_beat: float, bpm: float,
                    sr: int = SR):
    """Trouve lequel des 4 temps est le « 1 » de la mesure.

    Indices utilisés, temps par temps :
      - changements d'accord / de note de basse (chroma de la partie harmonique) :
        ils tombent surtout sur le 1, parfois sur le 3 ;
      - débuts de sections (entrée de la voix, d'un instrument, d'un break) :
        changement de niveau entre les 4 temps d'avant et les 4 d'après —
        une section commence toujours sur un 1 ;
      - le kick + la basse sont souvent plus appuyés sur le 1.
    Renvoie (décalage 0–3 en temps, confiance).
    """
    beat = 60.0 / bpm
    n = int((len(y) / sr - first_beat) / beat) - 1
    if n < 32:
        return 0, 0.0
    t_beats = first_beat + np.arange(n) * beat

    # chroma par temps
    hop = 256
    C = librosa.feature.chroma_cqt(y=yh, sr=KEY_SR, hop_length=hop, norm=None,
                                   fmin=librosa.note_to_hz("C2"), n_octaves=6)
    ct = librosa.frames_to_time(np.arange(C.shape[1]), sr=KEY_SR, hop_length=hop)
    idx = np.floor((ct - first_beat) / beat).astype(int)
    ok = (idx >= 0) & (idx < n)
    Bc = np.zeros((12, n))
    np.add.at(Bc.T, idx[ok], C[:, ok].T)
    Bn = Bc / (np.linalg.norm(Bc, axis=0) + 1e-9)
    chord = np.zeros(n)
    chord[1:] = 1 - np.sum(Bn[:, 1:] * Bn[:, :-1], axis=0)

    # niveau par temps (partie harmonique) -> débuts de sections
    lvl = np.zeros(n)
    yh_t = np.arange(len(yh)) / KEY_SR
    for i in range(n):
        seg = yh[int(t_beats[i] * KEY_SR):int((t_beats[i] + beat) * KEY_SR)]
        lvl[i] = 10 * np.log10(np.mean(seg ** 2) + 1e-10)
    k = 4
    section = np.zeros(n)
    for i in range(k, n - k):
        section[i] = abs(lvl[i:i + k].mean() - lvl[i - k:i].mean())

    # accent grave par temps (kick + basse)
    sos = butter(2, 150, fs=sr, output="sos")
    low = sosfiltfilt(sos, y)
    acc = np.zeros(n)
    for i in range(n):
        s0 = int(t_beats[i] * sr)
        acc[i] = np.sqrt(np.mean(low[s0:s0 + int(0.1 * sr)] ** 2))

    def z(v):
        sd = v.std()
        return (v - v.mean()) / sd if sd > 1e-9 else v * 0

    feat = z(chord) + 1.5 * z(section) + 0.5 * z(acc)
    scores = np.array([feat[ph::4].mean() for ph in range(4)])
    order = np.argsort(scores)[::-1]
    conf = float(scores[order[0]] - scores[order[1]])
    return int(order[0]), conf


def robust_downbeat(y: np.ndarray, yh: np.ndarray, first_beat: float, bpm: float) -> int:
    """Décalage (temps) du « 1 », avec garde-fous.

    On accepte la détection si elle est nette (confiance ≥ 0,3) ou si les deux
    moitiés du morceau, analysées séparément, donnent le même résultat. Sinon
    on garde le premier kick comme « 1 » : c'est le cas de la très grande
    majorité des extended mixes et edits de DJ, qui démarrent sur la mesure.
    """
    beat = 60.0 / bpm
    ph, conf = detect_downbeat(y, yh, first_beat, bpm)
    if ph == 0 or conf >= 0.3:
        return ph
    nb = int((len(y) / SR - first_beat) / beat)
    half = nb // 2
    t_half = first_beat + half * beat
    p1, _ = detect_downbeat(y[:int(t_half * SR)], yh[:int(t_half * KEY_SR)], first_beat, bpm)
    p2, _ = detect_downbeat(y[int(t_half * SR):], yh[int(t_half * KEY_SR):], 0.0, bpm)
    p2 = (p2 + half) % 4
    return ph if p1 == p2 == ph else 0


# ---------------------------------------------------------------- point d'entrée

def analyze_file(path: str) -> dict:
    y = load_mono(path)
    bpm, first_beat, _ = detect_tempo(y)
    yh = harmonic_part(y)
    # On recale la grille sur le 1er temps de la mesure (le « 1 »)
    phase = robust_downbeat(y, yh, first_beat, bpm)
    first_beat += phase * 60.0 / bpm
    pc, minor, key_conf = detect_key(y, yh=yh)
    mix_in, intro_end, outro_start, energy = detect_intro_outro(y, first_beat, bpm, yh=yh)
    return dict(
        bpm=bpm, first_beat=round(first_beat, 3),
        key_name=key_label(pc, minor), camelot=camelot(pc, minor), key_confidence=key_conf,
        mix_in=round(mix_in, 3), intro_end=round(intro_end, 3),
        outro_start=round(outro_start, 3), energy=round(energy, 3),
    )


def _worker(args):
    track_id, path = args
    try:
        return track_id, analyze_file(path), None
    except Exception as e:  # fichier corrompu, format exotique…
        return track_id, None, f"{path}: {e}"


def analyze_library(conn: sqlite3.Connection, workers: int | None = None,
                    force: bool = False, progress=None) -> list[str]:
    """Analyse en parallèle tous les morceaux nouveaux ou modifiés."""
    where = "" if force else "WHERE analyzed_mtime IS NULL OR analyzed_mtime != mtime"
    todo = conn.execute(f"SELECT id, path, mtime FROM tracks {where}").fetchall()
    mtimes = {r["id"]: r["mtime"] for r in todo}
    errors = []
    if not todo:
        return errors
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker, (r["id"], r["path"])) for r in todo]
        for done, fut in enumerate(as_completed(futures), 1):
            track_id, res, err = fut.result()
            if err:
                errors.append(err)
            else:
                conn.execute(
                    "UPDATE tracks SET bpm=?, first_beat=?, key_name=?, camelot=?, mix_in=?, "
                    "intro_end=?, outro_start=?, energy=?, analyzed_mtime=? WHERE id=?",
                    (res["bpm"], res["first_beat"], res["key_name"], res["camelot"],
                     res["mix_in"], res["intro_end"], res["outro_start"], res["energy"],
                     mtimes[track_id], track_id))
                conn.commit()
            if progress:
                progress(done, len(todo))
    return errors
