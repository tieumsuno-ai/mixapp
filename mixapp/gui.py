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
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QKeySequence, QPainter,
                           QPainterPath, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
                               QProxyStyle, QPushButton, QSlider, QSplitter, QStyle, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import analysis, covers, db, library, search
from .engine import Engine
from .widgets import Equalizer, MixBadge, SeekBar, make_icon
from .transitions import SR

APP_NAME = "MixApp"
COMPAT_COLORS = {"vert": "#3ecf6e", "orange": "#f0a43a", "rouge": "#e5534b", "inconnu": "#555"}

STYLE = """
* { font-family: -apple-system, "Segoe UI", "Helvetica Neue", Arial; font-size: 13px; }
QMainWindow, QWidget { background: #15171c; color: #e6e6e6; }
QLabel { background: transparent; }
QLineEdit { background: #22252d; border: 1px solid #333844; border-radius: 8px;
            padding: 8px 12px; font-size: 14px; color: #fff; }
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
QLabel#remain { color: #e6e6e6; font-weight: 600; min-width: 52px; }
QLabel#counter { color: #c8cdd8; font-size: 13px; }
QLabel#section { color: #8a90a0; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QLabel#nowTitle { font-size: 20px; font-weight: 700; color: #fff; }
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
                                      blocksize=0, latency=0.6)
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

class Bus(QObject):
    log = Signal(str)
    library_changed = Signal()
    analysis_progress = Signal(int, int)
    playback_ready = Signal(object)       # Engine ou Exception
    cover_ready = Signal(int, object)     # id du morceau, octets de l'image (ou None)
    hl_ready = Signal()                   # un passage « temps forts » de plus est calculé


# ------------------------------------------------------------------ fenêtre principale

class MainWindow(QMainWindow):
    def __init__(self, db_path=None):
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

        self._build_ui()
        self._connect()
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
        self.btn_folders.setToolTip("Ajouter un dossier de musique : chaque sous-dossier devient une playlist")
        top.addWidget(self.btn_folders)
        self.btn_full = QPushButton()
        self.btn_full.setObjectName("flat")
        self.btn_full.setIcon(make_icon("fullscreen", 18, "#c8cdd8"))
        self.btn_full.setIconSize(QSize(18, 18))
        self.btn_full.setToolTip("Plein écran (F11 · ⌃⌘F sur Mac) — Échap pour sortir")
        top.addWidget(self.btn_full)
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
        self.playlist_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.playlist_list.setTextElideMode(Qt.ElideRight)
        lv.addWidget(self.playlist_list, 1)
        drop_hint = QLabel("↧ glisse un dossier de MP3 ici")
        drop_hint.setStyleSheet("color:#6f7686; font-size:11px;")
        lv.addWidget(drop_hint)
        split.addWidget(left)

        # morceaux
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        self.tracks_title = QLabel("MORCEAUX")
        self.tracks_title.setObjectName("section")
        mv.addWidget(self.tracks_title)
        self.table = QTableWidget(0, 7)
        self.table.setToolTip("Gras ▶ : en cours · bleu : à venir · gris : déjà joué\n"
                              "Pastille : vert = s'enchaîne parfaitement, orange = possible, rouge = difficile")
        self.table.setHorizontalHeaderLabels(["", "Titre", "Artiste", "BPM", "Clé", "Durée", ""])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
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
        self.btn_play_next.setToolTip("Le morceau sélectionné passera juste après celui en cours (★)")
        self.btn_enqueue = QPushButton("Ajouter à la file")
        self.btn_enqueue.setToolTip("Ajoute le morceau sélectionné après les autres demandes")
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
        self.queue_list.setToolTip("Les prochains morceaux : 🔒 déjà préparé · ★ demande prioritaire")
        self.queue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.queue_list.setTextElideMode(Qt.ElideRight)
        self.queue_list.setUniformItemSizes(True)
        self.queue_list.setIconSize(QSize(38, 38))
        rv.addWidget(self.queue_list, 1)
        qa = QHBoxLayout()
        self.btn_up = QPushButton("↑")
        self.btn_up.setToolTip("Monter le morceau choisi dans « À suivre »")
        self.btn_down = QPushButton("↓")
        self.btn_down.setToolTip("Descendre le morceau choisi dans « À suivre »")
        self.btn_remove = QPushButton("Retirer")
        self.btn_remove.setToolTip("Retirer le morceau choisi de « À suivre »")
        for b in (self.btn_up, self.btn_down):
            b.setFixedWidth(40)
        qa.addWidget(self.btn_up)
        qa.addWidget(self.btn_down)
        qa.addStretch(1)
        qa.addWidget(self.btn_remove)
        rv.addLayout(qa)
        hint = QLabel("🔒 déjà préparé   ★ file prioritaire   ↑↓ / Retirer : tous les morceaux")
        hint.setStyleSheet("color:#6f7686; font-size:11px;")
        rv.addWidget(hint)
        split.addWidget(right)

        split.setSizes([230, 700, 330])
        v.addWidget(split, 1)

        # lecteur
        player = QFrame()
        player.setObjectName("player")
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
        row1.addLayout(col, 1)
        self.counter = QLabel("")
        self.counter.setToolTip("Temps restant de la soirée, nombre de morceaux et heure de fin estimée")
        self.counter.setObjectName("counter")
        self.counter.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.transition_label = QLabel("")
        self.transition_label.setObjectName("transition")
        rcol = QVBoxLayout()
        rcol.addWidget(self.counter, alignment=Qt.AlignRight)
        rcol.addWidget(self.transition_label, alignment=Qt.AlignRight)
        row1.addLayout(rcol)
        pv.addLayout(row1)
        self.mix_badge = MixBadge()
        self.mix_badge.setToolTip("Transition en cours : le curseur va du morceau A au morceau B")
        pv.addWidget(self.mix_badge)
        row2 = QHBoxLayout()
        self.pos_label = QLabel("0:00")
        self.progress = SeekBar()
        self.progress.setMaximumHeight(10)
        self.progress.setToolTip("Clique ou glisse pour avancer / reculer dans le morceau")
        self.progress.seek.connect(self._on_seek)
        self.len_label = QLabel("0:00")
        self.len_label.setStyleSheet("color:#8a90a0;")
        self.remain_label = QLabel("-0:00")
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
        self.btn_pullup = QPushButton("  Pull-up")
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
        row3.addStretch(1)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#8a90a0;")
        row3.addWidget(self.status)
        pv.addLayout(row3)
        v.addWidget(player)

        self._update_buttons()

    def _connect(self):
        self.search_box.textChanged.connect(self._on_search_changed)
        self._search_timer = QTimer(self, singleShot=True, interval=150)
        self._search_timer.timeout.connect(self.run_search)
        self.btn_folders.clicked.connect(self.choose_folders)
        self.playlist_list.currentItemChanged.connect(self._on_playlist_selected)
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
        self.btn_full.clicked.connect(self.toggle_fullscreen)
        self.btn_hl.toggled.connect(lambda _: self._on_highlights())
        self.btn_dj.toggled.connect(lambda _: self._on_dj())
        self.volume.valueChanged.connect(self._on_volume)

        self.bus.log.connect(self._on_log)
        self.bus.library_changed.connect(self._on_library_changed)
        self.bus.analysis_progress.connect(self._on_analysis_progress)
        self.bus.playback_ready.connect(self._on_playback_ready)
        self.bus.cover_ready.connect(self._on_cover_ready)
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

    def eventFilter(self, obj, ev):
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
                                              on_change=lambda res: self._after_watch_change())
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
                    library.scan(conn, roots)
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
        p.fillPath(path, QColor("#2a2e38"))
        p.setPen(QColor("#5c6375"))
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

    def _small_placeholder(self):
        if not hasattr(self, "_small_ph"):
            self._small_ph = self._placeholder(52)
        return self._small_ph

    def _on_cover_ready(self, track_id, data):
        pm = self._rounded(data, 168) if data else None
        self.cover_pix[track_id] = pm or self._placeholder(168)
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
            self.cover.setPixmap(self._placeholder(84))
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
        rows = [dict(r) for r in library.playlist_tracks(self.conn, folder)]
        if self.btn_dj.isChecked() and rows:
            from . import ordering
            rows = ordering.dj_order(rows, rows[0]["id"])
        self._fill_table(rows, keep_selection)
        self._update_playlist_header()
        self.want_hl(rows)                 # calcule les passages en fond (pour les durées)

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
        sel = self._selected_row()
        sel_id = sel["id"] if (sel and keep_selection) else None
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
            cells[1].setForeground(QColor(title_col))
            cells[2].setForeground(QColor(artist_col))
            for c in (3, 4, 5):
                cells[c].setForeground(QColor(info_col))
            if is_cur:
                for c in range(1, 6):
                    cells[c].setFont(bold)
            pm = self.cover_pix.get(r["id"])
            if pm is None:
                self.want_cover(r)                 # chargée au fil de l'eau (internet)
                pm = self._small_placeholder()
            cells[1].setIcon(QIcon(pm))
            for c, it in enumerate(cells):
                self.table.setItem(i, c, it)
            if sel_id == r["id"]:
                self.table.selectRow(i)
        self._update_buttons()

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

    def _update_play_icon(self):
        eng = self.engine
        running = eng is not None and not eng.paused and not eng.pausing
        want = "pause" if running else "play"
        if getattr(self, "_play_icon", None) != want:
            self._play_icon = want
            self.btn_play.setIcon(make_icon(want, 18))
            self.btn_play.setText("  Pause" if running else "  Lecture")

    # ---------------------------------------------------------------- lecture

    def on_double_click(self):
        if self.engine is None:
            self.play_from_selection()
        else:
            self.queue_selected(front=True)

    def play_from_selection(self):
        sel = self._selected_row()
        if self.mode == "playlist" and self.current_folder:
            rows = [dict(r) for r in library.playlist_tracks(self.conn, self.current_folder)]
            start = next((i for i, r in enumerate(rows) if sel and r["id"] == sel["id"]), 0)
        elif sel:
            # depuis une recherche : on lance la playlist (dossier) du morceau choisi
            rows = [dict(r) for r in library.playlist_tracks(self.conn, sel["folder"])]
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
        self._update_buttons()
        self.refresh_player()
        self._refill_dots()

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

    def want_hl(self, rows):
        with self._hl_lock:
            for r in rows:
                if r.get("bpm") and self._hl_key(r) not in self._hl_store:
                    self._hl_queue.append(r)
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

    def vocal_map(self, track):
        """Carte du chant d'un morceau (calculée une fois, puis gardée sur disque)."""
        from . import vocals as V
        r = track.row.get("_base") or track.row
        key = self._hl_key(r)
        a = self.vocal_cache.get(key)
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
        sel = self._selected_row()
        if not (sel and self.engine):
            return
        (self.engine.play_next if front else self.engine.enqueue)(sel)
        self.status.setText(f"« {sel.get('title')} » " +
                            ("jouera ensuite" if front else "ajouté à la file"))
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
        i = self._queue_pos()
        if i is None or self.engine is None:
            self.status.setText("Choisis d'abord un morceau dans « À suivre »")
            return
        if not self.engine.remove_upcoming(i):
            self.status.setText("Ce morceau est déjà en train de s'enchaîner")
        self._queue_sig = None
        self.refresh_queue(select_pos=min(i, self.queue_list.count() - 2))

    def move_in_queue(self, delta):
        i = self._queue_pos()
        if i is None or self.engine is None:
            self.status.setText("Choisis d'abord un morceau dans « À suivre »")
            return
        j = self.engine.move_upcoming(i, delta)
        if j == i and 0 <= i + delta < self.queue_list.count():
            self.status.setText("Ce morceau est déjà en train de s'enchaîner")
        self._queue_sig = None
        self.refresh_queue(select_pos=j)

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
        if self.now_title.text() != title or not new_played <= self.played_ids:
            self.played_ids |= new_played
            self.now_title.setText(title)
            self._refill_dots()
            self._show_current_cover()
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
        else:
            self.transition_label.setText({"beatmatch": "⇄ mix calé", "echo": "〰 sortie en écho", "filtre": "〰 filtre + écho", "pullup": "↺ pull-up",
                                           "fade": "fondu"}.get(kind, ""))
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
        self.want_hl(upcoming)
        if mode:
            left = cur_left + self.mix_duration(upcoming, mode, first_index_plays_from_start=False)
        else:
            left = cur_left + sum(effective_duration(r, i == len(upcoming) - 1)
                                  for i, r in enumerate(upcoming))
        end = time.localtime(time.time() + left)
        self.counter.setText(f"Reste ≈ {_fmt_long(left)} · {len(upcoming)} morceaux · "
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
        cur_sel = self._queue_pos() if select_pos is None else select_pos
        self.queue_list.clear()
        for pos, (kind, r) in enumerate(items):
            label = f"{r.get('title') or r['filename']}\n{r.get('artist') or ''}"
            icon = {"figé": "🔒 ", "prioritaire": "★ ", "playlist": ""}[kind]
            it = QListWidgetItem(icon + label)
            pm = self.cover_pix.get(r["id"])
            if pm is None:
                self.want_cover(r)
                pm = self._placeholder(76)
            it.setIcon(QIcon(pm))
            it.setData(Qt.UserRole, (kind, pos))
            it.setForeground(QColor("#ffd479" if kind == "prioritaire" else "#8fb3ff"))
            self.queue_list.addItem(it)
            if cur_sel == pos:
                self.queue_list.setCurrentItem(it)

    def closeEvent(self, e):
        self.stop()
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
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
