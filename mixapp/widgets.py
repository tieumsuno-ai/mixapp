"""Petits éléments visuels du lecteur : icônes dessinées, égaliseur animé,
badge « mix en cours »."""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QProgressBar, QWidget

from .theme import c as tc


# ------------------------------------------------------------------ icônes

def _vinyl(p: QPainter, cx, cy, r, color: QColor, angle=0.0):
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(cx, cy), r, r)
    groove = QColor(0, 0, 0, 90)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(groove, max(1.0, r * 0.06)))
    for k in (0.82, 0.66):
        p.drawEllipse(QPointF(cx, cy), r * k, r * k)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 150))
    p.drawEllipse(QPointF(cx, cy), r * 0.36, r * 0.36)
    p.setBrush(color)
    p.drawEllipse(QPointF(cx, cy), r * 0.1, r * 0.1)
    # reflet qui tourne
    p.setPen(QPen(QColor(255, 255, 255, 110), max(1.0, r * 0.08), Qt.SolidLine, Qt.RoundCap))
    rect = QRectF(cx - r * 0.74, cy - r * 0.74, r * 1.48, r * 1.48)
    p.drawArc(rect, int((angle + 30) * 16), int(40 * 16))


def make_icon(kind: str, size: int = 28, color: str = "#ffffff") -> QIcon:
    pm = QPixmap(size * 2, size * 2)          # 2x pour les écrans Retina
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size * 2
    c = QColor(color)
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    if kind == "play":
        path = QPainterPath()
        path.moveTo(s * 0.32, s * 0.2)
        path.lineTo(s * 0.32, s * 0.8)
        path.lineTo(s * 0.82, s * 0.5)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "pause":
        p.drawRoundedRect(QRectF(s * 0.26, s * 0.2, s * 0.17, s * 0.6), s * 0.04, s * 0.04)
        p.drawRoundedRect(QRectF(s * 0.57, s * 0.2, s * 0.17, s * 0.6), s * 0.04, s * 0.04)
    elif kind == "next":
        for x0 in (0.14, 0.42):
            path = QPainterPath()
            path.moveTo(s * x0, s * 0.24)
            path.lineTo(s * x0, s * 0.76)
            path.lineTo(s * (x0 + 0.3), s * 0.5)
            path.closeSubpath()
            p.drawPath(path)
        p.drawRect(QRectF(s * 0.74, s * 0.24, s * 0.09, s * 0.52))
    elif kind == "turntable":
        _vinyl(p, s * 0.42, s * 0.5, s * 0.36, c)
        # flèche qui descend : le disque ralentit
        pen = QPen(c, s * 0.07, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(s * 0.84, s * 0.16), QPointF(s * 0.84, s * 0.8))
        p.drawLine(QPointF(s * 0.84, s * 0.8), QPointF(s * 0.74, s * 0.68))
        p.drawLine(QPointF(s * 0.84, s * 0.8), QPointF(s * 0.94, s * 0.68))
    elif kind == "pullup":
        _vinyl(p, s * 0.5, s * 0.5, s * 0.3, c)
        pen = QPen(c, s * 0.07, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        rect = QRectF(s * 0.08, s * 0.08, s * 0.84, s * 0.84)
        p.drawArc(rect, 30 * 16, 250 * 16)            # flèche qui tourne à l'envers
        tip = QPointF(s * 0.5 + s * 0.42 * math.cos(math.radians(30)),
                      s * 0.5 - s * 0.42 * math.sin(math.radians(30)))
        p.drawLine(tip, tip + QPointF(-s * 0.02, -s * 0.16))
        p.drawLine(tip, tip + QPointF(-s * 0.15, s * 0.03))
    elif kind == "fullscreen":
        pen = QPen(c, s * 0.08, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        a, b = s * 0.18, s * 0.82
        k = s * 0.22
        for (x, y, dx, dy) in ((a, a, 1, 1), (b, a, -1, 1), (a, b, 1, -1), (b, b, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * k, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * k))
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


# ------------------------------------------------------------------ égaliseur

class Equalizer(QWidget):
    """Petites barres qui dansent avec la musique (discret)."""

    BANDS = 12

    def __init__(self, engine_getter, parent=None):
        super().__init__(parent)
        self._get = engine_getter
        self.setFixedSize(64, 26)
        self.levels = np.zeros(self.BANDS)
        edges = np.geomspace(40, 14000, self.BANDS + 1)
        freqs = np.fft.rfftfreq(2048, 1 / 44100)
        self._bins = [(np.searchsorted(freqs, lo), max(np.searchsorted(freqs, hi),
                                                       np.searchsorted(freqs, lo) + 1))
                      for lo, hi in zip(edges[:-1], edges[1:])]
        self._win = np.hanning(2048)
        self.timer = QTimer(self, interval=40)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self):
        eng = self._get()
        target = np.zeros(self.BANDS)
        if eng is not None:
            blk = eng.last_block
            if len(blk) >= 256 and not eng.paused:
                x = blk.mean(1)
                x = np.pad(x, (0, max(0, 2048 - len(x))))[:2048] * self._win
                mag = np.abs(np.fft.rfft(x))
                for i, (a, b) in enumerate(self._bins):
                    db = 20 * np.log10(mag[a:b].mean() + 1e-9)
                    target[i] = np.clip((db + 10) / 50, 0, 1)
        # monte vite, redescend doucement
        self.levels = np.where(target > self.levels, target, self.levels * 0.82)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        bw = w / self.BANDS
        for i, lv in enumerate(self.levels):
            bh = max(2.0, lv * h)
            col = QColor("#6c8cff")
            col.setAlphaF(0.45 + 0.5 * lv)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(i * bw + 1, h - bh, bw - 2, bh), 1.5, 1.5)
        p.end()


# ------------------------------------------------------------------ mix en cours

class MixBadge(QWidget):
    """Bandeau animé pendant une transition : deux disques qui tournent, le
    curseur d'un crossfader qui glisse de A vers B, et ce que fait l'appli."""

    KINDS = {"beatmatch": "MIX EN COURS", "echo": "SORTIE EN ÉCHO", "fade": "FONDU ENCHAÎNÉ",
             "filtre": "FILTRE + ÉCHO", "pullup": "PULL-UP",
             "stop": "ARRÊT", "pause": "PAUSE"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self.kind = None
        self.progress = 0.0
        self.text = ""
        self._t0 = time.time()
        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self.update)
        self.hide()

    def set_state(self, kind, progress=0.0, text=""):
        if kind is None:
            if self.isVisible():
                self.hide()
                self.timer.stop()
            return
        self.kind, self.progress, self.text = kind, float(progress), text
        if not self.isVisible():
            self.show()
            self.timer.start()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        t = time.time() - self._t0
        accent = QColor("#f0a43a") if self.kind != "beatmatch" else QColor("#6c8cff")
        bg = QColor(accent)
        bg.setAlpha(int(34 + 18 * (0.5 + 0.5 * math.sin(t * 4))))     # pulsation douce
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(0, 0, w, h), 10, 10)
        # deux disques qui tournent
        r = h * 0.32
        speed = 200 if self.kind in ("beatmatch", "echo", "fade", "filtre", "pullup") else 60
        _vinyl(p, 22, h / 2, r, QColor("#d8dce6"), angle=-t * speed)
        _vinyl(p, 22 + 2.4 * r, h / 2, r, accent, angle=-t * speed * 1.03)
        x0 = 22 + 3.6 * r + 8
        # texte
        f = QFont()
        f.setPixelSize(12)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        p.setFont(f)
        p.setPen(accent.lighter(130))
        title = self.KINDS.get(self.kind, "")
        p.drawText(QRectF(x0, 4, w - x0 - 170, h / 2), Qt.AlignVCenter | Qt.AlignLeft, title)
        f2 = QFont()
        f2.setPixelSize(12)
        p.setFont(f2)
        p.setPen(QColor(tc("#c8cdd8")))
        p.drawText(QRectF(x0, h / 2 - 2, w - x0 - 170, h / 2), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(self.text, Qt.ElideRight, int(w - x0 - 175)))
        # crossfader A → B
        cw, cx = 140, w - 154
        cy = h / 2
        p.setPen(QPen(QColor(tc("#596074")), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + cw, cy))
        f3 = QFont()
        f3.setPixelSize(10)
        p.setFont(f3)
        p.setPen(QColor(tc("#8a90a0")))
        p.drawText(QRectF(cx - 14, cy - 8, 12, 16), Qt.AlignCenter, "A")
        p.drawText(QRectF(cx + cw + 2, cy - 8, 12, 16), Qt.AlignCenter, "B")
        k = self.progress if self.kind in ("beatmatch", "echo", "fade", "filtre", "pullup") else 0.0
        knob = cx + cw * min(max(k, 0.0), 1.0)
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(knob - 6, cy - 10, 12, 20), 3, 3)
        p.end()


# ------------------------------------------------------------------ barre de lecture

class SeekBar(QProgressBar):
    """Barre de progression cliquable / glissable pour avancer ou reculer."""

    seek = Signal(float)          # fraction 0..1, émise au relâchement
    dragging_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextVisible(False)
        self.setRange(0, 1000)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.dragging = False
        self.seekable = True
        self.zone = None            # (début, fin) en fraction : passage « temps forts »

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if self.zone:
            a, b = self.zone
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            col = QColor("#e0a526")
            col.setAlpha(150)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            w, h = self.width(), self.height()
            p.drawRoundedRect(QRectF(a * w, h - 3, max(2.0, (b - a) * w), 3), 1.5, 1.5)
            p.end()

    def _frac(self, ev):
        return min(max(ev.position().x() / max(1, self.width()), 0.0), 1.0)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.seekable:
            self.dragging = True
            self.dragging_changed.emit(True)
            self.setValue(int(1000 * self._frac(ev)))

    def mouseMoveEvent(self, ev):
        if self.dragging:
            self.setValue(int(1000 * self._frac(ev)))

    def mouseReleaseEvent(self, ev):
        if self.dragging:
            self.dragging = False
            self.dragging_changed.emit(False)
            self.seek.emit(self._frac(ev))


# ------------------------------------------------------------------ thème

class ThemeSwitch(QWidget):
    """Petit interrupteur discret : lune (sombre) / soleil (clair)."""

    toggled = Signal(bool)          # True = clair

    def __init__(self, light=False, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.light = bool(light)
        self._pos = 1.0 if self.light else 0.0
        self._anim = QTimer(self, interval=16)
        self._anim.timeout.connect(self._step)

    def setLight(self, on: bool):
        on = bool(on)
        if on == self.light:
            return
        self.light = on
        self._anim.start()
        self.toggled.emit(on)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.rect().contains(ev.position().toPoint()):
            self.setLight(not self.light)

    def _step(self):
        target = 1.0 if self.light else 0.0
        self._pos += (target - self._pos) * 0.35
        if abs(target - self._pos) < 0.02:
            self._pos = target
            self._anim.stop()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        k = self._pos
        track = QColor("#2a2e38") if k < 0.5 else QColor("#d6dbe5")
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), h / 2, h / 2)
        r = h / 2 - 3
        cx = 3 + r + k * (w - 6 - 2 * r)
        cy = h / 2
        if k >= 0.5:                                    # soleil
            sun = QColor("#f0a43a")
            p.setBrush(sun)
            p.drawEllipse(QPointF(cx, cy), r * 0.5, r * 0.5)
            p.setPen(QPen(sun, 1.4, Qt.SolidLine, Qt.RoundCap))
            for i in range(8):
                a = i * math.pi / 4
                p.drawLine(QPointF(cx + math.cos(a) * r * 0.72, cy + math.sin(a) * r * 0.72),
                           QPointF(cx + math.cos(a) * r * 0.98, cy + math.sin(a) * r * 0.98))
        else:                                           # lune
            path = QPainterPath()
            path.addEllipse(QPointF(cx, cy), r * 0.8, r * 0.8)
            cut = QPainterPath()
            cut.addEllipse(QPointF(cx + r * 0.42, cy - r * 0.3), r * 0.7, r * 0.7)
            p.setBrush(QColor("#c8cdd8"))
            p.drawPath(path.subtracted(cut))
        p.end()
