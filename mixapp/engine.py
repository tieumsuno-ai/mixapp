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

from . import highlights as HL
from . import ordering
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
        self.stopping = False          # arrêt (ou mise en pause) en cours
        self.stop_style = "echo"       # echo | turntable | pullup | pause
        self.paused = False            # en pause (reprise possible)
        self._resume: tuple | None = None   # (segment à reprendre, transition prévue)
        self.last_block = np.zeros((0, 2), np.float32)   # pour l'égaliseur animé
        self.hl_mode: str | None = None        # « temps forts » : None | "on"
        self.mix_seconds: float | None = None  # durée des transitions (réglette) ; None = auto
        self.next_style: str | None = None     # pad : transition imposée pour le prochain enchaînement
        self.style_events: list[str] = []      # messages pour l'interface (pad utilisé, repli…)
        self.dj_order = False                  # « Ordre DJ » : file réordonnée tempo / clé / énergie
        self._folder_order: list[int] = []
        self._hl_info: dict[int, dict | None] = {}
        self.vocal_provider = None     # fonction(track) → activité vocale par trame de 0,25 s (ou None)
        self.stop_requests = 0
        self._thread = None
        self._stop = False

    # ------------------------------------------------------------ files d'attente

    def load_playlist(self, rows: list[dict], start: int = 0):
        rows = [dict(r) for r in rows]
        if not rows:
            raise ValueError("playlist vide")
        self._folder_order = [r["id"] for r in rows]
        if self.dj_order:
            rows = ordering.dj_order(rows, rows[start]["id"])
            start = 0
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

    def play_next_many(self, rows: list[dict]):
        """« Jouer ensuite » sur plusieurs morceaux : ils passent juste après
        celui en cours, dans l'ordre donné."""
        with self.lock:
            for r in reversed(rows):
                self.priority.appendleft(dict(r))

    def enqueue_many(self, rows: list[dict]):
        with self.lock:
            for r in rows:
                self.priority.append(dict(r))

    def remove_upcoming_many(self, positions) -> int:
        """Retire plusieurs morceaux de « À suivre » d'un coup ; renvoie combien."""
        n = 0
        with self.lock:
            for pos in sorted(set(positions), reverse=True):
                n += bool(self.remove_upcoming(pos))
        return n

    def move_upcoming_many(self, positions, delta: int) -> list[int]:
        """Déplace d'un cran un groupe de morceaux (sélection multiple) ;
        renvoie leurs nouvelles places. Le groupe garde son ordre."""
        with self.lock:
            out, taken = [], set()
            for pos in sorted(set(positions), reverse=delta > 0):
                if pos + delta in taken:          # bloqué par le voisin du groupe
                    out.append(pos)
                    taken.add(pos)
                    continue
                j = self.move_upcoming(pos, delta)
                out.append(j)
                taken.add(j)
            return sorted(out)

    def _cancel_pending(self, requeue: bool) -> bool:
        """Annule la transition déjà préparée (si elle n'a pas commencé).
        requeue : remettre son morceau en tête de file prioritaire. Sous verrou."""
        tr, seg = self.pending, self.cur
        if tr is None or seg is None or seg.trans is not None or seg.next is not None:
            return False
        if seg.pos > tr.a_end - int(1.0 * SR):        # trop près : l'enchaînement est lancé
            return False
        seg.end = len(seg.audio)
        self.pending = None
        if requeue:
            src = tr.b.row
            row = dict(src.get("_base") or src)
            if src.get("_from_priority") or (src.get("_base") or {}).get("_from_priority"):
                row["_from_priority"] = True
            self._push_back(row)
        return True

    def _flat(self):
        return list(self.priority) + list(self.auto[self.auto_idx:])

    def _set_flat(self, flat, n_prio):
        self.priority = deque(flat[:n_prio])
        self.auto[self.auto_idx:] = flat[n_prio:]

    def remove_upcoming(self, pos: int) -> bool:
        """Retire le n° `pos` de la liste « À suivre » (telle que renvoyée par upcoming)."""
        with self.lock:
            if self.pending is not None:
                if pos == 0:
                    return self._cancel_pending(requeue=False)
                pos -= 1
            flat = self._flat()
            if not 0 <= pos < len(flat):
                return False
            n_prio = len(self.priority) - (1 if pos < len(self.priority) else 0)
            del flat[pos]
            self._set_flat(flat, n_prio)
            return True

    def move_upcoming(self, pos: int, delta: int) -> int:
        """Déplace d'un cran le n° `pos` de « À suivre » ; renvoie sa nouvelle place.
        Un morceau qui monte au-dessus d'un morceau prioritaire devient prioritaire."""
        with self.lock:
            target = pos + delta
            if self.pending is not None and (pos == 0 or target == 0):
                if not self._cancel_pending(requeue=True):
                    return pos                         # déjà en train de s'enchaîner
            off = 1 if self.pending is not None else 0
            flat = self._flat()
            i, j = pos - off, target - off
            if not (0 <= i < len(flat) and 0 <= j < len(flat)):
                return pos
            flat[i], flat[j] = flat[j], flat[i]
            self._set_flat(flat, len(self.priority))
            return target

    # ------------------------------------------------------------ ordre DJ
    def set_dj_order(self, on: bool):
        """Réordonne la suite de la playlist (ou revient à l'ordre du dossier).
        Le calcul se fait hors verrou : le son et l'interface ne sont jamais bloqués."""
        with self.lock:
            self.dj_order = on
            tr, seg = self.pending, self.cur
            if tr is not None and self._cancel_pending(requeue=False):
                self.auto.insert(self.auto_idx, dict(tr.b.row.get("_base") or tr.b.row))
            rest = list(self.auto[self.auto_idx:])
            cur = (seg.track.row.get("_base") or seg.track.row) if seg and seg.track else None
            snapshot = [r["id"] for r in rest]
        if on:
            if cur is not None:
                new = ordering.dj_order([dict(cur)] + rest, cur["id"])[1:]
            else:
                new = ordering.dj_order(rest, rest[0]["id"] if rest else None)
        else:
            pos = {i: k for k, i in enumerate(self._folder_order)}
            new = sorted(rest, key=lambda r: pos.get(r["id"], 1e9))
        with self.lock:
            if [r["id"] for r in self.auto[self.auto_idx:]] == snapshot:   # rien n'a bougé entre-temps
                self.auto[self.auto_idx:] = new
        self.log("Flow DJ activé" if on else "Ordre du dossier")

    # ------------------------------------------------------------ temps forts
    def set_highlights(self, mode: str | None):
        """Active / désactive le mode « temps forts » (à tout moment).
        L'enchaînement déjà préparé est recalculé s'il n'a pas commencé."""
        with self.lock:
            if mode == self.hl_mode:
                return
            self.hl_mode = mode
            self._cancel_pending(requeue=True)
        self.log({None: "Temps forts désactivés", "court": "Temps forts : court",
                  "moyen": "Temps forts : moyen"}.get(mode, ""))

    def set_next_style(self, style: str | None):
        """Pad : impose le style du prochain enchaînement (un seul coup) ;
        None / "auto" = l'appli choisit. Le mix déjà préparé est refait
        s'il n'a pas commencé."""
        style = None if style in (None, "auto") else style
        with self.lock:
            if style == self.next_style:
                return
            self.next_style = style
            self._cancel_pending(requeue=True)

    def next_transition(self):
        """Prochain enchaînement prévu : (secondes avant le début, type, explication,
        repli éventuel), ou None s'il n'est pas encore préparé."""
        with self.lock:
            seg, tr = self.cur, self.pending
            if seg is None or tr is None or seg.trans is not None or self.stopping:
                return None
            if seg.track is None or seg.audio is not seg.track.audio:
                return None
            return ((tr.a_end - seg.pos) / SR, tr.kind, tr.info.get("why", ""),
                    tr.info.get("fallback"))

    def deck_state(self):
        """Ce que montrent les platines : morceau et position (s) de chaque côté,
        position du crossfader (0 = A, 1 = B) et type de transition en cours."""
        with self.lock:
            seg = self.cur
            if seg is None:
                return None
            tr = seg.trans
            if tr is None:
                a = seg.track
                pos = seg.pos / SR
                if seg.next is not None and seg.next.track is a and a is not None \
                        and seg.audio is not a.audio:
                    pos = seg.next.pos / SR
                nb = self.pending.b if self.pending is not None else None
                b_pos = self.pending.b_start / SR if self.pending is not None else 0.0
                if nb is None:                       # pas encore préparé : le suivant, s'il est chargé
                    row = self.peek_next()
                    nb = self.cache.get(row["id"]) if row is not None else None
                    if nb is not None and nb.analyzed:
                        b_pos = nb.first_beat
                return {"a": a, "a_pos": pos, "b": nb, "b_pos": b_pos, "x": 0.0, "kind": None}
            n = len(tr.buffer)
            ov = tr.info.get("ov_len", n) if tr.kind == "beatmatch" else n
            p = seg.pos
            if tr.kind in ("beatmatch", "fade"):
                x = min(1.0, p / max(1, ov))
            else:                                   # effets : b entre à b_offset
                x = 0.0 if p < tr.b_offset else min(1.0, 0.5 + (p - tr.b_offset) / max(1, n - tr.b_offset))
            return {"a": seg.track, "a_pos": (tr.a_end + p) / SR, "b": tr.b,
                    "b_pos": max(0.0, (tr.b_start + p - tr.b_offset) / SR), "x": x,
                    "kind": tr.kind if p <= ov else None}

    def set_mix_seconds(self, seconds: float | None):
        """Durée des transitions choisie à la réglette (None = l'appli décide).
        L'enchaînement déjà préparé est recalculé s'il n'a pas commencé."""
        seconds = None if not seconds else float(seconds)
        with self.lock:
            if seconds == self.mix_seconds:
                return
            self.mix_seconds = seconds
            self._cancel_pending(requeue=True)

    def hl_window(self, t: Track, quick: bool = False) -> dict | None:
        """Passage retenu pour ce morceau dans le mode actuel (ou None)."""
        if not self.hl_mode or t is None or not t.analyzed:
            return None
        base = HL.view(t, None)
        if t.id not in self._hl_info:
            try:
                self._hl_info[t.id] = HL.find(base)
            except Exception:
                self._hl_info[t.id] = None
        vocal = None
        if self.vocal_provider is not None:
            try:
                try:
                    frames = self.vocal_provider(base, compute=not quick)
                except TypeError:
                    frames = None if quick else self.vocal_provider(base)
                if frames is not None:
                    vocal = HL.vocal_bars(frames, base)
            except Exception:
                vocal = None
        return HL.window(base, self.hl_mode, self._hl_info[t.id], vocal)

    def _view(self, t: Track, now: float | None = None, quick: bool = False) -> Track:
        return HL.view(t, self.hl_window(t, quick), now)

    def seek(self, seconds: float) -> bool:
        """Déplace la lecture dans le morceau en cours (fondu croisé de 20 ms).
        Impossible pendant un enchaînement ou un arrêt."""
        with self.lock:
            seg = self.cur
            if seg is None or seg.trans is not None or self.stopping or self.paused:
                return False
            real = seg
            if seg.next is not None and seg.next.track is seg.track and seg.next.audio is not seg.audio:
                real = seg.next                       # petit segment de raccord en tête
            elif seg.next is not None or seg.track is None:
                return False
            t = real.track
            target = int(max(0.0, min(seconds, t.length - 2.0)) * SR)
            tr = self.pending
            if tr is not None and target >= tr.a_end - int(1.0 * SR):
                # on saute au-delà de l'enchaînement prévu : il sera recalculé
                tr, self.pending = self.pending, None
                src = tr.b.row
                row = dict(src.get("_base") or src)
                if src.get("_from_priority"):
                    row["_from_priority"] = True
                self._push_back(row)
                tr = None
            end = tr.a_end if tr is not None else len(real.audio)
            n = XFADE
            ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
            out_ = np.zeros((n, 2), np.float32)
            cur_src = seg.audio[seg.pos:seg.pos + n]
            out_[:len(cur_src)] = cur_src * (1 - ramp[:len(cur_src)])
            new_src = real.audio[target:target + n]
            out_[:len(new_src)] += new_src * ramp[:len(new_src)]
            real.pos, real.end = target + n, end
            self.cur = Segment(out_, 0, n, track=t, nxt=real)
            return True

    def remove_from_queue(self, index: int):
        with self.lock:
            del self.priority[index]

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
                r = self.priority.popleft()
                r["_from_priority"] = True
                return r
            if self.auto_idx < len(self.auto):
                self.auto_idx += 1
                r = self.auto[self.auto_idx - 1]
                r.pop("_from_priority", None)
                return r
            return None

    def _push_back(self, row: dict):
        """Remet un morceau sorti de la file exactement où il était (sous verrou)."""
        row = dict(row.get("_base") or row) if row.get("_base") else row
        if row.get("_from_priority"):
            self.priority.appendleft(row)
        else:
            self.auto.insert(self.auto_idx, row)

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
                nxt = seg.next
                if nxt is not None and nxt.track is seg.track and nxt.audio is not seg.audio:
                    return seg.track, nxt.pos / SR       # court raccord (reprise, saut)
                return seg.track, seg.pos / SR
            tr = seg.trans
            # pendant un mix calé, le morceau entrant devient « en cours » à mi-parcours
            ov = tr.info.get("ov_len", len(tr.buffer))
            switch = ov // 2 if tr.kind == "beatmatch" else tr.b_offset
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

    def stop_soft(self, style: str = "echo"):
        """Stop avec effet (echo | turntable | pullup). Un 2e appui coupe tout de suite."""
        with self.lock:
            if self.paused and self.cur is None:
                self.finished = True
                return
            if self.stopping and self.cur is not None and self.stop_style != "pause":
                # 2e appui : fondu de 30 ms et fin
                seg = self.cur
                n = min(int(0.03 * SR), seg.end - seg.pos)
                tail = seg.audio[seg.pos:seg.pos + n].copy()
                tail *= np.linspace(1, 0, len(tail), dtype=np.float32)[:, None]
                self.cur = Segment(tail, 0, len(tail))
                self.pending = None
                return
            self.stopping = True
            self.paused = False
            self._resume = None
            self.stop_style = style
            self.stop_requests += 1

    def pause(self):
        """Pause : le son part en écho + fader ; reprise au même endroit."""
        with self.lock:
            if self.stopping or self.paused or self.cur is None:
                return
            self.stopping = True
            self.stop_style = "pause"
            self.stop_requests += 1

    def resume(self):
        """Reprise après une pause (fondu d'entrée de 60 ms, sur le temps)."""
        with self.lock:
            if self._resume is None:
                if self.stopping and self.stop_style == "pause" and self.cur is not None:
                    # la pause n'a pas encore commencé : on l'annule simplement
                    self.stopping = False
                    self.stop_requests = 0
                    self.stop_style = "echo"
                    return True
                return False
            seg, pending = self._resume
            self._resume = None
            n = min(int(0.06 * SR), seg.end - seg.pos)
            head = seg.audio[seg.pos:seg.pos + n].copy()
            head *= np.linspace(0, 1, len(head), dtype=np.float32)[:, None]
            seg.pos += n
            self.cur = Segment(head, 0, len(head), track=seg.track, trans=None, nxt=seg)
            self.pending = pending
            self.paused = False
            self.stopping = False
            self.stop_style = "echo"
            self.finished = False
        self.log("▶ reprise")
        return True

    @property
    def pausing(self) -> bool:
        return self.stopping and self.stop_style == "pause"

    def transition_state(self):
        """(type, avancement 0..1, explication) de la transition en cours, ou None."""
        with self.lock:
            seg = self.cur
            if seg is None or seg.trans is None:
                return None
            tr = seg.trans
            ov = tr.info.get("ov_len", len(tr.buffer)) if tr.kind == "beatmatch" else len(tr.buffer)
            if seg.pos > ov:
                return None                    # mélange fini (retour en douceur au tempo de b)
            return tr.kind, seg.pos / max(1, ov), tr.info.get("why", "")

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
        self.last_block = out
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
            if tr.info.get("forced") and self.next_style == tr.info["forced"]:
                self.next_style = None             # pad « un coup » : retour en Auto
                self.style_events.append("auto")
            if not self.history or self.history[-1].get("id") != tr.b.id:
                self.history.append(tr.b.row)
            self.log(f"⇄ {tr.b.label}  ({_KIND[tr.kind]})")
            return
        else:                                             # fin de A sans transition prête
            self.cur = None
            if self.stopping and self.stop_style == "pause":
                self.paused = True
                self.stopping = False
                self._deferred.append("❚❚ pause")
                return
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
        """Travail de fond (hors ligne : les deux parties à la suite)."""
        self.service_commands()
        self.service_prepare()

    def service_commands(self):
        """Touches Suivant / Stop / Pause : traitées tout de suite, jamais
        bloquées par la préparation d'un mix (fil séparé)."""
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
            idle = self.cur is None and not self.finished and not self.paused
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

    def service_prepare(self):
        """Préchargement du morceau suivant et préparation du mix (lent : 2 à 15 s)."""
        cand = self.peek_next()
        if cand is not None and cand["id"] not in self.cache:
            self._get_track(cand)

        with self.lock:
            seg = self.cur
            ready = (seg is not None and seg.trans is None and seg.next is None
                     and self.pending is None and not self.stopping and not self.skip_requests)
            if ready:
                a = seg.track
                now = seg.pos / SR
        if not ready:
            return
        a = self._view(a, now)
        if now < lock_point(a):
            return
        # on choisit le morceau sans le retirer de la file : si une touche est
        # pressée pendant le calcul, le résultat est simplement abandonné
        row = self.peek_next()
        if row is None:
            return
        b = self._view(self._get_track(row))
        style = self.next_style
        tr = render_auto(a, b, self.mix_seconds, style)
        with self.lock:
            still = (self.cur is seg and self.pending is None and not self.stopping
                     and self.next_style == style
                     and not self.skip_requests and self.peek_next() is not None
                     and self.peek_next()["id"] == row["id"])
            if not still:
                return
            popped = self._pop_next()
            tr.b.row["_from_priority"] = popped.get("_from_priority", False)
            if seg.pos < tr.a_end - int(0.05 * SR):
                seg.end = tr.a_end
                self.pending = tr
                return
            pos_now = seg.pos
        # Trop tard (morceau très court, ou ajout de dernière minute) : écho au prochain temps
        tr = self._echo_from(seg, pos_now, b)
        with self.lock:
            if self.cur is seg and seg.pos < tr.a_end and self.pending is None:
                seg.end = tr.a_end
                self.pending = tr
            else:
                self._push_back(popped)                    # on réessaiera

    def _echo_from(self, seg: Segment, pos: int, b: Track) -> Transition:
        a = seg.track
        t = pos / SR + SKIP_SAFETY
        if seg.trans is None and a.analyzed:
            t = a.next_beat_after(t)
            bpm = a.bpm
        else:
            bpm = a.bpm if a.analyzed else 120.0
        p = int(round(t * SR))
        b = self._view(b, quick=True)
        return render_echo_out(seg.audio, p, bpm, b, b_start=b.row.get("_hl_entry"))

    def _handle_stop(self):
        from . import effects
        with self.lock:
            self.stop_requests = 0
            seg = self.cur
            if seg is None:
                if not self.paused:
                    self.finished = True
                return
            pos = seg.pos
            style = self.stop_style
            pending = self.pending
        if seg.next is not None and pos + int(0.5 * SR) >= seg.end:
            with self.lock:                      # court segment de raccord : on attend la suite
                self.stop_requests += 1
            return
        a = seg.track
        t = pos / SR + SKIP_SAFETY
        if style != "turntable" and seg.trans is None and a is not None and a.analyzed:
            t = a.next_beat_after(t)                     # l'effet part sur le temps
        bpm = a.bpm if (a is not None and a.analyzed) else 120.0
        p = int(round(t * SR))
        if style == "turntable":
            tail = effects.render_turntable_stop(seg.audio, p, bpm)
        elif style == "pullup":
            tail = effects.render_pullup(seg.audio, p, bpm)
        else:
            tail = render_echo_stop(seg.audio, p, bpm)
        with self.lock:
            if self.cur is seg and seg.pos < p and self.stopping:
                if style == "pause":
                    # de quoi reprendre exactement là où l'effet commence
                    keep = Segment(seg.audio, p, seg.end, track=seg.track,
                                   trans=seg.trans, nxt=seg.next)
                    self._resume = (keep, pending)
                seg.end = min(p, seg.end)
                seg.next = Segment(tail, 0, len(tail))
                seg.trans = None
                self.pending = None
                self.log({"pause": "❚❚ pause en écho", "turntable": "◉ arrêt 33 tours",
                          "pullup": "↺ pull-up"}.get(style, "〰 arrêt en écho"))
            elif self.cur is not None and self.stopping:
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
        def loop(fn, pause):
            while not self._stop:
                try:
                    fn()
                except Exception as e:  # ne jamais tuer le fil
                    self.log(f"⚠ {e}")
                time.sleep(pause)
        self._thread = threading.Thread(target=loop, args=(self.service_commands, 0.02),
                                        daemon=True, name="mixapp-commandes")
        self._thread_prep = threading.Thread(target=loop, args=(self.service_prepare, 0.1),
                                             daemon=True, name="mixapp-preparation")
        self._thread.start()
        self._thread_prep.start()

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


_KIND = {"beatmatch": "mix calé", "echo": "écho", "fade": "fondu", "filtre": "filtre + écho", "pullup": "pull-up"}
