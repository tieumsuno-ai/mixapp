#!/usr/bin/env python3
# Relais de l'écran maison — tourne en continu sur le NAS (Container Station).
# Toutes les 15 minutes :
#   1. met à jour la page (index.html) dans le dossier Web si une nouvelle version est publiée ;
#   2. une fois par jour, relève les actualités des sites des mairies (ACTUS) dans actus.json ;
#   2 bis. si FOOT_FFF est fourni, résultats et classement de clubs amateurs (API FFF) dans foot_fff.json ;
#   2 ter. si l'adresse iCal secrète d'un agenda Google est fournie (AGENDA_ICS), écrit agenda.json ;
#   3. si les codes Netatmo sont fournis, relève la température de chaque pièce
#      et l'écrit dans interieur.json, que la page affiche.
# En parallèle, une petite API (port 8099) permet à la page de piloter les enceintes Sonos
# du réseau local (lecture, pause, titre suivant/précédent, volume du groupe),
# et, si une box Tydom est configurée, les lumières et volets Delta Dore (connexion locale à la box).
# Les codes Netatmo arrivent par variables d'environnement au premier démarrage ;
# le jeton de renouvellement (il change à chaque fois) est ensuite gardé dans /config/jeton.json.
# config.js (réglages personnels) n'est jamais touché.
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
import base64, datetime, email.utils, hashlib, html, unicodedata, ipaddress, json, os, re, socket, ssl, struct, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = "https://api.netatmo.com"
DEPOT = os.environ.get("DEPOT", "https://raw.githubusercontent.com/tieumsuno-ai/mixapp/main/ecran-maison/")
WEB = "/web"
CONF = "/config/jeton.json"
INTERVALLE = int(os.environ.get("INTERVALLE_MINUTES", "15")) * 60
NETATMO = bool(os.environ.get("NETATMO_CLIENT_ID") and os.environ.get("NETATMO_CLIENT_SECRET"))


def log(*a):
    print(datetime.datetime.now().strftime("%d/%m %H:%M"), *a, flush=True)


def ecrire(nom, contenu):
    cible = os.path.join(WEB, nom)
    tmp = cible + ".tmp"
    with open(tmp, "wb") as f:
        f.write(contenu)
    os.chmod(tmp, 0o644)
    os.replace(tmp, cible)


# ---------- 1. Mise à jour de la page ----------
def maj_page():
    try:
        with urllib.request.urlopen(DEPOT + "index.html?t=%d" % time.time(), timeout=30) as r:
            nouv = r.read()
        if len(nouv) < 5000 or b"<!DOCTYPE html>" not in nouv[:300] or b"config.js" not in nouv:
            log("Page téléchargée invalide, ignorée")
            return
        try:
            with open(os.path.join(WEB, "index.html"), "rb") as f:
                anc = f.read()
        except OSError:
            anc = b""
        if nouv != anc:
            ecrire("index.html", nouv)
            log("Page mise à jour (%d octets)" % len(nouv))
    except Exception as e:
        log("Mise à jour de la page impossible :", repr(e))


# ---------- 2 bis. Actualités des mairies (flux RSS des sites officiels) ----------
ACTUS = [x.strip() for x in os.environ.get("ACTUS", "").split(",") if x.strip()]   # « Nom=https://site/feed/ »
ACTUS_HEURE = int(os.environ.get("ACTUS_HEURE", "6"))   # une seule lecture par jour, à partir de 6 h
actus_jour = None


def actus_a_faire():
    """Vrai au démarrage, puis une fois par jour après ACTUS_HEURE."""
    maintenant = datetime.datetime.now()
    if actus_jour is None:
        return True
    return actus_jour != maintenant.date() and maintenant.hour >= ACTUS_HEURE


def texte_propre(t):
    t = re.sub(r"<[^>]+>", " ", html.unescape(t or ""))
    t = unicodedata.normalize("NFKC", t).replace("...", "…")   # 𝗔𝗕𝗖 (gras « Facebook ») → ABC
    t = re.sub(r"\s*\[(…|&hellip;|\.\.\.)\]\s*$", "…", t)
    t = re.sub(r"(Lire la suite|Continue reading|The post .*? appeared first on).*$", "", t)
    return re.sub(r"\s+", " ", t).strip()


def maj_actus():
    global actus_jour
    tout = []
    for source in ACTUS:
        nom, _, url = source.partition("=")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ecran-maison)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                xml = r.read().decode("utf-8", "replace")
            for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:6]:
                def c(nom_champ):
                    m = re.search(r"<%s[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</%s>" % (nom_champ, nom_champ), item, re.S)
                    return m.group(1) if m else ""
                try:
                    date = email.utils.parsedate_to_datetime(c("pubDate")).astimezone().strftime("%Y-%m-%dT%H:%M")
                except Exception:
                    continue
                contenu = c("content:encoded")
                img = re.search(r'<img[^>]+src="([^"]+)"', html.unescape(contenu))
                resume = texte_propre(c("description")) or texte_propre(contenu)
                titre = texte_propre(c("title"))
                lettres = [x for x in titre if x.isalpha()]
                if lettres and sum(x.isupper() for x in lettres) > 0.7 * len(lettres):   # titre tout en capitales
                    titre = titre.lower()
                    titre = re.sub(r"\b" + re.escape(nom.strip().lower()) + r"\b", nom.strip(), titre)
                    i = next((k for k, x in enumerate(titre) if x.isalpha()), 0)
                    titre = titre[:i] + titre[i:i + 1].upper() + titre[i + 1:]
                resume = re.sub(r"[.…]\s*…$", ".", resume)
                tout.append({"source": nom.strip(), "titre": titre, "date": date,
                             "resume": resume[:220] + ("…" if len(resume) > 220 else ""),
                             "image": img.group(1) if img else "", "lien": texte_propre(c("link"))})
        except Exception as e:
            log("Actus %s indisponibles :" % nom, repr(e))
    if tout:
        actus_jour = datetime.datetime.now().date()
        tout.sort(key=lambda x: x["date"], reverse=True)
        ecrire("actus.json", json.dumps(tout, ensure_ascii=False).encode("utf-8"))


# ---------- 2 ter. Agenda Google (adresse secrète au format iCal) ----------
AGENDA_ICS = os.environ.get("AGENDA_ICS", "")
AGENDA_JOURS = int(os.environ.get("AGENDA_JOURS", "14"))
JOURS_ICS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def date_ics(valeur, params):
    """→ (datetime local naïf, journée entière ?)"""
    v = valeur.strip()
    if "VALUE=DATE" in params or len(v) == 8:
        return datetime.datetime.strptime(v[:8], "%Y%m%d"), True
    d = datetime.datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        d = d.replace(tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)
    else:
        m = re.search(r"TZID=([^;:]+)", params)
        if m:
            try:
                from zoneinfo import ZoneInfo
                d = d.replace(tzinfo=ZoneInfo(m.group(1))).astimezone().replace(tzinfo=None)
            except Exception:
                pass
    return d, False


def occurrences(debut, regle, exdates, fin_fenetre):
    """Dates de début d'un événement répétitif (RRULE simple : DAILY/WEEKLY/MONTHLY/YEARLY)."""
    r = dict(x.split("=", 1) for x in regle.split(";") if "=" in x)
    freq, pas = r.get("FREQ"), int(r.get("INTERVAL", "1"))
    compte = int(r["COUNT"]) if "COUNT" in r else None
    jusqua = date_ics(r["UNTIL"], "")[0] if "UNTIL" in r else None
    jours = [JOURS_ICS[j[-2:]] for j in r.get("BYDAY", "").split(",") if j[-2:] in JOURS_ICS] if freq == "WEEKLY" else []
    res, n, i = [], 0, 0
    while i < 3000:
        if freq == "DAILY":
            cands = [debut + datetime.timedelta(days=i * pas)]
        elif freq == "WEEKLY":
            lundi = debut - datetime.timedelta(days=debut.weekday()) + datetime.timedelta(weeks=i * pas)
            cands = [lundi + datetime.timedelta(days=j) for j in sorted(jours)] if jours else [debut + datetime.timedelta(weeks=i * pas)]
        elif freq in ("MONTHLY", "YEARLY"):
            mois = debut.month - 1 + (i * pas if freq == "MONTHLY" else i * pas * 12)
            try:
                cands = [debut.replace(year=debut.year + mois // 12, month=mois % 12 + 1)]
            except ValueError:
                cands = []
        else:
            return [debut]
        i += 1
        for c in cands:
            if c < debut:
                continue
            if (jusqua and c > jusqua) or c > fin_fenetre or (compte is not None and n >= compte):
                return res
            n += 1
            if c not in exdates:
                res.append(c)
    return res


def maj_agenda():
    try:
        with urllib.request.urlopen(AGENDA_ICS, timeout=30) as r:
            ics = r.read().decode("utf-8", "replace")
    except Exception as e:
        log("Agenda indisponible :", type(e).__name__)      # l'adresse est secrète : pas de détail dans le journal
        return
    ics = re.sub(r"\r?\n[ \t]", "", ics)                     # lignes repliées
    auj = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    fin = auj + datetime.timedelta(days=AGENDA_JOURS)
    evts, annules = [], set()
    for bloc in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ics, re.S):
        champs, exdates = {}, set()
        for ligne in bloc.strip().splitlines():
            if ":" not in ligne:
                continue
            tete, _, val = ligne.partition(":")
            nom, _, params = tete.partition(";")
            if nom == "EXDATE":
                for v in val.split(","):
                    exdates.add(date_ics(v, params)[0])
            else:
                champs.setdefault(nom, (val, params))
        if "DTSTART" not in champs or champs.get("STATUS", ("",))[0] == "CANCELLED":
            if "RECURRENCE-ID" in champs:
                annules.add((champs.get("UID", ("",))[0], date_ics(*champs["RECURRENCE-ID"])[0]))
            continue
        debut, journee = date_ics(*champs["DTSTART"])
        fin_evt = date_ics(*champs["DTEND"])[0] if "DTEND" in champs else debut + datetime.timedelta(days=1 if journee else 0, hours=0 if journee else 1)
        duree = fin_evt - debut
        uid = champs.get("UID", ("",))[0]
        if "RECURRENCE-ID" in champs:                          # occurrence modifiée d'une série
            annules.add((uid, date_ics(*champs["RECURRENCE-ID"])[0]))
        dates = occurrences(debut, champs["RRULE"][0], exdates, fin) if "RRULE" in champs else [debut]
        titre = champs.get("SUMMARY", ("(sans titre)",))[0].replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").strip()
        lieu = champs.get("LOCATION", ("",))[0].replace("\\,", ",").replace("\\n", " ").strip()
        for d in dates:
            f = d + duree
            if f <= auj or d >= fin:
                continue
            evts.append({"uid": uid, "rid": d, "mod": "RECURRENCE-ID" in champs, "titre": titre, "lieu": lieu.split(",")[0],
                         "debut": d.strftime("%Y-%m-%dT%H:%M"), "fin": f.strftime("%Y-%m-%dT%H:%M"), "journee": journee})
    evts = [e for e in evts if e["mod"] or (e["uid"], e["rid"]) not in annules]
    for e in evts:
        for k in ("uid", "rid", "mod"):
            e.pop(k, None)
    evts.sort(key=lambda e: (e["debut"], not e["journee"]))
    ecrire("agenda.json", json.dumps(evts, ensure_ascii=False).encode("utf-8"))


# ---------- 2 quater. Menus de la cantine (PDF déposés dans le dossier Web/cantine) ----------
# Déposez chaque mois le PDF du menu dans Web/cantine ; le relais le lit et écrit cantine.json.
CANTINE = os.path.join(WEB, "cantine")
JOURS_MENU = ["LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI"]
pdfminer_pret = False


def preparer_pdfminer():
    global pdfminer_pret
    if pdfminer_pret:
        return True
    try:
        import pdfminer  # noqa: F401
    except ImportError:
        import subprocess, sys
        log("Cantine : installation du lecteur de PDF…")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "pdfminer.six"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            log("Cantine : installation impossible", r.stderr[-300:])
            return False
    pdfminer_pret = True
    return True


def lignes_pdf(page):
    from pdfminer.layout import LTTextLine
    res, H = [], page.height
    def visite(o):
        if isinstance(o, LTTextLine):
            t = o.get_text().strip()
            if t:
                res.append((o.x0, o.x1, H - o.y1, t))
        elif hasattr(o, "__iter__"):
            for e in o:
                visite(e)
    visite(page)
    return res


def lire_menu(chemin, annee_ref=None):
    menus = {}
    from pdfminer.high_level import extract_pages
    for page in extract_pages(chemin):
        L = lignes_pdf(page)
        tout = " ".join(t for _, _, _, t in L)
        m = re.search(r"Semaine du (\d\d)/(\d\d) au (\d\d)/(\d\d)", tout)
        if not m:
            continue
        # colonnes : centres des en-têtes de jours
        centres = {}
        for x0, x1, y, t in L:
            for j in JOURS_MENU:
                if t.upper() == j:
                    centres[j] = (x0 + x1) / 2
        if len(centres) < 5:
            continue
        cx = [centres[j] for j in JOURS_MENU]
        bornes_x = [cx[0] - (cx[1] - cx[0]) / 2] + [(cx[i] + cx[i + 1]) / 2 for i in range(4)] + [cx[4] + (cx[4] - cx[3]) / 2]
        haut_jours = min(y for x0, x1, y, t in L if t.upper() in JOURS_MENU)
        # lignes : étiquettes de la colonne de gauche
        etiq = []
        for x0, x1, y, t in L:
            if x1 < bornes_x[0] + 5:
                tu = t.upper()
                if tu.startswith("HORS"): etiq.append((y, "entree"))
                elif tu.startswith("PLAT"): etiq.append((y, "plat"))
                elif tu.startswith("GARNITURE"): etiq.append((y, "garniture"))
                elif tu.startswith("PRODUIT"): etiq.append((y, "laitier"))
                elif tu.startswith("DESSERT"): etiq.append((y, "dessert"))
                elif tu.startswith("DÉCLINAISON") or tu.startswith("DECLINAISON"): etiq.append((y, "sansporc"))
        etiq.sort()
        if not etiq:
            continue
        # la 1re « déclinaison sans porc » suit l'entrée, la 2e suit le plat
        noms, vu_plat = [], False
        for y, n in etiq:
            if n == "plat": vu_plat = True
            if n == "sansporc": n = "plat_sansporc" if vu_plat else "entree_sansporc"
            noms.append((y, n))
        bornes_y = [haut_jours + 8]
        for i in range(len(noms) - 1):
            bornes_y.append((noms[i][0] + noms[i + 1][0]) / 2 + 4)
        dernier = noms[-1][0]
        bornes_y.append(dernier + 28)              # la légende commence juste sous le dessert
        # date du lundi
        a = annee_ref or datetime.date.today().year
        lundi = datetime.date(a, int(m.group(2)), int(m.group(1)))
        if annee_ref is None and (lundi - datetime.date.today()).days > 200:
            lundi = lundi.replace(year=a - 1)
        if annee_ref is None and (datetime.date.today() - lundi).days > 200:
            lundi = lundi.replace(year=a + 1)
        cases = {}
        for x0, x1, y, t in sorted(L, key=lambda e: (e[2], e[0])):
            xc = (x0 + x1) / 2
            if xc < bornes_x[0] or xc > bornes_x[5] or y < bornes_y[0] or y > bornes_y[-1]:
                continue
            col = next(i for i in range(5) if bornes_x[i] <= xc <= bornes_x[i + 1])
            lig = next((i for i in range(len(noms)) if bornes_y[i] <= y < bornes_y[i + 1]), None)
            if lig is None or re.search(r"agriculture|biologique|végétarien|Fabrication maison|LE CHEF|ET SON|SOUHAITENT|clicetmiam|code à saisir|Produit de saison|contenant du porc", t, re.I):
                continue
            cases.setdefault((col, noms[lig][1]), []).append(t)
        for col in range(5):
            jour = (lundi + datetime.timedelta(days=col)).isoformat()
            d = {}
            for (c, n), morceaux in cases.items():
                if c == col:
                    texte = re.sub(r"\s+", " ", " ".join(morceaux)).strip()
                    if texte:
                        d[n] = texte
            if d:
                menus[jour] = d
    return menus



def maj_cantine():
    try:
        os.makedirs(CANTINE, exist_ok=True)
        os.chmod(CANTINE, 0o777)
    except OSError:
        pass
    try:
        pdfs = [os.path.join(CANTINE, n) for n in os.listdir(CANTINE) if n.lower().endswith(".pdf")]
    except OSError:
        return
    if not pdfs:
        return
    cible = os.path.join(WEB, "cantine.json")
    recent = max(os.path.getmtime(x) for x in pdfs)
    if os.path.exists(cible) and os.path.getmtime(cible) >= recent:
        return
    if not preparer_pdfminer():
        return
    menus = {}
    for x in sorted(pdfs, key=os.path.getmtime):
        try:
            menus.update(lire_menu(x))
        except Exception as e:
            log("Cantine : PDF illisible", os.path.basename(x), repr(e))
    if menus:
        ecrire("cantine.json", json.dumps(menus, ensure_ascii=False).encode("utf-8"))
        log("Cantine : %d jours de menus (%s → %s)" % (len(menus), min(menus), max(menus)))


# ---------- 2 quinquies. Clubs amateurs (championnats et coupes FFF) ----------
# FOOT_FFF = « 17767=Chambly » (numéro de club FFF = nom court ; plusieurs clubs séparés par des virgules).
# Résultats, calendrier et classement de l'équipe première senior, lus sur l'API publique de la FFF
# (celle des sites des districts et ligues) ; la page ne peut pas la lire elle-même (pas de CORS) → foot_fff.json.
FOOT_FFF = [x.strip() for x in os.environ.get("FOOT_FFF", "").split(",") if x.strip()]
FFF = "https://api-dofa.fff.fr"
fff_derniere = 0
SIGLES = {"SAS", "ASC", "FCSR", "USCO", "ESTAC", "OGC", "AJA", "SCO", "USL", "USM", "ESA", "ASPTT"}
PETITS_MOTS = {"DE", "DU", "DES", "LA", "LE", "LES", "ST", "STE", "SUR", "EN", "AU", "ET"}


def fff_json(chemin):
    req = urllib.request.Request(FFF + chemin, headers={"User-Agent": "Mozilla/5.0 (ecran-maison)", "Accept": "application/ld+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def nom_club(n):
    """« CHAMBLY OISE FC » → « Chambly Oise FC » ; sigles (FC, US, AS…) gardés en capitales."""
    mots = []
    for m in (n or "").split():
        if m in PETITS_MOTS:
            mots.append(m.capitalize() if m in ("ST", "STE") else m.lower())
        elif m == "S/MER":
            mots.append("s/Mer")
        elif len(m.strip(".")) <= 3 and "." not in m[:-1] or m in SIGLES:
            mots.append(m)
        else:
            mots.append(re.sub(r"[^\W\d_]+", lambda x: x.group(0).capitalize(), m))
    t = " ".join(mots)
    return t[:1].upper() + t[1:]


def est_nous(eq, club):
    return bool(eq) and (eq.get("club") or {}).get("cl_no") == club and eq.get("category_code") == "SEM" and eq.get("number") == 1


def match_fff(x, club):
    dom, ext = x.get("home") or {}, x.get("away") or {}
    if not x.get("date") or not (est_nous(dom, club) or est_nous(ext, club)):
        return None
    c = x.get("competition") or {}
    if c.get("type") not in ("CH", "CP") or x.get("seems_postponed") == "O":   # pas les amicaux
        return None
    h = re.match(r"(\d{1,2})H(\d{2})", x.get("time") or "")
    date = x["date"][:10] + ("T%02d:%s" % (int(h.group(1)), h.group(2)) if h else "T00:00")
    sd, se = x.get("home_score"), x.get("away_score")
    nom_c = c.get("name") or ""
    if c.get("type") == "CP" and "COUPE DE FRANCE" in nom_c.upper():
        nom_c = "Coupe de France"
    elif c.get("type") == "CP":
        nom_c = nom_club(nom_c)
    m = {"date": date, "heure": bool(h), "compet": nom_c, "type": c.get("type"),
         "dom": nom_club(dom.get("short_name")), "ext": nom_club(ext.get("short_name")),
         "logoDom": (dom.get("club") or {}).get("logo") or "", "logoExt": (ext.get("club") or {}).get("logo") or "",
         "nousDom": est_nous(dom, club), "termine": sd is not None and se is not None, "sd": sd, "se": se}
    if x.get("home_nb_tir_but") is not None and x.get("away_nb_tir_but") is not None:
        m["tab"] = [x["home_nb_tir_but"], x["away_nb_tir_but"]]
    if c.get("type") == "CH":
        m["poule"] = [c.get("cp_no"), (x.get("phase") or {}).get("number"), (x.get("poule") or {}).get("stage_number"),
                      (x.get("poule") or {}).get("name") or "", (x.get("poule_journee") or {}).get("number")]
    return m


def classement_fff(cp, phase, poule, club):
    l = fff_json("/api/compets/%s/phases/%s/poules/%s/classement_journees" % (cp, phase, poule)).get("hydra:member") or []
    table = []
    for e in l:
        eq = e.get("equipe") or {}
        bp, bc = e.get("goals_for_count"), e.get("goals_against_count")
        table.append({"rang": e.get("rank"), "nom": nom_club(eq.get("short_name")), "pts": e.get("point_count"),
                      "j": e.get("total_games_count"), "g": e.get("won_games_count"), "n": e.get("draw_games_count"),
                      "p": e.get("lost_games_count"),
                      "diff": bp - bc if isinstance(bp, int) and isinstance(bc, int) else e.get("goals_diff"),
                      "nous": est_nous(eq, club)})
    table.sort(key=lambda t: t["rang"] or 99)
    return table


def equipe_fff(club, court):
    debut = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    chemin, matchs, vus, logo = "/api/clubs/%d/matchs?ma_dat%%5Bafter%%5D=%s" % (club, debut), [], set(), ""
    for _ in range(25):
        a = fff_json(chemin)
        for x in a.get("hydra:member") or []:
            m = match_fff(x, club)
            if m and x.get("ma_no") not in vus:
                vus.add(x.get("ma_no"))
                matchs.append(m)
            for cote in ("home", "away"):
                if est_nous(x.get(cote), club) and (x[cote].get("club") or {}).get("logo"):
                    logo = x[cote]["club"]["logo"]
        suite = (a.get("hydra:view") or {}).get("hydra:next")
        if not suite:
            break
        chemin = suite
    matchs.sort(key=lambda m: m["date"])
    res = {"nom": court, "court": court, "club": club, "logo": logo, "matchs": matchs}
    champ_ = [m for m in matchs if m.get("poule")]
    if champ_:
        p = champ_[-1]["poule"]
        res["compet"] = champ_[-1]["compet"] + (" · " + nom_club(p[3]) if p[3] else "")
        res["competCourt"] = champ_[-1]["compet"]
        try:
            res["table"] = classement_fff(p[0], p[1], p[2], club)
        except Exception as e:
            log("FFF : classement indisponible", repr(e))
    return res


def maj_fff():
    global fff_derniere
    maintenant = time.time()
    aujourdhui = datetime.date.today().isoformat()
    jour_de_match = False
    try:
        with open(os.path.join(WEB, "foot_fff.json"), encoding="utf-8") as f:
            for e in json.load(f).get("equipes", []):
                jour_de_match |= any(m["date"][:10] == aujourdhui for m in e.get("matchs", []))
    except Exception:
        pass
    if maintenant - fff_derniere < (14 * 60 if jour_de_match else 55 * 60):
        return
    equipes = []
    for x in FOOT_FFF:
        num, _, court = x.partition("=")
        try:
            equipes.append(equipe_fff(int(num), court.strip() or num))
        except Exception as e:
            log("FFF : club %s indisponible" % num, repr(e))
    if equipes:
        fff_derniere = maintenant
        ecrire("foot_fff.json", json.dumps({"maj": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M"), "equipes": equipes},
                                           ensure_ascii=False).encode("utf-8"))


# ---------- 2. Netatmo ----------
def post(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get(url, token, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def charger_refresh():
    try:
        with open(CONF) as f:
            return json.load(f)["refresh_token"]
    except Exception:
        return os.environ["NETATMO_REFRESH_TOKEN"]


def sauver_refresh(rt):
    tmp = CONF + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"refresh_token": rt}, f)
    os.replace(tmp, CONF)


def nouveau_jeton():
    t = post(API + "/oauth2/token", {
        "grant_type": "refresh_token",
        "refresh_token": charger_refresh(),
        "client_id": os.environ["NETATMO_CLIENT_ID"],
        "client_secret": os.environ["NETATMO_CLIENT_SECRET"],
    })
    sauver_refresh(t["refresh_token"])
    return t["access_token"], time.time() + t.get("expires_in", 10800) - 300


def releve(token):
    pieces = []
    for h in get(API + "/api/homesdata", token)["body"].get("homes", []):
        noms = {r["id"]: r.get("name", "?") for r in h.get("rooms", [])}
        if not noms:
            continue
        etat = get(API + "/api/homestatus", token, {"home_id": h["id"]})["body"]["home"]
        for r in etat.get("rooms", []):
            t = r.get("therm_measured_temperature")
            if t is None:
                continue
            pieces.append({"nom": noms.get(r["id"], "?"), "temp": t,
                           "consigne": r.get("therm_setpoint_temperature")})
    return pieces


token, expire = None, 0


def maj_netatmo():
    global token, expire
    try:
        if not token or time.time() > expire:
            token, expire = nouveau_jeton()
        p = releve(token)
        ecrire("interieur.json", json.dumps(p, ensure_ascii=False).encode("utf-8"))
        log("Netatmo OK :", ", ".join("%s %.1f°" % (x["nom"], x["temp"]) for x in p))
    except urllib.error.HTTPError as e:
        log("Erreur Netatmo", e.code, e.read()[:300])
        token = None
    except Exception as e:
        log("Erreur Netatmo", repr(e))
        token = None


# ---------- 3. Sonos (UPnP local, port 1400 des enceintes) ----------
SONOS = os.environ.get("SONOS", "1") != "0"
SONOS_PORT_API = int(os.environ.get("SONOS_PORT_API", "8099"))
SONOS_RESEAU = os.environ.get("SONOS_RESEAU", "")   # ex. 192.168.1.0/24 (ou 192.168.1)
SONOS_IPS = [x.strip() for x in os.environ.get("SONOS_IPS", "").split(",") if x.strip()]
SONOS_PAS_VOLUME = int(os.environ.get("SONOS_PAS_VOLUME", "3"))

AV = ("/MediaRenderer/AVTransport/Control", "urn:schemas-upnp-org:service:AVTransport:1")
GRC = ("/MediaRenderer/GroupRenderingControl/Control", "urn:schemas-upnp-org:service:GroupRenderingControl:1")
ZGT = ("/ZoneGroupTopology/Control", "urn:schemas-upnp-org:service:ZoneGroupTopology:1")


def soap(ip, service, action, args=None, delai=4):
    chemin, urn = service
    corps = "".join("<%s>%s</%s>" % (k, html.escape(str(v)), k) for k, v in (args or {}).items())
    env = ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
           's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:%s xmlns:u="%s">%s</u:%s>'
           '</s:Body></s:Envelope>') % (action, urn, corps, action)
    req = urllib.request.Request("http://%s:1400%s" % (ip, chemin), data=env.encode("utf-8"), method="POST",
                                 headers={"Content-Type": 'text/xml; charset="utf-8"',
                                          "SOAPACTION": '"%s#%s"' % (urn, action)})
    with urllib.request.urlopen(req, timeout=delai) as r:
        return r.read().decode("utf-8", "replace")


def champ(xml, nom):
    m = re.search(r"<(?:\w+:)?%s(?:\s[^>]*)?>(.*?)</(?:\w+:)?%s>" % (nom, nom), xml, re.S)
    return html.unescape(m.group(1)) if m else ""


def attr(balise, nom):
    m = re.search(r'\s%s="([^"]*)"' % nom, balise)
    return html.unescape(m.group(1)) if m else ""


def adresses_reseau(reseau=None):
    res = []
    for r in [x.strip() for x in (reseau if reseau is not None else SONOS_RESEAU).split(",") if x.strip()]:
        if "/" not in r:
            r = r.rstrip(".") + ".0/24"
        res += [str(h) for h in ipaddress.ip_network(r, strict=False).hosts()]
    return res


def port_ouvert(ip):
    try:
        with socket.create_connection((ip, 1400), timeout=0.6):
            return ip
    except OSError:
        return None


connues = list(SONOS_IPS)
topo = {"t": 0, "groupes": []}
verrou = threading.Lock()


def topologie():
    """Groupes d'enceintes : [{id, ip, nom}] (ip = coordinateur du groupe)."""
    global connues
    if time.time() - topo["t"] < 60 and topo["groupes"]:
        return topo["groupes"]
    with verrou:
        xml = None
        for ip in connues:
            try:
                xml = champ(soap(ip, ZGT, "GetZoneGroupState", delai=3), "ZoneGroupState")
                break
            except Exception:
                continue
        if xml is None:
            cand = adresses_reseau()
            if cand:
                log("Recherche des enceintes Sonos sur %d adresses…" % len(cand))
                with ThreadPoolExecutor(128) as ex:
                    ouverts = [i for i in ex.map(port_ouvert, cand) if i]
                for ip in ouverts:
                    try:
                        xml = champ(soap(ip, ZGT, "GetZoneGroupState", delai=3), "ZoneGroupState")
                        break
                    except Exception:
                        continue
        if xml is None:
            topo["t"], topo["groupes"] = time.time() - 40, []   # nouvel essai dans 20 s
            return []
        groupes, ips = [], []
        for m in re.finditer(r"<ZoneGroup\s([^>]*)>(.*?)</ZoneGroup>", xml, re.S):
            coord = attr(" " + m.group(1), "Coordinator")
            ip_coord, noms = None, []
            for b in re.findall(r"<(?:ZoneGroupMember|Satellite)\s[^>]*>", m.group(2)):
                u = re.search(r"//([\d.]+):1400", attr(b, "Location"))
                if u:
                    ips.append(u.group(1))
                if attr(b, "UUID") == coord and u:
                    ip_coord = u.group(1)
                if b.startswith("<ZoneGroupMember") and attr(b, "Invisible") != "1":
                    n = attr(b, "ZoneName")
                    if n and n not in noms:
                        noms.insert(0, n) if attr(b, "UUID") == coord else noms.append(n)
            if ip_coord and noms:
                groupes.append({"id": coord, "ip": ip_coord, "nom": " + ".join(noms)})
        nouv = sorted(set(ips))
        if nouv != sorted(connues):
            log("Sonos :", ", ".join(g["nom"] for g in groupes))
        connues = nouv or connues
        topo["t"], topo["groupes"] = time.time(), groupes
        return groupes


def secondes(t):
    try:
        h, m, s = [int(x) for x in t.split(":")]
        return h * 3600 + m * 60 + s
    except Exception:
        return 0


def etat_groupe(g):
    ip = g["ip"]
    e = {"id": g["id"], "nom": g["nom"], "etat": "?", "titre": "", "artiste": "", "album": "",
         "pochette": "", "volume": None, "pos": 0, "duree": 0}
    try:
        e["etat"] = champ(soap(ip, AV, "GetTransportInfo", {"InstanceID": 0}), "CurrentTransportState")
        pi = soap(ip, AV, "GetPositionInfo", {"InstanceID": 0})
        meta = champ(pi, "TrackMetaData")
        e["titre"] = champ(meta, "title")
        e["artiste"] = champ(meta, "creator")
        e["album"] = champ(meta, "album")
        flux = champ(meta, "streamContent")
        if flux and not e["artiste"]:           # radio : « Artiste - Titre »
            a, _, t = flux.partition(" - ")
            e["artiste"], e["titre"] = (a, t) if t else (e["titre"], flux)
        art = champ(meta, "albumArtURI")
        if art:
            e["pochette"] = art if art.startswith("http") else "http://%s:1400%s" % (ip, art)
        e["pos"], e["duree"] = secondes(champ(pi, "RelTime")), secondes(champ(pi, "TrackDuration"))
        e["volume"] = int(champ(soap(ip, GRC, "GetGroupVolume", {"InstanceID": 0}), "CurrentVolume") or 0)
    except Exception as ex:
        e["erreur"] = repr(ex)
        topo["t"] = 0                            # topologie à revoir
    return e


def commande(g, a):
    ip = g["ip"]
    if a in ("play", "pause", "next", "prev"):
        action = {"play": "Play", "pause": "Pause", "next": "Next", "prev": "Previous"}[a]
        args = {"InstanceID": 0}
        if a == "play":
            args["Speed"] = 1
        soap(ip, AV, action, args)
    elif a in ("volup", "voldown"):
        soap(ip, GRC, "SnapshotGroupVolume", {"InstanceID": 0})
        soap(ip, GRC, "SetRelativeGroupVolume",
             {"InstanceID": 0, "Adjustment": SONOS_PAS_VOLUME if a == "volup" else -SONOS_PAS_VOLUME})
    else:
        raise ValueError("action inconnue")
    time.sleep(0.35)


# ---------- 4. Delta Dore (box Tydom, connexion locale) ----------
# Variables : TYDOM_HOTE (adresse IP de la box), TYDOM_MAC (12 caractères, sur l'étiquette de la box)
# et soit TYDOM_MDP (mot de passe de la box), soit DELTADORE_EMAIL + DELTADORE_MDP (compte de l'appli Tydom,
# utilisé une seule fois pour récupérer le mot de passe de la box, ensuite gardé dans /config/tydom.json).
TYDOM_HOTE = os.environ.get("TYDOM_HOTE", "")
TYDOM_PORT = int(os.environ.get("TYDOM_PORT", "443"))
TYDOM_MAC = os.environ.get("TYDOM_MAC", "").replace(":", "").replace("-", "").upper()
TYDOM_RESEAU = os.environ.get("TYDOM_RESEAU", "")         # sinon SONOS_RESEAU ; sert à trouver la box si TYDOM_HOTE est vide
TYDOM = bool(TYDOM_MAC)
TYDOM_ACCUEIL = [x.strip() for x in os.environ.get("TYDOM_ACCUEIL", "").split(",") if x.strip()]   # lumières de l'écran principal
TYDOM_CONF = "/config/tydom.json"
DD_AUTH = ("https://deltadoreadb2ciot.b2clogin.com/deltadoreadb2ciot.onmicrosoft.com/v2.0/"
           ".well-known/openid-configuration?p=B2C_1_AccountProviderROPC_SignIn")
DD_CLIENT = "8782839f-3264-472a-ab87-4d4e23524da4"
DD_SCOPE = "openid profile offline_access " + " ".join("https://deltadoreadb2ciot.onmicrosoft.com/iotapi/" + x for x in (
    "video_config", "video_allowed", "sites_management_allowed", "sites_management_gateway_credentials",
    "sites_management_camera_credentials", "comptage_europe_collect_reader", "comptage_europe_site_config_contributor",
    "pilotage_allowed", "consent_mgt_contributor", "b2caccountprovider_manage_account", "b2caccountprovider_allow_view_account",
    "tydom_backend_allowed", "websocket_remote_access", "orkestrator_device", "orkestrator_view", "orkestrator_space",
    "orkestrator_connector", "orkestrator_endpoint", "rule_management_allowed", "collect_read_datas"))


def post_multipart(url, champs):
    """Envoi « multipart/form-data », comme l'appli Tydom."""
    borne = "----ecran" + os.urandom(8).hex()
    corps = b"".join(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n" % (borne, k)).encode() + v.encode("utf-8") + b"\r\n"
                     for k, v in champs.items()) + ("--%s--\r\n" % borne).encode()
    req = urllib.request.Request(url, data=corps, method="POST", headers={"Content-Type": "multipart/form-data; boundary=" + borne})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8", "replace"))
            detail = "%s — %s" % (err.get("error", ""), (err.get("error_description") or "").split("\r\n")[0][:160])
        except Exception:
            detail = "HTTP %d" % e.code
        raise RuntimeError("connexion au compte Delta Dore refusée : " + detail)
DD_SITES = "https://prod.iotdeltadore.com/sitesmanagement/api/v1/sites?gateway_mac="


def mdp_tydom():
    try:
        with open(TYDOM_CONF) as f:
            return json.load(f)["mdp"]
    except Exception:
        pass
    if os.environ.get("TYDOM_MDP"):
        return os.environ["TYDOM_MDP"]
    email, mdp = os.environ.get("DELTADORE_EMAIL"), os.environ.get("DELTADORE_MDP")
    if not (email and mdp):
        raise RuntimeError("mot de passe de la box Tydom absent (TYDOM_MDP ou DELTADORE_EMAIL/DELTADORE_MDP)")
    with urllib.request.urlopen(DD_AUTH, timeout=30) as r:
        jeton_url = json.load(r)["token_endpoint"]
    t = post_multipart(jeton_url, {"username": email, "password": mdp, "grant_type": "password",
                                   "client_id": DD_CLIENT, "scope": DD_SCOPE})
    sites = get(DD_SITES + TYDOM_MAC, t["access_token"]).get("sites", [])
    for site in sites:
        gw = site.get("gateway") or {}
        if gw.get("mac", "").upper() == TYDOM_MAC and gw.get("password"):
            tmp = TYDOM_CONF + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"mdp": gw["password"]}, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, TYDOM_CONF)
            log("Delta Dore : mot de passe de la box récupéré et gardé")
            return gw["password"]
    raise RuntimeError("box Tydom %s introuvable sur le compte Delta Dore" % TYDOM_MAC)


class WS:
    """Client websocket minimal (sans dépendance) avec authentification digest, comme l'appli Tydom."""

    def __init__(self, hote, port, chemin, utilisateur, mdp, distant=False):
        ctx = ssl.create_default_context()
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        if not distant:                           # la box utilise un certificat auto-signé et un vieux TLS
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                ctx.minimum_version = ssl.TLSVersion.TLSv1
                ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
            except (ssl.SSLError, ValueError, AttributeError):
                pass
        self.prefixe = b"\x02" if distant else b""
        self.ctx, self.hote, self.port, self.chemin = ctx, hote, port, chemin
        # 1) demande sans authentification pour obtenir le « nonce »
        s = self._ouvrir()
        s.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nAccept: */*\r\n\r\n" % (chemin, hote, port)).encode())
        tete = self._lire_tete(s)
        s.close()
        m = re.search(r'nonce="([^"]+)"', tete)
        if not m:
            raise RuntimeError("la box n'a pas proposé d'authentification")
        royaume = (re.search(r'realm="([^"]+)"', tete) or [None, "protected area"])[1]
        ha1 = hashlib.md5(("%s:%s:%s" % (utilisateur, royaume, mdp)).encode()).hexdigest()
        ha2 = hashlib.md5(("GET:%s" % chemin).encode()).hexdigest()
        cnonce = os.urandom(8).hex()
        rep = hashlib.md5(("%s:%s:00000001:%s:auth:%s" % (ha1, m.group(1), cnonce, ha2)).encode()).hexdigest()
        auth = ('Digest username="%s", realm="%s", nonce="%s", uri="%s", response="%s", qop=auth, nc=00000001, cnonce="%s"'
                % (utilisateur, royaume, m.group(1), chemin, rep, cnonce))
        # 2) ouverture du websocket
        cle = base64.b64encode(os.urandom(16)).decode()
        s = self._ouvrir()
        s.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nAuthorization: %s\r\n\r\n"
                   % (chemin, hote, port, cle, auth)).encode())
        tete = self._lire_tete(s)
        if " 101 " not in tete.split("\r\n")[0]:
            s.close()
            raise PermissionError("connexion refusée par la box : " + tete.split("\r\n")[0])
        self.s, self.verrou = s, threading.Lock()

    def _ouvrir(self):
        brut = socket.create_connection((self.hote, self.port), timeout=40)
        return self.ctx.wrap_socket(brut, server_hostname=self.hote)

    @staticmethod
    def _lire_tete(s):
        d = b""
        while b"\r\n\r\n" not in d:
            b = s.recv(4096)
            if not b:
                break
            d += b
        return d.decode("latin-1")

    def envoyer(self, donnees):
        donnees = self.prefixe + donnees
        n = len(donnees)
        tete = bytes([0x82]) + (bytes([0x80 | n]) if n < 126 else
                                bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                                bytes([0x80 | 127]) + struct.pack(">Q", n))
        masque = os.urandom(4)
        corps = bytes(b ^ masque[i % 4] for i, b in enumerate(donnees))
        with self.verrou:
            self.s.sendall(tete + masque + corps)

    def _exact(self, n):
        d = b""
        while len(d) < n:
            b = self.s.recv(n - len(d))
            if not b:
                raise ConnectionError("box Tydom déconnectée")
            d += b
        return d

    def recevoir(self):
        """Renvoie un message complet (bytes) ; répond aux pings."""
        morceaux = b""
        while True:
            a, b = self._exact(2)
            n = b & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            masque = self._exact(4) if b & 0x80 else None
            d = self._exact(n)
            if masque:
                d = bytes(x ^ masque[i % 4] for i, x in enumerate(d))
            op = a & 0x0F
            if op == 0x9:                       # ping → pong
                with self.verrou:
                    self.s.sendall(bytes([0x8A, 0x80 | len(d)]) + b"\0\0\0\0" + d)
                continue
            if op == 0x8:
                raise ConnectionError("box Tydom : fermeture")
            if op == 0xA:
                continue
            morceaux += d
            if a & 0x80:
                return morceaux.lstrip(b"\x02")

    def ping(self):
        with self.verrou:
            self.s.sendall(bytes([0x89, 0x80]) + os.urandom(4))

    def fermer(self):
        try:
            self.s.close()
        except Exception:
            pass


def requete_tydom(methode, uri, corps=None):
    b = json.dumps(corps) if corps is not None else ""
    return ("%s %s HTTP/1.1\r\nContent-Length: %d\r\nContent-Type: application/json; charset=UTF-8\r\n"
            "Transac-Id: %d\r\n\r\n%s" % (methode, uri, len(b), int(time.time() * 1000), b)).encode("ascii")


def lire_reponse(brut):
    """Message HTTP de la box → (uri d'origine, objet JSON ou None)."""
    tete, _, corps = brut.partition(b"\r\n\r\n")
    lignes = tete.decode("latin-1").split("\r\n")
    entetes = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip() for l in lignes[1:] if ":" in l}
    uri = entetes.get("uri-origin") or (lignes[0].split(" ")[1] if not lignes[0].startswith("HTTP") else "")
    if "chunked" in entetes.get("transfer-encoding", "").lower():
        res, i = b"", 0
        while True:
            j = corps.find(b"\r\n", i)
            if j < 0:
                break
            try:
                t = int(corps[i:j].split(b";")[0], 16)
            except ValueError:
                break
            if t == 0:
                break
            res += corps[j + 2:j + 2 + t]
            i = j + 2 + t + 2
        corps = res
    try:
        return uri, json.loads(corps.decode("utf-8")) if corps.strip() else None
    except Exception:
        return uri, None


TYPES = {"light": "lumiere", "shutter": "volet", "awning": "volet", "window": "volet", "belmonte": "volet",
         "klineShutter": "volet", "plug": "prise", "gate": "portail", "garage_door": "portail"}


def est_tydom(ip):
    """Vrai si l'appareil répond comme une box Tydom (demande d'authentification sur /mediation/client)."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1
            ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        except (ssl.SSLError, ValueError, AttributeError):
            pass
        with socket.create_connection((ip, TYDOM_PORT), timeout=15) as brut:
            with ctx.wrap_socket(brut, server_hostname=ip) as s:
                s.settimeout(20)
                s.sendall(("GET /mediation/client?mac=%s&appli=1 HTTP/1.1\r\nHost: %s\r\n\r\n" % (TYDOM_MAC, ip)).encode())
                tete = s.recv(2048).decode("latin-1")
        diag[ip] = tete.split("\r\n")[0][:60] + (" [Digest]" if "Digest" in tete else "")
        return ip if (" 401" in tete.split("\r\n")[0] and "Digest" in tete) else None
    except Exception as e:
        diag[ip] = type(e).__name__
        return None


diag = {}


def trouver_tydom():
    reseau = TYDOM_RESEAU or SONOS_RESEAU
    if not reseau:
        return None
    cand = adresses_reseau(reseau)
    log("Delta Dore : recherche de la box sur %d adresses…" % len(cand))
    with ThreadPoolExecutor(64) as ex:
        ouverts = [i for i in ex.map(lambda ip: ip if port_ouvert_n(ip, TYDOM_PORT) else None, cand) if i]
    for ip in ouverts:
        if est_tydom(ip):
            log("Delta Dore : box trouvée à l'adresse", ip)
            return ip
    log("Delta Dore : pas de box Tydom sur le réseau local (%s)" % ", ".join("%s → %s" % (k, v) for k, v in diag.items()))
    return None


def port_ouvert_n(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=0.6):
            return True
    except OSError:
        return False


class Tydom:
    def __init__(self):
        self.ws, self.equip, self.verrou, self.pret = None, {}, threading.Lock(), False

    def boucle(self):
        attente = 10
        hote = TYDOM_HOTE
        while True:
            try:
                distant = False
                if not hote:
                    hote = trouver_tydom()
                if not hote or hote == "mediation.tydom.com":
                    hote, distant = "mediation.tydom.com", True
                    log("Delta Dore : connexion par le serveur Delta Dore")
                self.ws = WS(hote, 443 if distant else TYDOM_PORT, "/mediation/client?mac=%s&appli=1" % TYDOM_MAC, TYDOM_MAC, mdp_tydom(), distant)
                log("Delta Dore : connecté à la box" + (" (via le serveur Delta Dore)" if distant else " (" + hote + ")"))
                attente = 10
                for m, u in (("GET", "/ping"), ("GET", "/configs/file"), ("POST", "/refresh/all"), ("GET", "/devices/data")):
                    self.ws.envoyer(requete_tydom(m, u))
                dernier = dernier_ping = time.time()
                self.ws.s.settimeout(10 if distant else 30)
                while True:
                    try:
                        self.traiter(self.ws.recevoir())
                    except socket.timeout:
                        pass
                    if distant and time.time() - dernier_ping > 15:      # le serveur coupe les liaisons muettes
                        self.ws.ping()
                        dernier_ping = time.time()
                    if time.time() - dernier > 60:          # garder la liaison et rafraîchir les états
                        self.ws.envoyer(requete_tydom("GET", "/ping"))
                        self.ws.envoyer(requete_tydom("GET", "/devices/data"))
                        dernier = time.time()
            except PermissionError as e:
                log("Delta Dore :", e, "— vérifiez TYDOM_MAC et le mot de passe")
                try:
                    os.remove(TYDOM_CONF)                  # le mot de passe sera redemandé au compte
                except OSError:
                    pass
                attente = 600
            except Exception as e:
                log("Delta Dore : liaison perdue", repr(e))
                if "compte Delta Dore refusée" in str(e):
                    attente = 6 * 3600        # identifiants refusés : ne pas insister (éviter un blocage du compte)
                if not TYDOM_HOTE:
                    hote = None                      # l'adresse a pu changer : on cherchera de nouveau
            self.pret = False
            if self.ws:
                self.ws.fermer()
            time.sleep(attente)
            attente = min(attente * 2, 600) if attente < 600 else 600

    def traiter(self, brut):
        uri, d = lire_reponse(brut)
        if d is None:
            return
        with self.verrou:
            if uri.startswith("/configs/file") and isinstance(d, dict):
                for e in d.get("endpoints", []):
                    cle = "%s_%s" % (e.get("id_device"), e.get("id_endpoint"))
                    usage = e.get("last_usage") or e.get("first_usage") or ""
                    x = self.equip.setdefault(cle, {"id": cle})
                    x.update({"dev": e.get("id_device"), "ep": e.get("id_endpoint"), "nom": e.get("name") or cle,
                              "usage": usage, "type": TYPES.get(usage, "autre")})
                self.pret = True
                log("Delta Dore :", ", ".join("%s (%s)" % (x["nom"], x["type"]) for x in self.equip.values()
                                             if x.get("type") != "autre") or "aucun équipement")
            elif uri.startswith("/devices/data") or uri.startswith("/devices/") and "/data" in uri:
                for dev in d if isinstance(d, list) else [d]:
                    if not isinstance(dev, dict):
                        continue
                    for ep in dev.get("endpoints", []):
                        cle = "%s_%s" % (dev.get("id"), ep.get("id"))
                        x = self.equip.setdefault(cle, {"id": cle, "dev": dev.get("id"), "ep": ep.get("id"),
                                                        "nom": cle, "type": "autre"})
                        for v in ep.get("data") or []:
                            if v.get("validity", "upToDate") != "expired" and v.get("name"):
                                x.setdefault("valeurs", {})[v["name"]] = v.get("value")

    def liste(self):
        res = []
        with self.verrou:
            for x in self.equip.values():
                if x.get("type") == "autre":
                    continue
                v = x.get("valeurs", {})
                e = {"id": x["id"], "nom": x["nom"], "type": x["type"]}
                if x["type"] in ("lumiere", "prise"):
                    if "level" in v and v["level"] is not None:
                        e["niveau"] = v["level"]
                        e["allume"] = v["level"] > 0
                    elif "plugCmd" in v or "on" in v:
                        e["allume"] = v.get("on") in (True, "ON") or v.get("plugCmd") == "ON"
                elif x["type"] == "volet" and v.get("position") is not None:
                    e["position"] = v["position"]       # 100 = ouvert, 0 = fermé
                res.append(e)
        return sorted(res, key=lambda e: e["nom"])

    def commande(self, ident, action, valeur=None):
        with self.verrou:
            x = self.equip.get(ident)
        if not x or not self.ws or not self.pret:
            raise LookupError("équipement inconnu ou box non connectée")
        v = x.get("valeurs", {})
        if action in ("on", "off", "basculer"):
            allume = (v.get("level") or 0) > 0 or v.get("on") in (True, "ON") or v.get("plugCmd") == "ON"
            on = (not allume) if action == "basculer" else action == "on"
            if x["type"] == "prise" and "level" not in v:
                nom, val = "plugCmd", "ON" if on else "OFF"
            elif "level" in v:
                nom, val = "level", 100 if on else 0
            else:
                nom, val = "levelCmd", "ON" if on else "OFF"
        elif action in ("haut", "bas", "stop"):
            nom, val = "positionCmd", {"haut": "UP", "bas": "DOWN", "stop": "STOP"}[action]
        elif action == "position":
            nom, val = "position", max(0, min(100, int(valeur)))
        elif action == "niveau":
            nom, val = "level", max(0, min(100, int(valeur)))
        else:
            raise ValueError("action inconnue")
        self.ws.envoyer(requete_tydom("PUT", "/devices/%s/endpoints/%s/data" % (x["dev"], x["ep"]),
                                      [{"name": nom, "value": val}]))
        if nom in ("level", "position"):                    # affichage immédiat, confirmé ensuite par la box
            with self.verrou:
                x.setdefault("valeurs", {})[nom] = val
        threading.Timer(1.5, lambda: self.ws and self.ws.envoyer(requete_tydom("GET", "/devices/data"))).start()


tydom = Tydom() if TYDOM else None


class ApiSonos(BaseHTTPRequestHandler):
    def repondre(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path == "/maison":
                if not tydom:
                    return self.repondre(404, {"erreur": "Delta Dore non configuré"})
                self.repondre(200, {"connecte": tydom.pret, "equipements": tydom.liste(), "accueil": TYDOM_ACCUEIL})
            elif u.path == "/maison/cmd":
                if not tydom:
                    return self.repondre(404, {"erreur": "Delta Dore non configuré"})
                tydom.commande(q.get("id"), q.get("a", ""), q.get("v"))
                self.repondre(200, {"ok": True, "equipements": tydom.liste()})
            elif u.path == "/sonos":
                self.repondre(200, {"groupes": [etat_groupe(g) for g in topologie()]})
            elif u.path == "/sonos/cmd":
                g = next((x for x in topologie() if x["id"] == q.get("id")), None)
                if not g:
                    return self.repondre(404, {"erreur": "groupe introuvable"})
                commande(g, q.get("a", ""))
                self.repondre(200, etat_groupe(g))
            else:
                self.repondre(404, {"erreur": "inconnu"})
        except Exception as ex:
            self.repondre(500, {"erreur": repr(ex)})

    def log_message(self, *a):
        pass


def demarrer_sonos():
    srv = ThreadingHTTPServer(("0.0.0.0", SONOS_PORT_API), ApiSonos)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    g = topologie() if SONOS else []
    log("API Sonos sur le port %d — %s" % (SONOS_PORT_API,
        ", ".join(x["nom"] for x in g) if g else "aucune enceinte trouvée pour l'instant"))


log("Relais démarré — Netatmo %s" % ("activé" if NETATMO else "non configuré"))
if TYDOM:
    threading.Thread(target=tydom.boucle, daemon=True).start()
if SONOS or TYDOM:
    try:
        demarrer_sonos()
    except Exception as e:
        log("API Sonos impossible :", repr(e))
while True:
    maj_page()
    if ACTUS and actus_a_faire():
        maj_actus()
    if AGENDA_ICS:
        maj_agenda()
    maj_cantine()
    if FOOT_FFF:
        maj_fff()
    if NETATMO:
        maj_netatmo()
    time.sleep(INTERVALLE)
