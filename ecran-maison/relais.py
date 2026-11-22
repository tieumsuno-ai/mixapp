#!/usr/bin/env python3
# Relais de l'écran maison — tourne en continu sur le NAS (Container Station).
# Toutes les 15 minutes :
#   1. met à jour la page (index.html) dans le dossier Web si une nouvelle version est publiée ;
#   2. si les codes Netatmo sont fournis, relève la température de chaque pièce
#      et l'écrit dans interieur.json, que la page affiche.
# En parallèle, une petite API (port 8099) permet à la page de piloter les enceintes Sonos
# du réseau local (lecture, pause, titre suivant/précédent, volume du groupe).
# Les codes Netatmo arrivent par variables d'environnement au premier démarrage ;
# le jeton de renouvellement (il change à chaque fois) est ensuite gardé dans /config/jeton.json.
# config.js (réglages personnels) n'est jamais touché.
import datetime, html, ipaddress, json, os, re, socket, threading, time, urllib.error, urllib.parse, urllib.request
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
SONOS_RESEAU = os.environ.get("SONOS_RESEAU", "")   # ex. 192.168.68.0/22 (ou 192.168.68 pour un /24)
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


def adresses_reseau():
    res = []
    for r in [x.strip() for x in SONOS_RESEAU.split(",") if x.strip()]:
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


class API(BaseHTTPRequestHandler):
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
            if u.path == "/sonos":
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
    srv = ThreadingHTTPServer(("0.0.0.0", SONOS_PORT_API), API)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    g = topologie()
    log("API Sonos sur le port %d — %s" % (SONOS_PORT_API,
        ", ".join(x["nom"] for x in g) if g else "aucune enceinte trouvée pour l'instant"))


log("Relais démarré — Netatmo %s" % ("activé" if NETATMO else "non configuré"))
if SONOS:
    try:
        demarrer_sonos()
    except Exception as e:
        log("API Sonos impossible :", repr(e))
while True:
    maj_page()
    if NETATMO:
        maj_netatmo()
    time.sleep(INTERVALLE)
