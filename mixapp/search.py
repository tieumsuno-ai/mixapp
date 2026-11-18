"""Recherche de morceaux.

Syntaxe (combinable) :
    daft punk                 texte libre (titre, artiste, album, genre, fichier, dossier)
    bpm:128  bpm:120-128      tempo exact (±1) ou plage
    cle:8A   key:Am           tonalité (Camelot ou nom)
    genre:house  artiste:x    filtre sur un champ précis
La recherche se fait pendant la frappe : le dernier mot est traité comme un
préfixe (« dap » trouve « Daft Punk »), sans tenir compte des accents.
"""

from __future__ import annotations

import re
import shlex
import sqlite3

from .analysis import camelot_neighbors

FIELD_ALIASES = {
    "artist": "artist", "artiste": "artist",
    "title": "title", "titre": "title",
    "album": "album", "genre": "genre",
}


def _fts_term(word: str) -> str:
    word = re.sub(r'["*^():]', " ", word).strip()
    return f'"{word}"*' if word else ""


def search(conn: sqlite3.Connection, query: str, limit: int = 100) -> list[sqlite3.Row]:
    try:
        tokens = shlex.split(query)
    except ValueError:  # guillemet non fermé pendant la frappe
        tokens = query.replace('"', " ").split()

    fts_parts, where, params = [], [], []
    for tok in tokens:
        key, sep, val = tok.partition(":")
        key = key.lower()
        if sep and val:
            if key == "bpm":
                m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?", val)
                if m:
                    lo = float(m.group(1))
                    hi = float(m.group(2)) if m.group(2) else lo
                    if lo == hi:
                        lo, hi = lo - 1, hi + 1
                    where.append("t.bpm BETWEEN ? AND ?")
                    params += [lo, hi]
                    continue
            if key in ("cle", "clé", "key", "camelot"):
                v = val.upper() if re.fullmatch(r"\d{1,2}[abAB]", val) else val
                where.append("(t.camelot = ? OR t.key_name = ? COLLATE NOCASE)")
                params += [v, v]
                continue
            if key in FIELD_ALIASES:
                col = FIELD_ALIASES[key]
                fts_parts.append(f"{col} : ({' '.join(_fts_term(w) for w in val.split())})")
                continue
        term = _fts_term(tok)
        if term:
            fts_parts.append(term)

    sql = "SELECT t.* FROM tracks t"
    if fts_parts:
        sql += " JOIN tracks_fts f ON f.rowid = t.id AND tracks_fts MATCH ?"
        params.insert(0, " AND ".join(fts_parts))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + ("f.rank" if fts_parts else "t.artist COLLATE NOCASE, t.title COLLATE NOCASE")
    sql += " LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def tempo_ratio(bpm_a: float, bpm_b: float) -> float:
    """Écart de tempo relatif, en tenant compte du demi / double tempo."""
    best = min(abs(bpm_b * f - bpm_a) / bpm_a for f in (0.5, 1.0, 2.0))
    return best


def compatibility(a: sqlite3.Row, b: sqlite3.Row) -> str:
    """'vert', 'orange' ou 'rouge' : facilité d'enchaîner b après a."""
    if not (a["bpm"] and b["bpm"] and a["camelot"] and b["camelot"]):
        return "inconnu"
    dt = tempo_ratio(a["bpm"], b["bpm"])
    key_ok = b["camelot"] in camelot_neighbors(a["camelot"])
    if dt <= 0.04 and key_ok:
        return "vert"
    if dt <= 0.08:
        return "orange"
    return "rouge"


def compatible_tracks(conn: sqlite3.Connection, track_id: int, limit: int = 30):
    """Morceaux qui s'enchaînent bien après `track_id`, les meilleurs d'abord."""
    a = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if not a or not a["bpm"]:
        return []
    rows = conn.execute("SELECT * FROM tracks WHERE id != ? AND bpm IS NOT NULL",
                        (track_id,)).fetchall()
    neigh = camelot_neighbors(a["camelot"])

    def score(b):
        s = tempo_ratio(a["bpm"], b["bpm"]) * 10
        s += 0 if b["camelot"] == a["camelot"] else (0.2 if b["camelot"] in neigh else 1.0)
        s += abs((b["energy"] or 0.5) - (a["energy"] or 0.5))
        return s

    rows.sort(key=score)
    return [(b, compatibility(a, b)) for b in rows[:limit]]
