"""Captures d'écran réelles de l'installation et de l'utilisation de MixApp
sous Windows, pour le tutoriel. Lancé par GitHub Actions (windows-latest).

    python tools/tuto_win_captures.py installateur.exe dossier_musique dossier_captures
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path

from PIL import ImageGrab
from pywinauto import Desktop, keyboard, mouse

SETUP, MUSIC, OUT = sys.argv[1], Path(sys.argv[2]).resolve(), Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
LOG = []
N = [0]


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    LOG.append(msg)


def snap(name, wait=1.2):
    time.sleep(wait)
    N[0] += 1
    f = OUT / f"{N[0]:02d}_{name}.png"
    ImageGrab.grab(all_screens=True).save(f)
    log("capture", f.name)


def step(label, fn):
    try:
        fn()
    except Exception:
        log(f"⚠ étape « {label} » :", traceback.format_exc().splitlines()[-1])


def find_window(title_re, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for w in Desktop(backend="uia").windows():
            try:
                import re
                if re.search(title_re, w.window_text() or ""):
                    return Desktop(backend="uia").window(handle=w.handle)
            except Exception:
                pass
        time.sleep(0.5)
    raise TimeoutError(title_re)


def click(win, **kw):
    el = win.child_window(**kw).wrapper_object()
    el.click_input()
    return el


env = dict(os.environ, MIXAPP_FAKE_AUDIO="1")
appdata = Path(os.environ["APPDATA"]) / "MixApp"

# fenêtres de la machine de test (console…) réduites, pochettes en ligne coupées
# (musique fictive : on garde les pochettes intégrées aux MP3 de démo)
subprocess.run(["powershell", "-NoProfile", "-Command",
                "(New-Object -ComObject Shell.Application).MinimizeAll()"], capture_output=True)
time.sleep(2)
appdata.mkdir(parents=True, exist_ok=True)
(appdata / "settings.json").write_text(json.dumps({"covers_online": False, "volume": 0.9}))

# ------------------------------------------------------------------ 1. installateur
log("== installateur")
subprocess.Popen([SETUP], env=env)
wiz = find_window(r"(Installation|Setup).*MixApp|MixApp.*(Installation|Setup)", 90)
wiz.set_focus()
snap("install_dossier", 2.5)                      # page « Dossier de destination »
keyboard.send_keys("{ENTER}")
snap("install_taches", 2.0)                        # page « Tâches supplémentaires »
def tick():
    r = wiz.rectangle()
    # case « Créer une icône sur le Bureau » (position fixe dans l'assistant)
    mouse.click(coords=(r.left + 52, r.top + 175))
step("icône bureau", tick)
snap("install_taches_cochee", 0.8)
keyboard.send_keys("{ENTER}")
snap("install_pret", 2.0)                          # page « Prêt à installer »
keyboard.send_keys("{ENTER}")
snap("install_en_cours", 1.0)
time.sleep(25)
snap("install_fin", 1.0)                           # page « Fin de l'installation »
keyboard.send_keys("{ENTER}")                      # « Terminer » (lance MixApp)

# ------------------------------------------------------------------ 2. premier lancement
log("== premier lancement")
box = None
try:
    box = find_window(r"^MixApp$", 120)
    snap("bienvenue", 3.0)
    keyboard.send_keys("{ENTER}")                  # ferme « Bienvenue »
    snap("choix_dossier", 3.0)
    dlg = find_window(r"Dossier de musique", 20)

    def pick():
        ed = dlg.child_window(title_re="Dossier.*", control_type="Edit").wrapper_object()
        ed.set_edit_text(str(MUSIC))
        snap("choix_dossier_saisi", 0.8)
        click(dlg, title_re="Sélectionner.*dossier", control_type="Button")
    step("choix du dossier", pick)
except Exception:
    log("⚠ premier lancement :", traceback.format_exc().splitlines()[-1])

# si le choix du dossier n'a pas abouti, on le règle directement
time.sleep(3)
st = appdata / "settings.json"
try:
    roots = json.loads(st.read_text()).get("roots", [])
except Exception:
    roots = []
if str(MUSIC) not in roots:
    log("dossier réglé directement")
    subprocess.run(["taskkill", "/IM", "MixApp.exe", "/F"], capture_output=True)
    time.sleep(2)
    st.write_text(json.dumps({"roots": [str(MUSIC)], "volume": 0.9, "covers_online": False}))
    exe = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "MixApp" / "MixApp.exe"
    subprocess.Popen([str(exe)], env=env)

# ------------------------------------------------------------------ 3. analyse
log("== analyse")
db = appdata / "library.db"
for i in range(120):
    time.sleep(3)
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        n, tot = c.execute("select count(bpm), count(*) from tracks").fetchone()
        c.close()
    except Exception:
        n, tot = 0, 0
    if i == 3:
        snap("analyse_en_cours", 0)
    if tot and n >= tot:
        break
log("morceaux analysés :", n, "/", tot)
time.sleep(8)                                       # pochettes

main = find_window(r"^MixApp$", 30)
step("agrandir", lambda: main.maximize())
time.sleep(2)
main.set_focus()
snap("fenetre", 1.5)

# ------------------------------------------------------------------ 4. utilisation
# visite guidée automatique : l'appli se pilote toute seule et capture sa fenêtre
log("== visite guidée")
subprocess.run(["taskkill", "/IM", "MixApp.exe", "/F"], capture_output=True)
time.sleep(3)
exe = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "MixApp" / "MixApp.exe"
p = subprocess.Popen([str(exe)], env=dict(env, MIXAPP_TOUR=str((OUT / "tour").resolve())))
try:
    p.wait(timeout=600)
except subprocess.TimeoutExpired:
    log("⚠ visite trop longue")
    p.kill()
(OUT / "journal.txt").write_text("\n".join(LOG), encoding="utf-8")
log("terminé")
