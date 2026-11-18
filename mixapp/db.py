"""Base de données locale (SQLite) : morceaux, analyse et index de recherche.

Un seul fichier .db dans le dossier de données de l'utilisateur :
  - macOS   : ~/Library/Application Support/MixApp/library.db
  - Windows : %APPDATA%\\MixApp\\library.db
  - Linux   : ~/.local/share/MixApp/library.db
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    folder       TEXT NOT NULL,          -- dossier parent = playlist
    filename     TEXT NOT NULL,
    title        TEXT,
    artist       TEXT,
    album        TEXT,
    genre        TEXT,
    year         TEXT,
    track_no     INTEGER,
    duration     REAL,
    mtime        REAL NOT NULL,          -- pour savoir si le fichier a changé
    -- Résultats d'analyse (NULL tant que non analysé)
    analyzed_mtime REAL,
    bpm          REAL,
    first_beat   REAL,                   -- secondes : premier temps de la grille
    key_name     TEXT,                   -- ex. "Am", "F#"
    camelot      TEXT,                   -- ex. "8A"
    mix_in       REAL,                   -- secondes : point d'entrée conseillé
    intro_end    REAL,                   -- secondes : fin de l'intro
    outro_start  REAL,                   -- secondes : début de l'outro
    energy       REAL                    -- 0..1, énergie moyenne relative
);

CREATE INDEX IF NOT EXISTS idx_tracks_folder ON tracks(folder);
CREATE INDEX IF NOT EXISTS idx_tracks_bpm    ON tracks(bpm);

-- Index plein texte, insensible aux accents (« beyonce » trouve « Beyoncé »)
CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
    title, artist, album, genre, filename, folder_name,
    tokenize="unicode61 remove_diacritics 2"
);
"""


def data_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "MixApp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else data_dir() / "library.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def fts_upsert(conn: sqlite3.Connection, row: sqlite3.Row | dict) -> None:
    """Met à jour l'index de recherche pour un morceau."""
    conn.execute("DELETE FROM tracks_fts WHERE rowid = ?", (row["id"],))
    conn.execute(
        "INSERT INTO tracks_fts(rowid, title, artist, album, genre, filename, folder_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            row["id"],
            row["title"] or "",
            row["artist"] or "",
            row["album"] or "",
            row["genre"] or "",
            Path(row["filename"]).stem.replace("_", " "),
            Path(row["folder"]).name,
        ),
    )


def fts_delete(conn: sqlite3.Connection, track_id: int) -> None:
    conn.execute("DELETE FROM tracks_fts WHERE rowid = ?", (track_id,))
