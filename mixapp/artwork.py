"""Pochette créée par l'appli quand aucune image n'existe (ni dans le fichier,
ni sur internet) : titre en grand, artiste, sillons de vinyle, sur un dégradé
dont les couleurs dépendent du morceau (toujours la même image pour un même
morceau, des couleurs différentes d'un morceau à l'autre)."""

from __future__ import annotations

import colorsys
import hashlib
import re

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap


def clean(title: str) -> str:
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", title or "").strip()
    return t or (title or "").strip() or "♪"


def palette(key: str) -> tuple[QColor, QColor]:
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)
    hue = (h % 360) / 360
    hue2 = (hue + 0.08 + (h >> 9) % 20 / 100) % 1
    c1 = QColor.fromRgbF(*colorsys.hsv_to_rgb(hue, 0.75, 0.85))
    c2 = QColor.fromRgbF(*colorsys.hsv_to_rgb(hue2, 0.85, 0.35))
    return c1, c2


def generate(title: str, artist: str, size: int = 300, radius: float = 0.0) -> QPixmap:
    """Pochette carrée (pixmap), coins arrondis si `radius` > 0 (fraction de la taille)."""
    name = clean(title)
    artist = (artist or "").strip()
    c1, c2 = palette(artist.lower() + "|" + name.lower())
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    if radius > 0:
        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, size * radius, size * radius)
        p.setClipPath(path)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, c1)
    g.setColorAt(1, c2)
    p.fillRect(img.rect(), g)
    cx, cy = size * 0.78, size * 0.80
    for i in range(14):                                     # sillons
        p.setPen(QPen(QColor(255, 255, 255, 22 if i % 2 else 34), max(1.0, size / 300)))
        r = size * (0.16 + i * 0.045)
        p.drawEllipse(QPointF(cx, cy), r, r)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 70))
    p.drawEllipse(QPointF(cx, cy), size * 0.12, size * 0.12)
    p.setBrush(QColor(255, 255, 255, 200))
    p.drawEllipse(QPointF(cx, cy), size * 0.018, size * 0.018)
    if size >= 40:                                          # trop petit : couleurs seules
        f = QFont()
        f.setBold(True)
        f.setPixelSize(max(6, int(size * 0.11)))
        f.setLetterSpacing(QFont.PercentageSpacing, 96)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        rect = QRectF(size * 0.08, size * 0.08, size * 0.84, size * 0.55)
        txt = name.upper()
        while f.pixelSize() > size * 0.06 and p.fontMetrics().boundingRect(
                rect.toRect(), Qt.TextWordWrap, txt).height() > rect.height():
            f.setPixelSize(f.pixelSize() - max(1, size // 150))
            p.setFont(f)
        p.drawText(rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, txt)
        if size >= 120 and artist:
            f2 = QFont()
            f2.setPixelSize(int(size * 0.05))
            p.setFont(f2)
            p.setPen(QColor(255, 255, 255, 220))
            p.drawText(QRectF(size * 0.08, size * 0.66, size * 0.6, size * 0.2),
                       Qt.AlignLeft | Qt.TextWordWrap, artist)
    p.end()
    return QPixmap.fromImage(img)
