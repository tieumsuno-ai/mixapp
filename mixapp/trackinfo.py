"""Infos sur le morceau en cours : artiste, année de sortie et une anecdote
(classement dans les charts, record, histoire du titre…).

Sources gratuites, sans compte : MusicBrainz (date de sortie) et Wikipédia
(français, sinon anglais). Rien n'est inventé : le texte affiché est un
extrait de l'article Wikipédia du titre, avec la source. Si l'article n'est
pas clairement celui du morceau (bon titre ET bon artiste), on n'affiche rien.
Résultats gardés en cache (une recherche par morceau).
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

from .covers import _get_json, _norm, artists_of, clean_title, _artist_ok, _title_ok

RETRY_AFTER = 7 * 24 * 3600

# mots qui signalent une anecdote intéressante (classements, ventes, records…)
FACT_WORDS = {
    "fr": ["numéro un", "numéro 1", "n°1", "n° 1", "première place", "classement", "top 50",
           "top 10", "hit-parade", "exemplaires", "disque d'or", "disque de platine", "diamant",
           "certifié", "record", "semaines", "tube", "reprise", "écrit", "enregistré", "inspir",
           "clip", "grammy", "victoire"],
    "en": ["number one", "number 1", "no. 1", "topped", "peaked", "chart", "billboard",
           "copies", "certified", "platinum", "gold", "record", "weeks", "written", "recorded",
           "inspired", "grammy", "cover"],
}
SONG_WORDS = {"fr": ["chanson", "single", "titre", "morceau", "tube"],
              "en": ["song", "single", "track"]}


def _year_musicbrainz(artist: str, title: str, fetch=_get_json) -> str | None:
    q = f'recording:"{clean_title(title)}" AND artist:"{artist}"'
    url = "https://musicbrainz.org/ws/2/recording?" + urllib.parse.urlencode(
        {"query": q, "fmt": "json", "limit": 15})
    data = fetch(url)
    years = []
    for rec in data.get("recordings", []):
        if int(rec.get("score", 0)) < 85:
            continue
        credit = " ".join(c.get("name", "") for c in rec.get("artist-credit", []) if isinstance(c, dict))
        if not (_artist_ok(artist, credit) and _title_ok(title, rec.get("title", ""))):
            continue
        d = rec.get("first-release-date") or ""
        if re.match(r"^\d{4}", d):
            years.append(int(d[:4]))
    return str(min(years)) if years else None


def _sentences(text: str):
    text = re.sub(r"\s+", " ", text or "").strip()
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ«\"0-9])", text) if s.strip()]


def _wiki(lang: str, artist: str, title: str, fetch=_get_json):
    t = clean_title(title)
    api = f"https://{lang}.wikipedia.org/w/api.php?"
    q = api + urllib.parse.urlencode({"action": "query", "list": "search", "format": "json",
                                      "srsearch": f'"{t}" {artist}', "srlimit": 6})
    hits = fetch(q).get("query", {}).get("search", [])
    for h in hits:
        page = h.get("title", "")
        if not _title_ok(t, re.sub(r"\s*\(.*?\)\s*", " ", page)):
            continue
        ex = api + urllib.parse.urlencode({"action": "query", "prop": "extracts", "explaintext": 1,
                                           "exsectionformat": "plain", "redirects": 1,
                                           "titles": page, "format": "json"})
        pages = fetch(ex).get("query", {}).get("pages", {})
        text = next(iter(pages.values()), {}).get("extract", "") if pages else ""
        intro = text[:1500].lower()
        if not any(w in intro for w in SONG_WORDS[lang]):
            continue                                   # pas un article sur une chanson
        if not any(_norm(a) and _norm(a) in _norm(intro) for a in artists_of(artist)):
            continue                                   # pas le bon artiste
        return page, text
    return None


def _pick_fact(lang: str, text: str) -> str | None:
    sents = _sentences(text)
    if not sents:
        return None
    scored = []
    for i, s in enumerate(sents[:80]):
        if len(s) < 40 or len(s) > 260:
            continue
        low = s.lower()
        score = sum(2 for w in FACT_WORDS[lang] if w in low)
        score += 1 if re.search(r"\b(19|20)\d{2}\b", s) else 0
        score -= i * 0.02                               # préférer le début de l'article
        if score > 1:
            scored.append((score, i, s))
    if scored:
        best = max(scored)
        return best[2]
    first = sents[0]
    return first if len(first) <= 260 else None


ARTIST_WORDS = {"fr": ["chanteu", "groupe", "rappeu", "musicien", "dj", "disc jockey", "producteur",
                       "compositeu", "auteur-compositeur", "artiste", "duo", "orchestre"],
                "en": ["singer", "band", "rapper", "musician", "dj", "disc jockey", "producer",
                       "songwriter", "group", "duo", "artist"]}


def _wiki_artist(lang: str, artist: str, fetch=_get_json):
    """Début de l'article Wikipédia sur l'artiste (si c'est bien lui : même nom
    et article qui parle d'un musicien / groupe)."""
    api = f"https://{lang}.wikipedia.org/w/api.php?"
    q = api + urllib.parse.urlencode({"action": "query", "list": "search", "format": "json",
                                      "srsearch": artist, "srlimit": 5})
    for h in fetch(q).get("query", {}).get("search", []):
        page = h.get("title", "")
        if not _artist_ok(artist, re.sub(r"\s*\(.*?\)\s*", " ", page)):
            continue
        ex = api + urllib.parse.urlencode({"action": "query", "prop": "extracts", "explaintext": 1,
                                           "exintro": 1, "redirects": 1, "titles": page,
                                           "format": "json"})
        pages = fetch(ex).get("query", {}).get("pages", {})
        text = next(iter(pages.values()), {}).get("extract", "") if pages else ""
        if not any(w in text[:600].lower() for w in ARTIST_WORDS[lang]):
            continue
        sents = [x for x in _sentences(text) if 30 <= len(x) <= 260]
        if sents:
            fact = sents[0] if len(sents) == 1 or len(sents[0]) + len(sents[1]) > 260 \
                else sents[0] + " " + sents[1]
            return page, fact
    return None


KEY_FR = {"C": "Do", "C#": "Do♯", "Db": "Ré♭", "D": "Ré", "D#": "Ré♯", "Eb": "Mi♭", "E": "Mi",
          "F": "Fa", "F#": "Fa♯", "Gb": "Sol♭", "G": "Sol", "G#": "Sol♯", "Ab": "La♭", "A": "La",
          "A#": "La♯", "Bb": "Si♭", "B": "Si"}


def key_fr(name: str | None) -> str | None:
    if not name:
        return None
    minor = name.endswith("m")
    base = KEY_FR.get(name[:-1] if minor else name)
    return f"{base} {'mineur' if minor else 'majeur'}" if base else None


def _neighbours(cam: str) -> set[str]:
    n, l = int(cam[:-1]), cam[-1]
    return {cam, f"{n % 12 + 1}{l}", f"{(n - 2) % 12 + 1}{l}", f"{n}{'A' if l == 'B' else 'B'}"}


def fiche(row: dict, rows: list[dict]) -> str | None:
    """Fiche du morceau tirée de l'analyse de MixApp (toujours vraie, sans
    internet) : tempo, tonalité, énergie comparée à la playlist et morceaux
    qui se mixent le mieux avec lui."""
    bpm, cam = row.get("bpm"), row.get("camelot")
    if not bpm:
        return None
    parts = [f"{bpm:.0f} BPM"]
    k = key_fr(row.get("key_name"))
    if k and cam:
        parts.append(f"en {k} ({cam})")
    others = [o for o in rows if o.get("id") != row.get("id") and o.get("bpm")]
    txt = ", ".join(parts)
    en = [o.get("energy") for o in others if o.get("energy") is not None]
    if row.get("energy") is not None and len(en) >= 3:
        rank = sum(e < row["energy"] for e in en) / len(en)
        txt += (", parmi les plus énergiques de la playlist" if rank > 0.66 else
                ", plutôt calme comparé au reste de la playlist" if rank < 0.34 else
                ", d'énergie moyenne pour la playlist")
    txt += "."
    if cam and others:
        try:
            near = _neighbours(cam)
            ok = [clean_title(o.get("title") or o.get("filename") or "") for o in others
                  if o.get("camelot") in near and abs(o["bpm"] / bpm - 1) <= 0.05]
        except (ValueError, ZeroDivisionError):
            ok = []
        ok = [t for t in ok if t]
        if ok:
            txt += " Se mixe idéalement avec " + ", ".join(ok[:3]) + " (tempo et clé compatibles)."
        else:
            txt += " Aucun autre morceau de la playlist n'est dans une clé voisine."
    return txt


def lookup(artist: str, title: str, fetch=_get_json) -> dict:
    """{year, fact, source, url} (les champs introuvables sont absents)."""
    arts = artists_of(artist)
    main_artist = arts[0] if arts else artist
    t = clean_title(title)
    if " - " in t:                                     # « Artiste - Titre » dans le titre
        left, right = [x.strip() for x in t.split(" - ", 1)]
        main_artist, t = artists_of(left)[0] if artists_of(left) else left, right
    out: dict = {}
    try:
        y = _year_musicbrainz(main_artist, t, fetch)
        if y:
            out["year"] = y
    except Exception:
        pass
    for lang in ("fr", "en"):
        try:
            hit = _wiki(lang, main_artist, t, fetch)
        except Exception:
            continue
        if hit:
            page, text = hit
            fact = _pick_fact(lang, text)
            if fact:
                out.update(fact=fact, source=f"Wikipédia ({lang})",
                           url=f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(page.replace(" ", "_")))
                if "year" not in out:
                    m = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", text[:600])
                    if m:
                        out["year"] = m.group(1)
            break
    if "fact" not in out:                              # rien sur le morceau : l'artiste
        for lang in ("fr", "en"):
            try:
                hit = _wiki_artist(lang, main_artist, fetch)
            except Exception:
                continue
            if hit:
                page, fact = hit
                out.update(artist_fact=fact, source=f"Wikipédia ({lang})",
                           url=f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(page.replace(" ", "_")))
                break
    return out


class InfoCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except Exception:
            self.data = {}

    def get(self, key: str):
        e = self.data.get(key)
        if e is None:
            return None
        if not e.get("fact") and not e.get("year") and time.time() - e.get("t", 0) > RETRY_AFTER:
            return None                                 # rien trouvé il y a longtemps : réessayer
        return e

    def put(self, key: str, info: dict):
        self.data[key] = dict(info, t=time.time())
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False))
        except Exception:
            pass
