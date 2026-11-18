"""Lecteur en ligne de commande (en attendant l'interface graphique).

Commandes pendant la lecture (taper puis Entrée) :
    (Entrée)        état : morceau en cours, suivant
    n               morceau suivant (sortie en écho) — « n » deux fois : coupe nette
    s texte         recherche (mêmes filtres que la commande search)
    j 3             « Jouer ensuite » le résultat n°3 de la dernière recherche
    a 3             « Ajouter à la file » le résultat n°3
    f               afficher la file d'attente
    r 2             retirer le n°2 de la file prioritaire
    q               quitter
"""

from __future__ import annotations

import random
import sqlite3
import time

import numpy as np

from . import library, search
from .engine import Engine
from .transitions import SR

DOTS = {"vert": "\033[32m●\033[0m", "orange": "\033[33m●\033[0m",
        "rouge": "\033[31m●\033[0m", "inconnu": "○"}


def _fmt(s):
    return f"{int(s // 60)}:{int(s % 60):02d}"


def _label(r):
    return f"{r.get('artist') or '?'} — {r.get('title') or '?'}"


def _find_playlist(conn, name):
    pls = library.playlists(conn)
    m = [p for p in pls if p["name"].lower() == name.lower()]
    if not m:
        m = [p for p in pls if name.lower() in p["name"].lower()]
    if not m:
        raise SystemExit(f"Playlist introuvable : {name}")
    return m[0]


def playlist_rows(conn, name, shuffle=False):
    pl = _find_playlist(conn, name)
    rows = [dict(r) for r in library.playlist_tracks(conn, pl["folder"])]
    if shuffle:
        random.shuffle(rows)
    return pl, rows


def status_line(eng: Engine) -> str:
    t, pos = eng.now_playing()
    if t is None:
        return "■ arrêt"
    info = f"{t.bpm:.0f} BPM {t.row.get('camelot') or ''}" if t.analyzed else "non analysé"
    nxt = eng.peek_next()
    tr = eng.in_transition()
    s = f"▶ {t.label}  {_fmt(pos)} / {_fmt(t.length)}  ({info})"
    if tr:
        s += f"  [transition : {tr}]"
    if nxt:
        s += f"\n  suivant : {_label(nxt)}"
    return s


def run_play(conn: sqlite3.Connection, name: str, shuffle: bool = False):
    import sounddevice as sd

    pl, rows = playlist_rows(conn, name, shuffle)
    eng = Engine(log=lambda m: print(f"\n{m}\n> ", end="", flush=True))
    print(f"Playlist « {pl['name']} » — {len(rows)} morceaux. Tape « h » pour l'aide.")
    eng.load_playlist(rows)
    eng.start_worker()

    def callback(outdata, frames, time_info, status):
        outdata[:] = eng.pull(frames)

    last_results: list = []
    with sd.OutputStream(samplerate=SR, channels=2, dtype="float32", blocksize=1024,
                         latency="high", callback=callback):
        print(status_line(eng))
        while not eng.finished:
            try:
                cmd = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            op, _, arg = cmd.partition(" ")
            op = op.lower()
            if op == "":
                print(status_line(eng))
            elif op in ("h", "?", "aide"):
                print(__doc__)
            elif op == "n":
                eng.skip()
            elif op == "s":
                cur, _ = eng.now_playing()
                res = search.search(conn, arg, limit=15)
                last_results = [dict(r) for r in res]
                for i, r in enumerate(last_results, 1):
                    c = search.compatibility(cur.row, r) if cur else "inconnu"
                    bpm = f"{r['bpm']:.0f}" if r["bpm"] else "-"
                    print(f" {DOTS[c]} {i:>2}. {_label(r)}  ({bpm} BPM {r['camelot'] or ''})")
                if not last_results:
                    print("  aucun résultat")
            elif op in ("j", "a"):
                try:
                    r = last_results[int(arg) - 1]
                except (ValueError, IndexError):
                    print("  numéro invalide (fais d'abord une recherche avec « s »)")
                    continue
                (eng.play_next if op == "j" else eng.enqueue)(r)
                where = "jouera ensuite" if op == "j" else "ajouté à la file"
                print(f"  {_label(r)} : {where}")
            elif op == "f":
                for i, (kind, r) in enumerate(eng.upcoming(15)):
                    tag = {"figé": "🔒", "prioritaire": "★", "playlist": " "}[kind]
                    print(f"  {tag} {_label(r)}")
                print("  🔒 = déjà préparé   ★ = file prioritaire")
            elif op == "r":
                try:
                    eng.remove_from_queue(int(arg) - 1)
                except (ValueError, IndexError):
                    print("  numéro invalide (voir « f », compter à partir du 1er ★)")
            elif op == "q":
                break
            else:
                print("  commande inconnue — « h » pour l'aide")
    eng.stop()


def run_mix(conn: sqlite3.Connection, name: str, out: str, minutes: float,
            shuffle=False, skip_at=(), queue=()):
    """Exporte un mix dans un fichier (.wav ou .mp3) pour écouter les transitions."""
    from pedalboard.io import AudioFile

    pl, rows = playlist_rows(conn, name, shuffle)
    marks = []
    eng = Engine()
    eng.log = lambda m: marks.append(m)
    eng.load_playlist(rows)

    events = {}
    for t in skip_at:
        events[float(t)] = lambda e: e.skip()
    for spec in queue:                       # "ID@secondes" ou "ID@secondes:a"
        tid, _, rest = spec.partition("@")
        sec, _, mode = rest.partition(":")
        row = dict(conn.execute("SELECT * FROM tracks WHERE id = ?", (int(tid),)).fetchone())
        fn = (lambda r: (lambda e: e.enqueue(r)))(row) if mode == "a" else \
             (lambda r: (lambda e: e.play_next(r)))(row)
        events[float(sec)] = fn

    timeline = []
    eng.log = lambda m: timeline.append((eng.frames_out / SR, m))
    t0 = time.time()
    audio = eng.render_offline(minutes * 60, events=events)
    opts = {"quality": "V2"} if out.lower().endswith(".mp3") else {}   # MP3 ~190 kbit/s
    with AudioFile(out, "w", SR, 2, **opts) as f:
        f.write(np.ascontiguousarray(audio.T))
    print(f"Mix « {pl['name']} » : {_fmt(len(audio) / SR)} exportés dans {out} "
          f"(calculé en {time.time() - t0:.0f} s)")
    for t, m in timeline:
        print(f"  {_fmt(t)}  {m}")
    return audio, timeline
