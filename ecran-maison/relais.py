#!/usr/bin/env python3
# Relais de l'écran maison — tourne en continu sur le NAS (Container Station).
# Toutes les 15 minutes :
#   1. met à jour la page (index.html) dans le dossier Web si une nouvelle version est publiée ;
#   2. si les codes Netatmo sont fournis, relève la température de chaque pièce
#      et l'écrit dans interieur.json, que la page affiche.
# Les codes Netatmo arrivent par variables d'environnement au premier démarrage ;
# le jeton de renouvellement (il change à chaque fois) est ensuite gardé dans /config/jeton.json.
# config.js (réglages personnels) n'est jamais touché.
import datetime, json, os, time, urllib.error, urllib.parse, urllib.request

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


log("Relais démarré — Netatmo %s" % ("activé" if NETATMO else "non configuré"))
while True:
    maj_page()
    if NETATMO:
        maj_netatmo()
    time.sleep(INTERVALLE)
