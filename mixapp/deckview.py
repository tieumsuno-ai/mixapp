"""Vue « Platines » : deux pochettes sur les couleurs d'ambiance, les formes
d'onde qui défilent, deux platines, la table de mixage et les pads.

Pensée pour rester légère :
- tout ce qui est lourd est calculé une seule fois par morceau, hors du fil de
  l'interface (forme d'onde → image, couleur dominante de la pochette) ;
- à chaque image on ne fait que recopier des morceaux d'images déjà prêtes et
  tracer quelques traits (repère des platines, curseurs, vumètre) ;
- une seule horloge, 30 images/s au plus, qui ralentit d'elle-même si le
  dessin devient lent, et s'arrête quand la vue est cachée ou en pause.
"""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

SR = 44100
STRIP_W = 64              # largeur d'une forme d'onde (px)
PX_PER_S = 60.0           # vitesse de défilement
RPM = 100.0 / 3.0         # 33 tours ⅓

PEACH = (255, 212, 168)
VOCAL = (110, 168, 255)

MODES = {"fluides": 33, "légères": 66, "aucune": 500}   # intervalle de l'horloge (ms)


# ------------------------------------------------------------------ calculs (hors interface)

def build_strip(audio: np.ndarray, vocal=None) -> tuple[QImage, float]:
    """Forme d'onde verticale d'un morceau (le temps monte vers le haut),
    en image. Calculée une fois ; le chant détecté est teinté en bleu."""
    dur = len(audio) / SR
    pxs = min(PX_PER_S, 30000.0 / max(dur, 1.0))
    hop = max(1, int(SR / pxs))
    mono = np.abs(audio[:, 0]) + np.abs(audio[:, 1]) if audio.ndim == 2 else np.abs(audio)
    n = len(mono) // hop
    blk = mono[:n * hop].reshape(n, hop)
    env = np.sqrt(np.mean(np.square(blk), 1))                  # énergie de chaque ligne
    env = np.convolve(env, np.ones(3) / 3, "same")
    env = env / max(1e-6, np.percentile(env, 99.7))
    half = np.clip(env, 0.0, 1.0) ** 1.8 * (STRIP_W / 2 - 1) + 0.5   # contraste : les coups ressortent
    col = np.abs(np.arange(STRIP_W) - (STRIP_W - 1) / 2)
    mask = col[None, :] <= half[:, None]
    img = np.zeros((n, STRIP_W, 4), np.uint8)
    img[..., :3] = PEACH
    if vocal is not None and len(vocal):
        t = np.arange(n) / pxs
        vi = np.clip((t / 0.25).astype(int), 0, len(vocal) - 1)
        vrow = np.asarray(vocal)[vi] > 0.5
        img[vrow, :, :3] = VOCAL
    img[..., 3] = np.where(mask, 255, 0)
    img = np.ascontiguousarray(img[::-1])                     # le temps monte
    q = QImage(img.data, STRIP_W, n, STRIP_W * 4, QImage.Format_RGBA8888).copy()
    return q, pxs


def dominant_color(pm: QPixmap | None) -> QColor:
    """Couleur marquante d'une pochette (réduite à 16 × 16 : instantané)."""
    if pm is None or pm.isNull():
        return QColor("#3a4058")
    im = pm.toImage().scaled(16, 16).convertToFormat(QImage.Format_RGB888)
    a = np.frombuffer(im.constBits(), np.uint8, im.bytesPerLine() * 16).reshape(16, im.bytesPerLine())
    a = a[:, :48].reshape(-1, 3).astype(np.float32)
    mx, mn = a.max(1), a.min(1)
    sat = (mx - mn) / (mx + 1)
    w = sat ** 2 * (mx / 255) + 1e-3
    c = (a * w[:, None]).sum(0) / w.sum()
    col = QColor(int(c[0]), int(c[1]), int(c[2]))
    h, s, v, _ = col.getHsv()
    return QColor.fromHsv(max(h, 0), min(255, max(s, 90)), min(v, 200))


# ------------------------------------------------------------------ éléments dessinés

class WaveStrip(QWidget):
    """Les deux formes d'onde côte à côte, calées sur le même trait de lecture."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setFixedWidth(2 * STRIP_W + 30)
        self.decks = [None, None]     # (pixmap, pxs, track, pos) par côté
        self.label = ""
        self.cost = 0.0

    def paintEvent(self, _e):
        t0 = time.perf_counter()
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#07080a"))
        y0 = int(h * 0.66)                                    # trait de lecture
        for side, d in enumerate(self.decks):
            if d is None:
                continue
            pm, pxs, tr, pos = d
            x = 8 + side * (STRIP_W + 14)
            if pm is not None:
                row = pm.height() - 1 - (pos * pxs + y0)
                src = QRectF(0, row, STRIP_W, h)
                tgt = QRectF(x, 0, STRIP_W, h)
                if row < 0:                                   # début du morceau
                    tgt.setTop(-row)
                    src.setTop(0)
                p.drawPixmap(tgt, pm, src)
            if tr is not None and getattr(tr, "analyzed", False) and tr.bar > 0:
                # traits de mesure (quelques lignes seulement)
                p.setPen(QColor(255, 255, 255, 40))
                first = tr.first_beat
                t_top = pos + y0 / pxs
                k0 = math.floor((pos - (h - y0) / pxs - first) / tr.bar)
                k1 = math.ceil((t_top - first) / tr.bar)
                for k in range(k0, k1 + 1):
                    yy = y0 - (first + k * tr.bar - pos) * pxs
                    p.drawLine(QPointF(x - 3, yy), QPointF(x + STRIP_W + 3, yy))
        p.setPen(QPen(QColor("#ff4b3e"), 1.5))
        p.drawLine(0, y0, w, y0)
        if self.label:
            f = p.font()
            f.setPixelSize(11)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(self.label) + 10
            p.fillRect(QRect(3, y0 + 3, tw, 16), QColor(7, 8, 10, 210))
            p.setPen(QColor("#6ea8ff"))
            p.drawText(QRect(8, y0 + 3, tw, 16), Qt.AlignLeft | Qt.AlignVCenter, self.label)
        p.end()
        self.cost = time.perf_counter() - t0


class Jog(QWidget):
    """Platine : image fixe (plateau + pochette) préparée une fois, seul le
    repère blanc tourne."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(110, 110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent)     # se peint seul : rien à redessiner derrière
        self.angle = 0.0
        self.active = False
        self._cover = None
        self._cover_key = None
        self._base = None
        self._base_key = None
        self.cost = 0.0

    def set_cover(self, key, pm: QPixmap | None):
        if key != self._cover_key:
            self._cover_key = key
            self._cover = pm
            self._base = None

    def _side(self):
        return max(60, min(self.width(), self.height()) - 4)

    def _make_base(self, d, active):
        dpr = self.devicePixelRatioF()
        img = QPixmap(int(d * dpr), int(d * dpr))
        img.setDevicePixelRatio(dpr)
        img.fill(QColor("#1c1d23"))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        c = d / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0c0d11"))
        p.drawEllipse(QPointF(c, c), c - 1, c - 1)
        p.setPen(QPen(QColor("#2a2c34"), 1))
        for i in range(90):                                 # stries du plateau
            a = i * math.pi / 45
            p.drawLine(QPointF(c + math.cos(a) * c * 0.80, c + math.sin(a) * c * 0.80),
                       QPointF(c + math.cos(a) * c * 0.90, c + math.sin(a) * c * 0.90))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#1d1f26"), c * 0.10))
        p.drawEllipse(QPointF(c, c), c * 0.62, c * 0.62)
        r = c * 0.44
        if self._cover is not None and not self._cover.isNull():
            path = QPainterPath()
            path.addEllipse(QPointF(c, c), r, r)
            p.setClipPath(path)
            p.drawPixmap(QRectF(c - r, c - r, 2 * r, 2 * r), self._cover, QRectF(self._cover.rect()))
            p.setClipping(False)
        else:
            p.setBrush(QColor("#2a2e38"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(c, c), r, r)
        p.setBrush(QColor("#e8e8ea"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(c, c), c * 0.04, c * 0.04)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#e08a2c") if active else QColor("#3a2a18"), 2))
        p.drawEllipse(QPointF(c, c), c - 2, c - 2)
        p.end()
        return img

    def paintEvent(self, _e):
        t0 = time.perf_counter()
        d = self._side()
        key = (d, self._cover_key, self.active)
        if self._base is None or self._base_key != key:
            self._base, self._base_key = self._make_base(d, self.active), key
        p = QPainter(self)
        x0, y0 = (self.width() - d) / 2, (self.height() - d) / 2
        if x0 > 0 or y0 > 0:
            p.fillRect(self.rect(), QColor("#1c1d23"))
        p.drawPixmap(QPointF(x0, y0), self._base)
        p.setRenderHint(QPainter.Antialiasing)
        c = d / 2
        a = math.radians(self.angle - 90)
        p.setPen(QPen(QColor("#f2f2f2"), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x0 + c + math.cos(a) * c * 0.66, y0 + c + math.sin(a) * c * 0.66),
                   QPointF(x0 + c + math.cos(a) * c * 0.78, y0 + c + math.sin(a) * c * 0.78))
        p.end()
        self.cost = time.perf_counter() - t0


class Mixer(QWidget):
    """Vumètre, faders des deux voies et crossfader (positions réelles)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(130)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.levels = (0.0, 0.0)       # dB
        self.gains = (1.0, 0.0)        # voies gauche / droite
        self.x = 0.0                   # crossfader (0 = gauche)
        self.cost = 0.0

    def paintEvent(self, _e):
        t0 = time.perf_counter()
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#1c1d23"))
        cx = w / 2
        # vumètre : 2 colonnes de 12 diodes
        n, dh = 12, max(4.0, (h * 0.42) / 12)
        top = 6
        for ch, lv in enumerate(self.levels):
            lit = int(np.clip((lv + 36) / 36 * n, 0, n))
            x = cx - 9 + ch * 12
            for i in range(n):
                y = top + (n - 1 - i) * dh
                on = i < lit
                col = ("#e5484d" if i >= n - 1 else "#f0b429" if i >= n - 3 else "#3fbf6a") if on else "#23262d"
                p.fillRect(QRectF(x, y, 6, dh - 2), QColor(col))
        # faders de voie
        fy0, fy1 = top + n * dh + 10, h - 40
        for side, g in enumerate(self.gains):
            x = cx - 32 + side * 64
            p.fillRect(QRectF(x - 1.5, fy0, 3, fy1 - fy0), QColor("#2a2c34"))
            y = fy1 - g * (fy1 - fy0)
            p.fillRect(QRectF(x - 10, y - 5, 20, 10), QColor("#d8dbe2"))
        # crossfader
        y = h - 16
        p.fillRect(QRectF(cx - 46, y - 2, 92, 4), QColor("#2a2c34"))
        kx = cx - 46 + self.x * 92
        p.fillRect(QRectF(kx - 6, y - 10, 12, 20), QColor("#f2f2f2"))
        p.end()
        self.cost = time.perf_counter() - t0


# ------------------------------------------------------------------ la vue

_PAD_CSS = """
QPushButton { background:#2a2c34; color:#c9ccd6; border:none; border-radius:6px;
              padding:7px 4px; font-size:12px; min-width:64px; }
QPushButton:hover { background:#343743; }
QPushButton:checked { background:#e08a2c; color:#2a1600; font-weight:600; }
QPushButton#auto:checked { background:#4f6bed; color:#fff; }
"""


class DeckView(QWidget):
    style_chosen = Signal(str)        # pad de transition (auto, cale, echo…)
    action = Signal(str)              # pads de droite (stop33, pullup, pause, next, hl, dj)
    mode_changed = Signal(str)

    def __init__(self, cover_for, vocal_for, mode="fluides", parent=None):
        super().__init__(parent)
        self.cover_for = cover_for        # fonction(track) → QPixmap | None
        self.vocal_for = vocal_for        # fonction(track) → carte du chant | None (sans calcul)
        self.engine = None
        self.mode = mode if mode in MODES else "fluides"
        self.auto_light = False
        self._strips: dict[int, tuple] = {}
        self._pool = ThreadPoolExecutor(1)
        self._asked: set[int] = set()
        self._lock = threading.Lock()
        self._ready: list = []
        self._sides: dict[int, int] = {}  # id du morceau → platine (0 gauche, 1 droite)
        self._bg = None
        self._bg_key = None
        self._colors: dict[int, QColor] = {}
        self._show = [None, None]
        self._costs: list[float] = []
        self._frame = 0
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    # ------------------------------------------------------------ construction

    def _build(self):
        self.setObjectName("deckview")
        # la feuille de style générale peint le fond de chaque élément : ici
        # tout est transparent pour laisser voir le décor (pochettes, couleurs)
        self.setStyleSheet("QWidget#deckview, QWidget#deckview QWidget { background: transparent; }"
                           "QComboBox { background:#2a2c34; color:#c9ccd6; border:none; border-radius:6px;"
                           " padding:4px 10px; }")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(8)
        top = QHBoxLayout()
        self.cover_space = [QWidget(), QWidget()]      # zones peintes par le fond
        for w in self.cover_space:
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.wave = WaveStrip()
        top.addWidget(self.cover_space[0], 1)
        top.addWidget(self.wave)
        top.addWidget(self.cover_space[1], 1)
        v.addLayout(top, 5)

        info = QHBoxLayout()
        self.next_label = QLabel("")
        self.next_label.setStyleSheet("color:#c9ccd6; font-size:13px; background:transparent;")
        self.next_label.setAlignment(Qt.AlignCenter)
        info.addWidget(self.next_label, 1)
        self.mode_box = QComboBox()
        self.mode_box.addItems(["Animations fluides", "Animations légères", "Sans animation"])
        self.mode_box.setCurrentIndex(list(MODES).index(self.mode))
        self.mode_box.setToolTip("Fluides : 30 images/s · Légères : 15 images/s (vieux ordinateurs)\n"
                                 "Sans animation : affichage mis à jour 2 fois par seconde.\n"
                                 "L'appli passe d'elle-même en « légères » si l'ordinateur peine.")
        self.mode_box.currentIndexChanged.connect(self._on_mode)
        info.addWidget(self.mode_box)
        v.addLayout(info)

        ctl = QWidget()
        ctl.setObjectName("deckctl")
        ctl.setStyleSheet("QWidget#deckctl { background:#1c1d23; border-radius:12px; }" + _PAD_CSS)
        g = QGridLayout(ctl)
        g.setContentsMargins(12, 10, 12, 10)
        g.setHorizontalSpacing(12)
        self.jogs = [Jog(), Jog()]
        self.mixer = Mixer()
        g.addWidget(self.jogs[0], 0, 0)
        g.addWidget(self.mixer, 0, 1, 2, 1)
        g.addWidget(self.jogs[1], 0, 2)

        left = QGridLayout()
        left.setSpacing(5)
        self.style_pads = {}
        tips = {"auto": "L'appli choisit la meilleure transition (réglage normal)",
                "cale": "Tempo et clé calés : la transition longue de DJ",
                "echo": "Le morceau part dans un écho sur le temps",
                "filtre": "Filtre qui balaie + écho",
                "pullup": "Le disque est tiré en arrière, l'autre démarre",
                "fondu": "Fondu enchaîné sur le temps"}
        names = [("auto", "Auto"), ("cale", "Mix calé"), ("echo", "Écho"),
                 ("filtre", "Filtre"), ("pullup", "Pull-up"), ("fondu", "Fondu")]
        for i, (k, label) in enumerate(names):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            if k == "auto":
                b.setObjectName("auto")
            b.setToolTip(tips[k] + ("" if k == "auto" else
                                    "\nVaut pour le prochain enchaînement seulement, puis retour en Auto"))
            b.clicked.connect(lambda _=False, k=k: self._on_style(k))
            left.addWidget(b, i // 3, i % 3)
            self.style_pads[k] = b
        self.style_pads["auto"].setChecked(True)
        g.addLayout(left, 1, 0)

        right = QGridLayout()
        right.setSpacing(5)
        self.action_pads = {}
        acts = [("stop33", "Stop 33", "Arrêt : le disque ralentit comme une platine coupée (S)"),
                ("pullup", "Pull-up", "Arrêt : le disque est tiré en arrière (P)"),
                ("pause", "Pause", "Pause en écho / reprise (Espace)"),
                ("next", "Suivant", "Morceau suivant, en écho sur le temps (→)"),
                ("hl", "Temps forts", "Ne jouer que les meilleurs moments (H)"),
                ("dj", "Flow DJ", "Ordre automatique tempo / clé / énergie (D)")]
        for i, (k, label, tip) in enumerate(acts):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            if k in ("hl", "dj"):
                b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=k: self.action.emit(k))
            right.addWidget(b, i // 3, i % 3)
            self.action_pads[k] = b
        g.addLayout(right, 1, 2)
        g.setRowStretch(0, 1)
        v.addWidget(ctl, 4)

    # ------------------------------------------------------------ réglages

    def _on_mode(self, i):
        self.mode = list(MODES)[i]
        self.auto_light = False
        self._costs.clear()
        self.mode_changed.emit(self.mode)
        self._start_timer()

    def _on_style(self, k):
        for kk, b in self.style_pads.items():
            b.setChecked(kk == k)
        self.style_chosen.emit(k)

    def set_style_state(self, style: str | None):
        k = style or "auto"
        for kk, b in self.style_pads.items():
            if b.isChecked() != (kk == k):
                b.setChecked(kk == k)

    def set_toggles(self, hl: bool, dj: bool):
        self.action_pads["hl"].setChecked(hl)
        self.action_pads["dj"].setChecked(dj)

    # ------------------------------------------------------------ horloge

    def _interval(self):
        if self.mode == "fluides" and self.auto_light:
            return MODES["légères"]
        return MODES[self.mode]

    def _start_timer(self):
        if self.isVisible() and self.engine is not None:
            self.timer.start(self._interval())

    def showEvent(self, e):
        super().showEvent(e)
        self._start_timer()
        self.tick()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()                 # vue cachée : zéro travail

    def set_engine(self, eng):
        self.engine = eng
        self._sides.clear()
        if eng is None:
            self.timer.stop()
            self._show = [None, None]
            self.wave.decks = [None, None]
            self._bg = None
            self.update()
        else:
            self._start_timer()

    # ------------------------------------------------------------ données lourdes (une fois)

    def _strip(self, tr):
        if tr is None:
            return None
        s = self._strips.get(tr.id)
        if s is not None:
            if s[0] is None and isinstance(s[2], QImage):      # image prête → pixmap (fil interface)
                s = (QPixmap.fromImage(s[2]), s[1], None)
                self._strips[tr.id] = s
            return s
        if tr.id not in self._asked:
            self._asked.add(tr.id)
            voc = self.vocal_for(tr)

            def work(t=tr, voc=voc):
                try:
                    img, pxs = build_strip(t.audio, voc)
                except Exception:
                    return
                self._strips[t.id] = (None, pxs, img)
            self._pool.submit(work)
        return None

    def _color(self, tr):
        if tr is None:
            return QColor("#2a2e3a")
        c = self._colors.get(tr.id)
        if c is None:
            pm = self.cover_for(tr)
            if pm is None:
                return QColor("#3a4058")
            c = self._colors[tr.id] = dominant_color(pm)
        return c

    # ------------------------------------------------------------ une image

    def tick(self):
        eng = self.engine
        if eng is None:
            return
        t0 = time.perf_counter()
        st = eng.deck_state()
        paused = eng.paused or st is None
        if st is None:
            st = {"a": None, "a_pos": 0.0, "b": None, "b_pos": 0.0, "x": 0.0, "kind": None}
        a, b = st["a"], st["b"]
        # chaque morceau garde sa platine ; le suivant va sur l'autre
        if a is not None:
            if a.id not in self._sides:
                if b is not None and b.id in self._sides:
                    self._sides = {a.id: 1 - self._sides[b.id], b.id: self._sides[b.id]}
                else:
                    self._sides = {a.id: 0}
            if b is not None and b.id not in self._sides:
                self._sides = {a.id: self._sides[a.id], b.id: 1 - self._sides[a.id]}
        side_a = self._sides.get(a.id, 0) if a is not None else 0
        show = [None, None]
        show[side_a] = (a, st["a_pos"])
        if b is not None:
            show[1 - side_a] = (b, st["b_pos"])
        # fond (pochettes, couleurs, titres) : redessiné seulement s'il change
        key = (tuple((s[0].id if s and s[0] is not None else None) for s in show),
               side_a, self.width(), self.height(),
               tuple(self._color(s[0]).name() if s and s[0] is not None else "" for s in show),
               tuple(bool(s and s[0] is not None and self.cover_for(s[0]) is not None) for s in show))
        if key != self._bg_key:
            self._bg_key = key
            self._bg = None
            self._show = show
            for i, s in enumerate(show):
                tr = s[0] if s else None
                self.jogs[i].set_cover(tr.id if tr is not None else None,
                                       self.cover_for(tr) if tr is not None else None)
            self.update()
        # formes d'onde
        decks = [None, None]
        for i, s in enumerate(show):
            if s is None or s[0] is None:
                continue
            strip = self._strip(s[0])
            decks[i] = (strip[0] if strip else None, strip[1] if strip else PX_PER_S, s[0], s[1])
        self.wave.decks = decks
        if a is not None and a.analyzed and a.bar > 0:
            k = (st["a_pos"] - a.first_beat) / (a.bar / 4)
            self.wave.label = f"mesure {int(k // 4) + 1}.{int(k % 4) + 1}" if k >= 0 else ""
        else:
            self.wave.label = ""
        # platines
        for i, s in enumerate(show):
            j = self.jogs[i]
            j.active = s is not None and i == side_a and not paused
            if s is not None and not paused and self.mode != "aucune":
                j.angle = (s[1] * RPM / 60.0 * 360.0) % 360.0
        self._frame += 1
        slow = self._frame % 2 == 0 or self.mode != "fluides" or self.auto_light
        # table de mixage et texte : 15 images/s suffisent
        x = st["x"] if st["kind"] else 0.0
        xs = x if side_a == 0 else 1 - x
        self.mixer.x = xs
        ga, gb = math.cos(x * math.pi / 2), math.sin(x * math.pi / 2)
        if b is None or not st["kind"]:
            ga, gb = 1.0, 0.0
        self.mixer.gains = (ga, gb) if side_a == 0 else (gb, ga)
        blk = eng.last_block if slow else None
        if blk is None and not paused:
            pass
        elif paused or blk is None or len(blk) == 0:
            self.mixer.levels = (-60.0, -60.0)
        else:
            r = np.sqrt(np.mean(np.square(blk[-2048:]), axis=0) + 1e-12)
            self.mixer.levels = tuple(float(v) for v in 20 * np.log10(r * 2.2 + 1e-9))
        if slow:
            txt = self._announce(eng, st)
            if txt != self.next_label.text():
                self.next_label.setText(txt)
            self.mixer.update()
        self.wave.update()
        for j in self.jogs:
            j.update()
        # coût réel → passage automatique en « légères » si l'ordinateur peine
        cost = time.perf_counter() - t0 + self.wave.cost + sum(j.cost for j in self.jogs) + self.mixer.cost
        self._costs.append(cost)
        if len(self._costs) >= 30:
            avg = sum(self._costs) / len(self._costs)
            self._costs.clear()
            if self.mode == "fluides":
                if avg > 0.012:
                    self.auto_light = True         # l'ordinateur peine : 15 images/s
                elif avg < 0.004:
                    self.auto_light = False
        want = 500 if paused else self._interval()
        if self.timer.isActive() and self.timer.interval() != want:
            self.timer.setInterval(want)

    def _announce(self, eng, st):
        if eng.paused:
            return "❚❚ En pause"
        if eng.stopping:
            return "Arrêt en cours…"
        kinds = {"beatmatch": "mix calé", "echo": "écho sur le temps", "filtre": "filtre + écho",
                 "pullup": "pull-up", "fade": "fondu"}
        if st["kind"]:
            return f"Transition en cours · {kinds.get(st['kind'], st['kind'])}"
        nt = eng.next_transition()
        if nt is None:
            ns = eng.next_style
            return f"Prochain enchaînement : {ns_label(ns)} (en préparation…)" if ns else ""
        secs, kind, why, fallback = nt
        m, s = divmod(int(max(0, secs)), 60)
        txt = f"Prochain enchaînement dans {m}:{s:02d} · {kinds.get(kind, kind)}"
        if fallback:
            txt += f"   —   ⚠ {fallback}"
        return txt

    # ------------------------------------------------------------ fond (redessiné rarement)

    def resizeEvent(self, e):
        self._bg = None
        super().resizeEvent(e)

    def _make_bg(self):
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        ca = self._color(self._show[0][0]) if self._show[0] else QColor("#2a2e3a")
        cb = self._color(self._show[1][0]) if self._show[1] else QColor("#2a2e3a")
        g = QLinearGradient(0, 0, w, 0)
        g.setColorAt(0.0, ca.darker(115))
        g.setColorAt(0.42, ca.darker(320))
        g.setColorAt(0.58, cb.darker(320))
        g.setColorAt(1.0, cb.darker(115))
        p.fillRect(0, 0, w, h, g)
        shade = QLinearGradient(0, 0, 0, h)
        shade.setColorAt(0.0, QColor(0, 0, 0, 40))
        shade.setColorAt(0.75, QColor(8, 9, 12, 150))
        shade.setColorAt(1.0, QColor(8, 9, 12, 235))
        p.fillRect(0, 0, w, h, shade)
        for i, sp in enumerate(self.cover_space):
            s = self._show[i]
            r = sp.geometry()
            if s is None or s[0] is None or r.width() < 40:
                continue
            tr = s[0]
            side = int(min(r.width() * 0.62, r.height() * 0.62, 260))
            cx = r.center().x()
            y = r.top() + max(4, int((r.height() - side - 70) / 2))
            cov = self.cover_for(tr)
            rect = QRectF(cx - side / 2, y, side, side)
            if cov is not None:
                p.drawPixmap(rect, cov, QRectF(cov.rect()))
            else:
                p.fillRect(rect, QColor("#2a2e38"))
                p.setPen(QColor("#5c6375"))
                f = QFont(self.font())
                f.setPixelSize(int(side * 0.4))
                p.setFont(f)
                p.drawText(rect, Qt.AlignCenter, "♪")
            p.setPen(QPen(self._color(tr).lighter(130), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect)
            title = tr.row.get("title") or tr.label
            f = QFont(self.font())
            f.setPixelSize(20)
            f.setBold(True)
            p.setFont(f)
            fm = p.fontMetrics()
            tw = int(r.width() - 10)
            p.setPen(QColor("#f4f4f6"))
            p.drawText(QRect(int(cx - tw / 2), int(y + side + 8), tw, 26), Qt.AlignHCenter,
                       fm.elidedText(title, Qt.ElideRight, tw))
            f.setPixelSize(13)
            f.setBold(False)
            p.setFont(f)
            sub = tr.row.get("artist") or ""
            if tr.analyzed:
                sub += f"   ·   {tr.bpm:.0f} BPM · {tr.row.get('camelot') or ''}"
            p.setPen(QColor("#c9ccd6"))
            p.drawText(QRect(int(cx - tw / 2), int(y + side + 36), tw, 20), Qt.AlignHCenter,
                       p.fontMetrics().elidedText(sub, Qt.ElideRight, tw))
        p.end()
        return pm

    def paintEvent(self, e):
        p = QPainter(self)
        if self._bg is None or self._bg.size() != self.size() * self.devicePixelRatioF():
            if any(self._show):
                self._bg = self._make_bg()
            else:
                p.fillRect(self.rect(), QColor("#121318"))
                p.setPen(QColor("#8a90a0"))
                p.drawText(self.rect().adjusted(0, 0, 0, -200), Qt.AlignCenter,
                           "Lance une playlist : les platines s'animeront ici")
                p.end()
                return
        r = QRectF(e.rect())
        k = self.devicePixelRatioF()
        p.drawPixmap(r, self._bg, QRectF(r.x() * k, r.y() * k, r.width() * k, r.height() * k))
        p.end()


def ns_label(style):
    return {"cale": "mix calé", "echo": "écho", "filtre": "filtre", "pullup": "pull-up",
            "fondu": "fondu"}.get(style, "auto")
