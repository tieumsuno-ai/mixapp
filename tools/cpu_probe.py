"""Mesure du processeur pris par MixApp pendant la lecture (son simulé en
temps réel) : vue Platines, vue liste, et vue Platines pendant un mix.
Usage : python tools/cpu_probe.py [secondes_par_phase] [--plein-ecran]"""
import os
import sys
import threading
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication  # noqa: E402

from mixapp import gui  # noqa: E402

DUR = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1][0].isdigit() else 20
FULL = "--plein-ecran" in sys.argv
SIZE = next((a.split("=")[1] for a in sys.argv if a.startswith("--taille=")), "1440x860")
MODE = next((a.split("=")[1] for a in sys.argv if a.startswith("--mode=")), "")
QUICK = "--rapide" in sys.argv
app = QApplication(sys.argv[:1])
app.setStyleSheet(gui.STYLE)
w = gui.MainWindow(None)
w.resize(*[int(v) for v in SIZE.split("x")])
w.showFullScreen() if FULL else w.show()
proc = psutil.Process()
out = []


def say(s):
    print(s, flush=True)
    out.append(s)


def pump(s):
    t = time.time() + s
    while time.time() < t:
        app.processEvents()
        time.sleep(0.005)


def measure(label):
    th0 = {t.id: t.user_time + t.system_time for t in proc.threads()}
    c0 = proc.cpu_times()
    t1 = time.time()
    pump(DUR)
    dt = time.time() - t1
    c1 = proc.cpu_times()
    th1 = {t.id: t.user_time + t.system_time for t in proc.threads()}
    tot = (c1.user + c1.system - c0.user - c0.system) / dt * 100
    per = sorted(((th1[k] - th0.get(k, 0)) / dt * 100 for k in th1), reverse=True)
    say(f"{label:34s} {tot:6.1f} % d'un cœur · fils les plus actifs : "
        + ", ".join(f"{p:.1f}" for p in per[:5]))


t0 = time.time()
pump(3)
while w._analysis_running and time.time() - t0 < 600:
    pump(0.5)
say(f"écran : {app.primaryScreen().size().width()}x{app.primaryScreen().size().height()} "
    f"× {app.primaryScreen().devicePixelRatio()} · fenêtre {w.width()}x{w.height()} · {os.cpu_count()} cœurs")
if not QUICK:
    measure("repos (rien ne joue)")
for i in range(w.playlist_list.count()):
    if "Soirée Disco" in w.playlist_list.item(i).text():
        w.playlist_list.setCurrentRow(i)
pump(1)
w.table.selectRow(0)
w.play_from_selection()
pump(8)
if not QUICK:
    measure("lecture · vue liste")
w.btn_view.setChecked(True)
if MODE:
    w.deck.mode_box.setCurrentIndex({"fluide": 0, "leger": 1, "aucune": 2}[MODE])
say(f"   fenêtre réelle {w.width()}x{w.height()} · mode {w.deck.mode} · natif={os.environ.get('MIXAPP_NATIVE_ANIM', '0')}")
pump(3)
measure("lecture · vue Platines")
w._on_seek(0.80)
pump(4)
measure("vue Platines pendant un mix")
p = os.environ.get("GITHUB_STEP_SUMMARY")
if p:
    with open(p, "a") as f:
        f.write("```\n" + "\n".join(out) + "\n```\n")
os._exit(0)
