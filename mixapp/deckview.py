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
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer, Signal
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

PAD_COLORS = {"cale": "#f0a43a", "echo": "#5aa9ff", "filtre": "#a978ff",
              "pullup": "#ff5d5d", "fondu": "#46c58a"}

MODES = {"fluides": 33, "légères": 66, "aucune": 500}   # intervalle de l'horloge (ms)


# ------------------------------------------------------------------ calculs (hors interface)

LOW_C = (255, 106, 61)      # basses
MID_C = (255, 210, 90)      # médiums
HIGH_C = (90, 215, 255)     # aigus


def build_strip(audio: np.ndarray, vocal=None) -> tuple[QImage, float]:
    """Forme d'onde verticale en couleurs (basses orange, médiums jaunes,
    aigus bleus) ; le temps monte vers le haut. Calculée une fois par morceau,
    hors du fil de l'interface."""
    from scipy.signal import butter, sosfilt
    dur = len(audio) / SR
    pxs = min(PX_PER_S, 30000.0 / max(dur, 1.0))
    mono = audio.mean(1) if audio.ndim == 2 else audio
    k = 4                                                   # 11 kHz suffisent pour l'image
    m4 = mono[:len(mono) // k * k].reshape(-1, k).mean(1).astype(np.float32)
    fs = SR / k
    lo = sosfilt(butter(2, 180, "low", fs=fs, output="sos"), m4)
    hi = sosfilt(butter(2, 2200, "high", fs=fs, output="sos"), m4)
    mid = m4 - lo - hi
    hop = max(1, int(fs / pxs))
    n = len(m4) // hop

    def env(x):
        e = np.sqrt(np.mean(np.square(x[:n * hop].reshape(n, hop)), 1))
        return np.convolve(e, np.ones(3) / 3, "same")
    # même référence pour les trois bandes : chacune garde son vrai poids
    e_all = env(m4)
    ref = max(1e-6, np.percentile(e_all, 99.7))
    full = (STRIP_W / 2 - 1)
    widths = [(np.clip(e_all / ref, 0, 1) ** 1.6 * full, LOW_C),
              (np.clip(env(mid) / ref * 1.6, 0, 1) ** 1.6 * full * 0.8, MID_C),
              (np.clip(env(hi) / ref * 2.6, 0, 1) ** 1.6 * full * 0.5, HIGH_C)]
    col = np.abs(np.arange(STRIP_W) - (STRIP_W - 1) / 2)
    img = np.zeros((n, STRIP_W, 4), np.uint8)
    for half, c in widths:
        mask = col[None, :] <= (half[:, None] + 0.5)
        img[mask, 0], img[mask, 1], img[mask, 2], img[mask, 3] = c[0], c[1], c[2], 255
    img = np.ascontiguousarray(img[::-1])                     # le temps monte
    q = QImage(img.data, STRIP_W, n, STRIP_W * 4, QImage.Format_RGBA8888).copy()
    return q, pxs


def blurred(pm: QPixmap | None, size: int = 40) -> np.ndarray | None:
    """Pochette réduite et floutée (calcul instantané sur 40 × 40 pixels)."""
    if pm is None or pm.isNull():
        return None
    from scipy.ndimage import gaussian_filter
    im = pm.toImage().scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) \
        .convertToFormat(QImage.Format_RGB888)
    a = np.frombuffer(im.constBits(), np.uint8, im.bytesPerLine() * size) \
        .reshape(size, im.bytesPerLine())[:, :size * 3].reshape(size, size, 3).astype(np.float32)
    return gaussian_filter(a, (3.5, 3.5, 0))


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

class _Opaque:
    """Ces éléments peignent eux-mêmes tout leur fond : on le dit à Qt (la feuille
    de style « transparente » de la vue Platines retire ce réglage) pour qu'il ne
    redessine pas le décor derrière à chaque image."""

    def event(self, ev):
        r = QWidget.event(self, ev)
        if ev.type() in (QEvent.Polish, QEvent.StyleChange, QEvent.Show):
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        return r


class WaveStrip(_Opaque, QWidget):
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


class Jog(_Opaque, QWidget):
    """Platine : image fixe (plateau + pochette) préparée une fois, seul le
    repère blanc tourne."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(110, 110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent)     # se peint seul : rien à redessiner derrière
        self.angle = 0.0
        self.pulse = 0.0
        self.active = False
        self._cover = None
        self._cover_key = None
        self._base = None
        self._base_key = None
        self._frames = None
        self.spin = True
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
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#e08a2c") if active else QColor("#3a2a18"), 2))
        p.drawEllipse(QPointF(c, c), c - 2, c - 2)
        p.end()
        # rond central (la pochette) : 72 positions tournées, préparées une fois
        r = c * 0.46
        dl = int(2 * r * dpr) + 2
        lab = QPixmap(dl, dl)
        lab.fill(Qt.transparent)
        q = QPainter(lab)
        q.setRenderHint(QPainter.Antialiasing)
        q.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addEllipse(QPointF(dl / 2, dl / 2), dl / 2 - 1, dl / 2 - 1)
        q.setClipPath(path)
        if self._cover is not None and not self._cover.isNull():
            q.drawPixmap(QRectF(0, 0, dl, dl), self._cover, QRectF(self._cover.rect()))
        else:
            q.fillRect(0, 0, dl, dl, QColor("#2a2e38"))
            q.setPen(QColor("#5c6375"))
            f = QFont(self.font())
            f.setPixelSize(int(dl * 0.4))
            q.setFont(f)
            q.drawText(QRectF(0, 0, dl, dl), Qt.AlignCenter, "♪")
        q.setClipping(False)
        q.setBrush(QColor("#0c0d11"))
        q.setPen(Qt.NoPen)
        q.drawEllipse(QPointF(dl / 2, dl / 2), dl * 0.06, dl * 0.06)
        q.setBrush(QColor("#e8e8ea"))
        q.drawEllipse(QPointF(dl / 2, dl / 2), dl * 0.025, dl * 0.025)
        q.end()
        frames = []
        for k in range(72):
            fr = QPixmap(dl, dl)
            fr.fill(Qt.transparent)
            q = QPainter(fr)
            q.setRenderHint(QPainter.SmoothPixmapTransform)
            q.translate(dl / 2, dl / 2)
            q.rotate(k * 5)
            q.translate(-dl / 2, -dl / 2)
            q.drawPixmap(0, 0, lab)
            q.end()
            fr.setDevicePixelRatio(dpr)
            frames.append(fr)
        self._frames = frames
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
        c = d / 2
        if self._frames:
            fr = self._frames[int(self.angle / 5) % 72 if self.spin else 0]
            fw = fr.width() / fr.devicePixelRatio()
            p.drawPixmap(QPointF(x0 + c - fw / 2, y0 + c - fw / 2), fr)
        p.setRenderHint(QPainter.Antialiasing)
        if self.pulse > 0.02:                                  # battement sur le temps
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(224, 138, 44, int(200 * self.pulse)), 5))
            p.drawEllipse(QPointF(x0 + c, y0 + c), c - 4, c - 4)
        a = math.radians(self.angle - 90)
        p.setPen(QPen(QColor("#f2f2f2"), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x0 + c + math.cos(a) * c * 0.66, y0 + c + math.sin(a) * c * 0.66),
                   QPointF(x0 + c + math.cos(a) * c * 0.78, y0 + c + math.sin(a) * c * 0.78))
        p.end()
        self.cost = time.perf_counter() - t0


class Mixer(_Opaque, QWidget):
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
        self._wts = [1.0, 0.0]
        self._cov_state = None
        self._cov_rects = [None, None]
        self._blur: dict[int, np.ndarray] = {}
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
        self.drawer_btn = QPushButton("À suivre")
        self.drawer_btn.setCheckable(True)
        self.drawer_btn.setChecked(True)
        self.drawer_btn.setCursor(Qt.PointingHandCursor)
        self.drawer_btn.setToolTip("Afficher / cacher le volet « À suivre » à droite")
        self.drawer_btn.setStyleSheet("QPushButton { background:#2a2c34; color:#c9ccd6; border:none;"
                                      " border-radius:6px; padding:4px 12px; }"
                                      "QPushButton:checked { background:#3a3f55; color:#fff; }")
        info.addWidget(self.drawer_btn)
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
        g.addWidget(self.mixer, 0, 1)
        g.addWidget(self.jogs[1], 0, 2)

        left = QHBoxLayout()
        left.setSpacing(6)
        self.style_pads = {}
        tips = {"auto": "L'appli choisit la meilleure transition (réglage normal)",
                "cale": "Tempo et clé calés : la transition longue de DJ",
                "echo": "Le morceau part dans un écho sur le temps",
                "filtre": "Filtre qui balaie + écho",
                "pullup": "Transition : le disque est tiré en arrière, l'autre démarre",
                "fondu": "Fondu enchaîné sur le temps"}
        names = [("auto", "Auto"), ("cale", "Mix calé"), ("echo", "Écho"),
                 ("filtre", "Filtre"), ("pullup", "Pull-up"), ("fondu", "Fondu")]
        lab = QLabel("Prochaine transition")
        lab.setStyleSheet("color:#8a90a0; font-size:12px;")
        left.addStretch(1)
        left.addWidget(lab)
        left.addSpacing(6)
        for k, label in names:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(96)
            if k == "auto":
                b.setObjectName("auto")
            else:
                c = PAD_COLORS[k]
                b.setStyleSheet(f"QPushButton {{ border-bottom: 3px solid {c}; }}"
                                f"QPushButton:checked {{ background: {c}; color: #14161b; }}")
            b.setToolTip(tips[k] + ("" if k == "auto" else
                                    "\nVaut pour le prochain enchaînement seulement, puis retour en Auto"))
            b.clicked.connect(lambda _=False, k=k: self._on_style(k))
            left.addWidget(b)
            self.style_pads[k] = b
        left.addStretch(1)
        self.style_pads["auto"].setChecked(True)
        g.addLayout(left, 1, 0, 1, 3)
        self.action_pads = {}
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
        pass

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

    def _ck(self, tr):
        """Clé de la pochette affichée (change quand la vraie remplace la provisoire)."""
        pm = self.cover_for(tr) if tr is not None else None
        return (tr.id, pm.cacheKey()) if pm is not None else (tr.id if tr is not None else None, 0)

    def _color(self, tr):
        if tr is None:
            return QColor("#2a2e3a")
        k = self._ck(tr)
        c = self._colors.get(k)
        if c is None:
            pm = self.cover_for(tr)
            if pm is None:
                return QColor("#3a4058")
            c = self._colors[k] = dominant_color(pm)
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
        x0 = st["x"] if st["kind"] else 0.0
        wts = [0.0, 0.0]
        wts[side_a] = 1.0 - x0
        wts[1 - side_a] = x0 if b is not None else 0.0
        self._wts = wts
        key = (tuple((s[0].id if s and s[0] is not None else None) for s in show),
               side_a, self.width(), self.height(), round(wts[0] * 6),
               tuple(self._color(s[0]).name() if s and s[0] is not None else "" for s in show),
               tuple(self._ck(s[0]) if s and s[0] is not None else None for s in show))
        if key != self._bg_key:
            self._bg_key = key
            self._bg = None
            self._show = show
            for i, s in enumerate(show):
                tr = s[0] if s else None
                self.jogs[i].set_cover(self._ck(tr) if tr is not None else None,
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
        # battement sur le temps (morceau qui mène)
        lead = show[side_a][0] if show[side_a] else None
        pulse = 0.0
        if (lead is not None and lead.analyzed and not paused and self.mode == "fluides"
                and not self.auto_light):
            ph = ((show[side_a][1] - lead.first_beat) / (60.0 / lead.bpm)) % 1.0
            pulse = max(0.0, 1.0 - ph * 3.5)
        # platines
        for i, s in enumerate(show):
            j = self.jogs[i]
            j.active = s is not None and i == side_a and not paused
            j.pulse = pulse if i == side_a else 0.0
            j.spin = self.mode != "aucune"
            if s is not None and not paused and self.mode != "aucune":
                j.angle = (s[1] * RPM / 60.0 * 360.0) % 360.0
        # pochettes : taille / éclat selon qui mène, battement → on ne repeint que leur zone
        cov = tuple((round(wts[i], 2), round(pulse if i == side_a else 0.0, 1)) for i in range(2))
        if cov != self._cov_state and self._cov_rects:
            old = self._cov_state
            self._cov_state = cov
            for i, r in enumerate(self._cov_rects):
                if r is None:
                    continue
                if old is None or old[i][0] != cov[i][0]:
                    self.update(r.adjusted(-10, -10, 10, 10).toAlignedRect())   # taille / éclat
                elif old[i][1] != cov[i][1]:
                    # battement : on ne repeint que la fine bande du halo autour de la pochette
                    sc = 0.84 + 0.16 * cov[i][0]
                    cr = QRectF(r.center().x() - r.width() * sc / 2, r.center().y() - r.height() * sc / 2,
                                r.width() * sc, r.height() * sc)
                    outer = cr.adjusted(-10, -10, 10, 10).toAlignedRect()
                    m = 14
                    self.update(QRect(outer.left(), outer.top(), outer.width(), m))
                    self.update(QRect(outer.left(), outer.bottom() - m, outer.width(), m + 1))
                    self.update(QRect(outer.left(), outer.top(), m, outer.height()))
                    self.update(QRect(outer.right() - m, outer.top(), m + 1, outer.height()))
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

    def _blur_of(self, tr):
        if tr is None:
            return None
        k = self._ck(tr)
        a = self._blur.get(k)
        if a is None:
            pm = self.cover_for(tr)
            if pm is None:
                return None
            a = self._blur[k] = blurred(pm)
        return a

    def _layout_covers(self):
        rects = [None, None]
        for i, sp in enumerate(self.cover_space):
            r = sp.geometry()
            if r.width() < 40:
                continue
            side = int(min(r.width() * 0.62, (r.height() - 70) * 0.92, 300))
            cx = r.center().x()
            y = r.top() + max(4, int((r.height() - side - 70) / 2))
            rects[i] = QRectF(cx - side / 2, y, side, side)
        self._cov_rects = rects
        return rects

    def _scaled_cover(self, tr, pm, size):
        """Pochette déjà mise à la bonne taille (calculée une fois par taille)."""
        dpr = self.devicePixelRatioF()
        w = int(round(size.width() * dpr))
        k = (self._ck(tr), w)
        c = self.__dict__.setdefault("_scaled", {})
        out = c.get(k)
        if out is None:
            if len(c) > 40:
                c.clear()
            out = pm.scaled(w, w, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            out.setDevicePixelRatio(dpr)
            c[k] = out
        return out

    def _make_bg(self):
        """Décor : pochettes floutées en fond (côté qui mène plus lumineux) et titres.
        Refait seulement quand un morceau change, ou 6 fois pendant une transition."""
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor("#0d0e12"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        # 1. fond flou : un calque par platine, fondus l'un dans l'autre au milieu
        for i in range(2):
            s = self._show[i]
            tr = s[0] if s else None
            layer = QPixmap(int(w * dpr), int(h * dpr))
            layer.setDevicePixelRatio(dpr)
            layer.fill(Qt.transparent)
            q = QPainter(layer)
            q.setRenderHint(QPainter.SmoothPixmapTransform)
            arr = self._blur_of(tr)
            if arr is not None:
                a8 = np.ascontiguousarray(np.clip(arr, 0, 255).astype(np.uint8))
                img = QImage(a8.data, a8.shape[1], a8.shape[0], a8.shape[1] * 3, QImage.Format_RGB888)
                q.drawImage(QRectF(-w * 0.1, -h * 0.25, w * 1.2, h * 1.5), img)
            else:
                q.fillRect(0, 0, w, h, self._color(tr).darker(200) if tr is not None else QColor("#1a1c22"))
            bright = 0.42 + 0.38 * self._wts[i]                     # le côté qui mène s'éclaire
            q.fillRect(0, 0, w, h, QColor(8, 9, 12, int(255 * (1 - bright))))
            q.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            g = QLinearGradient(0, 0, w, 0)
            if i == 0:
                g.setColorAt(0.0, QColor(0, 0, 0, 255)); g.setColorAt(0.38, QColor(0, 0, 0, 255))
                g.setColorAt(0.62, QColor(0, 0, 0, 0)); g.setColorAt(1.0, QColor(0, 0, 0, 0))
            else:
                g.setColorAt(0.0, QColor(0, 0, 0, 0)); g.setColorAt(0.38, QColor(0, 0, 0, 0))
                g.setColorAt(0.62, QColor(0, 0, 0, 255)); g.setColorAt(1.0, QColor(0, 0, 0, 255))
            q.fillRect(0, 0, w, h, g)
            q.end()
            p.drawPixmap(0, 0, layer)
        shade = QLinearGradient(0, 0, 0, h)
        shade.setColorAt(0.0, QColor(0, 0, 0, 30))
        shade.setColorAt(0.6, QColor(8, 9, 12, 120))
        shade.setColorAt(1.0, QColor(8, 9, 12, 235))
        p.fillRect(0, 0, w, h, shade)
        # 2. titres en capitales, teintés de la couleur de la pochette
        for i, rect in enumerate(self._layout_covers()):
            s = self._show[i]
            if s is None or s[0] is None or rect is None:
                continue
            tr = s[0]
            r = self.cover_space[i].geometry()
            cx, tw = rect.center().x(), int(r.width() - 10)
            col = self._color(tr)
            hh, ss, _vv, _ = col.getHsv()
            tint = QColor.fromHsv(max(hh, 0), min(ss, 170), 255)
            title = (tr.row.get("title") or tr.label).upper()
            f = QFont(self.font())
            f.setPixelSize(26)
            f.setBold(True)
            f.setLetterSpacing(QFont.PercentageSpacing, 104)
            p.setFont(f)
            y = rect.bottom() + 10
            p.setPen(QColor(0, 0, 0, 160))
            p.drawText(QRect(int(cx - tw / 2) + 2, int(y) + 2, tw, 34), Qt.AlignHCenter,
                       p.fontMetrics().elidedText(title, Qt.ElideRight, tw))
            p.setPen(tint)
            p.drawText(QRect(int(cx - tw / 2), int(y), tw, 34), Qt.AlignHCenter,
                       p.fontMetrics().elidedText(title, Qt.ElideRight, tw))
            f.setPixelSize(13)
            f.setBold(False)
            f.setLetterSpacing(QFont.PercentageSpacing, 100)
            p.setFont(f)
            sub = tr.row.get("artist") or ""
            if tr.analyzed:
                sub += f"   ·   {tr.bpm:.0f} BPM · {tr.row.get('camelot') or ''}"
            p.setPen(QColor("#e4e6ee"))
            p.drawText(QRect(int(cx - tw / 2), int(y + 36), tw, 20), Qt.AlignHCenter,
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
        # pochettes : celle qui mène est grande et vive, l'autre plus petite et pâle
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        st = self._cov_state or ((1.0, 0.0), (0.0, 0.0))
        for i, rect in enumerate(self._cov_rects):
            s = self._show[i]
            if rect is None or s is None or s[0] is None:
                continue
            wgt, pulse = st[i]
            sc = 0.84 + 0.16 * wgt
            cr = QRectF(rect.center().x() - rect.width() * sc / 2, rect.center().y() - rect.height() * sc / 2,
                        rect.width() * sc, rect.height() * sc)
            if not cr.intersects(r.adjusted(-12, -12, 12, 12)):
                continue
            col = self._color(s[0])
            if pulse > 0.02:
                glow = QColor(col.lighter(150))
                glow.setAlpha(int(170 * pulse))
                p.setPen(QPen(glow, 8))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(cr.adjusted(-4, -4, 4, 4), 8, 8)
            p.setOpacity(0.5 + 0.5 * wgt)
            cov = self.cover_for(s[0])
            core = cr.adjusted(16, 16, -16, -16).toAlignedRect()
            if not e.region().intersects(core):      # seule la bande du halo est à repeindre
                cov = "skip"
            path = QPainterPath()
            path.addRoundedRect(cr, 6, 6)
            p.setClipPath(path)
            if cov == "skip":
                pass
            elif cov is not None:
                pm = self._scaled_cover(s[0], cov, cr.size())
                p.drawPixmap(cr.topLeft(), pm)
            else:
                p.fillRect(cr, QColor("#2a2e38"))
                p.setPen(QColor("#5c6375"))
                f = QFont(self.font())
                f.setPixelSize(int(cr.width() * 0.4))
                p.setFont(f)
                p.drawText(cr, Qt.AlignCenter, "♪")
            p.setClipping(False)
            p.setOpacity(1.0)
            p.setPen(QPen(col.lighter(140), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(cr, 6, 6)
        p.end()


def ns_label(style):
    return {"cale": "mix calé", "echo": "écho", "filtre": "filtre", "pullup": "pull-up",
            "fondu": "fondu"}.get(style, "auto")
