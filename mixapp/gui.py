"""Interface graphique de MixApp (PySide6 / Qt) — Mac et Windows.

    python -m mixapp            (ou double-clic sur MixApp.pyw)

Disposition :
  ┌ recherche ─────────────────────────────────────────── [Dossiers…] ┐
  │ Playlists │ Morceaux (playlist ou résultats)        │ File d'attente │
  ├───────────┴──────────────────────────────────────────┴───────────────┤
  │ En cours : titre, BPM, tonalité, progression, transition en cours    │
  │ [■ Stop] [⏭ Suivant]   volume                 état de l'analyse      │
  └──────────────────────────────────────────────────────────────────────┘

Raccourcis : ⌘/Ctrl+F recherche · Entrée = Jouer ensuite · ⌘/Ctrl+Entrée =
Ajouter à la file · ⌘/Ctrl+→ ou touche « piste suivante » = Suivant (écho ;
2 appuis rapides = coupe nette) · Suppr = retirer de la file.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QKeySequence, QLinearGradient, QPainter,
                           QPainterPath, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QProxyStyle, QPushButton, QSizePolicy, QSlider, QSplitter, QStackedWidget, QStyle, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import analysis, covers, db, library, search
from .engine import Engine
from . import theme
from .theme import c as tc
from . import artwork
from .deckview import DeckView, dominant_color
from .widgets import Equalizer, MixBadge, SeekBar, ThemeSwitch, make_icon
from .transitions import SR

APP_NAME = "MixApp"
MOD = "⌘" if sys.platform == "darwin" else "Ctrl"
COMPAT_COLORS = {"vert": "#3ecf6e", "orange": "#f0a43a", "rouge": "#e5534b", "inconnu": "#555"}

STYLE = """
* { font-family: -apple-system, "Segoe UI", "Helvetica Neue", Arial; font-size: 13px; }
QMainWindow, QWidget { background: #15171c; color: #e6e6e6; }
QLabel { background: transparent; }
QLineEdit { background: #22252d; border: 1px solid #333844; border-radius: 8px;
            padding: 8px 12px; font-size: 14px; color: #f2f2f2; }
QLineEdit:focus { border-color: #6c8cff; }
QListWidget, QTableWidget { background: #1b1e24; border: none; border-radius: 8px;
            alternate-background-color: #1f2229; outline: 0; }
QListWidget::item { padding: 6px 8px; border-radius: 6px; }
QListWidget::item:selected, QTableWidget::item:selected { background: #2f3b66; color: #fff; }
QHeaderView::section { background: #1b1e24; color: #8a90a0; border: none;
            padding: 6px; font-size: 11px; text-transform: uppercase; }
QPushButton { background: #2a2e38; border: 1px solid #353a46; border-radius: 8px;
            padding: 7px 14px; color: #e6e6e6; }
QPushButton:hover { background: #343947; }
QPushButton:disabled { color: #666; }
QPushButton#primary { background: #4f6bed; border-color: #4f6bed; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #5d79ff; }
QPushButton#next { background: #2e7d5b; border-color: #2e7d5b; color: white;
            font-weight: 600; font-size: 15px; padding: 10px 22px; }
QPushButton#next:hover { background: #379a6f; }
QPushButton#stop { background: #c0392b; border-color: #c0392b; color: white;
            font-weight: 600; font-size: 13px; padding: 8px 14px; }
QPushButton#stop:hover { background: #d84a3b; }
QPushButton#stop:disabled { background: #3a2a2a; border-color: #3a2a2a; color: #7a6060; }
QPushButton#play { background: #4f6bed; border-color: #4f6bed; color: white;
            font-weight: 600; font-size: 15px; padding: 10px 22px; }
QPushButton#play:hover { background: #5d79ff; }
QPushButton#deck { background: #2a2e38; border-color: #3a4050; color: #e6e6e6;
            font-weight: 600; font-size: 13px; padding: 8px 14px; }
QPushButton#deck:hover { background: #343947; }
QPushButton#deck:disabled, QPushButton#next:disabled { color: #666; background: #22252d; border-color: #2a2e38; }
QPushButton#flat { background: transparent; border: none; padding: 6px; }
QPushButton#flat:hover { background: #2a2e38; }
QPushButton#hl { background: #2a2e38; border: 1px solid #3a4050; color: #c8cdd8;
            font-weight: 600; padding: 8px 12px; }
QPushButton#hl:checked { background: #b8860b; border-color: #e0a526; color: #fff; }
QComboBox { background: #22252d; border: 1px solid #333844; border-radius: 8px; padding: 6px 10px; color: #e6e6e6; }
QComboBox QAbstractItemView { background: #22252d; color: #e6e6e6; selection-background-color: #2f3b66; }
QLabel#fact { color: #aab0bf; font-size: 12px; padding: 0 14px; }
QLabel#remain { color: #e6e6e6; font-weight: 600; min-width: 52px; }
QLabel#counter { color: #c8cdd8; font-size: 13px; }
QLabel#section { color: #8a90a0; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QLabel#nowTitle { font-size: 20px; font-weight: 700; color: #f2f2f2; }
QLabel#mixlen { color: #c8cdd8; font-weight: 600; min-width: 34px; }
QLabel#nowInfo { color: #9aa3b5; font-size: 13px; }
QLabel#transition { color: #f0a43a; font-weight: 600; }
QFrame#player { background: #1b1e24; border-radius: 12px; }
QProgressBar { background: #2a2e38; border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #6c8cff; border-radius: 4px; }
QProgressBar:hover { background: #343a48; }
QSlider::groove:horizontal { background: #2a2e38; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: #e6e6e6; width: 12px; margin: -5px 0; border-radius: 6px; }
QSplitter::handle { background: #15171c; }
"""


def effective_duration(r: dict, last: bool = False) -> float:
    """Temps qu'un morceau occupe réellement dans le mix (s).

    Il commence à être entendu au début de son intro (superposée au précédent)
    et laisse la place au suivant au début de son outro. Le dernier morceau
    est joué jusqu'au bout.
    """
    d = float(r.get("duration") or 0)
    if not r.get("bpm"):
        return max(0.0, d - (0 if last else 6))
    start = float(r.get("first_beat") or 0)
    end = d if last else float(r.get("outro_start") or d)
    return max(0.0, end - start)


def _fmt_long(s):
    s = max(0, int(s or 0))
    h, m = divmod(s // 60, 60)
    return f"{h} h {m:02d}" if h else f"{m} min"


def _fmt(s):
    s = max(0, int(s or 0))
    return f"{s // 60}:{s % 60:02d}"


# ------------------------------------------------------------------ réglages

def settings_path() -> Path:
    return db.data_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {"roots": [], "volume": 0.9}


def save_settings(s: dict):
    settings_path().write_text(json.dumps(s, ensure_ascii=False, indent=2), "utf-8")


# ------------------------------------------------------------------ sortie audio

class AudioOutput:
    """Envoie le son du moteur à la carte son (sounddevice).

    Si MIXAPP_FAKE_AUDIO=1 (tests sans carte son), un fil lit le moteur au
    rythme du temps réel sans rien jouer.
    """

    def __init__(self, engine: Engine):
        self.engine = engine
        self.stream = None
        self._fake = None

    def start(self):
        if os.environ.get("MIXAPP_FAKE_AUDIO"):
            self._fake = True
            speed = float(os.environ.get("MIXAPP_FAKE_SPEED", "1"))

            def loop():
                while self._fake:
                    self.engine.pull(1024)
                    time.sleep(1024 / SR / speed)
            threading.Thread(target=loop, daemon=True).start()
            return
        import sounddevice as sd

        # Écriture « bloquante » depuis un fil dédié : la carte son lit dans un
        # tampon géré en C (~0,6 s), sans avoir besoin de Python. Si Python est
        # occupé un instant (préparation d'un mix, analyse…), le son continue :
        # plus de craquements, contrairement au mode « callback ».
        self.stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32",
                                      blocksize=0, latency=0.4)
        self.stream.start()
        self._run = True

        def writer():
            block = 1024
            while self._run:
                try:
                    self.stream.write(self.engine.pull(block))
                except Exception:
                    if not self._run:
                        break
                    time.sleep(0.01)
        self._thread = threading.Thread(target=writer, daemon=True, name="mixapp-audio")
        self._thread.start()

    def stop(self):
        self._fake = False
        self._run = False
        if getattr(self, "_thread", None) is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


# ------------------------------------------------------------------ signaux inter-fils

class ElideLabel(QLabel):
    """Texte sur une ligne qui se termine par « … » s'il manque de place
    (au lieu d'élargir la fenêtre ou d'être coupé net)."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        txt = p.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width())
        p.drawText(self.rect(), int(self.alignment()) | Qt.AlignVCenter, txt)
        p.end()


class StableLabel(QLabel):
    """Étiquette pour les textes qui changent souvent (temps, « dans 0:42 »…).
    Tant que le nouveau texte tient dans la place déjà prise, on le redessine
    simplement, sans relancer la mise en page de la fenêtre (qui redessinait
    aussi le « Le saviez-vous ? » plusieurs fois par seconde)."""

    def __init__(self, text=""):
        super().__init__(text)
        self._txt = text

    def text(self):
        return self._txt

    def setText(self, t):
        t = t or ""
        if t == self._txt:
            return
        self._txt = t
        fm = self.fontMetrics()
        m = self.contentsMargins()
        need = fm.horizontalAdvance(t) + m.left() + m.right() + 2
        if not self.isVisible() or need > self.width() or not t or not super().text():
            super().setText(t)          # place à agrandir (rare) : mise en page normale
        else:
            self.update()

    def paintEvent(self, e):
        if self._txt == super().text():
            return super().paintEvent(e)
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        p.drawText(self.contentsRect(), int(self.alignment()) | Qt.AlignVCenter, self._txt)
        p.end()


class AmbientFrame(QFrame):
    """Cadre du lecteur teinté de la couleur de la pochette en cours
    (couleur calculée une fois par morceau, dessin d'un simple dégradé)."""

    def __init__(self):
        super().__init__()
        self.color = None
        self.track_id = None

    def set_color(self, color, track_id=None):
        self.color, self.track_id = color, track_id
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.color is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, self.width(), 0)
        c0 = QColor(self.color)
        c0.setAlpha(70 if not theme.is_light() else 45)
        c1 = QColor(self.color)
        c1.setAlpha(0)
        g.setColorAt(0, c0)
        g.setColorAt(0.6, c1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        p.fillPath(path, g)
        p.end()


class Bus(QObject):
    log = Signal(str)
    library_changed = Signal()
    analysis_progress = Signal(int, int)
    playback_ready = Signal(object)       # Engine ou Exception
    cover_ready = Signal(int, object)     # id du morceau, octets de l'image (ou None)
    info_ready = Signal(str, object)      # clé du morceau, infos (année, anecdote…)
    hl_ready = Signal()                   # un passage « temps forts » de plus est calculé


# ------------------------------------------------------------------ fenêtre principale

class MainWindow(QMainWindow):
    def __init__(self, db_path=None):
        # curseur de texte fixe : son clignotement faisait redessiner toute la
        # fenêtre deux fois par seconde (coûteux sur les grands écrans Retina)
        QApplication.setCursorFlashTime(0)
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 780)
        self.db_path = db_path
        self.conn = db.connect(db_path)
        self.settings = load_settings()
        self.bus = Bus()
        self.engine: Engine | None = None
        self.output: AudioOutput | None = None
        self.observer = None
        self.current_folder: str | None = None
        self.shown_rows: list[dict] = []
        self.mode = "playlist"            # ou "search"
        self._analysis_running = False
        self._analysis_again = False
        self._hl_cache: dict = {}
        # passages « temps forts » par morceau : {chemin|mtime: {"court": win, "moyen": win}}
        from . import trackinfo
        self.info_cache = trackinfo.InfoCache(db.data_dir() / "infos.json")
        self._hl_file = db.data_dir() / "highlights2.json"
        from . import vocals as V
        self.vocals_ok = V.available()
        self.vocal_cache = V.VocalCache(db.data_dir() / "vocals")
        self._vocal_lock = threading.Lock()
        try:
            self._hl_store = json.loads(self._hl_file.read_text())
        except Exception:
            self._hl_store = {}
        self._hl_queue: list = []
        self._hl_queued: set = set()
        self._hl_lock = threading.Lock()
        self._hl_event = threading.Event()
        threading.Thread(target=self._hl_worker, daemon=True).start()
        self.played_ids: set[int] = set()     # morceaux déjà joués pendant la session
        self.logos: set[str] = set()          # images « logo » partagées par plusieurs MP3
        self.cover_pix: dict[int, QPixmap] = {}
        self._cover_wanted: dict[int, dict] = {}
        self._cover_urgent: list[dict] = []
        self._cover_lock = threading.Lock()
        self._cover_event = threading.Event()
        threading.Thread(target=self._cover_worker, daemon=True).start()

        theme.set_light(self.settings.get("theme") == "clair")
        self._build_ui()
        self._connect()
        if theme.is_light():
            self.apply_theme()
        self.refresh_playlists()
        QTimer.singleShot(200, self._startup)

    # ---------------------------------------------------------------- construction

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(12)

        # barre du haut
        top = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setToolTip("Rechercher un titre ou un artiste (Ctrl/⌘ F), puis Entrée = « Jouer ensuite »\n"
                                   "Filtres possibles : bpm:120-128  cle:8A  genre:house")
        self.search_box.setPlaceholderText(
            "Rechercher un titre, un artiste…   (filtres : bpm:120-128  cle:8A  genre:house)")
        self.search_box.setClearButtonEnabled(True)
        top.addWidget(self.search_box, 1)
        self.btn_folders = QPushButton("Dossiers de musique…")
        self.btn_folders.setToolTip("Tes dossiers de musique : ajouter, retirer, playlists retirées")
        top.addWidget(self.btn_folders)
        self.btn_view = QPushButton("◎  Platines")
        self.btn_view.setCheckable(True)
        self.btn_view.setToolTip("Vue Platines : pochettes, formes d'onde, platines, table de mixage\n"
                                 "et pads pour choisir la prochaine transition (V)")
        top.addWidget(self.btn_view)
        self.btn_full = QPushButton()
        self.btn_full.setObjectName("flat")
        self.btn_full.setIcon(make_icon("fullscreen", 18, tc("#c8cdd8")))
        self.btn_full.setIconSize(QSize(18, 18))
        self.btn_full.setToolTip("Plein écran (F11 · ⌃⌘F sur Mac) — Échap pour sortir")
        top.addWidget(self.btn_full)
        self.theme_switch = ThemeSwitch(theme.is_light())
        self.theme_switch.setToolTip("Thème clair ☀ / sombre ☾ (T)")
        top.addWidget(self.theme_switch)
        v.addLayout(top)

        # trois colonnes
        split = QSplitter(Qt.Horizontal)
        # playlists
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lab = QLabel("PLAYLISTS")
        lab.setObjectName("section")
        lv.addWidget(lab)
        self.playlist_list = QListWidget()
        self.playlist_list.setToolTip("Glisse ici un dossier de MP3 pour l'ajouter")
        self.playlist_list.setAcceptDrops(True)
        self.playlist_list.viewport().setAcceptDrops(True)
        self.playlist_list.viewport().installEventFilter(self)
        self.playlist_list.installEventFilter(self)
        self.playlist_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.playlist_list.setTextElideMode(Qt.ElideRight)
        lv.addWidget(self.playlist_list, 1)
        drop_hint = QLabel("↧ glisse un dossier de MP3 ici")
        self._ss(drop_hint, "color:#6f7686; font-size:11px;")
        lv.addWidget(drop_hint)
        split.addWidget(left)

        # morceaux
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        self.tracks_title = QLabel("MORCEAUX")
        self.tracks_title.setObjectName("section")
        th = QHBoxLayout()
        th.addWidget(self.tracks_title, 1)
        self.dupes_btn = QPushButton("")
        self.dupes_btn.setObjectName("flat")
        self.dupes_btn.setCursor(Qt.PointingHandCursor)
        self.dupes_btn.setToolTip("Morceaux en double dans tes playlists : rien n'est supprimé de ton\n"
                                  "ordinateur, l'appli les masque seulement (tu peux tout réafficher)")
        self.dupes_btn.hide()
        th.addWidget(self.dupes_btn)
        mv.addLayout(th)
        self.table = QTableWidget(0, 7)
        self.table.installEventFilter(self)
        self.table.setToolTip("Gras ▶ : en cours · bleu : à venir · gris : déjà joué\n"
                              "Pastille : vert = s'enchaîne parfaitement, orange = possible, rouge = difficile")
        self.table.setHorizontalHeaderLabels(["", "Titre", "Artiste", "BPM", "Clé", "Durée", ""])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(26, 26))
        self.table.verticalHeader().setDefaultSectionSize(32)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 26)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        for c, w in ((3, 64), (4, 70), (5, 60)):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
            self.table.setColumnWidth(c, w)
        self.table.setColumnHidden(6, True)
        mv.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.btn_play_here = QPushButton("▶  Lancer la playlist ici")
        self.btn_play_here.setToolTip("Lance la playlist à partir du morceau sélectionné (Entrée ou double-clic)")
        self.btn_play_here.setObjectName("primary")
        self.btn_play_next = QPushButton("Jouer ensuite")
        self.btn_play_next.setToolTip("Les morceaux sélectionnés passeront juste après celui en cours (★)\n"
                                      f"Sélection multiple : {MOD}+clic ou Maj+clic")
        self.btn_enqueue = QPushButton("Ajouter à la file")
        self.btn_enqueue.setToolTip("Ajoute les morceaux sélectionnés après les autres demandes\n"
                                    f"Sélection multiple : {MOD}+clic ou Maj+clic")
        actions.addWidget(self.btn_play_here)
        actions.addStretch(1)
        actions.addWidget(self.btn_play_next)
        actions.addWidget(self.btn_enqueue)
        mv.addLayout(actions)
        split.addWidget(mid)

        # file d'attente
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        lab = QLabel("À SUIVRE")
        lab.setObjectName("section")
        rv.addWidget(lab)
        self.queue_list = QListWidget()
        self.queue_list.installEventFilter(self)
        self.queue_list.setToolTip("Les prochains morceaux : 🔒 déjà préparé · ★ demande prioritaire")
        self.queue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.queue_list.setTextElideMode(Qt.ElideRight)
        self.queue_list.setUniformItemSizes(True)
        self.queue_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.queue_list.setIconSize(QSize(38, 38))
        rv.addWidget(self.queue_list, 1)
        qa = QHBoxLayout()
        self.btn_up = QPushButton("↑")
        self.btn_up.setToolTip("Monter les morceaux choisis dans « À suivre »")
        self.btn_down = QPushButton("↓")
        self.btn_down.setToolTip("Descendre les morceaux choisis dans « À suivre »")
        self.btn_remove = QPushButton("Retirer")
        self.btn_remove.setToolTip("Retirer les morceaux choisis de « À suivre » (Suppr)\n"
                                   f"Sélection multiple : {MOD}+clic ou Maj+clic")
        for b in (self.btn_up, self.btn_down):
            b.setFixedWidth(40)
        qa.addWidget(self.btn_up)
        qa.addWidget(self.btn_down)
        qa.addStretch(1)
        qa.addWidget(self.btn_remove)
        rv.addLayout(qa)
        hint = QLabel(f"🔒 déjà préparé   ★ prioritaire   {MOD}/Maj+clic : plusieurs à la fois")
        self._ss(hint, "color:#6f7686; font-size:11px;")
        rv.addWidget(hint)
        split.addWidget(right)
        self.queue_panel = right
        self.split = split

        split.setSizes([230, 700, 330])
        self.stack = QStackedWidget()
        self.stack.addWidget(split)
        self.deck = DeckView(self._deck_cover, lambda t: self.vocal_map(t, compute=False),
                             self.settings.get("animations", "fluides"))
        # page Platines : la vue + le volet « À suivre » (le même panneau que la
        # vue liste, déplacé ici : sélection multiple, Retirer, ↑↓ marchent pareil)
        page = QWidget()
        ph = QHBoxLayout(page)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(12)
        ph.addWidget(self.deck, 1)
        self.drawer = QWidget()
        self.drawer.setFixedWidth(330)
        self.drawer_layout = QVBoxLayout(self.drawer)
        self.drawer_layout.setContentsMargins(0, 0, 0, 0)
        ph.addWidget(self.drawer)
        self.drawer.setVisible(self.settings.get("drawer", True))
        self.stack.addWidget(page)
        v.addWidget(self.stack, 1)

        # lecteur
        player = AmbientFrame()
        player.setObjectName("player")
        self.player_frame = player
        pv = QVBoxLayout(player)
        pv.setContentsMargins(18, 14, 18, 14)
        row1 = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setToolTip("Pochette (cherchée sur internet si besoin)")
        self.cover.setFixedSize(84, 84)
        self.cover.setPixmap(self._placeholder(84))
        row1.addWidget(self.cover)
        row1.addSpacing(10)
        col = QVBoxLayout()
        self.now_title = QLabel("Rien en cours")
        self.now_title.setObjectName("nowTitle")
        self.now_info = QLabel("Choisis une playlist puis « Lancer la playlist ici ».")
        self.now_info.setObjectName("nowInfo")
        col.addWidget(self.now_title)
        col.addWidget(self.now_info)
        row1.addLayout(col, 2)
        self.fact_label = QLabel("")
        self.fact_label.setObjectName("fact")
        self.fact_label.setWordWrap(True)
        self.fact_label.setOpenExternalLinks(True)
        self.fact_label.setTextFormat(Qt.RichText)
        self.fact_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.fact_label.setToolTip("Le saviez-vous ? Année de sortie (MusicBrainz) et anecdote tirée de "
                                   "Wikipédia, sans rien inventer")
        row1.addWidget(self.fact_label, 3)
        self.counter = StableLabel("")
        self.counter.setToolTip("Temps restant de la soirée, nombre de morceaux et heure de fin estimée")
        self.counter.setObjectName("counter")
        self.counter.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.transition_label = StableLabel("")
        self.transition_label.setObjectName("transition")
        rcol = QVBoxLayout()
        rcol.addWidget(self.counter, alignment=Qt.AlignRight)
        rcol.addWidget(self.transition_label, alignment=Qt.AlignRight)
        row1.addLayout(rcol)
        pv.addLayout(row1)
        self.mix_badge = MixBadge()
        self.mix_badge.setToolTip("Transition en cours : le curseur va du morceau A au morceau B")
        # place toujours réservée : la fenêtre ne change plus de taille quand un mix commence
        sp = self.mix_badge.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.mix_badge.setSizePolicy(sp)
        pv.addWidget(self.mix_badge)
        row2 = QHBoxLayout()
        self.pos_label = StableLabel("0:00")
        self.progress = SeekBar()
        self.progress.setMaximumHeight(10)
        self.progress.setToolTip("Clique ou glisse pour avancer / reculer dans le morceau")
        self.progress.seek.connect(self._on_seek)
        self.len_label = StableLabel("0:00")
        self._ss(self.len_label, "color:#8a90a0;")
        self.remain_label = StableLabel("-0:00")
        self.remain_label.setObjectName("remain")
        self.remain_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.remain_label.setToolTip("Temps restant du morceau en cours")
        row2.addWidget(self.pos_label)
        row2.addWidget(self.progress, 1)
        row2.addWidget(self.remain_label)
        row2.addWidget(self.len_label)
        pv.addLayout(row2)
        row3 = QHBoxLayout()
        self.btn_play = QPushButton("  Lecture")
        self.btn_play.setObjectName("play")
        self.btn_play.setIcon(make_icon("play", 18))
        self.btn_play.setIconSize(QSize(18, 18))
        self.btn_play.setToolTip("Lecture / pause (espace) — la pause part en écho, la reprise repart au même endroit")
        self.btn_stop33 = QPushButton("  Stop 33 tours")
        self.btn_stop33.setObjectName("deck")
        self.btn_stop33.setIcon(make_icon("turntable", 20))
        self.btn_stop33.setIconSize(QSize(20, 20))
        self.btn_stop33.setToolTip("Arrêt : le disque ralentit jusqu'à l'arrêt (S) · 2e appui : arrêt immédiat")
        self.btn_pullup = QPushButton("  Stop pull-up")
        self.btn_pullup.setObjectName("stop")
        self.btn_pullup.setIcon(make_icon("pullup", 20))
        self.btn_pullup.setIconSize(QSize(20, 20))
        self.btn_pullup.setToolTip("Arrêt façon DJ : scratch, rembobinage, écho (P) · 2e appui : arrêt immédiat")
        self.btn_stop = self.btn_pullup         # compatibilité
        self.btn_next = QPushButton("  Suivant")
        self.btn_next.setObjectName("next")
        self.btn_next.setIcon(make_icon("next", 18))
        self.btn_next.setIconSize(QSize(18, 18))
        self.btn_next.setToolTip("Sortie en écho vers le morceau suivant (→) · 2 appuis rapides : coupe nette")
        for b in (self.btn_play, self.btn_stop33, self.btn_pullup, self.btn_next):
            row3.addWidget(b)
        row3.addSpacing(14)
        self.btn_hl = QPushButton("★ Temps forts")
        self.btn_hl.setObjectName("hl")
        self.btn_hl.setCheckable(True)
        self.btn_hl.setToolTip("Ne mixer que les meilleurs moments de chaque morceau (refrain / drop),\n"
                               "sans les longues intros et outros. Activable à tout moment :\n"
                               "s'applique dès le prochain enchaînement. (H)")
        self.btn_dj = QPushButton("⇅ Flow DJ")
        self.btn_dj.setObjectName("hl")
        self.btn_dj.setCheckable(True)
        self.btn_dj.setToolTip("Flow DJ : l'appli choisit elle-même l'ordre des morceaux pour des\n"
                               "enchaînements fluides — tempos proches (≤ 5 %), tonalités compatibles,\n"
                               "énergie qui monte. Les demandes « Jouer ensuite » restent prioritaires. (D)")
        self.btn_hl.setChecked(bool(self.settings.get("highlights")))
        self.btn_dj.setChecked(bool(self.settings.get("dj_order")))
        row3.addWidget(self.btn_hl)
        row3.addWidget(self.btn_dj)
        row3.addSpacing(14)
        mix_lab = QLabel("⇄")
        mix_lab.setObjectName("mixlen")
        mix_lab.setToolTip("Durée des transitions")
        row3.addWidget(mix_lab)
        self.mix_len = QSlider(Qt.Horizontal)
        self.mix_len.setRange(1, 15)            # 1 = Auto, puis 2 à 15 secondes
        ms = self.settings.get("mix_seconds")
        self.mix_len.setValue(int(ms) if ms else 1)
        self.mix_len.setFixedWidth(110)
        self.mix_len.setPageStep(1)
        tip = ("Durée des transitions : Auto (l'appli choisit selon les morceaux)\n"
               "ou de 2 à 15 secondes. L'appli garde des mesures entières pour rester\n"
               "dans le rythme, et raccourcit si des clés frottent ou s'il y a des paroles.")
        self.mix_len.setToolTip(tip)
        row3.addWidget(self.mix_len)
        self.mix_len_label = QLabel("")
        self.mix_len_label.setObjectName("mixlen")
        self.mix_len_label.setToolTip(tip)
        row3.addWidget(self.mix_len_label)
        self._show_mix_len(self.mix_len.value())
        row3.addSpacing(14)
        row3.addWidget(QLabel("🔈"))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setToolTip("Volume")
        self.volume.setRange(0, 100)
        self.volume.setValue(int(self.settings.get("volume", 0.9) * 100))
        self.volume.setFixedWidth(140)
        row3.addWidget(self.volume)
        row3.addSpacing(16)
        self.eq = Equalizer(lambda: self.engine)
        self.eq.setToolTip("Égaliseur : la musique en direct")
        row3.addWidget(self.eq)
        self.status = ElideLabel("")
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._ss(self.status, "color:#8a90a0;")
        # les textes longs se coupent au lieu d'élargir la fenêtre (plein écran sur portable)
        for lab in (self.now_title, self.now_info, self.fact_label):
            lab.setSizePolicy(QSizePolicy.Ignored, lab.sizePolicy().verticalPolicy())
            lab.setMinimumWidth(60)
        row3.addSpacing(12)
        row3.addWidget(self.status, 1)
        pv.addLayout(row3)
        v.addWidget(player)

        self._update_buttons()

    def _connect(self):
        self.search_box.textChanged.connect(self._on_search_changed)
        self._search_timer = QTimer(self, singleShot=True, interval=150)
        self._search_timer.timeout.connect(self.run_search)
        self.btn_folders.clicked.connect(self.show_folders)
        self.playlist_list.currentItemChanged.connect(self._on_playlist_selected)
        self.playlist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.playlist_list.customContextMenuRequested.connect(self._playlist_menu)
        self.table.itemDoubleClicked.connect(lambda *_: self.on_double_click())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.btn_play_here.clicked.connect(self.play_from_selection)
        self.btn_play_next.clicked.connect(lambda: self.queue_selected(front=True))
        self.btn_enqueue.clicked.connect(lambda: self.queue_selected(front=False))
        self.btn_remove.clicked.connect(self.remove_from_queue)
        self.btn_up.clicked.connect(lambda: self.move_in_queue(-1))
        self.btn_down.clicked.connect(lambda: self.move_in_queue(+1))
        self.btn_play.clicked.connect(self.play_pause)
        self.btn_stop33.clicked.connect(lambda: self.styled_stop("turntable"))
        self.btn_pullup.clicked.connect(lambda: self.styled_stop("pullup"))
        self.btn_next.clicked.connect(self.skip)
        self.btn_view.toggled.connect(self._on_view)
        QShortcut(QKeySequence(Qt.Key_V), self, activated=self.btn_view.toggle)
        self.deck.style_chosen.connect(self._on_pad_style)
        self.deck.action.connect(self._on_pad_action)
        self.deck.mode_changed.connect(self._on_anim_mode)
        self.deck.drawer_btn.toggled.connect(self._on_drawer)
        if self.settings.get("view") == "platines":
            self.btn_view.setChecked(True)
        self.btn_full.clicked.connect(self.toggle_fullscreen)
        self.btn_hl.toggled.connect(lambda _: self._on_highlights())
        self.btn_dj.toggled.connect(lambda _: self._on_dj())
        self.volume.valueChanged.connect(self._on_volume)
        self._dupes_timer = QTimer(self, singleShot=True, interval=1500)
        self._dupes_timer.timeout.connect(self.check_dupes)
        self.dupes_btn.clicked.connect(self.show_dupes)
        QTimer.singleShot(2500, self.check_dupes)
        self._mix_len_timer = QTimer(self, singleShot=True, interval=350)
        self._mix_len_timer.timeout.connect(self._apply_mix_len)
        self.mix_len.valueChanged.connect(self._on_mix_len)
        self.theme_switch.toggled.connect(self._on_theme)
        QShortcut(QKeySequence(Qt.Key_T), self,
                  activated=lambda: self.theme_switch.setLight(not self.theme_switch.light))

        self.bus.log.connect(self._on_log)
        self.bus.library_changed.connect(self._on_library_changed)
        self.bus.analysis_progress.connect(self._on_analysis_progress)
        self.bus.playback_ready.connect(self._on_playback_ready)
        self.bus.cover_ready.connect(self._on_cover_ready)
        self.bus.info_ready.connect(self._on_info_ready)
        self._hl_refresh = QTimer(self, singleShot=True, interval=400)
        self._hl_refresh.timeout.connect(self._update_playlist_header)
        self.bus.hl_ready.connect(self._hl_refresh.start)

        QShortcut(QKeySequence.Find, self, activated=lambda: (self.search_box.setFocus(),
                                                               self.search_box.selectAll()))
        for seq in (QKeySequence("Ctrl+Right"), QKeySequence(Qt.Key_MediaNext)):
            QShortcut(seq, self, activated=self.skip, context=Qt.ApplicationShortcut)
        QShortcut(QKeySequence(Qt.Key_MediaPlay), self, activated=self.play_pause,
                  context=Qt.ApplicationShortcut)
        QShortcut(QKeySequence(Qt.Key_MediaTogglePlayPause), self, activated=self.play_pause,
                  context=Qt.ApplicationShortcut)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.play_pause)
        QShortcut(QKeySequence(Qt.Key_S), self, activated=lambda: self.styled_stop("turntable"))
        QShortcut(QKeySequence(Qt.Key_P), self, activated=lambda: self.styled_stop("pullup"))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=self.skip)
        QShortcut(QKeySequence(Qt.Key_H), self, activated=self.btn_hl.toggle)
        QShortcut(QKeySequence(Qt.Key_D), self, activated=self.btn_dj.toggle)
        for seq in (QKeySequence(Qt.Key_F11), QKeySequence("Ctrl+Meta+F")):
            QShortcut(seq, self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key_Escape), self,
                  activated=lambda: self.isFullScreen() and self.toggle_fullscreen())
        QShortcut(QKeySequence(Qt.Key_Return), self.table, activated=lambda: self.queue_selected(True))
        QShortcut(QKeySequence("Ctrl+Return"), self.table, activated=lambda: self.queue_selected(False))
        QShortcut(QKeySequence(Qt.Key_Delete), self.queue_list, activated=self.remove_from_queue)
        QShortcut(QKeySequence(Qt.Key_Backspace), self.queue_list, activated=self.remove_from_queue)
        self.search_box.returnPressed.connect(lambda: (self.table.setFocus(), self.table.selectRow(0)))

        self.ui_timer = QTimer(self, interval=200)
        self.ui_timer.timeout.connect(self.refresh_player)
        self.ui_timer.start()

    # ---------------------------------------------------------------- démarrage / dossiers

    def _startup(self):
        roots = [r for r in self.settings.get("roots", []) if Path(r).is_dir()]
        if not roots:
            QMessageBox.information(
                self, APP_NAME,
                "Bienvenue !\n\nChoisis le dossier qui contient ta musique.\n"
                "Chaque sous-dossier contenant des MP3 deviendra une playlist.")
            self.choose_folders()
        else:
            self.start_library_tasks()

    def choose_folders(self):
        d = QFileDialog.getExistingDirectory(self, "Dossier de musique à ajouter",
                                             str(Path.home() / "Music"))
        if d:
            self.add_folders([d])

    def add_folders(self, dirs):
        """Ajoute des dossiers de musique (bouton ou glisser-déposer)."""
        roots = self.settings.setdefault("roots", [])
        added = []
        for d in dirs:
            d = str(Path(d).resolve())
            inside = any(Path(d) == Path(r) or Path(r) in Path(d).parents for r in roots)
            if not inside:
                # un dossier parent remplace ses sous-dossiers déjà ajoutés
                roots[:] = [r for r in roots if Path(d) not in Path(r).parents]
                roots.append(d)
                added.append(d)
        save_settings(self.settings)
        if self.observer is not None and added:
            try:
                self.observer.stop()
            except Exception:
                pass
            self.observer = None
        self.status.setText(f"Ajout de {len(added)} dossier(s)…" if added else "Dossier déjà dans la bibliothèque")
        self.start_library_tasks()

    # ------------------------------------------------------------ dossiers et playlists retirées

    def _removed(self):
        return list(self.settings.get("removed", []))

    def _restart_watch(self):
        if self.observer is not None:
            try:
                self.observer.stop()
            except Exception:
                pass
            self.observer = None

    def _forget(self, folder, recursive):
        """MixApp oublie un dossier : base, analyses et pochettes de ses morceaux.
        Aucun fichier de musique n'est touché."""
        gone = library.forget(self.conn, folder, recursive)
        paths = {g["path"] for g in gone}
        keys = {self._hl_key(g) for g in gone}
        import hashlib
        cdir = covers.cache_dir()
        for pth in paths:
            k = hashlib.md5(pth.encode("utf-8")).hexdigest()
            for f in cdir.glob(k + ".*"):
                try:
                    f.unlink()
                except OSError:
                    pass
        for k in keys:
            try:
                self.vocal_cache.mem.pop(k, None)
                self.vocal_cache._file(k).unlink(missing_ok=True)
            except OSError:
                pass
            self._hl_store.pop(k, None)
            self.info_cache.data.pop(k, None)
        try:
            self._hl_file.write_text(json.dumps(self._hl_store))
            self.info_cache.path.write_text(json.dumps(self.info_cache.data, ensure_ascii=False))
        except Exception:
            pass
        hidden = self.settings.get("hidden", {})
        for f in list(hidden):
            if Path(f) == Path(folder) or (recursive and Path(folder) in Path(f).parents):
                hidden.pop(f, None)
        return gone

    def _after_forget(self, folders):
        if self.current_folder and any(
                Path(self.current_folder) == Path(f) or Path(f) in Path(self.current_folder).parents
                for f in folders):
            self.current_folder = None
            self._fill_table([], False)
        self.refresh_playlists(keep=True)
        if self.current_folder is None and self.playlist_list.count():
            self.playlist_list.setCurrentRow(0)
        save_settings(self.settings)
        self._on_analysis_progress(0, 0)

    def _ask(self, title, text, ok_text, icon="turntable"):
        """Petite fenêtre de confirmation au style de l'appli ; « Annuler » par défaut."""
        from PySide6.QtWidgets import QDialog
        dlg = QDialog(self)
        dlg.setWindowTitle(APP_NAME)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(22, 20, 22, 18)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(make_icon(icon, 30, tc("#6c8cff")).pixmap(30, 30))
        head.addWidget(ic, alignment=Qt.AlignTop)
        col = QVBoxLayout()
        t = QLabel(title)
        t.setObjectName("nowTitle")
        col.addWidget(t)
        sub = QLabel(text)
        sub.setObjectName("nowInfo")
        col.addWidget(sub)
        head.addSpacing(10)
        head.addLayout(col, 1)
        v.addLayout(head)
        v.addSpacing(10)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Annuler")
        ok = QPushButton(ok_text)
        ok.setObjectName("primary")
        for b in (cancel, ok):
            b.setAutoDefault(False)
            row.addWidget(b)
        cancel.setDefault(True)
        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        v.addLayout(row)
        dlg.setStyleSheet(theme.css("QDialog { background: #1b1e24; }"))
        cancel.setFocus()
        return dlg.exec() == QDialog.Accepted

    def remove_playlist(self, folder):
        rows = library.playlist_tracks(self.conn, folder)
        name = self._display_name(folder)
        n = len(rows)
        if not self._ask(f"Retirer « {name} » de MixApp ?",
                         f"{n} morceau{'x' if n > 1 else ''} · leurs fichiers restent sur ton ordinateur.\n"
                         "La playlist n'apparaîtra plus, même si le dossier est toujours là.\n"
                         "Pour la retrouver : Dossiers de musique… › Playlists retirées.",
                         "Retirer la playlist"):
            return
        rem = self.settings.setdefault("removed", [])
        if folder not in rem:
            rem.append(folder)
        self._forget(folder, recursive=False)
        self._restart_watch()
        self._after_forget([folder])
        self.start_library_tasks()
        self.status.setText(f"« {name} » retirée de MixApp (fichiers intacts)")

    def restore_playlist(self, folder):
        rem = self.settings.setdefault("removed", [])
        if folder in rem:
            rem.remove(folder)
        save_settings(self.settings)
        self._restart_watch()
        self.start_library_tasks()

    def remove_root(self, root, ask=True):
        if ask and not self._ask(f"Retirer le dossier « {Path(root).name} » ?",
                                 "MixApp oublie ce dossier et toutes ses playlists.\n"
                                 "Aucun fichier n'est supprimé de ton ordinateur ;\n"
                                 "tu pourras l'ajouter à nouveau plus tard.",
                                 "Retirer le dossier"):
            return False
        roots = self.settings.setdefault("roots", [])
        if root in roots:
            roots.remove(root)
        self.settings["removed"] = [f for f in self.settings.get("removed", [])
                                    if not (Path(root) == Path(f) or Path(root) in Path(f).parents)]
        self._forget(root, recursive=True)
        self._restart_watch()
        self._after_forget([root])
        self.start_library_tasks()
        return True

    def _root_stats(self, root):
        rows = self.conn.execute("SELECT folder FROM tracks").fetchall()
        r = Path(root)
        folders = [Path(x[0]) for x in rows if Path(x[0]) == r or r in Path(x[0]).parents]
        return len(set(folders)), len(folders)

    def show_folders(self):
        """Fenêtre « Dossiers de musique » : ajouter, retirer, oublier les
        dossiers introuvables, remettre une playlist retirée."""
        from PySide6.QtWidgets import QDialog, QScrollArea
        dlg = QDialog(self)
        dlg.setWindowTitle("Dossiers de musique")
        dlg.setMinimumWidth(640)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(10)
        title = QLabel("Dossiers de musique")
        title.setObjectName("nowTitle")
        outer.addWidget(title)
        sub = QLabel("Chaque sous-dossier contenant des MP3 devient une playlist. "
                     "Retirer un dossier ne supprime aucun fichier : MixApp l'oublie, c'est tout.")
        sub.setWordWrap(True)
        sub.setObjectName("nowInfo")
        outer.addWidget(sub)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("fbody")
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 4, 0, 4)
        v.setSpacing(10)
        area.setWidget(body)
        outer.addWidget(area, 1)
        bottom = QHBoxLayout()
        add = QPushButton("Ajouter un dossier…")
        clean = QPushButton()
        close = QPushButton("Fermer")
        close.setObjectName("primary")
        for b in (add, clean, close):
            b.setAutoDefault(False)
        close.setDefault(True)
        bottom.addWidget(add)
        bottom.addStretch(1)
        bottom.addWidget(clean)
        bottom.addWidget(close)
        outer.addLayout(bottom)

        def short(p):
            home = str(Path.home())
            return "~" + p[len(home):] if p.startswith(home) else p

        def why(p):
            pp = Path(p)
            on_drive = (sys.platform == "darwin" and str(pp).startswith("/Volumes/")) or \
                       (sys.platform.startswith("win") and pp.drive and not Path(pp.drive + "\\").exists())
            return "disque débranché ?" if on_drive else "dossier supprimé ou déplacé"

        def build():
            while v.count():
                it = v.takeAt(0)
                wdg = it.widget()
                if wdg is not None:
                    wdg.hide()
                    wdg.setParent(None)
                    wdg.deleteLater()
            roots = list(self.settings.get("roots", []))
            missing = [r for r in roots if not Path(r).is_dir()]
            for r in roots:
                lost = r in missing
                fr = QFrame()
                fr.setObjectName("frowLost" if lost else "frow")
                h = QHBoxLayout(fr)
                h.setContentsMargins(14, 10, 10, 10)
                col = QVBoxLayout()
                col.setSpacing(1)
                t = QLabel(("⚠  " if lost else "") + (Path(r).name or r))
                t.setObjectName("fTitle")
                col.addWidget(t)
                pl = QLabel(short(r))
                pl.setObjectName("fPath")
                col.addWidget(pl)
                npl, ntr = self._root_stats(r)
                info = f"{npl} playlist{'s' if npl > 1 else ''} · {ntr} morceau{'x' if ntr > 1 else ''}"
                il = QLabel(f"Introuvable ({why(r)}) · {info}" if lost else info)
                il.setObjectName("fWarn" if lost else "fInfo")
                col.addWidget(il)
                h.addLayout(col, 1)
                b = QPushButton("Retirer")
                b.setAutoDefault(False)
                b.clicked.connect(lambda _=False, r=r: (self.remove_root(r), build()))
                h.addWidget(b, alignment=Qt.AlignVCenter)
                v.addWidget(fr)
            if not roots:
                e = QLabel("Aucun dossier pour l'instant : clique sur « Ajouter un dossier… ».")
                e.setObjectName("fInfo")
                v.addWidget(e)
            rem = [f for f in self.settings.get("removed", [])]
            if rem:
                sec = QLabel("PLAYLISTS RETIRÉES")
                sec.setObjectName("fSec")
                v.addSpacing(4)
                v.addWidget(sec)
                for f in rem:
                    fr = QFrame()
                    fr.setObjectName("frow")
                    h = QHBoxLayout(fr)
                    h.setContentsMargins(14, 6, 10, 6)
                    lb = QLabel(f"{self._display_name(f)}  <span style='color:{tc('#8a90a0')}'>· {short(f)}</span>")
                    h.addWidget(lb, 1)
                    b = QPushButton("Remettre")
                    b.setAutoDefault(False)
                    b.clicked.connect(lambda _=False, f=f: (self.restore_playlist(f), build()))
                    h.addWidget(b)
                    v.addWidget(fr)
            v.addStretch(1)
            clean.setText(f"Oublier les dossiers introuvables ({len(missing)})")
            clean.setVisible(bool(missing))
            body.adjustSize()
            area.setMinimumHeight(min(440, body.sizeHint().height() + 8))

        def forget_missing():
            missing = [r for r in self.settings.get("roots", []) if not Path(r).is_dir()]
            n = len(missing)
            if not missing or not self._ask(
                    f"Oublier {n} dossier{'s' if n > 1 else ''} introuvable{'s' if n > 1 else ''} ?",
                    "Leurs playlists disparaissent de MixApp.\n"
                    "Si c'est un disque débranché, rien n'est perdu : rebranche-le\n"
                    "et ajoute le dossier à nouveau.", "Oublier"):
                return
            for r in missing:
                self.remove_root(r, ask=False)
            build()

        add.clicked.connect(lambda: (self.choose_folders(), build()))
        clean.clicked.connect(forget_missing)
        close.clicked.connect(dlg.accept)
        dlg.setStyleSheet(theme.css("""
            QDialog { background: #1b1e24; }
            QWidget#fbody, QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
            QFrame#frow, QFrame#frowLost { background: #1f2229; border: 1px solid #353a46; border-radius: 10px; }
            QFrame#frowLost { border-color: #e0b341; }
            QFrame#frow QLabel, QFrame#frowLost QLabel { background: transparent; border: none; }
            QLabel#fTitle { font-weight: 600; }
            QLabel#fPath, QLabel#fInfo { color: #8a90a0; font-size: 12px; }
            QLabel#fWarn { color: #e0b341; font-size: 12px; }
            QLabel#fSec { color: #8a90a0; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
        """))
        build()
        self._folders_dialog = dlg
        dlg.exec()
        self._folders_dialog = None

    def _playlist_menu(self, pos):
        it = self.playlist_list.itemAt(pos)
        if it is None:
            return
        folder = it.data(Qt.UserRole)
        menu = QMenu(self)
        a_play = menu.addAction("Lire cette playlist")
        a_show = menu.addAction("Afficher dans le Finder" if sys.platform == "darwin"
                                else "Afficher dans l'Explorateur")
        menu.addSeparator()
        a_rem = menu.addAction("Retirer de MixApp…")
        a_show.setEnabled(Path(folder).is_dir())
        act = menu.exec(self.playlist_list.viewport().mapToGlobal(pos))
        if act is a_play:
            self.playlist_list.setCurrentItem(it)
            self.table.clearSelection()
            self.play_from_selection()
        elif act is a_show:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        elif act is a_rem:
            self.remove_playlist(folder)

    def eventFilter(self, obj, ev):
        # Espace / → dans les listes : ce sont les touches du lecteur (sinon la
        # liste les « mange » pour sa propre navigation, surtout sous Windows)
        if ev.type() == ev.Type.KeyPress and obj in (self.table, self.queue_list, self.playlist_list) \
                and not ev.modifiers():
            if ev.key() == Qt.Key_Space:
                self.play_pause()
                return True
            if ev.key() == Qt.Key_Right:
                self.skip()
                return True
            if obj is self.queue_list and ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
                self.remove_from_queue()
                return True
        if obj is self.playlist_list.viewport():
            t = ev.type()
            if t in (ev.Type.DragEnter, ev.Type.DragMove):
                md = ev.mimeData()
                if md.hasUrls() and any(Path(u.toLocalFile()).is_dir() for u in md.urls()):
                    ev.acceptProposedAction()
                    self.playlist_list.setStyleSheet("QListWidget { border: 2px dashed #6c8cff; }")
                    return True
            elif t == ev.Type.DragLeave:
                self.playlist_list.setStyleSheet("")
            elif t == ev.Type.Drop:
                self.playlist_list.setStyleSheet("")
                dirs = [u.toLocalFile() for u in ev.mimeData().urls()
                        if Path(u.toLocalFile()).is_dir()]
                if dirs:
                    ev.acceptProposedAction()
                    self.add_folders(dirs)
                    return True
        return super().eventFilter(obj, ev)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def start_library_tasks(self):
        """Scan + analyse en arrière-plan, puis surveillance des dossiers."""
        roots = [r for r in self.settings.get("roots", []) if Path(r).is_dir()]
        if not roots:
            return
        self._run_scan_and_analysis(roots)
        if self.observer is None:
            try:
                self.observer = library.watch(lambda: db.connect(self.db_path), roots,
                                              on_change=lambda res: self._after_watch_change(),
                                              exclude=self._removed)
            except Exception as e:  # watchdog absent ou dossier inaccessible
                self.bus.log.emit(f"Surveillance des dossiers indisponible : {e}")

    def _after_watch_change(self):
        # appelé depuis le fil de surveillance : le scan est fait, on analyse le nouveau
        roots = [r for r in self.settings.get("roots", []) if Path(r).is_dir()]
        self._run_scan_and_analysis(roots)

    def _run_scan_and_analysis(self, roots):
        if self._analysis_running:
            self._analysis_again = True
            return
        self._analysis_running = True

        def work():
            try:
                while True:
                    self._analysis_again = False
                    conn = db.connect(self.db_path)
                    self.bus.log.emit("Lecture des dossiers…")
                    library.scan(conn, roots, self._removed())
                    self.bus.library_changed.emit()
                    paths = [r[0] for r in conn.execute("SELECT path FROM tracks")]
                    self.logos = covers.logo_hashes(paths)
                    analysis.analyze_library(
                        conn, progress=lambda d, t: self.bus.analysis_progress.emit(d, t))
                    conn.close()
                    self.bus.library_changed.emit()
                    if not self._analysis_again:
                        break
            except Exception as e:
                self.bus.log.emit(f"⚠ {e}")
            finally:
                self._analysis_running = False
                self.bus.analysis_progress.emit(0, 0)
        threading.Thread(target=work, daemon=True).start()

    def _on_analysis_progress(self, done, total):
        if total == 0:
            n = self.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            self.status.setText(f"{n} morceaux · bibliothèque à jour")
        else:
            self.status.setText(f"Analyse des morceaux : {done}/{total}")
        if total and done % 5 == 0:
            self._on_library_changed()

    def _on_library_changed(self):
        self._dupes_timer.start()
        self.refresh_playlists(keep=True)
        if self.mode == "search":
            self.run_search()
        elif self.current_folder:
            self.show_playlist(self.current_folder, keep_selection=True)

    def _on_log(self, msg):
        self.status.setText(msg)

    # ---------------------------------------------------------------- jaquettes

    def _placeholder(self, size: int) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, size * 0.12, size * 0.12)
        p.fillPath(path, QColor(tc("#2a2e38")))
        p.setPen(QColor(tc("#5c6375")))
        f = QFont()
        f.setPixelSize(int(size * 0.45))
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, "♪")
        p.end()
        return pm

    def _rounded(self, data: bytes, size: int) -> QPixmap | None:
        src = QPixmap()
        if not src.loadFromData(data):
            return None
        src = src.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, size * 0.12, size * 0.12)
        p.setClipPath(path)
        p.drawPixmap((size - src.width()) // 2, (size - src.height()) // 2, src)
        p.end()
        return pm

    def want_cover(self, row: dict, urgent: bool = False):
        """Demande (sans bloquer) la jaquette d'un morceau."""
        if row["id"] in self.cover_pix:
            return
        with self._cover_lock:
            if urgent:
                self._cover_urgent.append(row)
            else:
                self._cover_wanted.setdefault(row["id"], row)
        self._cover_event.set()

    def _cover_worker(self):
        done = set()
        while True:
            self._cover_event.wait()
            with self._cover_lock:
                if self._cover_urgent:
                    r = self._cover_urgent.pop()
                elif self._cover_wanted:
                    r = self._cover_wanted.pop(next(iter(self._cover_wanted)))
                else:
                    self._cover_event.clear()
                    continue
            if r["id"] in done:
                continue
            try:
                data = covers.get_cover(r, self.logos,
                                        online=self.settings.get("covers_online", True))
            except Exception:
                data = None
            done.add(r["id"])
            self.bus.cover_ready.emit(r["id"], data)

    # ------------------------------------------------------------ « le saviez-vous ? »
    def _show_info(self, r):
        key = self._hl_key(r)
        self._info_key = key
        e = self.info_cache.get(key)
        if e is not None:
            self._render_info(r, e)
            return
        self._render_info(r, {})          # tout de suite : la fiche du morceau (jamais vide)
        if not self.settings.get("covers_online", True):
            return

        def work(row=dict(r), k=key):
            from . import trackinfo
            try:
                info = trackinfo.lookup(row.get("artist") or "", row.get("title") or "")
            except Exception:
                info = None
            if info is not None:
                self.info_cache.put(k, info)
                self.bus.info_ready.emit(k, (row, info))
        threading.Thread(target=work, daemon=True).start()

    def _on_info_ready(self, key, payload):
        if key == getattr(self, "_info_key", None):
            self._render_info(*payload)

    def _render_info(self, r, e):
        """« Le saviez-vous ? » : jamais vide, jamais inventé. Dans l'ordre :
        anecdote Wikipédia sur le morceau, sinon sur l'artiste, sinon la fiche
        du morceau tirée de l'analyse de MixApp."""
        import html
        from . import trackinfo
        src_css = f'style="color:{tc("#6f7686")}; font-size:11px;"'
        link = lambda: (f'<a style="color:{tc("#6c8cff")};" href="{e.get("url", "")}">'
                        f'{html.escape(e.get("source", "Wikipédia"))}</a>')
        parts = [f'<span style="color:{tc("#e0b341")}; font-size:11px; font-weight:700; letter-spacing:1px;">'
                 'LE SAVIEZ-VOUS ?</span>']
        if e.get("year"):
            parts.append(f"<b>{html.escape(r.get('artist') or '')}</b> · sorti en {e['year']}")
        fact = e.get("fact") or e.get("artist_fact")
        if fact:
            if len(fact) > 230:
                fact = fact[:227].rsplit(" ", 1)[0] + "…"
            lead = "" if e.get("fact") else "Sur l'artiste : "
            parts.append(f"<i>{lead}{html.escape(fact)}</i> {link()}")
        else:
            rows = list(self.engine.auto) if self.engine is not None else list(self.shown_rows)
            base = r.get("_base") or r
            live = next((x for x in rows if x.get("id") == base.get("id")), base)
            txt = trackinfo.fiche(live, rows)
            if txt is None:
                d = live.get("duration") or 0
                folder = self._display_name(Path(live.get("folder") or ".").name) if live.get("folder") else ""
                txt = (f"Morceau de la playlist {folder}" if folder else "Morceau") + \
                       (f", durée {int(d // 60)}:{int(d % 60):02d}" if d else "") + \
                       ". Analyse du tempo et de la clé en cours…"
            parts.append(html.escape(txt))
            parts.append(f'<span {src_css}>Source : analyse du morceau par MixApp</span>')
        self.fact_label.setText("<br>".join(parts))

    def _generated(self, row: dict, size: int) -> QPixmap:
        """Pochette créée par l'appli (en attendant la vraie, ou si elle n'existe pas)."""
        cache = self.__dict__.setdefault("_gen_pix", {})
        k = (row["id"], size)
        pm = cache.get(k)
        if pm is None:
            pm = cache[k] = artwork.generate(row.get("title") or row.get("filename") or "",
                                             row.get("artist") or "", size, radius=0.12)
        return pm

    def _row_of(self, track_id):
        for r in self.shown_rows:
            if r["id"] == track_id:
                return r
        t = self.engine.cache.get(track_id) if self.engine else None
        return t.row if t is not None else None

    def _small_placeholder(self):
        if not hasattr(self, "_small_ph"):
            self._small_ph = self._placeholder(52)
        return self._small_ph

    def _on_cover_ready(self, track_id, data):
        full = self.__dict__.setdefault("cover_full", {})
        if data:                                  # pleine définition (vue Platines)
            big = QPixmap()
            if big.loadFromData(data) and not big.isNull():
                if max(big.width(), big.height()) > 900:
                    big = big.scaled(900, 900, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                full[track_id] = big
        pm = self._rounded(data, 168) if data else None
        if pm is None:                            # aucune image : pochette créée par l'appli
            row = self._row_of(track_id) or {"id": track_id, "title": "", "artist": ""}
            pm = self._generated(row, 168)
        self.cover_pix[track_id] = pm
        self._queue_sig = None            # forcer le rafraîchissement des icônes
        self._show_current_cover()
        for i, r in enumerate(self.shown_rows):      # vignette dans la liste des morceaux
            if r["id"] == track_id:
                it = self.table.item(i, 1)
                if it is not None:
                    it.setIcon(QIcon(self.cover_pix[track_id]))

    def _show_current_cover(self):
        t = self.engine.now_playing()[0] if self.engine else None
        if t is None:
            self.cover.setPixmap(self._placeholder(84))
            return
        pm = self.cover_pix.get(t.id)
        if pm is None:
            self.want_cover(t.row, urgent=True)
            self.cover.setPixmap(self._generated(t.row, 84))
        else:
            self.cover.setPixmap(pm.scaled(84, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ---------------------------------------------------------------- playlists / morceaux

    def _display_name(self, folder: str) -> str:
        return self._raw_display_name(folder).replace("_", " ")

    def _raw_display_name(self, folder: str) -> str:
        for r in self.settings.get("roots", []):
            try:
                rel = Path(folder).relative_to(r)
                parts = rel.parts
                if parts:
                    return " › ".join(parts[-2:]) if len(parts) > 1 else parts[0]
                return Path(folder).name
            except ValueError:
                continue
        return Path(folder).name

    def refresh_playlists(self, keep=False):
        cur = self.current_folder
        self.playlist_list.blockSignals(True)
        self.playlist_list.clear()
        for pl in library.playlists(self.conn):
            it = QListWidgetItem(f"{self._display_name(pl['folder'])}")
            it.setToolTip(f"{pl['count']} morceaux · {_fmt(pl['duration'])} · "
                          f"{pl['analyzed']}/{pl['count']} analysés")
            it.setData(Qt.UserRole, pl["folder"])
            self.playlist_list.addItem(it)
            if keep and pl["folder"] == cur:
                self.playlist_list.setCurrentItem(it)
        self.playlist_list.blockSignals(False)

    def _on_playlist_selected(self, item, _prev=None):
        if item is None:
            return
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self.show_playlist(item.data(Qt.UserRole))

    def show_playlist(self, folder, keep_selection=False):
        self.mode = "playlist"
        self.current_folder = folder
        rows = self._visible_rows(folder)
        if self.btn_dj.isChecked() and rows:
            from . import ordering
            rows = ordering.dj_order(rows, rows[0]["id"])
        self._fill_table(rows, keep_selection)
        self._update_playlist_header()
        self.want_hl(rows)                 # calcule les passages en fond (pour les durées)

    # ------------------------------------------------------------ doublons

    def _visible_rows(self, folder):
        """Morceaux d'une playlist, sans ceux masqués comme doublons."""
        hidden = set(self.settings.get("hidden", {}).get(folder, []))
        return [dict(r) for r in library.playlist_tracks(self.conn, folder) if r["path"] not in hidden]

    def check_dupes(self):
        """Cherche les doublons ; masque d'office les copies identiques ; affiche
        le lien « doublons à vérifier » s'il reste des choix à faire."""
        from . import dupes
        try:
            rows = [dict(r) for r in self.conn.execute("SELECT * FROM tracks")]
        except Exception:
            return
        hidden = self.settings.setdefault("hidden", {})
        done = set(self.settings.get("dupes_done", []))
        res = dupes.analyse(rows, hidden, done)
        changed = False
        for a in res["auto"]:
            lst = hidden.setdefault(a["folder"], [])
            for r in a["hide"]:
                if r["path"] not in lst:
                    lst.append(r["path"])
                    changed = True
        if changed:
            save_settings(self.settings)
            if self.current_folder and self.mode == "playlist":
                self.show_playlist(self.current_folder, keep_selection=True)
        self._dupes = res
        todo = len(res["versions"]) + len(res["cross"])
        if todo:
            self.dupes_btn.setText(f"⚠  {todo} doublon{'s' if todo > 1 else ''} à vérifier")
            self.dupes_btn.setStyleSheet("color:#e0a84a; font-weight:600;")
            self.dupes_btn.show()
        elif res["auto"]:
            n = sum(len(a["hide"]) for a in res["auto"])
            self.dupes_btn.setText(f"{n} doublon{'s' if n > 1 else ''} masqué{'s' if n > 1 else ''}")
            self.dupes_btn.setStyleSheet("")
            self.dupes_btn.show()
        else:
            self.dupes_btn.hide()

    def show_dupes(self):
        from PySide6.QtWidgets import QButtonGroup, QCheckBox, QDialog, QRadioButton, QScrollArea
        from . import dupes
        res = getattr(self, "_dupes", None)
        if res is None:
            self.check_dupes()
            res = self._dupes
        dlg = QDialog(self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setModal(True)
        dlg.setStyleSheet(theme.css("""
            QDialog { background:#15171c; }
            QFrame#card { background:#1f2229; border-radius:10px; }
            QFrame#card QLabel, QRadioButton, QCheckBox { background: transparent; }
            QLabel#h { font-size:15px; font-weight:700; color:#f2f2f2; }
            QLabel#tag { font-size:11px; font-weight:700; letter-spacing:1px; }
            QLabel#sub { color:#9aa3b5; }
            QLabel#hint { color:#e0a84a; font-size:12px; }
            QRadioButton, QCheckBox { spacing:8px; padding:3px 0; color:#e6e6e6; }
            QRadioButton::indicator, QCheckBox::indicator { width:14px; height:14px;
                border:2px solid #6f7686; background:#15171c; }
            QRadioButton::indicator { border-radius:9px; }
            QCheckBox::indicator { border-radius:4px; }
            QRadioButton::indicator:checked, QCheckBox::indicator:checked {
                background:#6c8cff; border-color:#6c8cff; }"""))
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(24, 22, 24, 20)
        t = QLabel("Doublons trouvés dans tes playlists")
        t.setObjectName("nowTitle")
        outer.addWidget(t)
        sub = QLabel("Rien n'est supprimé de ton ordinateur : l'appli masque seulement les morceaux "
                     "en double.\nTu peux tout réafficher plus tard.")
        sub.setObjectName("sub")
        outer.addWidget(sub)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 6, 8, 6)
        v.setSpacing(12)
        area.setWidget(inner)
        outer.addWidget(area, 1)
        name = lambda f: self._display_name(Path(f).name)
        short = lambda t: re.sub(r"\s*[\(\[].*", "", t or "").strip() or (t or "")
        dur = lambda r: _fmt(r.get("duration") or 0)

        def card(tag, color, title, where):
            f = QFrame()
            f.setObjectName("card")
            l = QVBoxLayout(f)
            l.setContentsMargins(16, 12, 16, 12)
            l.setSpacing(4)
            for txt, obj, css in ((tag, "tag", f"color:{color};"), (title, "h", ""), (where, "sub", "")):
                lab = QLabel(txt)
                lab.setObjectName(obj)
                if css:
                    lab.setStyleSheet(css)
                lab.setWordWrap(True)
                l.addWidget(lab)
            v.addWidget(f)
            return l

        def hint(layout, widget, txt):
            row = QHBoxLayout()
            row.addWidget(widget)
            if txt:
                h = QLabel("★ " + txt)
                h.setObjectName("hint")
                row.addWidget(h)
            row.addStretch(1)
            layout.addLayout(row)

        actions = []          # fonctions appelées sur « Appliquer mes choix »
        unhide = []
        for a in res["auto"]:
            k = a["keep"]
            l = card("COPIES IDENTIQUES · MASQUÉES AUTOMATIQUEMENT", "#7f8aa3",
                     (k.get("title") or k.get("filename") or "") + " — " + (k.get("artist") or ""),
                     f"{name(a['folder'])} · {len(a['hide']) + 1} fichiers identiques")
            row = QHBoxLayout()
            kb = dupes.bitrate_kbps(k)
            row.addWidget(QLabel("On garde la meilleure copie : " + (f"{kb} kbps, " if kb else "") + dur(k)))
            row.addStretch(1)
            b = QPushButton("Réafficher l'autre" if len(a["hide"]) == 1 else "Réafficher les autres")
            b.setCheckable(True)
            b.toggled.connect(lambda on, b=b: b.setText("Sera réaffiché" if on else "Réafficher"))
            row.addWidget(b)
            l.addLayout(row)
            unhide.append((a, b))
        for g in res["versions"]:
            r0 = g["suggest"]
            l = card("PLUSIEURS VERSIONS · À TOI DE CHOISIR", "#f0a43a",
                     short(r0.get("title")) + " — " + (r0.get("artist") or ""),
                     f"{name(g['folder'])} · {len(g['rows'])} versions")
            grp = QButtonGroup(dlg)
            opts = []
            for r in sorted(g["rows"], key=lambda r: r is not g["suggest"]):
                rb = QRadioButton(f"{dupes.version_name(r.get('title') or '')} · {dur(r)}")
                rb.setChecked(r is g["suggest"])
                grp.addButton(rb)
                hint(l, rb, g["reason"] if r is g["suggest"] else "")
                opts.append((rb, r))
            both = QRadioButton("Garder toutes les versions" if len(g["rows"]) > 2 else "Garder les deux")
            grp.addButton(both)
            hint(l, both, "")

            def act(g=g, opts=opts, both=both):
                if both.isChecked():
                    return
                keep = next(r for rb, r in opts if rb.isChecked())
                lst = self.settings.setdefault("hidden", {}).setdefault(g["folder"], [])
                for rb, r in opts:
                    if r is not keep and r["path"] not in lst:
                        lst.append(r["path"])
            actions.append((g["key"], act))
        for c in res["cross"]:
            l = card("DANS PLUSIEURS PLAYLISTS · À VALIDER", "#5aa9ff",
                     short(c["title"]) + " — " + (c["artist"] or ""),
                     f"présent dans {len(c['folders'])} playlists · coche celles où il reste")
            boxes = []
            for f in sorted(c["folders"], key=lambda f: f != c["suggest"]):
                cb = QCheckBox(name(f))
                cb.setChecked(f == c["suggest"])
                hint(l, cb, c["reason"] if f == c["suggest"] else "")
                boxes.append((cb, f))

            def act(c=c, boxes=boxes):
                if not any(cb.isChecked() for cb, _ in boxes):
                    return                                    # tout décoché : on ne masque rien
                for cb, f in boxes:
                    if not cb.isChecked():
                        lst = self.settings.setdefault("hidden", {}).setdefault(f, [])
                        for r in c["folders"][f]:
                            if r["path"] not in lst:
                                lst.append(r["path"])
            actions.append((c["key"], act))
        if not (res["auto"] or res["versions"] or res["cross"]):
            v.addWidget(QLabel("Aucun doublon dans tes playlists."))
        v.addStretch(1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        later = QPushButton("Plus tard")
        ok = QPushButton("Appliquer mes choix")
        ok.setObjectName("primary")
        later.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        btns.addWidget(later)
        btns.addWidget(ok)
        outer.addSpacing(6)
        outer.addLayout(btns)
        dlg.resize(640, min(760, 220 + 150 * (len(res["auto"]) + len(res["versions"]) + len(res["cross"]))))
        self._dupes_dialog = dlg
        accepted = dlg.exec() == QDialog.Accepted
        self._dupes_dialog = None
        if not accepted:
            return
        done = self.settings.setdefault("dupes_done", [])
        for key, act in actions:
            act()
            if key not in done:
                done.append(key)
        for a, b in unhide:
            if b.isChecked():
                lst = self.settings.setdefault("hidden", {}).get(a["folder"], [])
                for r in a["hide"]:
                    if r["path"] in lst:
                        lst.remove(r["path"])
                if a["key"] not in done:
                    done.append(a["key"])
        save_settings(self.settings)
        self.check_dupes()
        if self.current_folder and self.mode == "playlist":
            self.show_playlist(self.current_folder, keep_selection=True)
        self.status.setText("Doublons mis à jour (aucun fichier supprimé)")

    def _update_playlist_header(self):
        if self.mode != "playlist" or not self.current_folder:
            return
        rows = self.shown_rows
        mode = self._hl_mode()
        if mode:
            total = self.mix_duration(rows, mode)
            extra = " EN TEMPS FORTS"
        else:
            total = sum(effective_duration(r, i == len(rows) - 1) for i, r in enumerate(rows))
            extra = " EN MIX"
        self.tracks_title.setText(f"PLAYLIST · {self._display_name(self.current_folder).upper()} · "
                                  f"{len(rows)} MORCEAUX · {_fmt_long(total)}{extra}")

    def _on_search_changed(self, _text):
        self._search_timer.start()

    def run_search(self):
        q = self.search_box.text().strip()
        if not q:
            if self.current_folder:
                self.show_playlist(self.current_folder)
            else:
                self._fill_table([])
            return
        self.mode = "search"
        rows = [dict(r) for r in search.search(self.conn, q, limit=200)]
        self.tracks_title.setText(f"RECHERCHE · {len(rows)} RÉSULTAT{'S' if len(rows) > 1 else ''}")
        self._fill_table(rows)

    def _current_track_row(self):
        if self.engine is None:
            return None
        t, _ = self.engine.now_playing()
        return t.row if t else None

    def _fill_table(self, rows, keep_selection=False):
        sel_ids = {r["id"] for r in self._selected_rows()} if keep_selection else set()
        self.shown_rows = rows
        cur = self._current_track_row()
        cur_id = cur["id"] if cur else None
        bold = QFont()
        bold.setBold(True)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            is_cur = r["id"] == cur_id
            played = r["id"] in self.played_ids and not is_cur
            comp = search.compatibility(cur, r) if (cur and not is_cur) else "inconnu"
            dot = QTableWidgetItem("▶" if is_cur else "●")
            dot.setForeground(QColor("#3ecf6e" if is_cur else COMPAT_COLORS[comp]))
            dot.setTextAlignment(Qt.AlignCenter)
            dot.setToolTip({"vert": "S'enchaîne parfaitement avec le morceau en cours",
                            "orange": "S'enchaîne avec un tempo un peu différent",
                            "rouge": "Tempo trop éloigné : enchaînement en écho",
                            "inconnu": ""}[comp])
            cells = [dot,
                     QTableWidgetItem(r.get("title") or r["filename"]),
                     QTableWidgetItem(r.get("artist") or ""),
                     QTableWidgetItem(f"{r['bpm']:.0f}" if r.get("bpm") else "…"),
                     QTableWidgetItem(f"{r['camelot']}  {r['key_name']}" if r.get("camelot") else ""),
                     QTableWidgetItem(_fmt(r.get("duration"))),
                     QTableWidgetItem(str(r["id"]))]
            # en cours : blanc et gras · déjà joué : grisé · pas encore joué : bleu
            if is_cur:
                title_col, artist_col, info_col = "#ffffff", "#ffffff", "#e6e6e6"
            elif played:
                title_col, artist_col, info_col = "#646a78", "#585e6b", "#555b67"
            else:
                title_col, artist_col, info_col = "#8fb3ff", "#7891c9", "#9aa3b5"
            cells[1].setForeground(QColor(tc(title_col)))
            cells[2].setForeground(QColor(tc(artist_col)))
            for c in (3, 4, 5):
                cells[c].setForeground(QColor(tc(info_col)))
            if is_cur:
                for c in range(1, 6):
                    cells[c].setFont(bold)
            pm = self.cover_pix.get(r["id"])
            if pm is None:
                self.want_cover(r)                 # chargée au fil de l'eau (internet)
                pm = self._generated(r, 52)
            cells[1].setIcon(QIcon(pm))
            for c, it in enumerate(cells):
                self.table.setItem(i, c, it)
            if r["id"] in sel_ids:
                self.table.selectionModel().select(
                    self.table.model().index(i, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self._update_buttons()

    def _selected_rows(self):
        """Morceaux sélectionnés dans la liste, dans l'ordre de la liste."""
        sm = self.table.selectionModel()
        idx = sorted({i.row() for i in sm.selectedRows()}) if sm else []
        return [self.shown_rows[i] for i in idx if i < len(self.shown_rows)]

    def _selected_row(self):
        idx = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not idx:
            return None
        i = idx[0].row()
        return self.shown_rows[i] if i < len(self.shown_rows) else None

    def _update_buttons(self):
        has_sel = self._selected_row() is not None
        playing = self.engine is not None
        self.btn_play_here.setEnabled(has_sel or (self.mode == "playlist" and bool(self.shown_rows)))
        self.btn_play_next.setEnabled(has_sel and playing)
        self.btn_enqueue.setEnabled(has_sel and playing)
        self.btn_next.setEnabled(playing)
        self.btn_stop33.setEnabled(playing)
        self.btn_pullup.setEnabled(playing)
        self._update_play_icon()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._set_compact(self._want_compact())

    def showEvent(self, e):
        super().showEvent(e)
        self._set_compact(self._want_compact())

    def _want_compact(self) -> bool:
        scr = self.screen()
        small_screen = (scr is not None and scr.availableGeometry().width() < 1600
                        and not os.environ.get("MIXAPP_TOUR"))      # tutoriels : boutons avec leur nom
        return self.width() < 1460 or small_screen

    def _set_compact(self, on: bool):
        """Fenêtre étroite (portable) : boutons du lecteur en icônes seules
        (leur nom reste en info-bulle), égaliseur masqué."""
        if getattr(self, "_compact", None) == on:
            return
        self._compact = on
        full = {self.btn_stop33: "  Stop 33 tours", self.btn_pullup: "  Stop pull-up",
                self.btn_next: "  Suivant", self.btn_hl: "★ Temps forts", self.btn_dj: "⇅ Flow DJ"}
        short = {self.btn_stop33: "", self.btn_pullup: "", self.btn_next: "",
                 self.btn_hl: "★", self.btn_dj: "⇅"}
        for b, t in full.items():
            b.setText(short[b] if on else t)
            if on and not b.property("tip0"):
                b.setProperty("tip0", b.toolTip())
                b.setToolTip(t.strip() + "\n" + b.toolTip())
            elif not on and b.property("tip0"):
                b.setToolTip(b.property("tip0"))
                b.setProperty("tip0", None)
        self.eq.setVisible(not on)
        self._play_icon = None
        self._update_play_icon()

    def _update_play_icon(self):
        eng = self.engine
        running = eng is not None and not eng.paused and not eng.pausing
        want = "pause" if running else "play"
        if getattr(self, "_play_icon", None) != want:
            self._play_icon = want
            self.btn_play.setIcon(make_icon(want, 18))
            self.btn_play.setText("" if getattr(self, "_compact", False) else
                                  ("  Pause" if running else "  Lecture"))

    # ---------------------------------------------------------------- lecture

    def on_double_click(self):
        if self.engine is None:
            self.play_from_selection()
        else:
            self.queue_selected(front=True)

    def play_from_selection(self):
        sel = self._selected_row()
        if self.mode == "playlist" and self.current_folder:
            rows = self._visible_rows(self.current_folder)
            start = next((i for i, r in enumerate(rows) if sel and r["id"] == sel["id"]), 0)
        elif sel:
            # depuis une recherche : on lance la playlist (dossier) du morceau choisi
            rows = self._visible_rows(sel["folder"])
            start = next((i for i, r in enumerate(rows) if r["id"] == sel["id"]), 0)
        else:
            return
        if not rows:
            return
        self.stop()
        self.now_title.setText(rows[start].get("title") or rows[start]["filename"])
        self.now_info.setText("Préparation…")

        def on_analyzed(row):
            c = db.connect(self.db_path)
            c.execute("UPDATE tracks SET bpm=?, first_beat=?, key_name=?, camelot=?, mix_in=?, "
                      "intro_end=?, outro_start=?, energy=?, analyzed_mtime=mtime WHERE id=?",
                      (row["bpm"], row["first_beat"], row["key_name"], row["camelot"],
                       row["mix_in"], row["intro_end"], row["outro_start"], row["energy"],
                       row["id"]))
            c.commit()
            c.close()
            self.bus.library_changed.emit()

        def work():
            try:
                eng = Engine(log=self.bus.log.emit, on_analyzed=on_analyzed)
                eng.volume = self.volume.value() / 100
                eng.hl_mode = self._hl_mode()
                eng.dj_order = self.btn_dj.isChecked()
                eng.mix_seconds = self._mix_seconds()
                if self.vocals_ok:
                    eng.vocal_provider = self.vocal_map
                eng.load_playlist(rows, start)
                self.bus.playback_ready.emit(eng)
            except Exception as e:
                self.bus.playback_ready.emit(e)
        threading.Thread(target=work, daemon=True).start()

    def _on_playback_ready(self, eng):
        if isinstance(eng, Exception):
            QMessageBox.warning(self, APP_NAME, f"Lecture impossible :\n{eng}")
            self.now_info.setText("")
            return
        self.engine = eng
        try:
            self.output = AudioOutput(eng)
            self.output.start()
        except Exception as e:
            self.engine = None
            QMessageBox.warning(self, APP_NAME, f"Sortie audio impossible :\n{e}")
            return
        eng.start_worker()
        self.deck.set_engine(eng)
        self._update_buttons()
        self.refresh_player()
        self._refill_dots()

    # ------------------------------------------------------------ vue Platines et pads

    def _deck_cover(self, track):
        full = self.__dict__.get("cover_full", {}).get(track.id)
        if full is not None:
            return full
        if self.cover_pix.get(track.id) is None:
            self.want_cover(track.row)
        cache = self.__dict__.setdefault("_gen_big", {})
        pm = cache.get(track.id)
        if pm is None:                            # pochette créée, en grand et carrée (la vue arrondit)
            pm = cache[track.id] = artwork.generate(track.row.get("title") or track.row.get("filename") or "",
                                                    track.row.get("artist") or "", 600, radius=0.0)
        return pm

    def _on_view(self, on):
        if on:
            self._split_sizes = self.split.sizes()
            self.drawer_layout.addWidget(self.queue_panel)
        else:
            self.split.insertWidget(2, self.queue_panel)
            if getattr(self, "_split_sizes", None):
                self.split.setSizes(self._split_sizes)
        self.queue_panel.show()
        self.btn_view.setText("☰  Playlists" if on else "◎  Platines")
        self.btn_view.setToolTip("Revenir aux playlists et aux morceaux (V)" if on else
                                 "Vue Platines : pochettes, formes d'onde, platines, table de mixage\n"
                                 "et pads pour choisir la prochaine transition (V)")
        self.deck.drawer_btn.setChecked(self.settings.get("drawer", True))
        self.stack.setCurrentIndex(1 if on else 0)
        # les platines ont leur propre vumètre : le petit égaliseur se repose
        (self.eq.timer.stop if on else self.eq.timer.start)()
        if on:
            self.eq.levels[:] = 0
            self.eq.update()
        self.settings["view"] = "platines" if on else "liste"
        save_settings(self.settings)

    def _on_drawer(self, on):
        self.drawer.setVisible(on)
        self.settings["drawer"] = on
        save_settings(self.settings)

    def _on_anim_mode(self, mode):
        self.settings["animations"] = mode
        save_settings(self.settings)

    def _on_pad_style(self, style):
        names = {"auto": "Auto", "cale": "Mix calé", "echo": "Écho", "filtre": "Filtre",
                 "pullup": "Pull-up", "fondu": "Fondu"}
        if self.engine is None:
            self.deck.set_style_state(None)
            self.status.setText("Lance d'abord une playlist")
            return
        self.engine.set_next_style(style)
        self.status.setText("L'appli choisit les transitions" if style == "auto" else
                            f"Prochain enchaînement : {names[style]} (une fois, puis retour en Auto)")

    def _on_pad_action(self, k):
        if k == "stop33":
            self.btn_stop33.click()
        elif k == "pullup":
            self.btn_pullup.click()
        elif k == "pause":
            self.play_pause()
        elif k == "next":
            self.skip()
        elif k == "hl":
            self.btn_hl.click()
        elif k == "dj":
            self.btn_dj.click()

    def play_pause(self):
        eng = self.engine
        if eng is None:
            self.play_from_selection()
        elif eng.paused or eng.pausing:
            eng.resume()
        else:
            eng.pause()
        self._update_play_icon()

    @staticmethod
    def _hl_key(r):
        return f"{r['path']}|{r.get('mtime')}"

    def hl_known(self, r, mode):
        """Passage retenu (dict) / False si pas de passage / None si pas encore calculé."""
        e = self._hl_store.get(self._hl_key(r))
        if e is None:
            return None
        return e.get(mode) or False

    def want_hl(self, rows, urgent=False):
        """Demande le calcul (en fond) des passages « temps forts ». Les morceaux
        « À suivre » passent devant ; une même demande n'est jamais dupliquée."""
        if not self._hl_mode():
            return
        added = False
        with self._hl_lock:
            for r in rows[:3] if urgent else rows:
                k = self._hl_key(r)
                if not r.get("bpm") or k in self._hl_store:
                    continue
                if k in self._hl_queued:
                    if urgent:                         # remonter en tête de file
                        self._hl_queue = [x for x in self._hl_queue if self._hl_key(x) != k]
                        self._hl_queue.insert(0, r)
                    continue
                self._hl_queued.add(k)
                (self._hl_queue.insert(0, r) if urgent else self._hl_queue.append(r))
                added = True
        if added or urgent:
            self._hl_event.set()

    def _hl_worker(self):
        from . import highlights as HL
        from .transitions import load_track
        dirty = 0
        while True:
            self._hl_event.wait()
            with self._hl_lock:
                if not self._hl_queue:
                    self._hl_event.clear()
                    if dirty:
                        try:
                            self._hl_file.write_text(json.dumps(self._hl_store))
                        except Exception:
                            pass
                        dirty = 0
                    continue
                r = self._hl_queue.pop(0)
            k = self._hl_key(r)
            if k in self._hl_store:
                continue
            time.sleep(0.3)                          # laisser respirer l'interface entre deux morceaux
            try:
                tr = load_track(r)
                info = HL.find(tr)
                vocal = None
                if self.vocals_ok:
                    vocal = HL.vocal_bars(self.vocal_map(tr), tr)
                self._hl_store[k] = {"on": HL.window(tr, "on", info, vocal), "vocal": vocal is not None}
            except Exception:
                self._hl_store[k] = {}
            dirty += 1
            with self._hl_lock:
                self._hl_queued.discard(k)
            self.bus.hl_ready.emit()

    def mix_duration(self, rows, mode, first_index_plays_from_start=True):
        """Durée estimée d'une suite de morceaux dans le mix (s)."""
        total = 0.0
        for i, r in enumerate(rows):
            last = i == len(rows) - 1
            w = self.hl_known(r, mode) if mode else False
            if w:
                start = (float(r.get("first_beat") or 0) if (i == 0 and first_index_plays_from_start)
                         else w["entry"])
                end = float(r.get("duration") or 0) if last else w["exit"]
                total += max(0.0, end - start)
            elif w is None:                        # pas encore calculé : estimation
                total += min(effective_duration(r, last), 170.0)
            else:
                total += effective_duration(r, last)
        return total

    def _hl_mode(self):
        return "on" if self.btn_hl.isChecked() else None

    def vocal_map(self, track, compute=True):
        """Carte du chant d'un morceau (calculée une fois, puis gardée sur disque)."""
        from . import vocals as V
        r = track.row.get("_base") or track.row
        key = self._hl_key(r)
        a = self.vocal_cache.get(key)
        if a is None and not compute:
            return None
        if a is None:
            with self._vocal_lock:
                a = self.vocal_cache.get(key)
                if a is None:
                    a = V.activity(track.audio)
                    self.vocal_cache.put(key, a)
        return a

    def _on_dj(self):
        on = self.btn_dj.isChecked()
        self.settings["dj_order"] = on
        save_settings(self.settings)
        if self.engine is not None:
            threading.Thread(target=self.engine.set_dj_order, args=(on,), daemon=True).start()
        if self.mode == "playlist" and self.current_folder:
            self.show_playlist(self.current_folder, keep_selection=True)
        self._queue_sig = None
        self.status.setText("Flow DJ : enchaînements au tempo et à la tonalité" if on
                            else "Ordre du dossier")

    def _on_highlights(self):
        mode = self._hl_mode()
        self.settings["highlights"] = mode
        save_settings(self.settings)
        if self.engine is not None:
            threading.Thread(target=self.engine.set_highlights, args=(mode,), daemon=True).start()
        self._update_playlist_header()
        self.status.setText("Temps forts : dès le prochain enchaînement, sans couper les paroles"
                            if mode else "Temps forts désactivés : morceaux en entier")
        if mode:
            self.want_hl(self.shown_rows)

    def _on_seek(self, frac):
        eng = self.engine
        if eng is None:
            return
        t, _ = eng.now_playing()
        if t is None:
            return
        if not eng.seek(frac * t.length):
            self.status.setText("Impossible de se déplacer pendant un enchaînement")
        self.refresh_player()

    def styled_stop(self, style):
        if self.engine is None:
            return
        self.engine.stop_soft(style)
        self._update_play_icon()

    def soft_stop(self):
        """Bouton Stop : sortie en écho ; 2e appui pendant l'écho = arrêt immédiat."""
        if self.engine is None:
            return
        self.engine.stop_soft()
        self.transition_label.setText("〰 arrêt en écho")

    def stop(self):
        if self.output:
            self.output.stop()
            self.output = None
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.deck.set_engine(None)
        self.deck.set_style_state(None)
        self._shown_track_id = None
        self.player_frame.set_color(None)
        self.now_title.setText("Rien en cours")
        self.now_info.setText("")
        self.transition_label.setText("")
        self.progress.setValue(0)
        self.remain_label.setText("-0:00")
        self.mix_badge.set_state(None)
        self.counter.setText("")
        self.cover.setPixmap(self._placeholder(84))
        self.queue_list.clear()
        self._queue_sig = None
        self._update_buttons()
        self._refill_dots()

    def skip(self):
        if self.engine:
            self.engine.skip()

    def queue_selected(self, front: bool):
        rows = self._selected_rows()
        if not (rows and self.engine):
            return
        (self.engine.play_next_many if front else self.engine.enqueue_many)(rows)
        if len(rows) == 1:
            self.status.setText(f"« {rows[0].get('title') or rows[0]['filename']} » " +
                                ("jouera ensuite" if front else "ajouté à la file"))
        else:
            self.status.setText(f"{len(rows)} morceaux " +
                                ("joueront ensuite" if front else "ajoutés à la file"))
        self.refresh_queue()

    def _queue_pos(self):
        it = self.queue_list.currentItem()
        if it is None:
            return None
        data = it.data(Qt.UserRole)
        return data[1] if data else None

    def _priority_index(self):          # compatibilité
        return self._queue_pos()

    def remove_from_queue(self):
        pos = self._queue_positions()
        if not pos or self.engine is None:
            self.status.setText("Choisis d'abord un ou plusieurs morceaux dans « À suivre »")
            return
        n = self.engine.remove_upcoming_many(pos)
        if n < len(pos):
            self.status.setText("Un morceau est déjà en train de s'enchaîner : il reste")
        elif n > 1:
            self.status.setText(f"{n} morceaux retirés de « À suivre »")
        self._queue_sig = None
        self.refresh_queue(select_pos=[max(0, min(min(pos), self.queue_list.count() - 1 - n))])

    def move_in_queue(self, delta):
        pos = self._queue_positions()
        if not pos or self.engine is None:
            self.status.setText("Choisis d'abord un ou plusieurs morceaux dans « À suivre »")
            return
        new = self.engine.move_upcoming_many(pos, delta)
        if new == sorted(pos) and 0 <= (min(pos) if delta < 0 else max(pos)) + delta < self.queue_list.count():
            self.status.setText("Ce morceau est déjà en train de s'enchaîner")
        self._queue_sig = None
        self.refresh_queue(select_pos=new)

    def _queue_positions(self):
        out = []
        for it in self.queue_list.selectedItems():
            data = it.data(Qt.UserRole)
            if data:
                out.append(data[1])
        if not out and self._queue_pos() is not None:
            out = [self._queue_pos()]
        return sorted(out)

    # ------------------------------------------------------- durée des transitions

    def _mix_seconds(self):
        v = self.mix_len.value()
        return None if v <= 1 else float(v)

    def _show_mix_len(self, v):
        self.mix_len_label.setText("Auto" if v <= 1 else f"{v} s")

    def _on_mix_len(self, v):
        self._show_mix_len(v)
        self._mix_len_timer.start()

    def _apply_mix_len(self):
        secs = self._mix_seconds()
        self.settings["mix_seconds"] = secs
        save_settings(self.settings)
        if self.engine:
            self.engine.set_mix_seconds(secs)
        self.status.setText("Transitions : durée choisie par l'appli" if secs is None
                            else f"Transitions d'environ {secs:.0f} s (dès le prochain enchaînement)")

    # ------------------------------------------------------------------ thème

    def _ss(self, w, sheet: str):
        """Feuille de style d'un élément, adaptée au thème (et réappliquée s'il change)."""
        w.setProperty("css0", sheet)
        w.setStyleSheet(theme.css(sheet))

    def _on_theme(self, light: bool):
        self.settings["theme"] = "clair" if light else "sombre"
        save_settings(self.settings)
        self.apply_theme()

    def apply_theme(self):
        theme.set_light(self.theme_switch.light)
        QApplication.instance().setStyleSheet(theme.app_css(STYLE))
        for w in self.findChildren(QWidget):
            sheet = w.property("css0")
            if sheet:
                w.setStyleSheet(theme.css(sheet))
        self.btn_full.setIcon(make_icon("fullscreen", 18, tc("#c8cdd8")))
        if hasattr(self, "_small_ph"):
            del self._small_ph
        if self.shown_rows:
            self._fill_table(self.shown_rows, keep_selection=True)
        self._show_current_cover()
        self._queue_sig = None
        self.refresh_queue()
        self.mix_badge.update()
        t = self.engine.now_playing()[0] if self.engine else None
        if t is not None:
            self._show_info(t.row.get("_base") or t.row)

    def _on_volume(self, v):
        if self.engine:
            self.engine.volume = v / 100
        self.settings["volume"] = v / 100
        save_settings(self.settings)

    # ---------------------------------------------------------------- rafraîchissement

    def refresh_player(self):
        eng = self.engine
        if eng is None:
            return
        if eng.finished:
            stopped = eng.stopping
            self.stop()
            self.now_info.setText("Arrêté" if stopped else "Fin de la playlist")
            return
        self._update_play_icon()
        if eng.style_events:
            eng.style_events.clear()
            self.status.setText("Pad utilisé : retour en Auto pour les enchaînements suivants")
        self.deck.set_style_state(eng.next_style)
        self.deck.set_toggles(self.btn_hl.isChecked(), self.btn_dj.isChecked())
        st = eng.transition_state()
        if eng.paused:
            self.mix_badge.set_state(None)
            self.transition_label.setText("❚❚ en pause — Lecture pour reprendre")
            return
        if eng.stopping:
            self.mix_badge.set_state("pause" if eng.pausing else "stop", 0,
                                     {"pause": "Pause en écho…", "turntable": "Le disque ralentit…",
                                      "pullup": "Pull-up !"}.get(eng.stop_style, "Arrêt en écho…"))
        elif st:
            kind, frac, why = st
            tgt = eng.transition_target()
            name = (tgt.row.get("title") or "") if tgt is not None else ""
            self.mix_badge.set_state(kind, frac, f"→ {name}" + (f"   ·   {why}" if why else ""))
        else:
            self.mix_badge.set_state(None)
        t, pos = eng.now_playing()
        if t is None:
            return
        with eng.lock:
            hist = [r["id"] for r in eng.history]
        new_played = set(hist) - {t.id}
        title = t.row.get("title") or Path(t.row["path"]).stem
        if (self.now_title.text() != title or not new_played <= self.played_ids
                or getattr(self, "_shown_track_id", None) != t.id):
            self._shown_track_id = t.id
            self.played_ids |= new_played
            self.now_title.setText(title)
            self._refill_dots()
            self._show_current_cover()
            self._show_info(t.row.get("_base") or t.row)
        if self.player_frame.track_id != t.id or self.player_frame.color is None:
            pm = self.cover_pix.get(t.id)
            if pm is not None:
                self.player_frame.set_color(dominant_color(pm), t.id)
        self._update_counter(eng, t, pos)
        info = t.row.get("artist") or ""
        if t.analyzed:
            info += f"   ·   {t.bpm:.0f} BPM   ·   {t.row.get('camelot')} ({t.row.get('key_name')})"
        self.now_info.setText(info)
        self.pos_label.setText(_fmt(pos))
        self.len_label.setText(_fmt(t.length))
        self.remain_label.setText("-" + _fmt(max(0.0, t.length - pos)))
        self.progress.seekable = not (eng.in_transition() or eng.stopping)
        win = self._hl_cache.get((t.id, eng.hl_mode)) if eng.hl_mode else None
        if eng.hl_mode and (t.id, eng.hl_mode) not in self._hl_cache:
            self._hl_cache[(t.id, eng.hl_mode)] = None
            def calc(tr=t, mode=eng.hl_mode, e=eng):
                try:
                    self._hl_cache[(tr.id, mode)] = e.hl_window(tr)
                except Exception:
                    pass
            threading.Thread(target=calc, daemon=True).start()
        self.progress.zone = ((win["entry"] / t.length, win["exit"] / t.length) if win else None)
        if self.progress.dragging:
            pos = self.progress.value() / 1000 * t.length      # aperçu pendant le glisser
            self.pos_label.setText(_fmt(pos))
            self.remain_label.setText("-" + _fmt(max(0.0, t.length - pos)))
        else:
            self.progress.setValue(int(1000 * pos / max(t.length, 1)))
        kind = eng.in_transition()
        target = eng.transition_target()
        if eng.stopping:
            self.transition_label.setText({"pause": "❚❚ pause en écho", "turntable": "◉ arrêt 33 tours",
                                           "pullup": "↺ pull-up"}.get(eng.stop_style, "〰 arrêt en écho"))
        elif kind and target is not None and target.id != t.id:
            name = target.row.get("title") or ""
            name = name if len(name) < 40 else name[:38] + "…"
            self.transition_label.setText({"beatmatch": "⇄ mix calé vers ", "echo": "〰 écho vers ", "filtre": "〰 filtre + écho vers ", "pullup": "↺ pull-up vers ",
                                           "fade": "fondu vers "}.get(kind, "") + name)
        elif kind:
            self.transition_label.setText({"beatmatch": "⇄ mix calé", "echo": "〰 sortie en écho", "filtre": "〰 filtre + écho", "pullup": "↺ pull-up",
                                           "fade": "fondu"}.get(kind, ""))
        else:
            nt = eng.next_transition()
            if nt is not None:
                secs, k, _why, fb = nt
                m, sec = divmod(int(max(0, secs)), 60)
                self.transition_label.setText(
                    f"prochain enchaînement dans {m}:{sec:02d} · " +
                    {"beatmatch": "mix calé", "echo": "écho", "filtre": "filtre + écho",
                     "pullup": "pull-up", "fade": "fondu"}.get(k, k) + (" ⚠" if fb else ""))
                self.transition_label.setToolTip(fb or _why)
            else:
                self.transition_label.setText("")
        self.refresh_queue()

    def _update_counter(self, eng, t, pos):
        """Durée totale, temps restant et heure de fin estimée."""
        upcoming = [r for _, r in eng.upcoming(1000)]
        mode = eng.hl_mode
        if not upcoming:
            cur_end = t.length
        elif mode and t.analyzed:
            w = self.hl_known(t.row.get("_base") or t.row, mode)
            cur_end = w["exit"] if w else t.outro_start
            if w and cur_end < pos + 12:            # activé après le passage : sortie à la phrase suivante
                cur_end = pos + 12 + 4 * t.bar
        else:
            cur_end = t.outro_start if t.analyzed else t.length
        cur_left = max(0.0, cur_end - pos)
        if mode:
            self.want_hl(upcoming, urgent=True)
        if mode:
            left = cur_left + self.mix_duration(upcoming, mode, first_index_plays_from_start=False)
        else:
            left = cur_left + sum(effective_duration(r, i == len(upcoming) - 1)
                                  for i, r in enumerate(upcoming))
        end = time.localtime(time.time() + left)
        self.counter.setText(f"Reste ≈ {_fmt_long(left)} · {len(upcoming)} morceau{'x' if len(upcoming) > 1 else ''} · "
                             f"fin vers {end.tm_hour}:{end.tm_min:02d}")

    def _refill_dots(self):
        if self.mode == "search":
            self.run_search()
        elif self.current_folder:
            self.show_playlist(self.current_folder, keep_selection=True)

    def refresh_queue(self, select_pos=None):
        if self.engine is None:
            return
        items = self.engine.upcoming(40)
        sig = [(k, r["id"]) for k, r in items]
        if getattr(self, "_queue_sig", None) == sig and select_pos is None:
            return
        self._queue_sig = sig
        if select_pos is None:
            cur_sel = set(self._queue_positions())
        else:
            cur_sel = set(select_pos) if isinstance(select_pos, (list, tuple, set)) else {select_pos}
        self.queue_list.blockSignals(True)
        self.queue_list.clear()
        for pos, (kind, r) in enumerate(items):
            label = f"{r.get('title') or r['filename']}\n{r.get('artist') or ''}"
            icon = {"figé": "🔒 ", "prioritaire": "★ ", "playlist": ""}[kind]
            it = QListWidgetItem(icon + label)
            pm = self.cover_pix.get(r["id"])
            if pm is None:
                self.want_cover(r)
                pm = self._generated(r, 76)
            it.setIcon(QIcon(pm))
            it.setData(Qt.UserRole, (kind, pos))
            it.setForeground(QColor(tc("#ffd479" if kind == "prioritaire" else "#8fb3ff")))
            self.queue_list.addItem(it)
        first = True
        for pos in sorted(cur_sel):
            it = self.queue_list.item(pos)
            if it is None:
                continue
            if first:
                self.queue_list.setCurrentItem(it)
                first = False
            it.setSelected(True)
        self.queue_list.blockSignals(False)

    def _confirm_quit(self) -> str:
        """Fenêtre de confirmation quand la musique joue : « continue » (par défaut,
        aussi pour Entrée / Espace / Échap), « echo » ou « now »."""
        from PySide6.QtWidgets import QDialog
        eng = self.engine
        dlg = QDialog(self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setModal(True)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(6)
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(make_icon("turntable", 30, tc("#6c8cff")).pixmap(30, 30))
        head.addWidget(icon, alignment=Qt.AlignTop)
        col = QVBoxLayout()
        title = QLabel("La musique joue encore")
        title.setObjectName("nowTitle")
        col.addWidget(title)
        t = eng.now_playing()[0] if eng else None
        n = len(eng.upcoming(1000)) if eng else 0
        info = []
        if t is not None:
            info.append("En cours : " + (t.row.get("title") or t.label))
        if n:
            rest = self.counter.text().split("·")[0].replace("Reste", "").strip()
            info.append(f"Encore {n} morceau{'x' if n > 1 else ''}" + (f", {rest}" if rest else ""))
        sub = QLabel("\n".join(info))
        sub.setObjectName("nowInfo")
        col.addWidget(sub)
        q = QLabel("Veux-tu vraiment quitter MixApp ?")
        q.setContentsMargins(0, 8, 0, 4)
        col.addWidget(q)
        head.addSpacing(10)
        head.addLayout(col, 1)
        v.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        choice = {"v": "continue"}
        now = QPushButton("Quitter tout de suite")
        echo = QPushButton("Finir en écho et quitter")
        keep = QPushButton("Continuer la soirée")
        keep.setObjectName("primary")
        for b, k in ((now, "now"), (echo, "echo"), (keep, "continue")):
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, k=k: (choice.__setitem__("v", k), dlg.accept()))
            row.addWidget(b)
        keep.setDefault(True)
        keep.setAutoDefault(True)
        v.addSpacing(8)
        v.addLayout(row)
        dlg.setStyleSheet(theme.css("QDialog { background: #1b1e24; }"))
        keep.setFocus()
        self._quit_dialog = dlg
        dlg.exec()                     # Échap ferme la fenêtre = on continue
        self._quit_dialog = None
        return choice["v"]

    def closeEvent(self, e):
        eng = self.engine
        if eng is not None and not eng.finished and not getattr(self, "_quitting", False):
            choice = self._confirm_quit()
            if choice == "continue":
                e.ignore()
                return
            if choice == "echo" and not eng.paused:
                self._quitting = True
                e.ignore()
                eng.stop_soft("echo")
                t0 = time.time()

                def wait():
                    if (self.engine is None or self.engine.finished or self.engine.cur is None
                            or time.time() - t0 > 8):
                        self.close()
                    else:
                        QTimer.singleShot(100, wait)
                QTimer.singleShot(100, wait)
                return
        self.stop()
        self.deck.shutdown()
        if self.observer:
            self.observer.stop()
        super().closeEvent(e)


class _TooltipDelay(QProxyStyle):
    """Infobulles après 3 s de survol (pour ne pas gêner pendant la soirée)."""

    def styleHint(self, hint, option=None, widget=None, data=None):
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return 3000
        if hint == QStyle.SH_ToolTip_FallAsleepDelay:
            return 3000
        return super().styleHint(hint, option, widget, data)


def main(argv=None):
    # le fil audio récupère la main plus vite quand l'analyse tourne en fond (anti-craquements)
    sys.setswitchinterval(0.001)
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    app.setStyle(_TooltipDelay(app.style().name()))
    app.setStyleSheet(STYLE)
    w = MainWindow(os.environ.get("MIXAPP_DB"))
    w.show()
    if os.environ.get("MIXAPP_TOUR"):              # captures pour les tutoriels
        from . import tour
        tour.run(w, os.environ["MIXAPP_TOUR"])
    code = app.exec()
    # tout est sauvegardé et la fenêtre fermée : on sort tout de suite, sans laisser
    # Python démonter Qt pendant qu'un fil de fond (pochette, analyse…) travaille
    # encore — ce qui peut faire « quitter inopinément » l'appli sur Mac
    try:
        w.conn.commit()
    except Exception:
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
