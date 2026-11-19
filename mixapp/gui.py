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
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QSlider, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import analysis, covers, db, library, search
from .engine import Engine
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
            font-weight: 600; font-size: 15px; padding: 10px 22px; }
QPushButton#stop:hover { background: #d84a3b; }
QPushButton#stop:disabled { background: #3a2a2a; border-color: #3a2a2a; color: #7a6060; }
QLabel#counter { color: #c8cdd8; font-size: 13px; }
QLabel#section { color: #8a90a0; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QLabel#nowTitle { font-size: 20px; font-weight: 700; color: #fff; }
QLabel#nowInfo { color: #9aa3b5; font-size: 13px; }
QLabel#transition { color: #f0a43a; font-weight: 600; }
QFrame#player { background: #1b1e24; border-radius: 12px; }
QProgressBar { background: #2a2e38; border: none; border-radius: 3px; height: 6px; }
QProgressBar::chunk { background: #6c8cff; border-radius: 3px; }
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

        def cb(outdata, frames, t, status):
            outdata[:] = self.engine.pull(frames)
        self.stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32",
                                      blocksize=2048, latency=0.35, callback=cb)
        self.stream.start()

    def stop(self):
        self._fake = False
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
        self.played_ids: set[int] = set()     # morceaux déjà joués pendant la session
        self.logos: set[str] = set()          # images « logo » partagées par plusieurs MP3
        self.cover_pix: dict[int, QPixmap] = {}
        self._cover_wanted: dict[int, dict] = {}
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
        self.search_box.setPlaceholderText(
            "Rechercher un titre, un artiste…   (filtres : bpm:120-128  cle:8A  genre:house)")
        self.search_box.setClearButtonEnabled(True)
        top.addWidget(self.search_box, 1)
        self.btn_folders = QPushButton("Dossiers de musique…")
        top.addWidget(self.btn_folders)
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
        self.playlist_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.playlist_list.setTextElideMode(Qt.ElideRight)
        lv.addWidget(self.playlist_list, 1)
        split.addWidget(left)

        # morceaux
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        self.tracks_title = QLabel("MORCEAUX")
        self.tracks_title.setObjectName("section")
        mv.addWidget(self.tracks_title)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["", "Titre", "Artiste", "BPM", "Clé", "Durée", ""])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
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
        self.btn_play_here.setObjectName("primary")
        self.btn_play_next = QPushButton("Jouer ensuite")
        self.btn_enqueue = QPushButton("Ajouter à la file")
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
        self.queue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.queue_list.setTextElideMode(Qt.ElideRight)
        self.queue_list.setUniformItemSizes(True)
        self.queue_list.setIconSize(QSize(38, 38))
        rv.addWidget(self.queue_list, 1)
        qa = QHBoxLayout()
        self.btn_up = QPushButton("↑")
        self.btn_down = QPushButton("↓")
        self.btn_remove = QPushButton("Retirer")
        for b in (self.btn_up, self.btn_down):
            b.setFixedWidth(40)
        qa.addWidget(self.btn_up)
        qa.addWidget(self.btn_down)
        qa.addStretch(1)
        qa.addWidget(self.btn_remove)
        rv.addLayout(qa)
        hint = QLabel("🔒 déjà préparé   ★ file prioritaire")
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
        self.counter.setObjectName("counter")
        self.counter.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.transition_label = QLabel("")
        self.transition_label.setObjectName("transition")
        rcol = QVBoxLayout()
        rcol.addWidget(self.counter, alignment=Qt.AlignRight)
        rcol.addWidget(self.transition_label, alignment=Qt.AlignRight)
        row1.addLayout(rcol)
        pv.addLayout(row1)
        row2 = QHBoxLayout()
        self.pos_label = QLabel("0:00")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(6)
        self.len_label = QLabel("0:00")
        row2.addWidget(self.pos_label)
        row2.addWidget(self.progress, 1)
        row2.addWidget(self.len_label)
        pv.addLayout(row2)
        row3 = QHBoxLayout()
        self.btn_stop = QPushButton("■  Stop")
        self.btn_stop.setObjectName("stop")
        self.btn_stop.setToolTip("Arrêt en douceur (écho) · 2 appuis rapides : arrêt immédiat")
        self.btn_next = QPushButton("⏭  Suivant")
        self.btn_next.setObjectName("next")
        self.btn_next.setToolTip("Sortie en écho vers le morceau suivant (2 appuis rapides : coupe nette)")
        row3.addWidget(self.btn_stop)
        row3.addWidget(self.btn_next)
        row3.addSpacing(20)
        row3.addWidget(QLabel("🔈"))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(self.settings.get("volume", 0.9) * 100))
        self.volume.setFixedWidth(140)
        row3.addWidget(self.volume)
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
        self.btn_stop.clicked.connect(self.soft_stop)
        self.btn_next.clicked.connect(self.skip)
        self.volume.valueChanged.connect(self._on_volume)

        self.bus.log.connect(self._on_log)
        self.bus.library_changed.connect(self._on_library_changed)
        self.bus.analysis_progress.connect(self._on_analysis_progress)
        self.bus.playback_ready.connect(self._on_playback_ready)
        self.bus.cover_ready.connect(self._on_cover_ready)

        QShortcut(QKeySequence.Find, self, activated=lambda: (self.search_box.setFocus(),
                                                               self.search_box.selectAll()))
        for seq in (QKeySequence("Ctrl+Right"), QKeySequence(Qt.Key_MediaNext)):
            QShortcut(seq, self, activated=self.skip, context=Qt.ApplicationShortcut)
        QShortcut(QKeySequence(Qt.Key_Return), self.table, activated=lambda: self.queue_selected(True))
        QShortcut(QKeySequence("Ctrl+Return"), self.table, activated=lambda: self.queue_selected(False))
        QShortcut(QKeySequence(Qt.Key_Delete), self.queue_list, activated=self.remove_from_queue)
        QShortcut(QKeySequence(Qt.Key_Backspace), self.queue_list, activated=self.remove_from_queue)
        self.search_box.returnPressed.connect(lambda: (self.table.setFocus(), self.table.selectRow(0)))

        self.ui_timer = QTimer(self, interval=250)
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
        if not d:
            return
        roots = self.settings.setdefault("roots", [])
        if d not in roots:
            roots.append(d)
            save_settings(self.settings)
        self.start_library_tasks()

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

    def want_cover(self, row: dict):
        """Demande (sans bloquer) la jaquette d'un morceau."""
        if row["id"] in self.cover_pix:
            return
        with self._cover_lock:
            self._cover_wanted.setdefault(row["id"], row)
        self._cover_event.set()

    def _cover_worker(self):
        done = set()
        while True:
            self._cover_event.wait()
            with self._cover_lock:
                items = [(i, r) for i, r in self._cover_wanted.items() if i not in done]
                self._cover_wanted.clear()
                self._cover_event.clear()
            for i, r in items:
                try:
                    data = covers.get_cover(r, self.logos,
                                            online=self.settings.get("covers_online", True))
                except Exception:
                    data = None
                done.add(i)
                self.bus.cover_ready.emit(i, data)

    def _on_cover_ready(self, track_id, data):
        pm = self._rounded(data, 168) if data else None
        self.cover_pix[track_id] = pm or self._placeholder(168)
        self._queue_sig = None            # forcer le rafraîchissement des icônes
        self._show_current_cover()

    def _show_current_cover(self):
        t = self.engine.now_playing()[0] if self.engine else None
        if t is None:
            self.cover.setPixmap(self._placeholder(84))
            return
        pm = self.cover_pix.get(t.id)
        if pm is None:
            self.want_cover(t.row)
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
        total = sum(effective_duration(r, i == len(rows) - 1) for i, r in enumerate(rows))
        self.tracks_title.setText(f"PLAYLIST · {self._display_name(folder).upper()} · "
                                  f"{len(rows)} MORCEAUX · {_fmt_long(total)} EN MIX")
        self._fill_table(rows, keep_selection)

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
        self.btn_stop.setEnabled(playing)

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

    def _priority_index(self):
        it = self.queue_list.currentItem()
        if it is None:
            return None
        kind, idx = it.data(Qt.UserRole) or (None, None)
        return idx if kind == "prioritaire" else None

    def remove_from_queue(self):
        i = self._priority_index()
        if i is not None and self.engine:
            self.engine.remove_from_queue(i)
            self.refresh_queue()

    def move_in_queue(self, delta):
        i = self._priority_index()
        if i is not None and self.engine:
            j = self.engine.move_in_queue(i, delta)
            self.refresh_queue(select_priority=j)

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
        self.progress.setMaximum(1000)
        self.progress.setValue(int(1000 * pos / max(t.length, 1)))
        kind = eng.in_transition()
        target = eng.transition_target()
        if eng.stopping:
            self.transition_label.setText("〰 arrêt en écho")
        elif kind and target is not None and target.id != t.id:
            name = target.row.get("title") or ""
            name = name if len(name) < 40 else name[:38] + "…"
            self.transition_label.setText({"beatmatch": "⇄ mix calé vers ", "echo": "〰 écho vers ",
                                           "fade": "fondu vers "}.get(kind, "") + name)
        else:
            self.transition_label.setText({"beatmatch": "⇄ mix calé", "echo": "〰 sortie en écho",
                                           "fade": "fondu"}.get(kind, ""))
        self.refresh_queue()

    def _update_counter(self, eng, t, pos):
        """Durée totale, temps restant et heure de fin estimée."""
        upcoming = [r for _, r in eng.upcoming(1000)]
        if upcoming:
            cur_left = max(0.0, (t.outro_start if t.analyzed else t.length) - pos)
        else:
            cur_left = max(0.0, t.length - pos)
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

    def refresh_queue(self, select_priority=None):
        if self.engine is None:
            return
        items = self.engine.upcoming(40)
        sig = [(k, r["id"]) for k, r in items]
        if getattr(self, "_queue_sig", None) == sig and select_priority is None:
            return
        self._queue_sig = sig
        cur_sel = self._priority_index() if select_priority is None else select_priority
        self.queue_list.clear()
        p = 0
        for kind, r in items:
            label = f"{r.get('title') or r['filename']}\n{r.get('artist') or ''}"
            icon = {"figé": "🔒 ", "prioritaire": "★ ", "playlist": ""}[kind]
            it = QListWidgetItem(icon + label)
            pm = self.cover_pix.get(r["id"])
            if pm is None:
                self.want_cover(r)
                pm = self._placeholder(76)
            it.setIcon(QIcon(pm))
            if kind == "prioritaire":
                it.setData(Qt.UserRole, ("prioritaire", p))
                it.setForeground(QColor("#ffd479"))
                if cur_sel == p:
                    self.queue_list.addItem(it)
                    self.queue_list.setCurrentItem(it)
                    p += 1
                    continue
                p += 1
            elif kind == "figé":
                it.setForeground(QColor("#8fb3ff"))
                it.setData(Qt.UserRole, ("figé", None))
            else:
                it.setForeground(QColor("#8fb3ff"))
                it.setData(Qt.UserRole, ("playlist", None))
            self.queue_list.addItem(it)

    def closeEvent(self, e):
        self.stop()
        if self.observer:
            self.observer.stop()
        super().closeEvent(e)


def main(argv=None):
    # le fil audio récupère la main plus vite quand l'analyse tourne en fond (anti-craquements)
    sys.setswitchinterval(0.001)
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(STYLE)
    w = MainWindow(os.environ.get("MIXAPP_DB"))
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
