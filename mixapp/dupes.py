"""Doublons dans les playlists. Rien n'est jamais supprimé du disque : l'appli
se contente de masquer des morceaux dans ses listes.

- copies identiques (même titre, même version, même artiste, même playlist) :
  masquées d'office, on garde la meilleure copie (qualité, puis durée) ;
- plusieurs versions du même titre dans une playlist (Edit, Extended…) :
  l'appli propose une version, l'utilisateur choisit ;
- même titre dans plusieurs playlists : l'appli propose la playlist où il a le
  plus sa place (tempo), l'utilisateur valide.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict

from .covers import artists_of


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _strip_noise(title: str) -> str:
    t = re.sub(r"\[[^\]]*\]", " ", title or "")                 # [www.site.com]
    t = re.sub(r"www\.\S+", " ", t)
    t = re.sub(r"^\s*\d{1,3}\s*[-._)]\s*", "", t)                # numéro de piste
    t = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", t)                   # « (1) » de copie
    return t


def full_title(title: str) -> str:
    """Titre avec sa version : « danza kuduro edit mix »."""
    t = _strip_noise(title)
    t = re.sub(r"\b(feat|ft|featuring)\b\.?[^()\[\]]*", " ", t, flags=re.I)
    return _plain(t)


def base_title(title: str) -> str:
    """Titre sans la version : « danza kuduro »."""
    t = _strip_noise(title)
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\b(feat|ft|featuring)\b\.?.*", " ", t, flags=re.I)
    return _plain(t)


def main_artist(artist: str) -> str:
    a = artists_of(artist or "")
    return _plain(a[0] if a else artist or "")


def version_name(title: str) -> str:
    m = re.findall(r"\(([^)]*)\)", _strip_noise(title))
    return m[-1].strip() if m else "Version originale"


def bitrate_kbps(row: dict) -> int | None:
    try:
        size = os.path.getsize(row["path"])
    except OSError:
        return None
    d = row.get("duration") or 0
    return int(round(size * 8 / d / 1000)) if d > 1 else None


def _quality(row: dict):
    return (bitrate_kbps(row) or 0, row.get("duration") or 0)


VERSION_SCORE = [("extended", 3), ("club", 2), ("original mix", 2), ("12", 1),
                 ("remix", 0), ("edit", -1), ("radio", -2), ("short", -2), ("intro", 1)]


def _version_score(row: dict) -> float:
    v = _plain(version_name(row.get("title") or ""))
    sc = sum(p for w, p in VERSION_SCORE if w in v)
    return sc + (row.get("duration") or 0) / 10000.0


def _suggest_reason(row: dict) -> str:
    v = _plain(version_name(row.get("title") or ""))
    if "extended" in v or "club" in v:
        return "intro et fin plus longues, idéal pour mixer"
    return "la version la plus longue"


def analyse(rows: list[dict], hidden: dict[str, list[str]], done: set[str]) -> dict:
    """rows : tous les morceaux de la bibliothèque. hidden : {dossier: [chemins masqués]}.
    done : groupes déjà tranchés. Renvoie {"auto": [...], "versions": [...], "cross": [...]}."""
    hid = {(f, p) for f, ps in hidden.items() for p in ps}
    by_folder = defaultdict(list)
    for r in rows:
        by_folder[r["folder"]].append(r)
    auto, versions = [], []
    visible = []
    for folder, rs in by_folder.items():
        groups = defaultdict(list)
        for r in rs:
            groups[(full_title(r.get("title") or r.get("filename") or ""), main_artist(r.get("artist")))].append(r)
        kept = []
        for (ft, ar), g in groups.items():
            key = f"i|{folder}|{ft}|{ar}"
            if len(g) > 1 and ft and key not in done:
                best = max(g, key=_quality)
                auto.append({"key": key, "folder": folder, "keep": best,
                             "hide": [r for r in g if r is not best]})
                kept.append(best)
            else:
                kept.extend(g)
        kept = [r for r in kept if (folder, r["path"]) not in hid]
        vg = defaultdict(list)
        for r in kept:
            vg[(base_title(r.get("title") or r.get("filename") or ""), main_artist(r.get("artist")))].append(r)
        for (bt, ar), g in vg.items():
            if len(g) > 1 and bt and len({full_title(r.get("title") or "") for r in g}) > 1:
                key = f"v|{folder}|{bt}|{ar}"
                if key not in done:
                    best = max(g, key=_version_score)
                    versions.append({"key": key, "folder": folder, "rows": g, "suggest": best,
                                     "reason": _suggest_reason(best)})
        visible.extend(kept)
    # même titre dans plusieurs playlists
    cg = defaultdict(lambda: defaultdict(list))
    for r in visible:
        bt = base_title(r.get("title") or r.get("filename") or "")
        if bt:
            cg[(bt, main_artist(r.get("artist")))][r["folder"]].append(r)
    cross = []
    for (bt, ar), per in cg.items():
        key = f"c|{bt}|{ar}"
        if len(per) < 2 or key in done:
            continue
        sample = next(iter(per.values()))[0]
        suggest, reason = None, ""
        bpm = sample.get("bpm")
        if bpm:
            def gap(folder):
                b = [x["bpm"] for x in by_folder[folder] if x.get("bpm")]
                return abs(sum(b) / len(b) - bpm) if b else 1e9
            suggest = min(per, key=gap)
            reason = "tempo le plus proche de cette playlist"
        else:
            suggest = sorted(per)[0]
        cross.append({"key": key, "title": sample.get("title"), "artist": sample.get("artist"),
                      "folders": dict(per), "suggest": suggest, "reason": reason})
    return {"auto": auto, "versions": versions, "cross": cross}
