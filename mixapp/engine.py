"""Moteur de lecture : enchaînement automatique, file prioritaire, touche « suivant ».

Principe
--------
La sortie audio lit une suite de *segments* :

    [morceau A, lu tel quel] → [tampon de transition A→B] → [morceau B, lu tel quel] → …

Le rappel audio (``pull``) ne fait que copier des échantillons déjà prêts : il ne
calcule jamais rien, donc pas de craquement. Tout le travail lourd (décodage,
changement de tempo, effets) est fait à l'avance par ``service()``, appelé en
boucle par un fil d'exécution séparé (ou entre deux blocs en rendu hors ligne).

Files d'attente
---------------
- *file prioritaire* : « Jouer ensuite » (en tête) et « Ajouter à la file »
  (en queue) ; elle passe toujours avant la playlist ;
- *playlist automatique* : reprend là où elle s'était arrêtée quand la file
  prioritaire est vide.

Le morceau suivant est **figé ~25 s avant le début de la transition** (le temps
de la préparer). Un ajout fait après ce moment passe au morceau d'après.

Touche « suivant »
------------------
1er appui : sortie en écho sur le prochain temps, le morceau suivant entre
2 temps plus tard. 2e appui pendant l'écho (ou pendant une transition) : coupe
nette et immédiate vers le morceau suivant.
"""

from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

from .transitions import (SR, Track, Transition, load_track, lock_point,
                          render_auto, render_echo_out, render_echo_stop)

XFADE = int(0.02 * SR)        # fondu des coupes nettes (20 ms)
SKIP_SAFETY = 0.25            # s : délai minimal avant l'écho (temps de calcul)


class Segment:
    __slots__ = ("audio", "pos", "end", "track", "trans", "next")

    def __init__(self, audio, pos, end, track=None, trans=None, nxt=None):
        self.audio, self.pos, self.end = audio, pos, end
        self.track, self.trans, self.next = track, trans, nxt


class Engine:
    def __init__(self, log=None, on_analyzed=None):
        self.lock = threading.RLock()
        self.cur: Segment | None = None
        self.pending: Transition | None = None      # transition prévue pour le segment courant
        self.auto: list[dict] = []
        self.auto_idx = 0
        self.priority: deque[dict] = deque()
        self.cache: dict[int, Track] = {}
        self.skip_requests = 0
        self.finished = False
        self.history: list[dict] = []
        self.frames_out = 0
        self._log = log or (lambda msg: None)
        self._deferred: list = []
        self._audio_thread = None        # messages / objets à libérer hors du fil audio
        # appelé (row mis à jour) quand un morceau a été analysé à la volée
        self.on_analyzed = on_analyzed
        self.volume = 1.0
        self.stopping = False          # arrêt en écho en cours
        self.stop_requests = 0
        self._thread = None
        self._stop = False

    # ------------------------------------------------------------ files d'attente

    def load_playlist(self, rows: list[dict], start: int = 0):
        rows = [dict(r) for r in rows]
        if not rows:
            raise ValueError("playlist vide")
        first = self._get_track(rows[start])
        with self.lock:
            self.auto = rows
            self.auto_idx = start + 1
            self.cur = Segment(first.audio, 0, len(first.audio), track=first)
            self.pending = None
            self.finished = False
            self.history.append(first.row)
        self.log(f"▶ {first.label}")

    def play_next(self, row: dict):
        """« Jouer ensuite » : en tête de la file prioritaire."""
        with self.lock:
            self.priority.appendleft(dict(row))

    def enqueue(self, row: dict):
        """« Ajouter à la file » : en fin de file prioritaire."""
        with self.lock:
            self.priority.append(dict(row))

    def remove_from_queue(self, index: int):
        with self.lock:
            del self.priority[index]

    def move_in_queue(self, index: int, delta: int) -> int:
        """Déplace le n° `index` de la file prioritaire ; renvoie sa nouvelle place."""
        with self.lock:
            j = max(0, min(len(self.priority) - 1, index + delta))
            if j != index:
                item = self.priority[index]
                del self.priority[index]
                self.priority.insert(j, item)
            return j

    def peek_next(self) -> dict | None:
        with self.lock:
            if self.pending is not None:
                return self.pending.b.row
            if self.priority:
                return self.priority[0]
            if self.auto_idx < len(self.auto):
                return self.auto[self.auto_idx]
            return None

    def upcoming(self, n: int = 10) -> list[tuple[str, dict]]:
        """Ce qui va être joué : [('figé'|'prioritaire'|'playlist', row), …]"""
        with self.lock:
            out = []
            if self.pending is not None:
                out.append(("figé", self.pending.b.row))
            out += [("prioritaire", r) for r in self.priority]
            out += [("playlist", r) for r in self.auto[self.auto_idx:]]
            return out[:n]

    def _pop_next(self) -> dict | None:
        with self.lock:
            if self.priority:
                return self.priority.popleft()
            if self.auto_idx < len(self.auto):
                self.auto_idx += 1
                return self.auto[self.auto_idx - 1]
            return None

    def _get_track(self, row: dict) -> Track:
        t = self.cache.get(row["id"])
        if t is None:
            if not row.get("bpm"):
                # Analyse à la volée (~5 s) : faite pendant le préchargement,
                # donc bien avant que le morceau ne soit joué.
                from .analysis import analyze_file
                try:
                    self.log(f"… analyse de {row.get('title') or row['path']}")
                    row.update(analyze_file(row["path"]))
                    if self.on_analyzed:
                        self.on_analyzed(row)
                except Exception as e:
                    self.log(f"⚠ analyse impossible : {e}")
            t = load_track(row)
            self.cache[row["id"]] = t
        return t

    # ------------------------------------------------------------ état

    def now_playing(self) -> tuple[Track | None, float]:
        """Morceau principal en cours et position (s) dans ce morceau."""
        with self.lock:
            seg = self.cur
            if seg is None:
                return None, 0.0
            if seg.trans is None:
                return seg.track, seg.pos / SR
            tr = seg.trans
            # pendant un mix calé, le morceau entrant devient « en cours » à mi-parcours
            switch = len(tr.buffer) // 2 if tr.kind == "beatmatch" else tr.b_offset
            if seg.pos >= switch:
                return tr.b, (tr.b_start + seg.pos - tr.b_offset) / SR
            return seg.track, (tr.a_end + seg.pos) / SR

    def in_transition(self) -> str | None:
        with self.lock:
            return self.cur.trans.kind if self.cur and self.cur.trans else None

    def transition_target(self):
        """Morceau vers lequel on est en train d'enchaîner (ou None)."""
        with self.lock:
            return self.cur.trans.b if self.cur and self.cur.trans else None

    # ------------------------------------------------------------ commandes

    def skip(self):
        """Touche « suivant » (un 2e appui pendant l'écho coupe net)."""
        with self.lock:
            if not self.stopping:
                self.skip_requests += 1

    def stop_soft(self):
        """Stop : sortie en écho puis silence. Un 2e appui coupe tout de suite."""
        with self.lock:
            if self.stopping and self.cur is not None:
                # 2e appui : fondu de 30 ms et fin
                seg = self.cur
                n = min(int(0.03 * SR), seg.end - seg.pos)
                tail = seg.audio[seg.pos:seg.pos + n].copy()
                tail *= np.linspace(1, 0, len(tail), dtype=np.float32)[:, None]
                self.cur = Segment(tail, 0, len(tail))
                self.pending = None
                return
            self.stopping = True
            self.stop_requests += 1

    # ------------------------------------------------------------ sortie audio

    def pull(self, n: int) -> np.ndarray:
        """Renvoie n échantillons stéréo. Appelé par la carte son : jamais de calcul lourd."""
        self._audio_thread = threading.current_thread()
        out = np.zeros((n, 2), np.float32)
        filled = 0
        with self.lock:
            while filled < n and self.cur is not None:
                seg = self.cur
                take = min(n - filled, seg.end - seg.pos)
                if take > 0:
                    out[filled:filled + take] = seg.audio[seg.pos:seg.pos + take]
                    seg.pos += take
                    filled += take
                if seg.pos >= seg.end:
                    self._advance()
            self.frames_out += n
        if self.volume != 1.0:
            out *= self.volume
        # limiteur doux : rien ne change sous 0,75, compression au-dessus, plafond 0,95
        # (marge pour les crêtes que crée un encodage MP3)
        over = np.abs(out) > 0.75
        if over.any():
            x = out[over]
            out[over] = np.sign(x) * (0.75 + 0.2 * np.tanh((np.abs(x) - 0.75) / 0.2))
        return out

    def log(self, msg: str):
        """Journal : depuis le fil audio, le message est différé (rien de lent ici)."""
        if threading.current_thread() is self._audio_thread:
            self._deferred.append(msg)
        else:
            self._log(msg)

    def _flush_deferred(self):
        with self.lock:
            items, self._deferred = self._deferred, []
        for it in items:
            if isinstance(it, str):
                self._log(it)
        del items                       # libération des gros tableaux ici, pas dans le fil audio

    def _advance(self):
        """Passe au segment suivant (sous verrou, doit rester instantané)."""
        seg = self.cur
        if seg.next is not None:
            self.cur = seg.next
        elif seg.trans is not None:                       # fin de transition → morceau B
            b = seg.trans.b
            self.cur = Segment(b.audio, seg.trans.b_resume, len(b.audio), track=b)
        elif self.pending is not None:                    # fin de A → transition prévue
            tr, self.pending = self.pending, None
            self.cur = Segment(tr.buffer, 0, len(tr.buffer), track=seg.track, trans=tr)
            if not self.history or self.history[-1].get("id") != tr.b.id:
                self.history.append(tr.b.row)
            self.log(f"⇄ {tr.b.label}  ({_KIND[tr.kind]})")
            return
        else:                                             # fin de A sans transition prête
            self.cur = None
            if self.stopping:
                self.finished = True
                self.log("■ arrêt")
                return
            row = self.peek_next()
            if row is None:
                self.finished = True
                self.log("■ fin de la lecture")
            return
        if self.cur.trans is None and self.cur.track is not None and seg.trans is not None:
            keep = self.cur.track.id
            self._deferred.append([v for k, v in self.cache.items() if k != keep])
            self._deferred.append(seg)                    # libérés par le fil de travail
            self.cache = {k: v for k, v in self.cache.items() if k == keep}

    def _crossfade_to(self, seg: Segment, b: Track, b_pos: int):
        """Coupe nette (20 ms) de la position courante vers b à b_pos. Sous verrou."""
        a = np.zeros((XFADE, 2), np.float32)
        src = seg.audio[seg.pos:min(seg.pos + XFADE, seg.end)]
        a[:len(src)] = src
        bb = np.zeros((XFADE, 2), np.float32)
        s = b.audio[b_pos:b_pos + XFADE]
        bb[:len(s)] = s
        w = np.linspace(0, 1, XFADE, dtype=np.float32)[:, None]
        xf = a * (1 - w) + bb * w
        rest = Segment(b.audio, b_pos + XFADE, len(b.audio), track=b)
        self.cur = Segment(xf, 0, XFADE, track=b, nxt=rest)
        self.pending = None
        if not self.history or self.history[-1].get("id") != b.id:
            self.history.append(b.row)
        self.log(f"✂ {b.label}")

    # ------------------------------------------------------------ préparation (fil séparé)

    def service(self):
        """Travail de fond : touche suivant, préchargement, préparation des transitions."""
        self._flush_deferred()
        if self.stop_requests:
            self._handle_stop()
            return
        if self.stopping:
            return
        if self.skip_requests:
            self._handle_skip()

        # Reprise si la lecture s'est arrêtée faute de transition prête
        with self.lock:
            idle = self.cur is None and not self.finished
        if idle:
            row = self._pop_next()
            if row is None:
                with self.lock:
                    self.finished = True
                return
            b = self._get_track(row)
            with self.lock:
                s = int(b.first_beat * SR) if b.analyzed else 0
                self.cur = Segment(b.audio, s, len(b.audio), track=b)
                self.history.append(b.row)
            self.log(f"▶ {b.label}")

        # Préchargement du morceau candidat (décodage ~0,5 s)
        cand = self.peek_next()
        if cand is not None and cand["id"] not in self.cache:
            self._get_track(cand)

        # Préparer la transition quand on atteint le point de verrouillage
        with self.lock:
            seg = self.cur
            ready = (seg is not None and seg.trans is None and seg.next is None
                     and self.pending is None)
            if ready:
                a = seg.track
                due = seg.pos / SR >= lock_point(a)
        if not ready or not due:
            return
        row = self._pop_next()
        if row is None:
            return
        b = self._get_track(row)
        tr = render_auto(a, b)
        with self.lock:
            if self.cur is seg and seg.pos < tr.a_end - int(0.05 * SR):
                seg.end = tr.a_end
                self.pending = tr
                return
            pos_now = seg.pos
        # Trop tard (morceau très court, ou ajout de dernière minute) : écho au prochain temps
        tr = self._echo_from(seg, pos_now, b)
        with self.lock:
            if self.cur is seg and seg.pos < tr.a_end:
                seg.end = tr.a_end
                self.pending = tr
            else:
                self.priority.appendleft(row)              # on réessaiera

    def _echo_from(self, seg: Segment, pos: int, b: Track) -> Transition:
        a = seg.track
        t = pos / SR + SKIP_SAFETY
        if seg.trans is None and a.analyzed:
            t = a.next_beat_after(t)
            bpm = a.bpm
        else:
            bpm = a.bpm if a.analyzed else 120.0
        p = int(round(t * SR))
        return render_echo_out(seg.audio, p, bpm, b)

    def _handle_stop(self):
        with self.lock:
            self.stop_requests = 0
            seg = self.cur
            if seg is None:
                self.finished = True
                return
            pos = seg.pos
        a = seg.track
        t = pos / SR + SKIP_SAFETY
        if seg.trans is None and a is not None and a.analyzed:
            t = a.next_beat_after(t)
        bpm = a.bpm if (a is not None and a.analyzed) else 120.0
        p = int(round(t * SR))
        tail = render_echo_stop(seg.audio, p, bpm)
        with self.lock:
            if self.cur is seg and seg.pos < p:
                seg.end = min(p, seg.end)
                seg.next = Segment(tail, 0, len(tail))
                seg.trans = None
                self.pending = None
                self.log("〰 arrêt en écho")
            elif self.cur is not None:
                self.stop_requests += 1          # réessayer

    def _handle_skip(self):
        with self.lock:
            n, self.skip_requests = self.skip_requests, 0
            seg = self.cur
            if seg is None:
                return
            tr = seg.trans
            # Déjà en transition et le morceau suivant est audible → coupe nette
            if tr is not None and (tr.kind == "echo" or seg.pos >= tr.b_offset):
                self._crossfade_to(seg, tr.b, tr.b_start + max(0, seg.pos - tr.b_offset))
                return
            # Écho déjà programmé mais pas encore commencé + nouvel appui → coupe nette
            if (self.pending is not None and self.pending.info.get("skip")
                    and tr is None):
                b = self.pending.b
                self._crossfade_to(seg, b, int(b.first_beat * SR) if b.analyzed else 0)
                return
            if tr is not None:                 # pendant la rampe d'un mix : B est déjà choisi
                b = tr.b
            else:
                b = self.pending.b if self.pending is not None else None
            pos = seg.pos
        if b is None:
            row = self._pop_next()
            if row is None:                    # rien après : on laisse finir
                return
            b = self._get_track(row)
        echo = self._echo_from(seg, pos, b)
        echo.info["skip"] = True
        with self.lock:
            if self.cur is seg and seg.pos < echo.a_end:
                seg.end = echo.a_end
                if seg.trans is not None:      # écho pendant la rampe d'un mix :
                    seg.trans = None           # le tampon devient un simple segment de A
                self.pending = echo
                self.log("〰 sortie en écho")
                if n >= 2:                     # double appui très rapide
                    self.skip_requests += 1
            else:
                self.skip_requests += 1        # réessayer au prochain passage

    # ------------------------------------------------------------ temps réel

    def start_worker(self):
        def loop():
            while not self._stop:
                try:
                    self.service()
                except Exception as e:  # ne jamais tuer le fil
                    self.log(f"⚠ {e}")
                time.sleep(0.05)
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True

    def render_offline(self, seconds: float, block: int = 1024, events=None,
                       on_block=None) -> np.ndarray:
        """Rendu hors ligne (tests, export d'un mix). events = {seconde: callable}."""
        events = dict(sorted((events or {}).items()))
        chunks = []
        n_blocks = int(seconds * SR / block)
        for i in range(n_blocks):
            t = i * block / SR
            for et in [k for k in events if k <= t]:
                events.pop(et)(self)
            self.service()
            chunks.append(self.pull(block))
            if on_block:
                on_block(t)
            if self.finished:
                break
        return np.concatenate(chunks) if chunks else np.zeros((0, 2), np.float32)


_KIND = {"beatmatch": "mix calé", "echo": "écho", "fade": "fondu"}
