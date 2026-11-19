"""Jaquettes des morceaux.

Ordre de recherche :
1. cache local (dossier de données de l'appli, « covers ») ;
2. sur internet (si autorisé) : catalogue iTunes d'Apple, puis Deezer. Seuls
   l'artiste et le titre (nettoyés) sont envoyés. Le résultat n'est retenu que
   si l'artiste correspond, pour éviter les mauvaises pochettes ;
3. à défaut, l'image intégrée au MP3 si ce n'est pas un **logo** (image
   identique dans plusieurs morceaux : logo de DJ / de « remix pool ») ;
4. sinon rien (l'appli affiche une pochette neutre).

Les recherches infructueuses sont mémorisées 30 jours pour ne pas interroger
internet à chaque lancement.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import urllib.parse
import ssl
import urllib.request
from collections import Counter
from pathlib import Path

from mutagen import MutagenError
from mutagen.id3 import ID3

from .db import data_dir

RETRY_AFTER = 30 * 24 * 3600
TIMEOUT = 6
USER_AGENT = "MixApp/0.3 (lecteur personnel)"


def cache_dir() -> Path:
    d = data_dir() / "covers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(path: str) -> str:
    return hashlib.md5(path.encode("utf-8")).hexdigest()


# ------------------------------------------------------------ image intégrée

def embedded(path: str) -> bytes | None:
    try:
        pics = ID3(path).getall("APIC")
    except (MutagenError, OSError):
        return None
    if not pics:
        return None
    front = [p for p in pics if p.type == 3]          # 3 = couverture (recto)
    return (front or pics)[0].data or None


def logo_hashes(paths) -> set[str]:
    """Empreintes des images intégrées qui reviennent dans plusieurs morceaux."""
    c = Counter()
    for p in paths:
        d = embedded(p)
        if d:
            c[hashlib.md5(d).hexdigest()] += 1
    return {h for h, n in c.items() if n >= 2}


# ------------------------------------------------------------ recherche en ligne

_NOISE = re.compile(
    r"[\(\[][^\)\]]*(mix|edit|remix|version|bootleg|remaster|www|\.com|clan|intro|"
    r"original|extended|radio|official|lyrics?|video|clip|madison|feat|ft\.)[^\)\]]*[\)\]]",
    re.I)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def clean_title(title: str) -> str:
    t = _NOISE.sub(" ", title or "")
    t = re.split(r"\s(?:feat\.?|ft\.?)\s", t, flags=re.I)[0]
    return re.sub(r"\s+", " ", t).strip(" -_")


def clean_artist(artist: str) -> str:
    a = re.split(r"\s(?:feat\.?|ft\.?|&|,|x|vs\.?|with)\s", artist or "", flags=re.I)[0]
    return re.sub(r"\s+", " ", a).strip()


def _artist_ok(wanted: str, found: str) -> bool:
    w, f = set(_norm(wanted).split()), set(_norm(found).split())
    w -= {"the", "dj", "and", "les", "le", "la"}
    if w and len(w & f) >= max(1, len(w) // 2):
        return True
    # tolère les fautes de frappe des tags (« Euryhmics » pour « Eurythmics »)
    from difflib import SequenceMatcher
    a, b = _norm(wanted).replace(" ", ""), _norm(found).replace(" ", "")
    return len(a) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.8


def _ssl_context():
    # L'appli empaquetée n'a pas accès aux certificats du système : on utilise
    # ceux de certifi (sinon toutes les recherches HTTPS échouent en silence).
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_CTX = _ssl_context()


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
        return r.read()


def search_itunes(artist: str, title: str, fetch=_get_json) -> str | None:
    q = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 10})
    data = fetch(f"https://itunes.apple.com/search?{q}")
    for res in data.get("results", []):
        if _artist_ok(artist, res.get("artistName", "")) and res.get("artworkUrl100"):
            return res["artworkUrl100"].replace("100x100bb", "600x600bb")
    return None


def search_deezer(artist: str, title: str, fetch=_get_json) -> str | None:
    q = urllib.parse.quote(f'artist:"{artist}" track:"{title}"')
    data = fetch(f"https://api.deezer.com/search?q={q}&limit=10")
    for res in data.get("data", []):
        if _artist_ok(artist, (res.get("artist") or {}).get("name", "")):
            url = (res.get("album") or {}).get("cover_big")
            if url:
                return url
    return None


def search_online(artist: str, title: str, fetch=_get_json) -> str | None:
    a, t = clean_artist(artist), clean_title(title)
    if not (a and t):
        return None
    errors = 0
    for fn in (search_itunes, search_deezer):
        try:
            url = fn(a, t, fetch)
            if url:
                return url
        except Exception:
            errors += 1
    if errors == 2:              # aucun catalogue joignable (pas de réseau…)
        raise ConnectionError("catalogues injoignables")
    return None


# ------------------------------------------------------------ point d'entrée

def get_cover(row: dict, logos: set[str], online: bool = True,
              fetch_json=_get_json, fetch_bytes=_get_bytes) -> bytes | None:
    """Jaquette d'un morceau (octets JPEG / PNG) ou None. Peut prendre quelques
    secondes la 1re fois (internet) : à appeler hors de l'interface."""
    key = _key(row["path"])
    d = cache_dir()
    for ext in ("jpg", "png"):
        f = d / f"{key}.{ext}"
        if f.exists():
            return f.read_bytes()

    emb = embedded(row["path"])
    is_logo = emb is not None and hashlib.md5(emb).hexdigest() in logos

    miss = d / f"{key}.none"
    recently_missed = miss.exists() and time.time() - miss.stat().st_mtime < RETRY_AFTER
    if online and not recently_missed:
        try:
            url = search_online(row.get("artist") or "", row.get("title") or "", fetch_json)
            if url:
                img = fetch_bytes(url)
                if img and len(img) > 1000:
                    ext = "png" if img[:4] == b"\x89PNG" else "jpg"
                    (d / f"{key}.{ext}").write_bytes(img)
                    miss.unlink(missing_ok=True)
                    return img
            miss.touch()
        except Exception:
            pass                                     # pas de réseau : on réessaiera plus tard
    return emb if (emb and not is_logo) else None
