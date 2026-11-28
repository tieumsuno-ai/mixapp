"""Bibliothèque : scan des dossiers, lecture des tags, playlists par dossier.

Règle : chaque dossier qui contient au moins un MP3 devient une playlist,
nommée d'après le dossier. Les morceaux sont dans l'ordre des noms de
fichiers (« 2_… » avant « 10_… »).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mutagen import MutagenError
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

from . import db

AUDIO_EXTS = {".mp3"}


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    errors: list[str] | None = None


def _first(tags, key):
    try:
        v = tags.get(key)
        return v[0].strip() if v else None
    except Exception:
        return None


def read_tags(path: Path) -> dict:
    info = {"title": None, "artist": None, "album": None, "genre": None,
            "year": None, "track_no": None, "duration": None}
    try:
        audio = MP3(path)
        info["duration"] = float(audio.info.length)
    except MutagenError:
        return info
    try:
        tags = EasyID3(path)
    except MutagenError:
        tags = {}
    info["title"] = _first(tags, "title")
    info["artist"] = _first(tags, "artist")
    info["album"] = _first(tags, "album")
    info["genre"] = _first(tags, "genre")
    info["year"] = _first(tags, "date")
    tn = _first(tags, "tracknumber")
    if tn:
        try:
            info["track_no"] = int(tn.split("/")[0])
        except ValueError:
            pass
    if not info["title"]:
        info["title"] = path.stem.replace("_", " ")
    return info


def iter_audio_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        # ignorer les dossiers cachés (.Trash, .DS_Store…)
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if not name.startswith(".") and Path(name).suffix.lower() in AUDIO_EXTS:
                yield Path(dirpath) / name


def scan(conn: sqlite3.Connection, roots: list[str | Path], exclude=()) -> ScanResult:
    """Synchronise la base avec le contenu des dossiers racine.
    `exclude` : playlists (dossiers) retirées par l'utilisateur, ignorées."""
    res = ScanResult(errors=[])
    roots = [Path(r).expanduser().resolve() for r in roots]
    excl = {str(Path(e)) for e in exclude}
    seen: set[str] = set()

    for root in roots:
        for path in iter_audio_files(root):
            if str(path.parent) in excl:
                continue
            spath = str(path)
            seen.add(spath)
            try:
                mtime = path.stat().st_mtime
            except OSError as e:
                res.errors.append(f"{spath}: {e}")
                continue
            row = conn.execute("SELECT id, mtime FROM tracks WHERE path = ?", (spath,)).fetchone()
            if row and abs(row["mtime"] - mtime) < 1e-6:
                res.unchanged += 1
                continue
            tags = read_tags(path)
            values = dict(path=spath, folder=str(path.parent), filename=path.name,
                          mtime=mtime, **tags)
            if row:
                conn.execute(
                    "UPDATE tracks SET folder=:folder, filename=:filename, title=:title, "
                    "artist=:artist, album=:album, genre=:genre, year=:year, "
                    "track_no=:track_no, duration=:duration, mtime=:mtime WHERE path=:path",
                    values)
                values["id"] = row["id"]
                res.updated += 1
            else:
                cur = conn.execute(
                    "INSERT INTO tracks(path, folder, filename, title, artist, album, genre, "
                    "year, track_no, duration, mtime) VALUES (:path, :folder, :filename, "
                    ":title, :artist, :album, :genre, :year, :track_no, :duration, :mtime)",
                    values)
                values["id"] = cur.lastrowid
                res.added += 1
            db.fts_upsert(conn, values)

    # Supprimer les morceaux disparus (seulement sous les racines scannées)
    for row in conn.execute("SELECT id, path FROM tracks").fetchall():
        p = Path(row["path"])
        under_root = any(p.is_relative_to(r) for r in roots)
        if under_root and row["path"] not in seen:
            conn.execute("DELETE FROM tracks WHERE id = ?", (row["id"],))
            db.fts_delete(conn, row["id"])
            res.removed += 1

    conn.commit()
    return res


def playlists(conn: sqlite3.Connection) -> list[dict]:
    """Une playlist par dossier contenant des MP3."""
    rows = conn.execute(
        "SELECT folder, COUNT(*) AS n, SUM(duration) AS total, "
        "SUM(analyzed_mtime IS NOT NULL) AS analyzed "
        "FROM tracks GROUP BY folder ORDER BY folder COLLATE NOCASE").fetchall()
    return [dict(name=Path(r["folder"]).name, folder=r["folder"], count=r["n"],
                 duration=r["total"] or 0, analyzed=r["analyzed"]) for r in rows]


def _natural_key(name: str):
    """« 2_titre » avant « 10_titre » ; sans tenir compte des majuscules."""
    import re
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name)]


def playlist_tracks(conn: sqlite3.Connection, folder: str) -> list[sqlite3.Row]:
    """Morceaux d'une playlist, dans l'ordre des noms de fichiers (01_, 02_…).

    Le numéro de piste des tags vient souvent de l'album d'origine et ne
    reflète pas l'ordre voulu dans le dossier : on ne l'utilise pas.
    """
    rows = conn.execute("SELECT * FROM tracks WHERE folder = ?", (folder,)).fetchall()
    return sorted(rows, key=lambda r: _natural_key(r["filename"]))


def forget(conn: sqlite3.Connection, folder: str | Path, recursive: bool) -> list[dict]:
    """Retire de la base les morceaux d'un dossier (et de ses sous-dossiers si
    `recursive`). Ne touche à aucun fichier. Renvoie les morceaux retirés."""
    folder = Path(folder)
    gone = []
    for row in conn.execute("SELECT id, path, folder, mtime FROM tracks").fetchall():
        f = Path(row["folder"])
        if f == folder or (recursive and folder in f.parents):
            conn.execute("DELETE FROM tracks WHERE id = ?", (row["id"],))
            db.fts_delete(conn, row["id"])
            gone.append(dict(row))
    conn.commit()
    return gone


def watch(conn_factory, roots: list[str | Path], on_change=None, exclude=lambda: ()):
    """Surveille les dossiers et relance un scan à chaque changement.

    Nécessite le paquet optionnel `watchdog`. Les événements sont regroupés
    (attente de 2 s sans changement) pour ne pas rescanner pendant une copie.
    Renvoie l'observer (appeler .stop() pour arrêter).
    """
    import threading
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    RELEVANT = {"created", "deleted", "moved", "modified"}

    class Handler(FileSystemEventHandler):
        def __init__(self):
            self._timer = None
            self._lock = threading.Lock()

        def on_any_event(self, event):
            # Ignorer les simples lectures (ouverture / fermeture de fichier)
            if event.event_type not in RELEVANT:
                return
            paths = [event.src_path, getattr(event, "dest_path", "") or ""]
            if not event.is_directory and not any(
                    Path(p).suffix.lower() in AUDIO_EXTS for p in paths if p):
                return
            if event.is_directory and event.event_type == "modified":
                return
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(2.0, self._rescan)
            self._timer.daemon = True
            self._timer.start()

        def _rescan(self):
            with self._lock:  # jamais deux scans / analyses en même temps
                conn = conn_factory()
                res = scan(conn, roots, exclude())
                conn.close()
                if on_change and (res.added or res.updated or res.removed):
                    on_change(res)

    observer = Observer()
    handler = Handler()
    for r in roots:
        observer.schedule(handler, str(Path(r).expanduser()), recursive=True)
    observer.start()
    return observer
