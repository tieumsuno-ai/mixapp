"""Visite guidée automatique pour fabriquer les tutoriels : l'appli se pilote
toute seule et enregistre des captures de sa fenêtre (MIXAPP_TOUR=dossier).
Utilisé seulement par GitHub Actions, avec la musique de démonstration."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def run(w, out: str, playlist: str = "Soirée Disco", search: str = "playa"):
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = [0]
    log = []

    WIDGETS = ["search_box", "btn_folders", "btn_full", "playlist_list", "table", "btn_play_here",
               "btn_play_next", "btn_enqueue", "queue_list", "btn_up", "btn_down", "btn_remove",
               "cover", "now_title", "now_info", "fact_label", "counter", "transition_label",
               "progress", "remain_label", "btn_play", "btn_stop33", "btn_pullup", "btn_next",
               "btn_hl", "btn_dj", "volume", "eq", "mix_badge", "status", "tracks_title"]

    def geometry():
        g = {}
        for name in WIDGETS:
            wd = getattr(w, name, None)
            if wd is None or not wd.isVisible():
                continue
            p = wd.mapTo(w, wd.rect().topLeft())
            g[name] = [p.x(), p.y(), wd.width(), wd.height()]
        vp = w.table.viewport()
        for r in range(min(w.table.rowCount(), 6)):
            rect = w.table.visualRect(w.table.model().index(r, 1)).united(
                w.table.visualRect(w.table.model().index(r, w.table.columnCount() - 1)))
            p = vp.mapTo(w, rect.topLeft())
            g[f"row{r}"] = [p.x(), p.y(), rect.width(), rect.height()]
        vq = w.queue_list.viewport()
        for r in range(min(w.queue_list.count(), 4)):
            rect = w.queue_list.visualItemRect(w.queue_list.item(r))
            p = vq.mapTo(w, rect.topLeft())
            g[f"queue{r}"] = [p.x(), p.y(), rect.width(), rect.height()]
        vpl = w.playlist_list.viewport()
        for r in range(w.playlist_list.count()):
            rect = w.playlist_list.visualItemRect(w.playlist_list.item(r))
            p = vpl.mapTo(w, rect.topLeft())
            g[f"pl_{w.playlist_list.item(r).text().split(chr(10))[0]}"] = [p.x(), p.y(), rect.width(), rect.height()]
        return g

    def shot(name):
        import json
        n[0] += 1
        QApplication.processEvents()
        base = out_dir / f"{n[0]:02d}_{name}"
        w.grab().save(str(base) + ".png")
        (Path(str(base) + ".json")).write_text(json.dumps(geometry(), ensure_ascii=False), encoding="utf-8")
        log.append(name)

    def analysed():
        try:
            n_ok, tot = w.conn.execute("select count(bpm), count(*) from tracks").fetchone()
            return tot > 0 and n_ok >= tot
        except Exception:
            return False

    def select_playlist(name):
        for i in range(w.playlist_list.count()):
            if name in w.playlist_list.item(i).text():
                w.playlist_list.setCurrentRow(i)
                return

    def mixing(min_frac=0.35):
        eng = w.engine
        st = eng.transition_state() if eng else None
        return bool(st and st[1] >= min_frac)

    # (condition d'attente ou délai, action, capture)
    steps = [
        (analysed, None, None),
        (4.0, None, "fenetre"),
        (0.2, lambda: select_playlist(playlist), None),
        (1.5, None, "playlist"),
        (0.2, lambda: (w.table.selectRow(0), w.play_from_selection()), None),
        (7.0, None, "lecture"),
        (0.2, lambda: w.search_box.setText(search), None),
        (1.5, None, "recherche"),
        (0.2, lambda: w.table.selectRow(0), None),
        (0.8, None, "recherche_selection"),
        (0.2, lambda: w.queue_selected(front=True), None),
        (1.5, None, "jouer_ensuite"),
        (0.2, lambda: w.search_box.setText(""), None),
        (0.2, lambda: select_playlist(playlist), None),
        (1.5, lambda: w.btn_dj.setChecked(True), None),
        (2.0, None, "flow_dj"),
        (0.2, lambda: w.btn_hl.setChecked(True), None),
        (3.0, None, "temps_forts"),
        (0.2, lambda: w._on_seek(0.55), None),
        (1.0, None, "timeline"),
        (lambda: mixing(0.35), None, "mix_en_cours"),
        (4.0, None, "mix_en_cours_2"),
        (lambda: not mixing(0.0), None, None),
        (12.0, lambda: w.play_pause(), None),
        (0.8, None, "pause_echo"),
        (6.0, None, "en_pause"),
        (0.2, lambda: w.play_pause(), None),
        (3.0, None, "reprise"),
        (0.2, lambda: w.styled_stop("turntable"), None),
        (0.7, None, "stop_33"),
        (6.0, None, "arrete"),
    ]
    state = {"i": 0, "t0": time.time(), "acted": False}

    def tick():
        i = state["i"]
        if i >= len(steps):
            (out_dir / "journal.txt").write_text("\n".join(log), encoding="utf-8")
            QApplication.quit()
            return
        cond, action, name = steps[i]
        if not state["acted"]:
            if action:
                try:
                    action()
                except Exception as e:                     # on continue la visite
                    log.append(f"⚠ étape {i} : {e}")
            state["acted"], state["t0"] = True, time.time()
        waited = time.time() - state["t0"]
        done = (waited >= cond) if isinstance(cond, float) else (cond() or waited > 240)
        if done:
            if name:
                shot(name)
            state["i"] += 1
            state["acted"] = False
        QTimer.singleShot(250, tick)

    w.resize(1600, 980)
    QTimer.singleShot(1500, tick)
