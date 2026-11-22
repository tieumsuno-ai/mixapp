"""Vue « Platines » : deux pochettes, les formes d'onde qui défilent, le
crossfader et les pads de transition. Sobre : rien ne clignote ni ne tourne ;
seules les formes d'onde défilent et le crossfader bouge pendant les mix.

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
import re
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
STRIP_W = 50              # largeur d'une forme d'onde (px)
PX_PER_S = 60.0           # vitesse de défilement
RPM = 100.0 / 3.0         # 33 tours ⅓

PEACH = (255, 212, 168)
VOCAL = (110, 168, 255)

PAD_COLORS = {"cale": "#f0a43a", "echo": "#5aa9ff", "filtre": "#a978ff",
              "pullup": "#ff5d5d", "fondu": "#46c58a"}

MODES = {"fluides": 33, "légères": 66, "aucune": 500}   # intervalle de l'horloge (ms)


# ------------------------------------------------------------------ calculs (hors interface)

LOW_C = (214, 209, 200)     # basses : ivoire
MID_C = (128, 138, 160)     # médiums : gris bleuté
HIGH_C = (124, 156, 255)    # aigus : bleu (la couleur d'accent)


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

    g = np.exp(-0.5 * (np.arange(-4, 5) / 1.6) ** 2)
    g /= g.sum()

    def env(x):
        e = np.sqrt(np.mean(np.square(x[:n * hop].reshape(n, hop)), 1))
        return np.convolve(e, g, "same")                       # contour lissé
    e_all = env(m4)
    ref = max(1e-6, np.percentile(e_all, 99.5))
    full = (STRIP_W / 2 - 1.5)
    bands = [(np.clip(e_all / ref, 0, 1) ** 1.8 * full, LOW_C),
             (np.clip(env(mid) / ref * 1.6, 0, 1) ** 1.8 * full * 0.62, MID_C),
             (np.clip(env(hi) / ref * 2.6, 0, 1) ** 1.6 * full * 0.34, HIGH_C)]
    col = np.abs(np.arange(STRIP_W) - (STRIP_W - 1) / 2)
    rgb = np.zeros((n, STRIP_W, 3), np.float32)
    alpha = np.zeros((n, STRIP_W), np.float32)
    for half, c in bands:                                     # bords adoucis (anticrénelage)
        a = np.clip(half[:, None] + 0.5 - col[None, :], 0, 1)
        rgb = np.asarray(c, np.float32)[None, None, :] * a[..., None] + rgb * (1 - a[..., None])
        alpha = a + alpha * (1 - a)
    img = np.zeros((n, STRIP_W, 4), np.uint8)
    img[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    img[..., 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
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

ACCENT = "#7c9cff"
BG = "#0f1014"
PANEL = "#17181d"


class _Opaque:
    """Ces éléments peignent eux-mêmes tout leur fond : on le dit à Qt (la feuille
    de style « transparente » de la vue retire ce réglage) pour qu'il ne
    redessine pas le décor derrière à chaque image."""

    def event(self, ev):
        r = QWidget.event(self, ev)
        if ev.type() in (QEvent.Polish, QEvent.StyleChange, QEvent.Show):
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        return r


class WaveStrip(_Opaque, QWidget):
    """Les deux formes d'onde côte à côte, calées sur le même trait de lecture ;
    la partie déjà jouée est assombrie."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(2 * STRIP_W + 30)
        self.decks = [None, None]     # (pixmap, pxs, track, pos) par côté
        self.label = ""
        self.cost = 0.0

    def paintEvent(self, _e):
        t0 = time.perf_counter()
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
        p.fillRect(0, 0, w, h, QColor(BG))
        p.fillPath(path, QColor("#0a0b0e"))
        p.setRenderHint(QPainter.Antialiasing, False)
        y0 = int(h * 0.62)                                    # trait de lecture
        for side, d in enumerate(self.decks):
            if d is None:
                continue
            pm, pxs, tr, pos = d
            x = 10 + side * (STRIP_W + 10)
            if pm is not None:
                row = pm.height() - 1 - (pos * pxs + y0)
                src = QRectF(0, row, STRIP_W, h)
                tgt = QRectF(x, 0, STRIP_W, h)
                if row < 0:
                    tgt.setTop(-row)
                    src.setTop(0)
                p.drawPixmap(tgt, pm, src)
            if tr is not None and getattr(tr, "analyzed", False) and tr.bar > 0:
                p.setPen(QColor(255, 255, 255, 26))
                first = tr.first_beat
                k0 = math.floor((pos - (h - y0) / pxs - first) / tr.bar)
                k1 = math.ceil((pos + y0 / pxs - first) / tr.bar)
                for k in range(k0, k1 + 1):
                    yy = y0 - (first + k * tr.bar - pos) * pxs
                    p.drawLine(QPointF(x, yy), QPointF(x + STRIP_W, yy))
        p.fillRect(QRectF(0, y0, w, h - y0), QColor(10, 11, 14, 150))   # déjà joué
        p.setPen(QPen(QColor("#f4f4f6"), 1))
        p.drawLine(6, y0, w - 6, y0)
        if self.label:
            f = p.font()
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(QColor("#8a90a0"))
            p.drawText(QRect(0, y0 + 6, w, 16), Qt.AlignHCenter, self.label)
        p.end()
        self.cost = time.perf_counter() - t0


class XFader(_Opaque, QWidget):
    """Crossfader et niveaux, sobres : un trait, un curseur, deux fines jauges."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(230, 52)
        self.x = 0.0
        self.levels = (-60.0, -60.0)
        self.cost = 0.0

    def paintEvent(self, _e):
        t0 = time.perf_counter()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(PANEL))
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        f = p.font()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#6f7686"))
        p.drawText(QRect(0, 0, 18, h), Qt.AlignCenter, "A")
        p.drawText(QRect(w - 18, 0, 18, h), Qt.AlignCenter, "B")
        x0, x1, y = 26, w - 26, h * 0.62
        p.setPen(QPen(QColor("#33363f"), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x0, y), QPointF(x1, y))
        kx = x0 + self.x * (x1 - x0)
        p.setPen(QPen(QColor(ACCENT), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x0, y), QPointF(kx, y)) if self.x > 0 else None
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f4f4f6"))
        p.drawEllipse(QPointF(kx, y), 7, 7)
        for i, lv in enumerate(self.levels):                   # niveaux gauche / droite
            v = float(np.clip((lv + 40) / 40, 0, 1))
            yy = h * 0.18 + i * 6
            p.fillRect(QRectF(x0, yy, x1 - x0, 2.5), QColor("#26282f"))
            p.fillRect(QRectF(x0, yy, (x1 - x0) * v, 2.5), QColor("#9aa3b5"))
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
        img.fill(QColor(PANEL))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        c = d / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0c0d11"))
        p.drawEllipse(QPointF(c, c), c - 1, c - 1)
        p.setPen(QPen(QColor("#22242b"), 1))
        for i in range(90):                                 # stries du plateau
            a = i * math.pi / 45
            p.drawLine(QPointF(c + math.cos(a) * c * 0.80, c + math.sin(a) * c * 0.80),
                       QPointF(c + math.cos(a) * c * 0.90, c + math.sin(a) * c * 0.90))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#1d1f26"), c * 0.10))
        p.drawEllipse(QPointF(c, c), c * 0.62, c * 0.62)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(ACCENT) if active else QColor("#2a2c33"), 1.5))
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
            p.fillRect(self.rect(), QColor(PANEL))
        p.drawPixmap(QPointF(x0, y0), self._base)
        c = d / 2
        if self._frames:
            fr = self._frames[int(self.angle / 5) % 72 if self.spin else 0]
            fw = fr.width() / fr.devicePixelRatio()
            p.drawPixmap(QPointF(x0 + c - fw / 2, y0 + c - fw / 2), fr)
        p.setRenderHint(QPainter.Antialiasing)
        a = math.radians(self.angle - 90)
        p.setPen(QPen(QColor(255, 255, 255, 150), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x0 + c + math.cos(a) * c * 0.66, y0 + c + math.sin(a) * c * 0.66),
                   QPointF(x0 + c + math.cos(a) * c * 0.78, y0 + c + math.sin(a) * c * 0.78))
        p.end()
        self.cost = time.perf_counter() - t0



# ------------------------------------------------------------------ la vue

_PAD_CSS = f"""
QWidget#deckview QPushButton {{ background: rgba(255,255,255,0.07); color:#c9ccd6; border:none;
              border-radius:8px; padding:0 14px; font-size:13px; min-height:34px; }}
QWidget#deckview QPushButton:hover {{ background: rgba(255,255,255,0.13); }}
QWidget#deckview QPushButton:checked {{ background:{ACCENT}; color:#0f1014; font-weight:600; }}
QWidget#deckview QComboBox {{ background: rgba(255,255,255,0.07); color:#9aa3b5; border:none;
              border-radius:8px; padding:4px 10px; }}
QWidget#deckview QLabel {{ color:#8a90a0; background: transparent; }}
"""


def _split_title(title: str):
    """« Danza Kuduro (Edit Mix) » → (« Danza Kuduro », « Edit Mix »)."""
    t = re.sub(r"\s*\[[^\]]*\]", "", title or "").strip()
    vers = re.findall(r"\(([^)]*)\)", t)
    base = re.sub(r"\s*\([^)]*\)", "", t).strip() or t
    v = vers[-1] if vers else ""
    v = re.sub(r"\s*by fuvi clan", "", v, flags=re.I).strip()
    return base, v


class DeckView(QWidget):
    style_chosen = Signal(str)        # pad de transition (auto, cale, echo…)
    action = Signal(str)
    mode_changed = Signal(str)

    def __init__(self, cover_for, vocal_for, mode="fluides", parent=None):
        super().__init__(parent)
        self.cover_for = cover_for        # fonction(track) → QPixmap haute définition | None
        self.vocal_for = vocal_for
        self.engine = None
        self.mode = mode if mode in MODES else "fluides"
        self.auto_light = False
        self._strips: dict[int, tuple] = {}
        self._pool = ThreadPoolExecutor(1)
        self._asked: set[int] = set()
        self._sides: dict[int, int] = {}
        self._bg = None
        self._bg_key = None
        self._show = [None, None]
        self._wts = [1.0, 0.0]
        self._costs: list[float] = []
        self._frame = 0
        self._blur: dict = {}
        self._prog = None
        self.setObjectName("deckview")
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setStyleSheet("QWidget#deckview, QWidget#deckview QWidget { background: transparent; }" + _PAD_CSS)
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    # ------------------------------------------------------------ construction

    def _build(self):
        self.wave = WaveStrip(self)
        self.xf = XFader(self)
        self.jogs = [Jog(self), Jog(self)]
        self.pad_label = QLabel("Prochaine transition", self)
        self.style_pads = {}
        tips = {"auto": "L'appli choisit la meilleure transition (réglage normal)",
                "cale": "Tempo et clé calés : la transition longue de DJ",
                "echo": "Le morceau part dans un écho sur le temps",
                "filtre": "Filtre qui balaie + écho",
                "pullup": "Le disque est tiré en arrière, l'autre démarre",
                "fondu": "Fondu enchaîné sur le temps"}
        for k, label in [("auto", "Auto"), ("cale", "Mix calé"), ("echo", "Écho"),
                         ("filtre", "Filtre"), ("pullup", "Pull-up"), ("fondu", "Fondu")]:
            b = QPushButton(label, self)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tips[k] + ("" if k == "auto" else
                                    "\nVaut pour le prochain enchaînement seulement, puis retour en Auto"))
            b.clicked.connect(lambda _=False, k=k: self._on_style(k))
            self.style_pads[k] = b
        self.style_pads["auto"].setChecked(True)
        self.action_pads = {}
        self.next_label = QLabel("", self)
        self.next_label.setStyleSheet("color:#c9ccd6; font-size:13px;")
        self.drawer_btn = QPushButton("À suivre", self)
        self.drawer_btn.setCheckable(True)
        self.drawer_btn.setChecked(True)
        self.drawer_btn.setCursor(Qt.PointingHandCursor)
        self.drawer_btn.setToolTip("Afficher / cacher le volet « À suivre » à droite")
        self.mode_box = QComboBox(self)
        self.mode_box.addItems(["Défilement fluide", "Défilement léger", "Sans animation"])
        self.mode_box.setCurrentIndex(list(MODES).index(self.mode))
        self.mode_box.setToolTip("Fluide : 30 images/s · Léger : 15 images/s (vieux ordinateurs)\n"
                                 "Sans animation : affichage mis à jour 2 fois par seconde.")
        self.mode_box.currentIndexChanged.connect(self._on_mode)

    # ------------------------------------------------------------ mise en page (s'adapte à la taille)

    def _geom(self):
        W, H = self.width(), self.height()
        m = int(np.clip(W * 0.018, 12, 30))
        ch = int(np.clip(H * 0.27, 150, 230))                # console du bas (platines + pads)
        top, bot = m + 46, H - ch - m - 8
        ww = self.wave.width()
        cx = W // 2
        cols = [QRect(m, top, cx - ww // 2 - 2 * m, bot - top),
                QRect(cx + ww // 2 + m, top, W - m - (cx + ww // 2 + m), bot - top)]
        avail_h = bot - top
        # la pochette prend ce qui reste une fois réservée la place des textes
        # (étiquette, temps, titre sur 2 lignes, artiste, BPM / clé) : rien ne déborde
        cs = int(min(cols[0].width() * 0.74, 560))
        for _ in range(3):
            fs = float(np.clip(cs * 0.072, 17, 30))
            cs = int(min(cs, avail_h - self._text_h(fs)))
        horiz = cs < 150                       # fenêtre basse : pochette à gauche, infos à droite
        if horiz:
            cs = int(max(60, min(avail_h - 16, cols[0].width() * 0.42, 260)))
        return {"m": m, "ch": ch, "top": top, "bot": bot, "cx": cx, "cols": cols, "cs": cs, "horiz": horiz,
                "console": QRect(m, H - ch - m, W - 2 * m, ch)}

    def resizeEvent(self, e):
        g = self._geom()
        r0 = self._cover_rect(0, g)
        wy = max(g["top"], r0.top() - 22)
        wh = min(g["bot"] - wy, r0.height() + 110)                 # formes d'onde : hauteur des pochettes,
        self.wave.setGeometry(g["cx"] - self.wave.width() // 2, wy, self.wave.width(), wh)  # jamais sur la console
        c = g["console"]
        d = c.height() - 24                                         # platines aux deux bouts
        self.jogs[0].setGeometry(c.left() + 16, c.top() + 12, d, d)
        self.jogs[1].setGeometry(c.right() - 16 - d, c.top() + 12, d, d)
        left, right = c.left() + 16 + d + 28, c.right() - 16 - d - 28
        mid = (left + right) // 2
        # rangée 1 : pads de transition, centrés
        for b in self.style_pads.values():
            b.adjustSize()
        widths = [b.sizeHint().width() for b in self.style_pads.values()]
        total = sum(widths) + 6 * (len(widths) - 1)
        if total > right - left:                                    # fenêtre étroite : pads resserrés
            k = (right - left - 6 * (len(widths) - 1)) / max(1, sum(widths))
            widths = [max(54, int(wd * k)) for wd in widths]
            total = sum(widths) + 6 * (len(widths) - 1)
        block = 18 + 8 + 34 + 10 + self.xf.height() + 2 + 20     # titre, pads, crossfader, annonce
        y1 = c.top() + max(6, (c.height() - block) // 2) + 22
        self.pad_label.adjustSize()
        self.pad_label.move(mid - self.pad_label.width() // 2, y1 - self.pad_label.height() - 8)
        x = mid - total // 2
        for b, wd in zip(self.style_pads.values(), widths):
            b.setGeometry(x, y1, wd, 34)
            x += wd + 6
        # rangée 2 : crossfader, annonce dessous
        y2 = y1 + 34 + 10
        self.xf.move(mid - self.xf.width() // 2, y2)
        self.xf.setVisible(right - left > self.xf.width())
        self.next_label.setAlignment(Qt.AlignCenter)
        self.next_label.setGeometry(left, y2 + self.xf.height() + 2, right - left, 20)
        # en haut à droite : volet « À suivre » et défilement
        self.mode_box.adjustSize()
        self.drawer_btn.adjustSize()
        bx = self.width() - g["m"] - self.mode_box.width()
        self.mode_box.move(bx, g["m"] - 4)
        self.drawer_btn.setGeometry(bx - 8 - self.drawer_btn.sizeHint().width(), g["m"] - 4,
                                    self.drawer_btn.sizeHint().width(), 30)
        self._bg = None
        super().resizeEvent(e)

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
        self.timer.stop()

    def shutdown(self):
        """Fermeture de l'appli : on attend la fin des calculs en cours (forme
        d'onde) avant que Qt ne s'arrête, sinon l'appli peut planter en quittant."""
        self.timer.stop()
        try:
            self._pool.shutdown(wait=True, cancel_futures=True)
        except TypeError:                                   # Python < 3.9
            self._pool.shutdown(wait=True)

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
            if s[0] is None and isinstance(s[2], QImage):
                s = (QPixmap.fromImage(s[2]), s[1], None)
                self._strips[tr.id] = s
            return s
        if tr.id not in self._asked:
            self._asked.add(tr.id)

            def work(t=tr):
                try:
                    img, pxs = build_strip(t.audio)
                except Exception:
                    return
                self._strips[t.id] = (None, pxs, img)
            self._pool.submit(work)
        return None

    def _ck(self, tr):
        pm = self.cover_for(tr) if tr is not None else None
        return (tr.id, pm.cacheKey()) if pm is not None else (tr.id if tr is not None else None, 0)

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
        x0 = st["x"] if st["kind"] else 0.0
        wts = [0.0, 0.0]
        wts[side_a] = 1.0 - x0
        wts[1 - side_a] = x0 if b is not None else 0.0
        self._wts = wts
        # décor (pochettes, titres) : refait seulement quand il change (et 4 fois pendant un mix)
        key = (tuple(self._ck(s[0]) if s and s[0] is not None else None for s in show),
               side_a, self.width(), self.height(), round(wts[0] * 4))
        if key != self._bg_key:
            self._bg_key = key
            self._bg = None
            self._show = show
            self.update()
        for i, s in enumerate(show):                          # platines : seul le disque tourne
            j = self.jogs[i]
            tr = s[0] if s else None
            j.set_cover(self._ck(tr) if tr is not None else None,
                        self.cover_for(tr) if tr is not None else None)
            j.active = s is not None and i == side_a and not paused
            if s is not None and not paused and self.mode != "aucune":
                j.angle = (s[1] * RPM / 60.0 * 360.0) % 360.0
            j.update()
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
        self._frame += 1
        slow = self._frame % 2 == 0 or self.mode != "fluides" or self.auto_light
        if slow:
            xs = x0 if side_a == 0 else 1 - x0
            blk = eng.last_block
            if paused or blk is None or len(blk) == 0:
                lv = (-60.0, -60.0)
            else:
                r = np.sqrt(np.mean(np.square(blk[-2048:]), axis=0) + 1e-12)
                lv = tuple(float(v) for v in 20 * np.log10(r * 2.2 + 1e-9))
            if (round(xs, 3), tuple(round(v) for v in lv)) != (round(self.xf.x, 3), tuple(round(v) for v in self.xf.levels)):
                self.xf.x, self.xf.levels = xs, lv
                self.xf.update()
            txt = self._announce(eng, st)
            if txt != self.next_label.text():
                self.next_label.setText(txt)
        # temps restant sous la pochette qui mène : 2 fois par seconde
        if self._frame % (15 if self.mode == "fluides" else 8) == 0 and a is not None:
            prog = (side_a, int(st["a_pos"]))
            if prog != self._prog:
                self._prog = prog
                r = self._prog_rect(side_a)
                if r is not None:
                    self.update(r)
        self.wave.update()
        cost = time.perf_counter() - t0 + self.wave.cost + self.xf.cost + sum(j.cost for j in self.jogs)
        self._costs.append(cost)
        if len(self._costs) >= 30:
            avg = sum(self._costs) / len(self._costs)
            self._costs.clear()
            if self.mode == "fluides":
                if avg > 0.012:
                    self.auto_light = True
                elif avg < 0.004:
                    self.auto_light = False
        want = 500 if paused else self._interval()
        if self.timer.isActive() and self.timer.interval() != want:
            self.timer.setInterval(want)

    def _announce(self, eng, st):
        if eng.paused:
            return "En pause"
        if eng.stopping:
            return "Arrêt en cours…"
        kinds = {"beatmatch": "mix calé", "echo": "écho sur le temps", "filtre": "filtre + écho",
                 "pullup": "pull-up", "fade": "fondu"}
        if st["kind"]:
            return f"Transition en cours · {kinds.get(st['kind'], st['kind'])}"
        nt = eng.next_transition()
        if nt is None:
            ns = eng.next_style
            return f"Prochaine transition : {ns_label(ns)} (en préparation…)" if ns else ""
        secs, kind, why, fallback = nt
        m, s = divmod(int(max(0, secs)), 60)
        txt = f"Transition dans {m}:{s:02d} · {kinds.get(kind, kind)}"
        if fallback:
            txt += f"  ·  ⚠ {fallback}"
        return txt

    # ------------------------------------------------------------ décor (redessiné rarement)

    @staticmethod
    def _text_h(fs):
        """Hauteur des textes autour d'une pochette : étiquette au-dessus (22),
        temps (38), titre sur 2 lignes au plus, artiste (34), BPM / clé (26)."""
        return 22 + 38 + int(fs * 2.6) + 34 + 26 + 6

    def _lay(self, i, g=None):
        """Emplacements d'une platine : pochette, étiquette, titre, artiste, BPM / clé, temps."""
        g = g or self._geom()
        col, cs = g["cols"][i], g["cs"]
        if not g["horiz"]:                                    # pochette au-dessus, textes dessous
            fs = int(np.clip(cs * 0.072, 17, 30))
            block = cs + self._text_h(fs)
            y = col.top() + max(0, (col.height() - block) // 2) + 22
            r = QRect(col.center().x() - cs // 2, y, cs, cs)
            tw = int(min(cs * 1.25, col.width()))
            tx = r.center().x() - tw // 2
            return {"r": r, "fs": fs, "align": Qt.AlignHCenter, "tx": tx, "tw": tw,
                    "label": QRect(r.left(), r.top() - 22, cs, 16),
                    "title_y": r.bottom() + 38, "title_lines": 2, "tags": True,
                    "prog": QRect(r.left() - 2, r.bottom() + 8, r.width() + 4, 22)}
        # fenêtre basse : pochette à gauche, textes à droite
        fs = int(np.clip(cs * 0.11, 15, 22))
        tw = int(max(80, min(col.width() - cs - 18, 340)))
        x = col.left() + max(0, (col.width() - (cs + 18 + tw)) // 2)
        r = QRect(x, col.top() + max(0, (col.height() - cs) // 2), cs, cs)
        tx = r.right() + 18
        return {"r": r, "fs": fs, "align": Qt.AlignLeft, "tx": tx, "tw": tw,
                "label": QRect(tx, r.top(), tw, 16),
                "title_y": r.top() + 22, "title_lines": 1, "tags": cs >= 132,  # très petit : BPM dans le lecteur
                "prog": QRect(tx - 2, r.bottom() - 24, tw + 4, 22)}

    def _cover_rect(self, i, g=None):
        return self._lay(i, g)["r"]

    def _prog_rect(self, i):
        return self._lay(i)["prog"]

    def _blur_of(self, tr):
        k = self._ck(tr)
        a = self._blur.get(k)
        if a is None:
            pm = self.cover_for(tr)
            if pm is None:
                return None
            a = self._blur[k] = blurred(pm, 32)
        return a

    def _make_bg(self):
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(BG))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.TextAntialiasing)
        g = self._geom()
        # 1. une seule teinte d'ambiance, très discrète : la pochette qui mène, floutée
        lead = max(range(2), key=lambda i: self._wts[i])
        tr = self._show[lead][0] if self._show[lead] else None
        arr = self._blur_of(tr) if tr is not None else None
        if arr is not None:
            a8 = np.ascontiguousarray(np.clip(arr, 0, 255).astype(np.uint8))
            img = QImage(a8.data, a8.shape[1], a8.shape[0], a8.shape[1] * 3, QImage.Format_RGB888)
            p.setOpacity(0.22)
            p.drawImage(QRectF(-w * 0.1, -h * 0.3, w * 1.2, h * 1.6), img)
            p.setOpacity(1.0)
        vg = QRadialGradient(QPointF(w / 2, h * 0.4), max(w, h) * 0.75)
        vg.setColorAt(0.0, QColor(15, 16, 20, 0))
        vg.setColorAt(1.0, QColor(15, 16, 20, 235))
        p.fillRect(0, 0, w, h, vg)
        # 2. les deux platines : étiquette, pochette (ombre douce), titre, version, artiste, tempo
        for i in range(2):
            s = self._show[i]
            if s is None or s[0] is None:
                continue
            tr = s[0]
            L = self._lay(i, g)
            r = L["r"]
            wgt = self._wts[i]
            active = wgt >= 0.5
            cs = r.width()
            # étiquette
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setBold(True)
            f.setLetterSpacing(QFont.PercentageSpacing, 118)
            p.setFont(f)
            p.setPen(QColor(ACCENT) if active else QColor("#6f7686"))
            p.drawText(L["label"], Qt.AlignLeft,
                       "EN COURS" if i == max(range(2), key=lambda k: self._wts[k]) and self._wts[i] > 0.99
                       else ("EN TRANSITION" if 0.01 < wgt < 0.99 else "À SUIVRE"))
            # ombre douce
            for k, al in ((14, 18), (8, 30), (3, 50)):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, al))
                p.drawRoundedRect(QRectF(r).adjusted(-k / 2, k / 3, k / 2, k), 10 + k / 2, 10 + k / 2)
            cov = self.cover_for(tr)
            path = QPainterPath()
            path.addRoundedRect(QRectF(r), 10, 10)
            p.save()
            p.setClipPath(path)
            p.setOpacity(0.45 + 0.55 * wgt)
            if cov is not None:
                src = QRectF(cov.rect())
                side = min(src.width(), src.height())                 # recadrage carré centré
                src = QRectF(src.center().x() - side / 2, src.center().y() - side / 2, side, side)
                p.drawPixmap(QRectF(r), cov, src)
            else:
                p.fillRect(r, QColor("#22242b"))
            p.restore()
            p.setPen(QPen(QColor(255, 255, 255, 22), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
            # textes
            base, vers = _split_title(tr.row.get("title") or tr.label)
            y = L["title_y"]
            fs = L["fs"]
            al = L["align"]
            f = QFont(self.font())
            f.setPixelSize(fs)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.setPen(QColor("#f4f4f6") if active else QColor("#b9bdc8"))
            tw, tx = L["tw"], L["tx"]
            if L["title_lines"] == 1:                     # fenêtre basse : titre sur une ligne
                base = p.fontMetrics().elidedText(base, Qt.ElideRight, tw)
            title_r = QRect(tx, y, tw, int(fs * (2.6 if L["title_lines"] == 2 else 1.4)))
            br = p.fontMetrics().boundingRect(title_r, al | Qt.TextWordWrap, base)
            if br.height() > title_r.height():
                base = p.fontMetrics().elidedText(base, Qt.ElideRight, tw * 2 - 20)
                br = p.fontMetrics().boundingRect(title_r, al | Qt.TextWordWrap, base)
            p.drawText(title_r, al | Qt.AlignTop | Qt.TextWordWrap, base)
            y += min(br.height(), title_r.height()) + 6
            f2 = QFont(self.font())
            f2.setPixelSize(int(np.clip(fs * 0.56, 12, 16)))
            p.setFont(f2)
            p.setPen(QColor("#9aa3b5"))
            sub = tr.row.get("artist") or ""
            if vers:
                sub += f"  ·  {vers}"
            p.drawText(QRect(tx, y, tw, 22), al,
                       p.fontMetrics().elidedText(sub, Qt.ElideRight, tw))
            y += 28
            if tr.analyzed and L["tags"]:                     # étiquettes tempo / clé
                tags = [f"{tr.bpm:.0f} BPM", tr.row.get("camelot") or ""]
                tags = [t for t in tags if t]
                f3 = QFont(self.font())
                f3.setPixelSize(12)
                p.setFont(f3)
                fm = p.fontMetrics()
                widths = [fm.horizontalAdvance(t) + 18 for t in tags]
                xx = (r.center().x() - (sum(widths) + 8 * (len(tags) - 1)) // 2) if al == Qt.AlignHCenter else tx
                for t, wd in zip(tags, widths):
                    p.setPen(QPen(QColor(255, 255, 255, 40), 1))
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(QRectF(xx, y, wd, 22), 11, 11)
                    p.setPen(QColor("#c9ccd6"))
                    p.drawText(QRect(int(xx), y, wd, 22), Qt.AlignCenter, t)
                    xx += wd + 8
        # 3. console du bas
        c = g["console"]
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(PANEL))
        p.drawRoundedRect(QRectF(c), 14, 14)
        p.end()
        return pm

    def _draw_progress(self, p, i):
        s = self._show[i]
        if s is None or s[0] is None:
            return
        tr, pos = s
        r = self._prog_rect(i)
        y = r.top() + 6
        L = max(1.0, tr.length)
        frac = float(np.clip(pos / L, 0, 1))
        p.fillRect(QRectF(r.left() + 2, y, r.width() - 4, 2), QColor(255, 255, 255, 30))
        p.fillRect(QRectF(r.left() + 2, y, (r.width() - 4) * frac, 2), QColor(ACCENT))
        f = QFont(self.font())
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor("#8a90a0"))
        el = int(pos)
        rest = int(max(0, L - pos))
        p.drawText(QRect(r.left() + 2, y + 4, r.width() - 4, 14), Qt.AlignLeft, f"{el // 60}:{el % 60:02d}")
        p.drawText(QRect(r.left() + 2, y + 4, r.width() - 4, 14), Qt.AlignRight, f"-{rest // 60}:{rest % 60:02d}")

    def paintEvent(self, e):
        p = QPainter(self)
        if self._bg is None or self._bg.size() != self.size() * self.devicePixelRatioF():
            if any(self._show):
                self._bg = self._make_bg()
            else:
                p.fillRect(self.rect(), QColor(BG))
                g = self._geom()
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(PANEL))
                p.setRenderHint(QPainter.Antialiasing)
                p.drawRoundedRect(QRectF(g["console"]), 14, 14)
                p.setPen(QColor("#8a90a0"))
                p.drawText(self.rect().adjusted(0, 0, 0, -120), Qt.AlignCenter,
                           "Lance une playlist : les platines s'afficheront ici")
                p.end()
                return
        r = QRectF(e.rect())
        k = self.devicePixelRatioF()
        p.drawPixmap(r, self._bg, QRectF(r.x() * k, r.y() * k, r.width() * k, r.height() * k))
        lead = max(range(2), key=lambda i: self._wts[i])
        if self._show[lead] and e.rect().intersects(self._prog_rect(lead)):
            self._draw_progress(p, lead)
        p.end()


def ns_label(style):
    return {"cale": "mix calé", "echo": "écho", "filtre": "filtre", "pullup": "pull-up",
            "fondu": "fondu"}.get(style, "auto")
